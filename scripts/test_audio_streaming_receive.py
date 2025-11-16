#!/usr/bin/env python3
"""
Simple WebSocket Audio Streamer
Loads a .wav file, splits it into chunks, and streams to WebSocket server
"""

import json
import time
import base64
import logging
import numpy as np
import soundfile as sf
import websocket

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SimpleAudioStreamer:
    """Simple audio streamer for WebSocket"""
    
    def __init__(self, server_url: str, call_id: str = "test_call_123"):
        self.server_url = server_url
        self.call_id = call_id
        self.ws = None
        
        # Audio settings
        self.target_sample_rate = 16000
        self.chunk_duration_ms = 40  # 40ms chunks
        
    def connect(self):
        """Connect to WebSocket server"""
        ws_url = f"{self.server_url}/ws/call?call_id={self.call_id}"
        logger.info(f"Connecting to {ws_url}")
        
        self.ws = websocket.create_connection(ws_url)
        
        # Send connected event
        self.ws.send(json.dumps({"event": "connected"}))
        logger.info("✅ Connected!")
        
    def load_audio(self, file_path: str) -> np.ndarray:
        """Load and process audio file"""
        logger.info(f"Loading audio file: {file_path}")
        
        # Load audio
        audio_data, sample_rate = sf.read(file_path)
        
        # Convert to mono if stereo
        if len(audio_data.shape) > 1:
            audio_data = np.mean(audio_data, axis=1)
            logger.info("Converted stereo to mono")
        
        # Resample if needed
        if sample_rate != self.target_sample_rate:
            target_length = int(len(audio_data) * self.target_sample_rate / sample_rate)
            audio_data = np.interp(
                np.linspace(0, len(audio_data), target_length),
                np.arange(len(audio_data)),
                audio_data
            )
            logger.info(f"Resampled from {sample_rate}Hz to {self.target_sample_rate}Hz")
        
        # Convert to int16
        if audio_data.dtype != np.int16:
            if np.max(np.abs(audio_data)) > 1.0:
                audio_data = audio_data / np.max(np.abs(audio_data))
            audio_data = (audio_data * 32767).astype(np.int16)
        
        logger.info(f"Audio loaded: {len(audio_data)} samples, {len(audio_data)/self.target_sample_rate:.2f} seconds")
        return audio_data
    
    def stream_audio(self, audio_data: np.ndarray):
        """Stream audio in chunks to WebSocket"""
        # Calculate chunk size in samples
        chunk_samples = int(self.target_sample_rate * self.chunk_duration_ms / 1000)
        
        total_chunks = (len(audio_data) + chunk_samples - 1) // chunk_samples
        logger.info(f"Streaming {total_chunks} chunks of {self.chunk_duration_ms}ms each")
        
        start_time = time.time()
        
        for i in range(0, len(audio_data), chunk_samples):
            chunk = audio_data[i:i + chunk_samples]
            
            # Pad last chunk if needed
            if len(chunk) < chunk_samples:
                chunk = np.pad(chunk, (0, chunk_samples - len(chunk)), 'constant')
            
            # Convert to bytes
            audio_bytes = chunk.tobytes()
            
            # Encode to base64
            base64_audio = base64.b64encode(audio_bytes).decode('utf-8')
            
            # Create message
            message = {
                "event": "media",
                "media_name": "user_audio.pcm",
                "timestamp": int(time.time() * 1000),
                "media": {"payload": base64_audio},
                "description": "Client sends PCM audio chunks"
            }
            
            # Send to WebSocket
            self.ws.send(json.dumps(message))
            
            # Progress indicator
            chunk_num = i // chunk_samples + 1
            if chunk_num % 25 == 0:  # Print every 25 chunks (1 second)
                logger.info(f"Sent chunk {chunk_num}/{total_chunks}")
            
            # Wait for real-time streaming
            time.sleep(self.chunk_duration_ms / 1000.0)
        
        elapsed = time.time() - start_time
        logger.info(f"✅ Streaming completed in {elapsed:.2f}s")
    
    def disconnect(self):
        """Disconnect from WebSocket"""
        if self.ws:
            logger.info("Disconnecting...")
            self.ws.close()
            logger.info("✅ Disconnected")


def main():
    """Main function"""
    # Configuration
    SERVER_URL = "ws://localhost:9923"
    CALL_ID = "simple_test_001"
    # AUDIO_FILE = r"C:\Users\thanglq12\Documents\tmp__bz5lrw_right.wav"
    AUDIO_FILE = "/home/thanglq150188/Work/sentiment-scoring/right.wav"
    
    print("=" * 60)
    print("🎤 Simple Audio Streamer")
    print("=" * 60)
    print()
    
    # Create streamer
    streamer = SimpleAudioStreamer(SERVER_URL, CALL_ID)
    
    try:
        # Connect
        streamer.connect()
        
        # Load audio
        audio_data = streamer.load_audio(AUDIO_FILE)
        
        # Stream audio
        streamer.stream_audio(audio_data)
        
        print()
        print("🎉 Done!")
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        # Disconnect
        streamer.disconnect()


if __name__ == "__main__":
    main()