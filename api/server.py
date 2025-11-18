import asyncio
import json
import base64
import logging
import wave
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from api.ws_utils import WebSocketSynchronizer


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CallbotWSServer:
    """
    Simple WebSocket server to receive and print audio chunks
    """
    
    def __init__(self):
        self.active_clients = set()
        self._synchronizers = {}
        self._audio_chunk_counters = {}  # Track chunk count per call_id
        self._audio_buffers = {}  # Store audio chunks per call_id
        
        # Create recordings directory if it doesn't exist
        self.recordings_dir = Path("recordings")
        self.recordings_dir.mkdir(exist_ok=True)
        
        logger.info("CallbotWSServer initialized")
    
    def save_audio_to_wav(self, call_id: str, audio_data: bytes):
        """
        Save concatenated audio data to a WAV file.
        Assumes mulaw 8kHz audio (common for telephony).
        Adjust parameters if your audio format is different.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.recordings_dir / f"{call_id}_{timestamp}.wav"
        
        try:
            # WAV file parameters - adjust these based on your audio format
            # Common telephony formats:
            # - mulaw: 8000 Hz, 1 channel, 8-bit
            # - PCM: 16000 Hz, 1 channel, 16-bit
            
            with wave.open(str(filename), 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 2 bytes = 16-bit (change to 1 for 8-bit)
                wav_file.setframerate(16000)  # 8kHz sample rate (common for telephony)
                wav_file.writeframes(audio_data)
            
            print(f"[{call_id}] 💾 Audio saved to: {filename}")
            print(f"[{call_id}] 📊 File size: {len(audio_data)} bytes")
            print(f"[{call_id}] ⏱️ Duration: ~{len(audio_data) / (16000 * 2):.2f} seconds")
            print()
            
            return str(filename)
            
        except Exception as e:
            logger.error(f"[{call_id}] Error saving audio to WAV: {e}")
            return None
    
    async def user_message_handler(self, synchronizer: WebSocketSynchronizer, call_id: str):
        """
        Handles incoming messages from the client (user audio and control messages).
        Uses synchronizer for safe message receiving.
        """
        logger.info(f"[{call_id}] User message handler started")
        
        # Initialize counter and buffer for this call
        self._audio_chunk_counters[call_id] = 0
        self._audio_buffers[call_id] = bytearray()
        
        try:
            end_call = False
            
            while not end_call:
                # Receive message through synchronizer (which receives from WebSocket)
                try:
                    # Use a short timeout to allow checking end_call flag periodically
                    message = await synchronizer.receive_message(timeout=0.1)
                    if not message:
                        # No message received within timeout, continue checking
                        continue
                except Exception as e:
                    logger.error(f"[{call_id}] Receive message failed: {e}")
                    await asyncio.sleep(0.001)
                    continue

                audio = None
                
                # Parse JSON message if needed
                if 'text' in message and len(message['text']) > 0:
                    try:
                        message = json.loads(message['text'])
                    except Exception as e:
                        logger.error(f"[{call_id}] user handler failed: {e}, msg: {message['text']}")
                        await asyncio.sleep(0.004)
                        continue
                
                # Handle different message types
                if message.get("event") == "connected":
                    print(f"[{call_id}] ✅ Call connected!")
                    print()
                    
                elif "bytes" in message:
                    audio = message["bytes"]
                    
                elif message.get("media", {}).get("payload"):
                    payload = message["media"]["payload"]
                    # Decode base64
                    audio = base64.b64decode(payload)
                    
                elif message.get('type') == 'websocket.disconnect' or message.get('event') == 'websocket_disconnect':
                    reason = message.get('reason', 'unknown')
                    print(f"[{call_id}] 🔌 WebSocket disconnected - reason: {reason}")
                    print(f"[{call_id}] 📊 Total audio chunks received: {self._audio_chunk_counters[call_id]}")
                    print()
                    end_call = True
                    
                else:
                    print(f"[{call_id}] 📨 Unknown message type: {message}")
                    print()
                
                # Handle audio chunk
                if audio is not None:
                    self._audio_chunk_counters[call_id] += 1

                    # Append to buffer
                    self._audio_buffers[call_id].extend(audio)

                    # Print every 100 chunks
                    if self._audio_chunk_counters[call_id] % 100 == 0:
                        print(f"[{call_id}] 📦 Audio chunk #{self._audio_chunk_counters[call_id]}:")
                        print(f"  - Size: {len(audio)} bytes")
                        print(f"  - Total buffered: {len(self._audio_buffers[call_id])} bytes")
                        print(f"  - Receive queue size: {synchronizer._receive_queue.qsize()}")
                        # Print first 20 bytes as hex
                        preview = audio[:20].hex()
                        print(f"  - Preview (hex): {preview}...")
                        print()

                # Yield control to allow other tasks to run, but don't add unnecessary delay
                # Only yield when we've processed a message to keep consumption fast
                if audio is not None or message.get("event") in ["connected", "websocket_disconnect"]:
                    await asyncio.sleep(0)

        except Exception as e:
            import traceback
            logger.error(f"[{call_id}] Error in user message handler: {e}, traceback: {traceback.format_exc()}")
        finally:
            # Save audio to WAV file
            if call_id in self._audio_buffers and len(self._audio_buffers[call_id]) > 0:
                print(f"[{call_id}] 📊 Final audio chunks received: {self._audio_chunk_counters[call_id]}")
                print(f"[{call_id}] 💾 Saving audio to WAV file...")
                
                audio_data = bytes(self._audio_buffers[call_id])
                self.save_audio_to_wav(call_id, audio_data)
                
                # Cleanup
                del self._audio_buffers[call_id]
            
            if call_id in self._audio_chunk_counters:
                del self._audio_chunk_counters[call_id]
                
            logger.info(f"[{call_id}] User message handler cleanup")

    async def call_ws_handler(self, websocket: WebSocket, call_id: str):
        """
        Unified websocket endpoint with proper synchronization.
        Uses WebSocketSynchronizer to prevent duplex conflicts.
        """
        synchronizer = None
        try:
            # Create and initialize WebSocket synchronizer
            synchronizer = WebSocketSynchronizer(call_id)
            await synchronizer.initialize(websocket)
            self._synchronizers[call_id] = synchronizer
            
            # Create business logic handler that communicates through synchronizer
            user_task = asyncio.create_task(self.user_message_handler(synchronizer, call_id))
            
            # Wait for handler to complete
            await user_task
            
            logger.info(f"[{call_id}] WebSocket handler finished")
            
        except Exception as e:
            import traceback
            logger.error(f"[{call_id}] Error in call_ws_handler: {e}, traceback: {traceback.format_exc()}")
        finally:
            # Clean up synchronizer
            if synchronizer:
                await synchronizer.shutdown()
            self._synchronizers.pop(call_id, None)
            logger.info(f"[{call_id}] WebSocket handler cleanup complete")


# Create FastAPI app
app = FastAPI(title="CallBot WebSocket Server", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize the server instance
callbot_server = CallbotWSServer()


@app.websocket("/ws/call")
async def websocket_call_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for call handling with call_id as query parameter.
    Format: /ws/call?call_id=xxx
    """
    # Get call_id from query parameters
    query_params = websocket.query_params
    call_id = query_params.get('call_id')
    
    if not call_id:
        logger.error("Missing call_id in query parameters for WebSocket endpoint")
        await websocket.accept()
        await websocket.close(code=1008, reason="Missing call_id parameter")
        return
    
    logger.info(f"[{call_id}] New WebSocket connection established")
    
    # Accept the WebSocket connection
    await websocket.accept()
    logger.info(f"[{call_id}] WebSocket connection accepted")
    
    try:
        await callbot_server.call_ws_handler(websocket, call_id)
    except Exception as e:
        logger.error(f"[{call_id}] Error in websocket endpoint: {e}")
    
    logger.info(f"[{call_id}] WebSocket endpoint finished")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "active_connections": len(callbot_server.active_clients)
    }


@app.get("/")
async def root():
    """Root endpoint with usage instructions"""
    return {
        "message": "CallBot WebSocket Server",
        "usage": "Connect to: ws://localhost:9922/ws/call?call_id=YOUR_CALL_ID",
        "endpoints": {
            "websocket": "/ws/call?call_id=<call_id>",
            "health": "/health"
        },
        "recordings": f"Audio files saved to: {callbot_server.recordings_dir.absolute()}"
    }


def main():
    """Start the server"""
    print("=" * 60)
    print("🎧 CallBot WebSocket Server - Audio Receiver")
    print("=" * 60)
    print()
    print("Server starting on: http://0.0.0.0:9922")
    print("WebSocket endpoint: ws://0.0.0.0:9922/ws/call?call_id=<call_id>")
    print(f"Recordings directory: {callbot_server.recordings_dir.absolute()}")
    print()
    print("Example connection:")
    print("  ws://localhost:9922/ws/call?call_id=test123")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 60)
    print()
    
    # Run the server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=9922,
        log_level="info",
        ws_ping_interval=None,  # Disable WebSocket ping to avoid timeout disconnects
        ws_ping_timeout=None    # Disable WebSocket ping timeout
    )


if __name__ == "__main__":
    main()