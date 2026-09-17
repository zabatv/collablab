import json
import os
import time
from pathlib import Path
from config import Config


class JsonStore:
    """Простое хранилище в JSON файле"""

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


class Room:
    """Модель комнаты для совместного редактирования"""

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
        self.msgs.append({
            "name": name,
            "text": text[:Config.CHAT_MSG_MAX],
            "time": int(time.time())
        })
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
    """Модель пира (участника)"""

    def __init__(self, writer):
        self.writer = writer
        self.room = None
        self.file = None
        self.email = None
        self.name = ""


class Rooms:
    """Управление комнатами"""

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
