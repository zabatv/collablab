#!/usr/bin/env python3
import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import opendeep as _od
    HAS_OPENDEEP = True
except Exception:
    HAS_OPENDEEP = False
    _od = None


class Config:
    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    MAX_MSG = 10 * 1024 * 1024
    KINDS = ("xml", "csharp", "html")
    KIND_LABELS = {"xml": "XML", "csharp": "C#", "html": "HTML"}
    COMPILER_URL = "http://127.0.0.1:8081/compile"
    COMPILE_TIMEOUT = 90
    CHAT_MODEL = "deepseek-r1-0528"
    CHAT_TIMEOUT = 120
    CHAT_MAX_MESSAGES = 30
    CHAT_MAX_MSG_LEN = 6000
    CHAT_MSG_MAX = 600
    CHAT_HISTORY = 100
    SESSION_DAYS = 30
    SESSION_SECS = SESSION_DAYS * 86400
    PW_ITER = 200000
    AUTH_COOKIE = "auth"
    BASE = Path(__file__).resolve().parent
    WWW = BASE / "www"
    PROJECT = BASE / "project"
    USERS_FILE = BASE / "users.json"
    SESSIONS_FILE = BASE / "sessions.json"
    PUBLIC_PAGES = {"/", "/index.html"}
    PUBLIC_API = {"/api/me", "/api/register", "/api/login"}
    CONTENT_TYPES = {".html": "text/html", ".js": "application/javascript",
                     ".css": "text/css", ".png": "image/png",
                     ".svg": "image/svg+xml"}


class JsonStore:
    def __init__(self, path, default=None):
        self.path = Path(path)
        self.default = default if default is not None else {}
        self.data = self._load()

    def _load(self):
        try:
            return json.loads(self.path.read_text("utf-8"))
        except Exception:
            return self.default

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.path)


def pbkdf2_hash(password, salt=None, iterations=Config.PW_ITER):
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{digest.hex()}:{salt.hex()}:{iterations}"


def pbkdf2_verify(password, stored):
    try:
        digest, salt, iterations = stored.split(":")
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(calc.hex(), digest)
    except Exception:
        return False


class Users:
    def __init__(self, store):
        self.store = store

    def get(self, email):
        return self.store.data.get(email)

    def exists(self, email):
        return email in self.store.data

    def create(self, email, password, name):
        self.store.data[email] = {
            "name": name,
            "pw": pbkdf2_hash(password),
            "created": time.time(),
        }
        self.store.save()

    def name(self, email):
        user = self.get(email)
        return (user or {}).get("name") or email.split("@")[0]


class Sessions:
    def __init__(self, store):
        self.store = store
        self._prune()

    def _prune(self):
        now = time.time()
        drop = [t for t, s in self.store.data.items() if s.get("exp", 0) <= now]
        for token in drop:
            del self.store.data[token]
        if drop:
            self.store.save()

    def create(self, email, remember):
        token = secrets.token_urlsafe(32)
        exp = time.time() + (Config.SESSION_SECS if remember else 86400)
        self.store.data[token] = {"email": email, "exp": exp}
        self.store.save()
        return token, int(exp - time.time())

    def delete(self, token):
        self.store.data.pop(token, None)
        self.store.save()

    def email(self, cookie):
        if not cookie:
            return None
        for part in cookie.split(";"):
            key, _, value = part.strip().partition("=")
            if key == Config.AUTH_COOKIE and value:
                entry = self.store.data.get(value)
                if entry and entry.get("exp", 0) > time.time():
                    return entry.get("email")
        return None


