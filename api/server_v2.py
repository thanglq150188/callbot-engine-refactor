import asyncio
import json
import base64
import wave
import uvicorn
from .ws_utils import WebSocketSynchronizer
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any
from beegen.loggings.loggers import LOGGER
from beegen.streams.inmem_queue import BEE_STREAM
from beeflow.workflow import WorkflowEngine
from beeflow.nodes import StreamNode, CodeNode, START, END, INPUT, OUTPUT


logger = LOGGER


def process_audio_chunk(event: str, bytes_data: bytes = None, text_data: str = None) -> Dict[str, Any]:
    """Process incoming WebSocket message

    Returns:
        audio (bytes): Audio data if present
    """
    audio = None
    metadata = {}

    # Handle different message types
    if event == "connected":
        metadata["message"] = "Call connected"

    elif event == "audio_bytes":
        audio = bytes_data
        metadata["size"] = len(bytes_data) if bytes_data else 0

    elif event == "audio_base64" and text_data:
        try:
            audio = base64.b64decode(text_data)
            metadata["size"] = len(audio)
        except Exception as e:
            metadata["error"] = str(e)

    elif event == "disconnect":
        metadata["message"] = "Call disconnected"

    return {
        "audio": audio
    }


class CallbotWSServer:
    """
    WebSocket server using StreamNode workflow to process audio chunks
    """

    def __init__(self):
        self.active_clients = set()
        self._synchronizers = {}
        self._audio_buffers = {}  # Store audio chunks per call_id

        # Create recordings directory if it doesn't exist
        self.recordings_dir = Path("recordings")
        self.recordings_dir.mkdir(exist_ok=True)

        # Build workflow
        self._build_workflow()

        logger.info("CallbotWSServerV2 initialized with StreamNode workflow")

    def _build_workflow(self):
        """Build the audio processing workflow with StreamNode"""

        
        # Build workflow
        with WorkflowEngine(
            name="audio-stream-processor",
            description="Process audio chunks from WebSocket stream"
        ) as workflow:

            with StreamNode(
                name="audio_stream",
                description="Process audio chunks as they stream",
                inputs={"input_channel": INPUT},
                outputs={"output_channel": "websocket-in"},
                max_chunk_traces=100,
                log_interval=200
            ) as stream_node:
                processor = CodeNode(
                    name="process_chunk",
                    code_fn=process_audio_chunk,
                    inputs=INPUT,
                    outputs=OUTPUT
                )
                START >> processor >> END

            START >> stream_node >> END

        workflow.compile()
        self.workflow = workflow

    def save_audio_to_wav(self, call_id: str, audio_data: bytes):
        """Save concatenated audio data to a WAV file"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.recordings_dir / f"{call_id}_{timestamp}.wav"

        try:
            with wave.open(str(filename), 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 2 bytes = 16-bit
                wav_file.setframerate(16000)  # 16kHz sample rate
                wav_file.writeframes(audio_data)

            logger.info(f"[{call_id}] Audio saved to: {filename} ({len(audio_data)} bytes)")
            return str(filename)

        except Exception as e:
            logger.error(f"[{call_id}] Error saving audio to WAV: {e}")
            return None

    async def websocket_to_stream_producer(
        self,
        synchronizer: WebSocketSynchronizer,
        call_id: str,
        session_id: str,
        request_id: str,
        input_channel: str
    ):
        """
        Consume WebSocket messages and push to BEE_STREAM
        """
        logger.info(f"[{call_id}] Stream producer started")

        try:
            end_call = False

            while not end_call:
                # Receive message from WebSocket synchronizer
                try:
                    message = await synchronizer.receive_message(timeout=0.1)
                    if not message:
                        continue
                except Exception as e:
                    logger.error(f"[{call_id}] Receive message failed: {e}")
                    await asyncio.sleep(0.001)
                    continue

                # Parse message
                chunk_data = {}
                audio = None

                # Parse JSON message if needed
                if 'text' in message and len(message['text']) > 0:
                    try:
                        message = json.loads(message['text'])
                    except Exception:
                        pass

                # Handle different message types
                if message.get("event") == "connected":
                    chunk_data = {"event": "connected"}

                elif "bytes" in message:
                    audio = message["bytes"]
                    chunk_data = {"event": "audio_bytes", "bytes_data": audio}

                elif message.get("media", {}).get("payload"):
                    payload = message["media"]["payload"]
                    chunk_data = {"event": "audio_base64", "text_data": payload}

                elif message.get('type') == 'websocket.disconnect' or message.get('event') == 'websocket_disconnect':
                    chunk_data = {"event": "disconnect"}
                    end_call = True
                else:
                    chunk_data = {"event": "unknown", "data": str(message)}

                # Push to BEE_STREAM for workflow processing
                await BEE_STREAM.push(request_id, input_channel, chunk_data, session_id=session_id)

                # Yield control
                if audio is not None or chunk_data.get("event") in ["connected", "disconnect"]:
                    await asyncio.sleep(0)

            # End stream
            await BEE_STREAM.end(request_id, input_channel, session_id=session_id)
            logger.info(f"[{call_id}] Stream producer ended")

        except Exception as e:
            import traceback
            logger.error(f"[{call_id}] Error in stream producer: {e}\n{traceback.format_exc()}")

    async def stream_consumer(
        self,
        call_id: str,
        session_id: str,
        request_id: str,
        output_channel: str
    ):
        """
        Consume processed results from BEE_STREAM and save audio
        """
        logger.info(f"[{call_id}] Stream consumer started")

        # Initialize buffer
        self._audio_buffers[call_id] = bytearray()
        chunk_count = 0

        try:
            async for result in BEE_STREAM.get(request_id, output_channel, session_id=session_id):
                chunk_count += 1

                # Extract audio if present
                audio = result.get("audio")
                if audio and result.get("should_save"):
                    self._audio_buffers[call_id].extend(audio)

                    # Log every 100 chunks
                    if chunk_count % 100 == 0:
                        logger.info(
                            f"[{call_id}] Chunk #{chunk_count}: "
                            f"{len(audio)} bytes, total buffered: {len(self._audio_buffers[call_id])} bytes"
                        )

                # Handle events
                event = result.get("event")
                if event == "connected":
                    logger.info(f"[{call_id}] Call connected")
                elif event == "disconnect":
                    logger.info(f"[{call_id}] Call disconnected")

            logger.info(f"[{call_id}] Stream consumer ended, total chunks: {chunk_count}")

        except Exception as e:
            import traceback
            logger.error(f"[{call_id}] Error in stream consumer: {e}\n{traceback.format_exc()}")
        finally:
            # Save audio to WAV file
            if call_id in self._audio_buffers and len(self._audio_buffers[call_id]) > 0:
                logger.info(f"[{call_id}] Saving audio to WAV file...")
                audio_data = bytes(self._audio_buffers[call_id])
                self.save_audio_to_wav(call_id, audio_data)
                del self._audio_buffers[call_id]

            logger.info(f"[{call_id}] Stream consumer cleanup complete")

    async def call_ws_handler(self, websocket: WebSocket, call_id: str):
        """
        WebSocket handler using StreamNode workflow
        """
        synchronizer = None
        try:
            # Create and initialize WebSocket synchronizer
            synchronizer = WebSocketSynchronizer(call_id)
            await synchronizer.initialize(websocket)
            self._synchronizers[call_id] = synchronizer

            # Generate IDs for workflow
            import uuid
            session_id = call_id  # Use call_id as session for Langfuse grouping
            request_id = str(uuid.uuid4())  # Unique per workflow execution
            user_id = str(uuid.uuid4())  # Can be enhanced with actual caller info later
            input_channel = f"ws_input_{call_id}"
            output_channel = f"ws_output_{call_id}"

            # Create tasks
            producer_task = asyncio.create_task(
                self.websocket_to_stream_producer(
                    synchronizer, call_id, session_id, request_id, input_channel
                )
            )

            consumer_task = asyncio.create_task(
                self.stream_consumer(call_id, session_id, request_id, output_channel)
            )

            # Run workflow
            workflow_task = asyncio.create_task(
                self.workflow.run(
                    inputs={"input_channel": input_channel},
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id
                )
            )

            # Wait for all tasks to complete
            await asyncio.gather(producer_task, workflow_task, consumer_task)

            logger.info(f"[{call_id}] WebSocket handler finished")

        except Exception as e:
            import traceback
            logger.error(f"[{call_id}] Error in call_ws_handler: {e}\n{traceback.format_exc()}")
        finally:
            # Clean up synchronizer
            if synchronizer:
                await synchronizer.shutdown()
            self._synchronizers.pop(call_id, None)
            logger.info(f"[{call_id}] WebSocket handler cleanup complete")


# Create FastAPI app
app = FastAPI(title="CallBot WebSocket Server V2", version="2.0.0")

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
        "version": "2.0.0",
        "active_connections": len(callbot_server.active_clients)
    }


@app.get("/")
async def root():
    """Root endpoint with usage instructions"""
    return {
        "message": "CallBot WebSocket Server V2 - StreamNode Edition",
        "usage": "Connect to: ws://localhost:9922/ws/call?call_id=YOUR_CALL_ID",
        "features": [
            "StreamNode-based audio processing",
            "Async chunk processing with order preservation",
            "Langfuse tracing support",
            "BEE_STREAM integration"
        ],
        "endpoints": {
            "websocket": "/ws/call?call_id=<call_id>",
            "health": "/health"
        },
        "recordings": f"Audio files saved to: {callbot_server.recordings_dir.absolute()}"
    }


def main():
    """Start the server"""
    print("=" * 60)
    print("🐝 CallBot WebSocket Server V2 - StreamNode Edition")
    print("=" * 60)
    print()
    print("Server starting on: http://0.0.0.0:9922")
    print("WebSocket endpoint: ws://0.0.0.0:9922/ws/call?call_id=<call_id>")
    print(f"Recordings directory: {callbot_server.recordings_dir.absolute()}")
    print()
    print("Features:")
    print("  ✓ StreamNode workflow processing")
    print("  ✓ Async chunk processing")
    print("  ✓ Order preservation")
    print("  ✓ Langfuse tracing")
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
        ws_ping_interval=None,
        ws_ping_timeout=None
    )


if __name__ == "__main__":
    main()
