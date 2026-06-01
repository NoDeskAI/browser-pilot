from __future__ import annotations

import asyncio
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from Crypto.Cipher import DES
from fastapi import WebSocket


class RfbProxyError(RuntimeError):
    pass


class _RfbClientViewOnlyFilter:
    """Pass RFB display setup/update messages while dropping remote input."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._client_init_pending = True

    def feed(self, chunk: bytes) -> bytes:
        if not chunk:
            return b""
        self._buffer.extend(chunk)
        out = bytearray()
        if self._client_init_pending:
            if not self._buffer:
                return b""
            out.append(self._buffer[0])
            del self._buffer[0]
            self._client_init_pending = False

        while self._buffer:
            msg_type = self._buffer[0]
            size = self._message_size(msg_type)
            if size is None:
                self._buffer.clear()
                break
            if size == 0:
                break
            if len(self._buffer) < size:
                break
            message = bytes(self._buffer[:size])
            del self._buffer[:size]
            if msg_type in {0, 2, 3}:
                out.extend(message)
        return bytes(out)

    def _message_size(self, msg_type: int) -> int | None:
        if msg_type == 0:
            return 20
        if msg_type == 2:
            if len(self._buffer) < 4:
                return 0
            return 4 + struct.unpack(">H", bytes(self._buffer[2:4]))[0] * 4
        if msg_type == 3:
            return 10
        if msg_type == 4:
            return 8
        if msg_type == 5:
            return 6
        if msg_type == 6:
            if len(self._buffer) < 8:
                return 0
            return 8 + struct.unpack(">I", bytes(self._buffer[4:8]))[0]
        return None


@dataclass
class _ByteReader:
    receive: Callable[[], Awaitable[bytes]]
    buffer: bytearray = field(default_factory=bytearray)

    async def read_exact(self, size: int) -> bytes:
        while len(self.buffer) < size:
            self.buffer.extend(await self.receive())
        out = bytes(self.buffer[:size])
        del self.buffer[:size]
        return out

    def take_buffer(self) -> bytes:
        out = bytes(self.buffer)
        self.buffer.clear()
        return out


async def _downstream_receive(websocket: WebSocket) -> bytes:
    message = await websocket.receive()
    if "bytes" in message and message["bytes"] is not None:
        return message["bytes"]
    if "text" in message and message["text"] is not None:
        return message["text"].encode()
    raise RfbProxyError("Downstream WebSocket closed")


async def _upstream_receive(upstream) -> bytes:
    message = await upstream.recv()
    if isinstance(message, bytes):
        return message
    return str(message).encode()


def _reverse_bits(value: int) -> int:
    out = 0
    for _ in range(8):
        out = (out << 1) | (value & 1)
        value >>= 1
    return out


def _vnc_auth_response(password: str, challenge: bytes) -> bytes:
    raw_key = password.encode("latin-1", "ignore")[:8].ljust(8, b"\x00")
    key = bytes(_reverse_bits(byte) for byte in raw_key)
    return DES.new(key, DES.MODE_ECB).encrypt(challenge)


def _security_result_ok(result: bytes) -> bool:
    return len(result) == 4 and struct.unpack(">I", result)[0] == 0


async def authenticate_upstream_vnc(
    *,
    downstream: WebSocket,
    upstream,
    password: str,
) -> tuple[bytes, bytes]:
    """Authenticate to upstream VNC and expose a ticket-protected no-auth RFB stream.

    The browser noVNC client never receives the per-session VNC password. The
    backend consumes the upstream VNC challenge, then presents RFB security type
    None to the already-authorized downstream WebSocket.
    """

    client = _ByteReader(lambda: _downstream_receive(downstream))
    server = _ByteReader(lambda: _upstream_receive(upstream))

    server_version = await server.read_exact(12)
    await downstream.send_bytes(server_version)

    client_version = await client.read_exact(12)
    await upstream.send(client_version)

    if not server_version.startswith(b"RFB 003."):
        raise RfbProxyError("Unsupported RFB protocol banner")

    security_count = await server.read_exact(1)
    if security_count == b"\x00":
        reason_len = struct.unpack(">I", await server.read_exact(4))[0]
        reason = await server.read_exact(reason_len)
        raise RfbProxyError(reason.decode("utf-8", "replace") or "Upstream VNC rejected security negotiation")

    count = security_count[0]
    security_types = await server.read_exact(count)
    if 2 not in security_types:
        if 1 not in security_types:
            raise RfbProxyError("Upstream VNC does not offer supported security types")
        await downstream.send_bytes(security_count + security_types)
        downstream_choice = await client.read_exact(1)
        await upstream.send(downstream_choice)
        result = await server.read_exact(4)
        await downstream.send_bytes(result)
        if not _security_result_ok(result):
            raise RfbProxyError("Upstream VNC security negotiation failed")
        return client.take_buffer(), server.take_buffer()

    await downstream.send_bytes(b"\x01\x01")
    downstream_choice = await client.read_exact(1)
    if downstream_choice != b"\x01":
        raise RfbProxyError("Downstream client rejected ticket-authenticated RFB stream")

    await upstream.send(b"\x02")
    challenge = await server.read_exact(16)
    await upstream.send(_vnc_auth_response(password, challenge))
    result = await server.read_exact(4)
    if not _security_result_ok(result):
        await downstream.send_bytes(result)
        raise RfbProxyError("Upstream VNC authentication failed")

    await downstream.send_bytes(b"\x00\x00\x00\x00")
    return client.take_buffer(), server.take_buffer()


async def bridge_websockets(
    *,
    downstream: WebSocket,
    upstream,
    downstream_buffer: bytes = b"",
    upstream_buffer: bytes = b"",
    view_only: bool = False,
) -> tuple[int, int]:
    downstream_bytes = 0
    upstream_bytes = 0
    view_filter = _RfbClientViewOnlyFilter() if view_only else None

    if downstream_buffer:
        downstream_bytes += len(downstream_buffer)
        filtered = view_filter.feed(downstream_buffer) if view_filter else downstream_buffer
        if filtered:
            await upstream.send(filtered)
    if upstream_buffer:
        upstream_bytes += len(upstream_buffer)
        await downstream.send_bytes(upstream_buffer)

    async def downstream_to_upstream() -> None:
        nonlocal downstream_bytes
        while True:
            chunk = await _downstream_receive(downstream)
            downstream_bytes += len(chunk)
            filtered = view_filter.feed(chunk) if view_filter else chunk
            if filtered:
                await upstream.send(filtered)

    async def upstream_to_downstream() -> None:
        nonlocal upstream_bytes
        while True:
            chunk = await _upstream_receive(upstream)
            upstream_bytes += len(chunk)
            await downstream.send_bytes(chunk)

    tasks = [
        asyncio.create_task(downstream_to_upstream()),
        asyncio.create_task(upstream_to_downstream()),
    ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        task.result()
    return downstream_bytes, upstream_bytes
