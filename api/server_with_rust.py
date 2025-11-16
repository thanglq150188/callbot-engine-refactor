import asyncio
import json
import logging
import wave
import time
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from api.websocket_utils import WebSocketSynchronizer

# Import Rust-powered audio processor
try:
    import callbot_rust
    print("✅ Rust audio processor loaded successfully")
except ImportError as e:
    raise RuntimeError(
        "❌ Rust module is required but not available. "
        "Please build the Rust extension: cd callbot_rust && maturin develop --release"
    ) from e


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CallbotWSServer:
    """
    High-performance WebSocket server using Rust for audio processing
    """

    def __init__(self):
        self.active_clients = set()
        self._synchronizers = {}
        self._audio_chunk_counters = {}  # Track chunk count per call_id

        # Rust-powered audio processor
        self._audio_processor = callbot_rust.AudioProcessor()
        logger.info("🦀 Using Rust AudioProcessor for high performance")

        # Performance tracking
        self._perf_stats = {}  # call_id -> {start_time, chunk_times}

        # Create recordings directory if it doesn't exist
        self.recordings_dir = Path("recordings")
        self.recordings_dir.mkdir(exist_ok=True)

        logger.info("CallbotWSServer initialized with Rust AudioProcessor")

    def save_audio_to_wav(self, call_id: str, audio_data: bytes):
        """Save concatenated audio data to a WAV file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.recordings_dir / f"{call_id}_rust_{timestamp}.wav"

        try:
            with wave.open(str(filename), 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 2 bytes = 16-bit
                wav_file.setframerate(16000)  # 16kHz sample rate
                wav_file.writeframes(audio_data)

            print(f"[{call_id}] 💾 Audio saved to: {filename}")
            print(f"[{call_id}] 📊 File size: {len(audio_data)} bytes")
            print(f"[{call_id}] ⏱️  Duration: ~{len(audio_data) / (16000 * 2):.2f} seconds")
            print()

            return str(filename)

        except Exception as e:
            logger.error(f"[{call_id}] Error saving audio to WAV: {e}")
            return None

    async def user_message_handler(self, synchronizer: WebSocketSynchronizer, call_id: str):
        """
        Handles incoming messages from the client with Rust-accelerated processing
        """
        logger.info(f"[{call_id}] User message handler started")

        # Initialize counter and performance tracking
        self._audio_chunk_counters[call_id] = 0
        self._perf_stats[call_id] = {
            'start_time': time.perf_counter(),
            'total_decode_time': 0.0,
            'chunk_times': []
        }

        try:
            end_call = False

            while not end_call:
                # Receive message through synchronizer
                try:
                    message = await synchronizer.receive_message(timeout=0.1)
                    if not message:
                        continue
                except Exception as e:
                    logger.error(f"[{call_id}] Receive message failed: {e}")
                    await asyncio.sleep(0.001)
                    continue

                chunk_start = time.perf_counter()

                # Parse JSON message if needed
                if 'text' in message and len(message['text']) > 0:
                    try:
                        message = json.loads(message['text'])
                    except Exception as e:
                        logger.error(f"[{call_id}] JSON parse failed: {e}")
                        await asyncio.sleep(0.001)
                        continue

                # Handle different message types
                if message.get("event") == "connected":
                    print(f"[{call_id}] ✅ Call connected!")
                    print()

                elif "bytes" in message:
                    # Binary WebSocket frame
                    self._audio_processor.process_bytes(call_id, message["bytes"])
                    self._audio_chunk_counters[call_id] += 1

                elif message.get("media", {}).get("payload"):
                    # Base64-encoded audio chunk
                    payload = message["media"]["payload"]

                    # Rust fast path: decode + buffer in one operation
                    decode_start = time.perf_counter()
                    self._audio_processor.process_chunk(call_id, payload)
                    decode_time = time.perf_counter() - decode_start
                    self._perf_stats[call_id]['total_decode_time'] += decode_time

                    self._audio_chunk_counters[call_id] += 1

                elif message.get('type') == 'websocket.disconnect' or message.get('event') == 'websocket_disconnect':
                    reason = message.get('reason', 'unknown')
                    print(f"[{call_id}] 🔌 WebSocket disconnected - reason: {reason}")
                    print(f"[{call_id}] 📊 Total audio chunks received: {self._audio_chunk_counters[call_id]}")
                    print()
                    end_call = True

                else:
                    print(f"[{call_id}] 📨 Unknown message type: {message}")
                    print()

                # Track chunk processing time
                chunk_time = time.perf_counter() - chunk_start
                self._perf_stats[call_id]['chunk_times'].append(chunk_time)

                # Print stats every 100 chunks
                if self._audio_chunk_counters[call_id] % 100 == 0:
                    length, _chunks, capacity = self._audio_processor.get_stats(call_id)
                    print(f"[{call_id}] 🦀 Rust Audio chunk #{self._audio_chunk_counters[call_id]}:")
                    print(f"  - Total buffered: {length} bytes")
                    print(f"  - Capacity: {capacity} bytes")
                    print(f"  - Receive queue size: {synchronizer._receive_queue.qsize()}")

                    # Performance stats
                    avg_chunk_time = sum(self._perf_stats[call_id]['chunk_times'][-100:]) / 100
                    print(f"  - Avg chunk time: {avg_chunk_time*1000:.3f}ms")
                    print()

                # Yield control efficiently
                if self._audio_chunk_counters[call_id] % 10 == 0:
                    await asyncio.sleep(0)

        except Exception as e:
            import traceback
            logger.error(f"[{call_id}] Error in user message handler: {e}, traceback: {traceback.format_exc()}")
        finally:
            # Print final performance stats
            self._print_performance_stats(call_id)

            # Save audio to WAV file
            buffer_data = self._audio_processor.get_buffer(call_id)
            if buffer_data:
                self.save_audio_to_wav(call_id, bytes(buffer_data))
                self._audio_processor.clear_buffer(call_id)

            if call_id in self._audio_chunk_counters:
                del self._audio_chunk_counters[call_id]
            if call_id in self._perf_stats:
                del self._perf_stats[call_id]

            logger.info(f"[{call_id}] User message handler cleanup")

    def _print_performance_stats(self, call_id: str):
        """Print detailed performance statistics"""
        if call_id not in self._perf_stats:
            return

        stats = self._perf_stats[call_id]
        total_time = time.perf_counter() - stats['start_time']
        num_chunks = len(stats['chunk_times'])

        if num_chunks == 0:
            return

        print(f"\n[{call_id}] 📈 Performance Statistics:")
        print(f"  - Mode: 🦀 Rust")
        print(f"  - Total chunks: {num_chunks}")
        print(f"  - Total time: {total_time:.3f}s")
        print(f"  - Throughput: {num_chunks/total_time:.2f} chunks/s")
        print(f"  - Total decode time: {stats['total_decode_time']*1000:.2f}ms")

        if stats['chunk_times']:
            avg_time = sum(stats['chunk_times']) / len(stats['chunk_times'])
            min_time = min(stats['chunk_times'])
            max_time = max(stats['chunk_times'])
            print(f"  - Avg chunk time: {avg_time*1000:.3f}ms")
            print(f"  - Min chunk time: {min_time*1000:.3f}ms")
            print(f"  - Max chunk time: {max_time*1000:.3f}ms")
        print()

    async def call_ws_handler(self, websocket: WebSocket, call_id: str):
        """
        Unified websocket endpoint with proper synchronization
        """
        synchronizer = None
        try:
            # Create and initialize WebSocket synchronizer
            synchronizer = WebSocketSynchronizer(call_id)
            await synchronizer.initialize(websocket)
            self._synchronizers[call_id] = synchronizer

            # Create business logic handler
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
app = FastAPI(title="CallBot WebSocket Server V2 (Rust-Powered)", version="2.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize the server instance with Rust
callbot_server = CallbotWSServer()


@app.websocket("/ws/call")
async def websocket_call_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for call handling with call_id as query parameter.
    Format: /ws/call?call_id=xxx
    """
    query_params = websocket.query_params
    call_id = query_params.get('call_id')

    if not call_id:
        logger.error("Missing call_id in query parameters")
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
        "rust_enabled": True,
        "active_connections": len(callbot_server.active_clients),
        "active_buffers": callbot_server._audio_processor.active_buffers()
    }


