import json
import os
import urllib.parse
from pathlib import Path
from config import Config


class HttpRequest:
    """HTTP запрос"""

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
    """HTTP сервер для API и статики"""

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
        payload = (
            {"ok": True, "email": req.email, "name": self.app.users.name(req.email)}
            if req.email
            else {"ok": False, "error": "Не авторизован"}
        )
        await self._reply(writer, payload, status)

    async def _register(self, writer, req):
        from auth import pbkdf2_hash
        data = req.json()
        email = (data.get("email") or "").strip().lower()
        password = str(data.get("password") or "")
        name = str(data.get("name") or "").strip().replace("\n", " ")[:32]
        if not email or email.count("@") != 1 or len(password) < 6:
            await self._reply(
                writer,
                {"ok": False, "error": "Почта или пароль некорректны (пароль ≥6 символов)"},
                "400 Bad Request"
            )
            return
        if self.app.users.exists(email):
            await self._reply(writer, {"ok": False, "error": "Почта уже занята"}, "409 Conflict")
            return
        self.app.users.create(email, password, name or email.split("@")[0])
        await self._reply(writer, {"ok": True})

    async def _login(self, writer, req):
        from auth import pbkdf2_verify
        data = req.json()
        email = (data.get("email") or "").strip().lower()
        password = str(data.get("password") or "")
        user = self.app.users.get(email)
        if not user or not pbkdf2_verify(password, user["pw"]):
            await self._reply(
                writer,
                {"ok": False, "error": "Неверная почта или пароль"},
                "401 Unauthorized"
            )
            return
        token, max_age = self.app.sessions.create(email, bool(data.get("remember")))
        headers = {
            "Set-Cookie": f"{Config.AUTH_COOKIE}={token}; HttpOnly; Path=/; Max-Age={max_age}"
        }
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
            await self._reply(
                writer,
                {"ok": False, "error": "Ассистент недоступен (ключ не задан)"},
                "503 Service Unavailable"
            )
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
                await self._reply(writer, {"ok": False, "error": "Файл не найден"}, "400 Bad Request")
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
                await self._reply(writer, {"ok": False, "error": "Некорректное имя файла"}, "400 Bad Request")
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
                await self._reply(writer, {"ok": False, "error": "Файл не найден"}, "400 Bad Request")
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
        words = {
            "200": "OK", "400": "Bad Request", "401": "Unauthorized",
            "404": "Not Found", "409": "Conflict", "503": "Service Unavailable"
        }
        code = status.split()[0]
        out = [
            f"HTTP/1.1 {code} {words.get(code, 'Error')}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
        ]
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
