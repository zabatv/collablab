import base64
import hashlib
import struct
from config import Config


def ws_accept(key):
    """Генерировать WebSocket accept key"""
    digest = hashlib.sha1((key + Config.GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def ws_frame(opcode, payload=b"", fin=1):
    """Создать WebSocket фрейм"""
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