@app.get("/")
async def root():
    """Root endpoint with usage instructions"""
    return {
        "message": "CallBot WebSocket Server V2 (Rust-Powered)",
        "version": "2.0.0",
        "rust_enabled": True,
        "usage": "Connect to: ws://localhost:9923/ws/call?call_id=YOUR_CALL_ID",
        "endpoints": {
            "websocket": "/ws/call?call_id=<call_id>",
            "health": "/health"
        },
        "recordings": f"Audio files saved to: {callbot_server.recordings_dir.absolute()}"
    }


def main():
    """Start the server"""
    print("=" * 70)
    print("🦀 CallBot WebSocket Server V2 - Rust-Powered Audio Receiver")
    print("=" * 70)
    print()
    print("Server starting on: http://0.0.0.0:9923")
    print("WebSocket endpoint: ws://0.0.0.0:9923/ws/call?call_id=<call_id>")
    print(f"Recordings directory: {callbot_server.recordings_dir.absolute()}")
    print("Rust acceleration: ✅ ENABLED")
    print()
    print("Example connection:")
    print("  ws://localhost:9923/ws/call?call_id=test123")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 70)
    print()

    # Run the server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=9923,  # Different port from v1
        log_level="info",
        ws_ping_interval=None,  # Disable WebSocket ping
        ws_ping_timeout=None    # Disable WebSocket ping timeout
    )


if __name__ == "__main__":
    main()