def ws_accept(key):
    digest = hashlib.sha1((key + Config.GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def ws_frame(opcode, payload=b"", fin=1):
    out = bytearray([(0x80 if fin else 0) | opcode])
    size = len(payload)
    if size < 126:
        out.append(size)
    elif size < 65536:
        out.append(126)
        out += struct.pack(">H", size)
    else:
        out.append(127)
        out += struct.pack(">Q", size)
    out += payload
    return bytes(out)


class Room:
    def __init__(self, name, kind):
        self.name = name
        self.kind = kind
        self.peers = set()
        self.doc = ""
        self.msgs = []
        self.created = time.time()
        self.updated = time.time()
        self.compiling = False

    def add_msg(self, name, text):
        self.msgs.append({"name": name, "text": text[:Config.CHAT_MSG_MAX],
                          "time": int(time.time())})
        if len(self.msgs) > Config.CHAT_HISTORY:
            del self.msgs[:len(self.msgs) - Config.CHAT_HISTORY]
        return self.msgs[-1]

    def snapshot(self, now=None):
        now = now or time.time()
        return {
            "name": self.name,
            "kind": self.kind,
            "peers": len(self.peers),
            "updated": now - self.updated,
        }


class Peer:
    def __init__(self, writer):
        self.writer = writer
        self.room = None
        self.file = None
        self.email = None
        self.name = ""


class Rooms:
    def __init__(self):
        self._rooms = {}

    def get(self, name):
        return self._rooms.get(name)

    def get_or_create(self, name, kind):
        room = self._rooms.get(name)
        if room is None:
            room = self._rooms[name] = Room(name, kind)
        return room

    def maybe_drop(self, room):
        if not room.peers and room.name in self._rooms:
            del self._rooms[room.name]

    def snapshot(self):
        now = time.time()
        out = [room.snapshot(now) for room in self._rooms.values()]
        out.sort(key=lambda r: r["updated"])
        return out

    def file_dir(self, name):
        safe = os.path.basename(str(name or "demo"))[:64] or "demo"
        directory = (Config.PROJECT / safe).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        return directory


def compile_csharp_sync(code):
    request = urllib.request.Request(
        Config.COMPILER_URL,
        data=json.dumps({"code": code}, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=Config.COMPILE_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


class Chat:
    def __init__(self):
        self.key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if HAS_OPENDEEP and self.key:
            try:
                _od.configure(api_key=self.key)
            except Exception:
                pass

    @property
    def ready(self):
        return HAS_OPENDEEP and bool(self.key)

    async def _request(self, prompt):
        model = _od.AsyncGenerativeModel(Config.CHAT_MODEL)
        response = await model.generate_content(prompt, thinking_enabled=False)
        return (response.text or "").strip()

    def _build_prompt(self, raw_messages, kind, code):
        kind = kind if kind in Config.KINDS else "xml"
        hints = {"xml": "Текущий XML:", "csharp": "Текущий код C#:", "html": "Текущий HTML:"}
        system = (
            "Ты — OpenDeep, ассистент в совместном редакторе кода CollabLab. "
            f"Комната вида: {Config.KIND_LABELS[kind]}. "
            "Отвечай кратко и по делу на русском. Если дают код — помогай "
            "исправлять и объяснять.")
        messages = [{"role": "system", "content": system}]
        if code:
            messages.append({
                "role": "system",
                "content": hints[kind] + "\n" + code[:Config.CHAT_MAX_MSG_LEN]})
        for msg in (raw_messages or [])[-Config.CHAT_MAX_MESSAGES:]:
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            text = str(msg.get("content") or "")[:Config.CHAT_MAX_MSG_LEN]
            if text:
                messages.append({"role": role, "content": text})
        labels = {"user": "Пользователь", "assistant": "OpenDeep", "system": "Система"}
        return "\n\n".join(
            labels.get(m.get("role"), "Пользователь") + ":\n" + m.get("content", "")
            for m in messages)

    async def reply(self, raw_messages, kind, code):
        if not self.ready:
            return {"ok": False, "error": "Ассистент недоступен (ключ не задан на сервере)"}
        try:
            prompt = self._build_prompt(raw_messages, kind, code)
        except Exception as exc:
            return {"ok": False, "error": f"Некорректный запрос: {exc}"}
        try:
            text = await asyncio.wait_for(self._request(prompt), Config.CHAT_TIMEOUT)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "Превышено время ожидания ассистента"}
        except Exception as exc:
            return {"ok": False, "error": f"Ошибка ассистента: {exc}"}
        if not text:
            return {"ok": False, "error": "Ассистент вернул пустой ответ"}
        return {"ok": True, "text": text}


class App:
    def __init__(self):
        self.users = Users(JsonStore(Config.USERS_FILE))
        self.sessions = Sessions(JsonStore(Config.SESSIONS_FILE))
        self.rooms = Rooms()
        self.chat = Chat()
        Config.PROJECT.mkdir(parents=True, exist_ok=True)

    async def send(self, peer, obj):
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
        loop = asyncio.get_event_loop()
        code = 'using System; class W { static void Main() { System.Console.WriteLine("warm"); } }'
        for attempt in range(5):
            try:
                await loop.run_in_executor(None, lambda: compile_csharp_sync(code))
                print("Warm-up compile OK")
                return
            except Exception as exc:
                print(f"Warm-up {attempt + 1}: {exc}")
                await asyncio.sleep(10)
        print("Warm-up compile failed (cold start may be slow)")

    async def run_compile(self, room, code, requestor):
        if room.compiling:
            self.push(requestor, {"type": "run", "result": {
                "success": False, "stdout": "",
                "stderr": "Компиляция уже выполняется, подождите…"}})
            return
        room.compiling = True
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: compile_csharp_sync(code)),
                Config.COMPILE_TIMEOUT + 10)
        except asyncio.TimeoutError:
            result = {"success": False, "stdout": "",
                      "stderr": f"Превышено время ({Config.COMPILE_TIMEOUT}с)"}
        except Exception as exc:
            result = {"success": False, "stdout": "",
                      "stderr": f"Ошибка компилятора: {exc}"}
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
        handler = _WS_ROUTES.get(msg.get("type"))
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
                "msg": f"Комната «{name}» уже создана как {Config.KIND_LABELS[room.kind]}"})
            return
        if room is None:
            room = self.rooms.get_or_create(name, kind)
        peer.room = room
        room.updated = time.time()
        room.doc = msg.get("xml") or room.doc
        room.peers.add(peer)
        self.push(peer, {
            "type": "welcome", "room": name, "kind": room.kind,
            "peers": len(room.peers), "name": peer.name,
            "names": self.peer_names(room, peer),
            "chat": room.msgs[-Config.CHAT_HISTORY:]})
        for other in room.peers:
            if other is not peer:
                self.push(other, {"type": "peers", "count": len(room.peers),
                                  "names": self.peer_names(room, other)})
        for other in room.peers:
            if other is not peer and other.file:
                self.push(peer, {"type": "peerf", "file": other.file,
                                 "peer": "other", "name": other.name})
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
            value = str(msg.get("file") or "").strip()
            peer.file = os.path.basename(value)[:128] or None
            self.broadcast(room, peer, {
                "type": "peerf", "file": peer.file,
                "peer": "other", "name": peer.name})

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
                "type": "error", "msg": "C# доступен только в комнатах типа C#"})
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
                self.push(other, {"type": "peers", "count": len(room.peers),
                                  "names": self.peer_names(room, other)})
            self.broadcast(room, None, {"type": "peerf", "file": None})
        peer.room = None


