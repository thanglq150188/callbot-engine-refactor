import asyncio
import json
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Any
from fastapi import WebSocket, WebSocketDisconnect
import logging

logger = logging.getLogger(__name__)


class WebSocketState(Enum):
    """WebSocket connection states"""
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    CLOSED = "closed"


@dataclass
class WebSocketMessage:
    """Structured message for WebSocket communication"""
    message_type: str
    payload: Any
    timestamp: float = field(default_factory=time.time)


class WebSocketSynchronizer:
    """
    Synchronizes WebSocket send/receive operations to prevent duplex conflicts.
    Uses separate queues and background tasks for sending and receiving.
    """
    
    def __init__(self, call_id: str):
        self.call_id = call_id
        self._websocket: Optional[WebSocket] = None
        self._state = WebSocketState.CONNECTING
        self._connected = False
        
        # Separate queues for send/receive
        self._send_queue = asyncio.Queue()
        self._receive_queue = asyncio.Queue()
        
        # Background tasks
        self._sender_task: Optional[asyncio.Task] = None
        self._receiver_task: Optional[asyncio.Task] = None
        
        # Thread safety
        self._state_lock = asyncio.Lock()
        
        logger.info(f"[{call_id}] WebSocket synchronizer initialized")

    async def initialize(self, websocket: WebSocket):
        """Initialize synchronizer with WebSocket connection (assumes websocket is already accepted)"""
        self._websocket = websocket
        await self._set_state(WebSocketState.CONNECTED)
        self._connected = True
        
        # Start background tasks
        self._sender_task = asyncio.create_task(self._sender_loop())
        self._receiver_task = asyncio.create_task(self._receiver_loop())
        
        logger.info(f"[{self.call_id}] WebSocket synchronizer connected and tasks started")

    async def _set_state(self, new_state: WebSocketState):
        """Thread-safe state management"""
        async with self._state_lock:
            old_state = self._state
            self._state = new_state
            logger.debug(f"[{self.call_id}] WebSocket state: {old_state.value} -> {new_state.value}")

    async def send_message(self, message: WebSocketMessage) -> bool:
        """Queue message for sending"""
        # Allow sending messages during DISCONNECTING state (for hangup, etc.)
        if not self._connected and self._state != WebSocketState.DISCONNECTING:
            logger.warning(f"[{self.call_id}] Cannot send message - not connected, type: {message.message_type}")
            return False
        
        await self._send_queue.put(message)
        return True

    async def receive_message(self, timeout: Optional[float] = None) -> Optional[Any]:
        """Receive message from queue with optional timeout"""
        try:
            return await asyncio.wait_for(self._receive_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def get_send_queue_size(self) -> int:
        """Get number of pending messages in send queue"""
        return self._send_queue.qsize()
    
    async def wait_for_queue_empty(self, timeout: float = 5.0) -> bool:
        """
        Wait for send queue to be empty.
        Returns True if queue is empty, False if timeout occurred.
        """
        wait_start = time.time()
        while self._send_queue.qsize() > 0 and (time.time() - wait_start) < timeout:
            await asyncio.sleep(0.1)
        
        is_empty = self._send_queue.qsize() == 0
        if not is_empty:
            logger.warning(f"[{self.call_id}] Queue not empty after {timeout}s timeout")
        
        return is_empty

    async def _sender_loop(self):
        """Background task to send messages from queue"""
        logger.info(f"[{self.call_id}] WebSocket sender loop started")
        
        send_count = 0
        try:
            while self._connected or self._state == WebSocketState.DISCONNECTING:
                try:
                    # Get message from queue (with timeout to allow graceful shutdown)
                    message = await asyncio.wait_for(self._send_queue.get(), timeout=1.0)
                    
                    success = await self._safe_send(message)
                    if not success:
                        logger.warning(f"[{self.call_id}] Failed to send message: {message.message_type}")
                        # If we're disconnecting and send failed, break to avoid infinite loop
                        if self._state == WebSocketState.DISCONNECTING:
                            break
                    
                    # Yield control periodically to prevent event loop monopolization
                    send_count += 1
                    if send_count % 5 == 0:
                        await asyncio.sleep(0)  # Allow other tasks (like receiver) to run
                        
                except asyncio.TimeoutError:
                    # During disconnecting, if queue is empty, we're done
                    if self._state == WebSocketState.DISCONNECTING and self._send_queue.empty():
                        logger.info(f"[{self.call_id}] All messages sent during disconnection")
                        break
                    continue
                except Exception as e:
                    logger.error(f"[{self.call_id}] Error in sender loop: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"[{self.call_id}] Sender loop crashed: {e}")
        finally:
            logger.info(f"[{self.call_id}] WebSocket sender loop ended")
    
    async def _add_disconnect_message(self):
        """Queue disconnect message for handlers to process"""
        await self._receive_queue.put({
            "type": "websocket.disconnect",
            "event": "websocket_disconnect",
            "reason": "connection_closed"
        })
        
    async def _receiver_loop(self):
        """Background task to receive messages from WebSocket"""
        logger.info(f"[{self.call_id}] WebSocket receiver loop started")

        message_count = 0
        try:
            while self._connected:
                try:
                    # Receive from WebSocket directly
                    data = await self._websocket.receive()
                    message_count += 1

                    # Log queue size periodically
                    queue_size = self._receive_queue.qsize()
                    if message_count % 100 == 0:
                        logger.info(f"[{self.call_id}] Received {message_count} messages, queue size: {queue_size}")

                    if queue_size > 50:
                        logger.warning(f"[{self.call_id}] Receive queue size is high: {queue_size} messages")

                    # Handle different data types properly
                    if data["type"] == "websocket.receive":
                        if "text" in data:
                            try:
                                # Try to parse as JSON first
                                message = json.loads(data["text"])
                                await self._receive_queue.put({"text": data["text"], **message})
                            except json.JSONDecodeError:
                                # If not JSON, put as raw text
                                await self._receive_queue.put({"text": data["text"]})
                        elif "bytes" in data:
                            await self._receive_queue.put({"bytes": data["bytes"]})
                    elif data["type"] == "websocket.disconnect":
                        logger.warning(f"[{self.call_id}] WebSocket disconnected after {message_count} messages - ending call")
                        await self._add_disconnect_message()
                        break

                except WebSocketDisconnect as e:
                    logger.info(f"[{self.call_id}] WebSocket disconnected normally after {message_count} messages: {e}")
                    await self._add_disconnect_message()
                    break
                except Exception as e:
                    import traceback
                    logger.error(f"[{self.call_id}] Error in receiver loop after {message_count} messages: {e}, traceback: {traceback.format_exc()}")
                    await self._add_disconnect_message()
                    break

        except Exception as e:
            import traceback
            logger.error(f"[{self.call_id}] Receiver loop crashed after {message_count} messages: {e}, traceback: {traceback.format_exc()}")
        finally:
            self._connected = False
            await self._set_state(WebSocketState.DISCONNECTING)
            logger.info(f"[{self.call_id}] WebSocket receiver loop ended after processing {message_count} messages")

    async def _safe_send(self, message: WebSocketMessage) -> bool:
        """Safely send message via WebSocket with error handling"""
        try:
            if not self._websocket or not self._connected:
                return False
                
            if self._websocket.client_state.name != "CONNECTED":
                logger.warning(f"[{self.call_id}] WebSocket not connected, cannot send message")
                self._connected = False
                return False
            
            # Send based on payload type
            if isinstance(message.payload, dict):
                await self._websocket.send_json(message.payload)
            elif isinstance(message.payload, str):
                await self._websocket.send_text(message.payload)
            elif isinstance(message.payload, bytes):
                await self._websocket.send_bytes(message.payload)
            else:
                # Convert to JSON as fallback
                await self._websocket.send_json({"data": message.payload})
            
            logger.debug(f"[{self.call_id}] Sent {message.message_type} message")
            return True
            
        except Exception as e:
            logger.error(f"[{self.call_id}] Error sending message: {e}")
            self._connected = False
            return False

    async def shutdown(self, flush_timeout: float = 20.0):
        """Gracefully shutdown synchronizer, flushing pending messages"""
        logger.info(f"[{self.call_id}] Shutting down WebSocket synchronizer...")
        
        # Wait for pending messages to be sent (with timeout)
        flush_start = time.time()
        while not self._send_queue.empty() and (time.time() - flush_start) < flush_timeout:
            await asyncio.sleep(0.1)
            logger.debug(f"[{self.call_id}] Flushing {self._send_queue.qsize()} pending messages...")
        
        if not self._send_queue.empty():
            remaining = self._send_queue.qsize()
            logger.warning(f"[{self.call_id}] Timeout flushing messages, {remaining} messages remaining")
        else:
            logger.info(f"[{self.call_id}] All pending messages flushed")
            
        self._connected = False
        await self._set_state(WebSocketState.DISCONNECTING)
        
        # Cancel background tasks
        if self._sender_task and not self._sender_task.done():
            self._sender_task.cancel()
        if self._receiver_task and not self._receiver_task.done():
            self._receiver_task.cancel()
        
        # Wait for tasks to complete
        if self._sender_task:
            try:
                await self._sender_task
            except asyncio.CancelledError:
                pass
        if self._receiver_task:
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass
        
        # Close WebSocket if still connected
        if self._websocket:
            try:
                if self._websocket.client_state.name == "CONNECTED":
                    await self._websocket.close()
            except Exception as e:
                logger.debug(f"[{self.call_id}] Error closing WebSocket: {e}")
        
        await self._set_state(WebSocketState.CLOSED)
        logger.info(f"[{self.call_id}] WebSocket synchronizer shutdown complete")