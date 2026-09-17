#!/usr/bin/env python3
import argparse
import asyncio
import time
from config import Config
from models import JsonStore, Rooms
from auth import Users, Sessions
from chat import Chat
from http_server import HttpServer
from websocket_handler import Connection
from compiler import compile_csharp_sync


class App:
    """Главное приложение"""

    def __init__(self):
        self.users = Users(JsonStore(Config.USERS_FILE))
        self.sessions = Sessions(JsonStore(Config.SESSIONS_FILE))
        self.rooms = Rooms()
        self.chat = Chat()
        Config.PROJECT.mkdir(parents=True, exist_ok=True)
        self.loop = None

    async def send(self, peer, obj):
        from ws_protocol import ws_frame
        payload = json.dumps(obj, ensure_ascii=False).encode()
        peer.writer.write(ws_frame(0x1, payload))
        await peer.writer.drain()

    def push(self, peer, obj):
        try:
            self.loop.create_task(self.send(peer, obj))
        except Exception:
            pass

    def broadcast(self, room, exclude, obj):
        for peer in list(room.peers):
            if peer is not exclude:
                self.push(peer, obj)

    def peer_names(self, room, exclude=None):
        return [peer.name for peer in room.peers if peer is not exclude]

    async def warm_up(self):
        await asyncio.sleep(3)
        code = 'using System; class W { static void Main() { System.Console.WriteLine("warm"); } }'
        for attempt in range(5):
            try:
                await self.loop.run_in_executor(None, lambda: compile_csharp_sync(code))
                print("Warm-up compile OK")
                return
            except Exception as exc:
                print(f"Warm-up {attempt + 1}: {exc}")
                await asyncio.sleep(10)
        print("Warm-up compile failed (cold start may be slow)")

    async def run_compile(self, room, code, requestor):
        if room.compiling:
            self.push(requestor, {
                "type": "run",
                "result": {
                    "success": False,
                    "stdout": "",
                    "stderr": "Компиляция уже выполняется, подождите…"
                }
            })
            return
        room.compiling = True
        try:
            result = await asyncio.wait_for(
                self.loop.run_in_executor(None, lambda: compile_csharp_sync(code)),
                Config.COMPILE_TIMEOUT + 10
            )
        except asyncio.TimeoutError:
            result = {
                "success": False,
                "stdout": "",
                "stderr": f"Превышено время ({Config.COMPILE_TIMEOUT}с)"
            }
        except Exception as exc:
            result = {"success": False, "stdout": "", "stderr": f"Ошибка компилятора: {exc}"}
        finally:
            room.compiling = False
        message = {"type": "run", "result": result, "by": "csharp"}
        self.push(requestor, message)
        self.broadcast(room, requestor, message)

    async def ws_dispatch(self, peer, text):
        try:
            msg = json.loads(text)
        except Exception:
            return
        handler = WS_ROUTES.get(msg.get("type"))
        if handler:
            await handler(self, peer, msg)

    async def _ws_join(self, peer, msg):
        if peer.room:
            await self._ws_leave(peer)
        name = (msg.get("room") or "demo").strip()[:64] or "demo"
        kind = msg.get("kind") or "xml"
        if kind not in Config.KINDS:
            kind = "xml"
        room = self.rooms.get(name)
        if room and room.kind != kind:
            self.push(peer, {
                "type": "error",
                "msg": f"Комната «{name}» уже создана как {Config.KIND_LABELS[room.kind]}"
            })
            return
        if room is None:
            room = self.rooms.get_or_create(name, kind)
        peer.room = room
        room.updated = time.time()
        room.doc = msg.get("xml") or room.doc
        room.peers.add(peer)
        self.push(peer, {
            "type": "welcome",
            "room": name,
            "kind": room.kind,
            "peers": len(room.peers),
            "name": peer.name,
            "names": self.peer_names(room, peer),
            "chat": room.msgs[-Config.CHAT_HISTORY:]
        })
        for other in room.peers:
            if other is not peer:
                self.push(other, {
                    "type": "peers",
                    "count": len(room.peers),
                    "names": self.peer_names(room, other)
                })
        for other in room.peers:
            if other is not peer and other.file:
                self.push(peer, {
                    "type": "peerf",
                    "file": other.file,
                    "peer": "other",
                    "name": other.name
                })
        if room.doc:
            self.push(peer, {"type": "peer", "xml": room.doc})

    async def _ws_list(self, peer, msg):
        self.push(peer, {"type": "rooms", "rooms": self.rooms.snapshot()})

    async def _ws_edit(self, peer, msg):
        room = peer.room
        if room:
            room.updated = time.time()
            room.doc = msg.get("xml", "")
            self.broadcast(room, peer, {"type": "peer", "xml": room.doc})

    async def _ws_cursor(self, peer, msg):
        room = peer.room
        if room:
            self.broadcast(room, peer, {"type": "cursor", "pos": msg.get("pos", 0)})

    async def _ws_fsel(self, peer, msg):
        room = peer.room
        if room:
            import os
            value = str(msg.get("file") or "").strip()
            peer.file = os.path.basename(value)[:128] or None
            self.broadcast(room, peer, {
                "type": "peerf",
                "file": peer.file,
                "peer": "other",
                "name": peer.name
            })

    async def _ws_chat(self, peer, msg):
        room = peer.room
        text = str(msg.get("text") or "").strip()[:Config.CHAT_MSG_MAX]
        if not room or not text:
            return
        room.updated = time.time()
        entry = room.add_msg(peer.name or "аноним", text)
        self.broadcast(room, None, {"type": "chat", **entry})

    async def _ws_compile(self, peer, msg):
        room = peer.room
        if not room:
            self.push(peer, {"type": "error", "msg": "Сначала вступите в комнату"})
        elif room.kind != "csharp":
            self.push(peer, {
                "type": "error",
                "msg": "C# доступен только в комнатах типа C#"
            })
        else:
            room.updated = time.time()
            await self.run_compile(room, msg.get("xml") or room.doc, peer)

    async def _ws_leave(self, peer):
        room = peer.room
        if not room:
            return
        room.peers.discard(peer)
        if not room.peers:
            self.rooms.maybe_drop(room)
        else:
            for other in room.peers:
                self.push(other, {
                    "type": "peers",
                    "count": len(room.peers),
                    "names": self.peer_names(room, other)
                })
            self.broadcast(room, None, {"type": "peerf", "file": None})
        peer.room = None


WS_ROUTES = {
    "join": App._ws_join,
    "list": App._ws_list,
    "edit": App._ws_edit,
    "cursor": App._ws_cursor,
    "fsel": App._ws_fsel,
    "compile": App._ws_compile,
    "chat": App._ws_chat,
}


import json


async def main(args):
    app = App()
    app.loop = asyncio.get_event_loop()
    http = HttpServer(app)
    connection = Connection(app, http)
    server = await asyncio.start_server(connection.run, args.host, args.port)
    print(f"Collab: http://{args.host}:{args.port}")
    print(f"C# compiler: {Config.COMPILER_URL}")
    print(f"Юзеров: {len(app.users.store.data)} · ассистент: "
          f"{'ключ задан' if app.chat.ready else 'КЛЮЧ НЕ ЗАДАН'}")
    asyncio.create_task(app.warm_up())
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CollabLab — мультиплеер-компилятор")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8383)
    args = parser.parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\nСтоп.")
