import asyncio
import os
from config import Config

try:
    import opendeep as _od
    HAS_OPENDEEP = True
except Exception:
    HAS_OPENDEEP = False
    _od = None


class Chat:
    """AI ассистент на базе DeepSeek"""

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
        hints = {
            "xml": "Текущий XML:",
            "csharp": "Текущий код C#:",
            "html": "Текущий HTML:"
        }
        system = (
            "Ты — OpenDeep, ассистент в совместном редакторе кода CollabLab. "
            f"Комната вида: {Config.KIND_LABELS[kind]}. "
            "Отвечай кратко и по делу на русском. Если дают код — помогай "
            "исправлять и объяснять."
        )
        messages = [{"role": "system", "content": system}]
        if code:
            messages.append({
                "role": "system",
                "content": hints[kind] + "\n" + code[:Config.CHAT_MAX_MSG_LEN]
            })
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
            for m in messages
        )

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
