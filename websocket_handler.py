import asyncio
import json
import struct
import urllib.parse
from config import Config
from ws_protocol import ws_accept, ws_frame
from compiler import compile_csharp_sync


class Connection:
    """WebSocket соединение"""

    def __init__(self, app, http):
        self.app = app
        self.http = http

    async def run(self, reader, writer):
        try:
            request_line, headers = await self._recv_headers(reader)
        except ConnectionError:
            writer.close()
            return
        parts = request_line.split()
        method = parts[0] if parts else "GET"
        raw_path = parts[1] if len(parts) > 1 else "/"
        parsed = urllib.parse.urlsplit(raw_path)
        path = parsed.path
        query_dict = urllib.parse.parse_qs(parsed.query)

        if headers.get("upgrade", "").lower() != "websocket":
            body = b""
            length = int(headers.get("content-length") or 0)
            if length > 0:
                if length > 1024 * 1024:
                    writer.close()
                    return
                body = await self._read_exact(reader, length)
            from http_server import HttpRequest
            email = self.app.sessions.email(headers.get("cookie", ""))
            req = HttpRequest(method, path, query_dict, email, headers.get("cookie", ""), body)
            await self.http.handle(writer, req)
            return
        await self._websocket(reader, writer, headers)

    async def _recv_headers(self, reader):
        line = await asyncio.wait_for(reader.readline(), 15)
        if not line:
            raise ConnectionError("no request")
        request_line = line.decode("latin1").rstrip("\r\n")
        headers = {}
        while True:
            line = await reader.readline()
            if not line:
                raise ConnectionError("no end of headers")
            line = line.decode("latin1").rstrip("\r\n")
            if not line:
                break
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        return request_line, headers

    async def _read_exact(self, reader, size):
        data = b""
        while len(data) < size:
            chunk = await reader.read(size - len(data))
            if not chunk:
                raise ConnectionError("closed")
            data += chunk
        return data

    async def _read_frame(self, reader):
        hdr = await self._read_exact(reader, 2)
        b1, b2 = hdr
        masked = b2 & 0x80
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", await self._read_exact(reader, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", await self._read_exact(reader, 8))[0]
        if length > Config.MAX_MSG:
            raise ConnectionError("too big")
        mask = b""
        if masked:
            mask = await self._read_exact(reader, 4)
        payload = await self._read_exact(reader, length)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return b1 & 0x80, b1 & 0x0F, payload

    async def _websocket(self, reader, writer, headers):
        from models import Peer
        peer = None
        try:
            email = self.app.sessions.email(headers.get("cookie", ""))
            if not email:
                writer.close()
                return
            key = headers.get("sec-websocket-key", "")
            accept = ws_accept(key)
            writer.write(
                (f"HTTP/1.1 101 Switching Protocols\r\n"
                 "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                 f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode()
            )
            await writer.drain()
            peer = Peer(writer)
            peer.email = email
            peer.name = self.app.users.name(email)
            fragment = b""
            while True:
                fin, opcode, payload = await self._read_frame(reader)
                if opcode == 0x8:
                    await writer.write(ws_frame(0x8, b""))
                    await writer.drain()
                    break
                if opcode == 0x9:
                    await writer.write(ws_frame(0xA, payload))
                    await writer.drain()
                    continue
                if opcode == 0xA:
                    continue
                if opcode in (0x1, 0x0):
                    fragment += payload
                    if not fin:
                        continue
                    text, fragment = fragment, b""
                    if opcode == 0x1:
                        await self.app.ws_dispatch(peer, text.decode("utf-8", "replace"))
        except (ConnectionError, asyncio.CancelledError):
            pass
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            if peer:
                await self.app._ws_leave(peer)
            try:
                writer.close()
            except Exception:
                pass
