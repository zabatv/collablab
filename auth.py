import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from config import Config


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
    """Управление пользователями"""

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
    """Управление сессиями"""

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
