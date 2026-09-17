from pathlib import Path

class Config:
    """Конфигурация приложения"""

    # WebSocket
    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    MAX_MSG = 10 * 1024 * 1024

    # Поддерживаемые языки
    KINDS = ("xml", "csharp", "html")
    KIND_LABELS = {"xml": "XML", "csharp": "C#", "html": "HTML"}

    # C# компилятор
    COMPILER_URL = "http://127.0.0.1:8081/compile"
    COMPILE_TIMEOUT = 90

    # AI ассистент
    CHAT_MODEL = "deepseek-r1-0528"
    CHAT_TIMEOUT = 120
    CHAT_MAX_MESSAGES = 30
    CHAT_MAX_MSG_LEN = 6000
    CHAT_MSG_MAX = 600
    CHAT_HISTORY = 100

    # Сессии
    SESSION_DAYS = 30
    SESSION_SECS = SESSION_DAYS * 86400

    # Аутентификация
    PW_ITER = 200000
    AUTH_COOKIE = "auth"

    # Пути
    BASE = Path(__file__).resolve().parent
    WWW = BASE / "www"
    PROJECT = BASE / "project"
    USERS_FILE = BASE / "users.json"
    SESSIONS_FILE = BASE / "sessions.json"

    # API
    PUBLIC_PAGES = {"/", "/index.html"}
    PUBLIC_API = {"/api/me", "/api/register", "/api/login"}
    CONTENT_TYPES = {
        ".html": "text/html",
        ".js": "application/javascript",
        ".css": "text/css",
        ".png": "image/png",
        ".svg": "image/svg+xml"
    }