_WS_ROUTES = {
    "join": App._ws_join,
    "list": App._ws_list,
    "edit": App._ws_edit,
    "cursor": App._ws_cursor,
    "fsel": App._ws_fsel,
    "compile": App._ws_compile,
    "chat": App._ws_chat,
}


class HttpRequest:
    def __init__(self, method, path, query, email, cookie, body):
        self.method = method
        self.path = path
        self.query = query
        self.email = email
        self.cookie = cookie
        self.body = body
        self._json = None

    def json(self):
        if self._json is None:
            try:
                self._json = json.loads(self.body.decode("utf-8", "replace") or "{}")
            except Exception:
                self._json = {}
        return self._json


class HttpServer:
    FIXED = {
        ("GET", "/api/me"): ("me", True),
        ("POST", "/api/register"): ("register", True),
        ("POST", "/api/login"): ("login", True),
        ("POST", "/api/logout"): ("logout", False),
        ("GET", "/api/rooms"): ("rooms", False),
        ("POST", "/api/chat"): ("chat", False),
    }
    PUBLIC_PAGES = {"/", "/index.html"}
    REDIRECTS = {"/": "/index.html", "/room": "/room.html"}

    def __init__(self, app):
        self.app = app

    async def handle(self, writer, req):
        if req.path.startswith("/api/"):
            await self._api(writer, req)
            return
        await self._page(writer, req)

    async def _api(self, writer, req):
        if req.path.startswith("/api/files/"):
            await self._files(writer, req)
            return
        route = self.FIXED.get((req.method, req.path))
        if route is None:
            await self._reply(writer, {"ok": False, "error": "Не найдено"}, "404 Not Found")
            return
        name, public = route
        if not public and not req.email:
            await self._reply(writer, {"ok": False, "error": "Нужен вход"}, "401 Unauthorized")
            return
        handler = {
            "me": self._me, "register": self._register, "login": self._login,
            "logout": self._logout, "rooms": self._rooms, "chat": self._chat,
        }[name]
        await handler(writer, req)

    async def _page(self, writer, req):
        if req.method != "GET":
            await self._reply(writer, {"ok": False, "error": "Не найдено"}, "404 Not Found")
            return
        if req.path not in self.PUBLIC_PAGES and not req.email:
            await self._redirect(writer, "/")
            return
        rel = self.REDIRECTS.get(req.path, req.path.lstrip("/")).lstrip("/")
        target = (Config.WWW / rel).resolve()
        if not str(target).startswith(str(Config.WWW)) or not target.is_file():
            await self._raw(writer, b"404 not found", "text/plain", "404 Not Found")
            return
        ctype = Config.CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        await self._raw(writer, target.read_bytes(), ctype)

    async def _me(self, writer, req):
        status = "200 OK" if req.email else "401 Unauthorized"
        payload = ({"ok": True, "email": req.email,
                    "name": self.app.users.name(req.email)} if req.email
                   else {"ok": False, "error": "Не авторизован"})
        await self._reply(writer, payload, status)

    async def _register(self, writer, req):
        data = req.json()
        email = (data.get("email") or "").strip().lower()
        password = str(data.get("password") or "")
        name = str(data.get("name") or "").strip().replace("\n", " ")[:32]
        if not email or email.count("@") != 1 or len(password) < 6:
            await self._reply(writer, {"ok": False,
                                       "error": "Почта или пароль некорректны (пароль ≥6 символов)"},
                              "400 Bad Request")
            return
        if self.app.users.exists(email):
            await self._reply(writer, {"ok": False, "error": "Почта уже занята"}, "409 Conflict")
            return
        self.app.users.create(email, password, name or email.split("@")[0])
        await self._reply(writer, {"ok": True})

    async def _login(self, writer, req):
        data = req.json()
        email = (data.get("email") or "").strip().lower()
        password = str(data.get("password") or "")
        user = self.app.users.get(email)
        if not user or not pbkdf2_verify(password, user["pw"]):
            await self._reply(writer, {"ok": False, "error": "Неверная почта или пароль"},
                              "401 Unauthorized")
            return
        token, max_age = self.app.sessions.create(email, bool(data.get("remember")))
        headers = {"Set-Cookie": f"{Config.AUTH_COOKIE}={token}; HttpOnly; Path=/; Max-Age={max_age}"}
        await self._reply(writer, {"ok": True}, headers=headers)

    async def _logout(self, writer, req):
        for part in (req.cookie or "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == Config.AUTH_COOKIE and value:
                self.app.sessions.delete(value)
                break
        headers = {"Set-Cookie": f"{Config.AUTH_COOKIE}=; HttpOnly; Path=/; Max-Age=0"}
        await self._reply(writer, {"ok": True}, headers=headers)

    async def _rooms(self, writer, req):
        await self._reply(writer, {"rooms": self.app.rooms.snapshot()})

    async def _chat(self, writer, req):
        if not self.app.chat.ready:
            await self._reply(writer, {"ok": False, "error": "Ассистент недоступен (ключ не задан)"},
                              "503 Service Unavailable")
            return
        data = req.json()
        result = await self.app.chat.reply(data.get("messages"), data.get("kind"), data.get("code"))
        await self._reply(writer, result)

    async def _files(self, writer, req):
        action = req.path.split("/")[-1]
        if req.method == "GET" and action == "list":
            room = req.query.get("room", [""])[0]
            items = []
            for f in sorted(self.app.rooms.file_dir(room).iterdir()):
                if f.is_file() and len(items) < 100:
                    items.append({"name": f.name, "size": f.stat().st_size})
            await self._reply(writer, {"ok": True, "files": items})
            return
        if req.method == "GET" and action == "read":
            room = req.query.get("room", [""])[0]
            name = os.path.basename(req.query.get("name", [""])[0])[:128]
            target = (self.app.rooms.file_dir(room) / name).resolve()
            if not self._safe_file(name, target) or not target.is_file():
                await self._reply(writer, {"ok": False, "error": "Файл не найден"},
                                  "400 Bad Request")
                return
            text = target.read_text("utf-8", "replace")[:65536]
            await self._reply(writer, {"ok": True, "name": name, "text": text})
            return
        if req.method == "POST" and action == "write":
            data = req.json()
            name = os.path.basename(str(data.get("name", "")))[:128]
            room = str(data.get("room", ""))
            target = (self.app.rooms.file_dir(room) / name).resolve()
            if not self._safe_file(name, target):
                await self._reply(writer, {"ok": False, "error": "Некорректное имя файла"},
                                  "400 Bad Request")
                return
            target.write_text(str(data.get("text", ""))[:65536], "utf-8")
            await self._reply(writer, {"ok": True, "name": name})
            self._notify_files(room)
            return
        if req.method == "POST" and action == "delete":
            data = req.json()
            name = os.path.basename(str(data.get("name", "")))[:128]
            room = str(data.get("room", ""))
            target = (self.app.rooms.file_dir(room) / name).resolve()
            if not self._safe_file(name, target) or not target.is_file():
                await self._reply(writer, {"ok": False, "error": "Файл не найден"},
                                  "400 Bad Request")
                return
            target.unlink()
            await self._reply(writer, {"ok": True})
            self._notify_files(room)
            return
        if req.method == "POST" and action == "reset":
            data = req.json()
            room = str(data.get("room", ""))
            for f in self.app.rooms.file_dir(room).iterdir():
                if f.is_file():
                    f.unlink()
            await self._reply(writer, {"ok": True})
            self._notify_files(room)
            return
        await self._reply(writer, {"ok": False, "error": "Не найдено"}, "404 Not Found")

    def _safe_file(self, name, target):
        return bool(name) and str(target).startswith(str(Config.PROJECT))

    def _notify_files(self, room_name):
        room = self.app.rooms.get(room_name or "demo")
        if room:
            self.app.broadcast(room, None, {"type": "files"})

    async def _reply(self, writer, payload, status="200 OK", headers=None):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        words = {"200": "OK", "400": "Bad Request", "401": "Unauthorized",
                 "404": "Not Found", "409": "Conflict",
                 "503": "Service Unavailable"}
        code = status.split()[0]
        out = [f"HTTP/1.1 {code} {words.get(code, 'Error')}\r\n"
               "Content-Type: application/json\r\n"
               f"Content-Length: {len(body)}\r\n"]
        for key, value in (headers or {}).items():
            out.append(f"{key}: {value}\r\n")
        out.append("Cache-Control: no-store\r\nConnection: close\r\n\r\n")
        writer.write("".join(out).encode() + body)
        await writer.drain()
        writer.close()

    async def _redirect(self, writer, location):
        head = (f"HTTP/1.1 302 Found\r\nLocation: {location}\r\n"
                "Content-Length: 0\r\nConnection: close\r\n\r\n")
        writer.write(head.encode())
        await writer.drain()
        writer.close()

    async def _raw(self, writer, body, ctype, status="200 OK"):
        head = (f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Cache-Control: no-store\r\nConnection: close\r\n\r\n")
        writer.write(head.encode() + body)
        await writer.drain()
        writer.close()


async def recv_headers(reader):
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


class Connection:
    def __init__(self, app, http):
        self.app = app
        self.http = http

    async def run(self, reader, writer):
        try:
            request_line, headers = await recv_headers(reader)
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
            email = self.app.sessions.email(headers.get("cookie", ""))
            req = HttpRequest(method, path, query_dict, email,
                              headers.get("cookie", ""), body)
            await self.http.handle(writer, req)
            return
        await self._websocket(reader, writer, headers)

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
        peer = None
        try:
            email = self.app.sessions.email(headers.get("cookie", ""))
            if not email:
                writer.close()
                return
            key = headers.get("sec-websocket-key", "")
            accept = ws_accept(key)
            writer.write((f"HTTP/1.1 101 Switching Protocols\r\n"
                          "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
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
    parser = argparse.ArgumentParser(description="Мультиплеер-компилятор")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8383)
    args = parser.parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\nСтоп.")