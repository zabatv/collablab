import json
import urllib.error
import urllib.request
from config import Config


def compile_csharp_sync(code):
    """Компилировать и запустить C# код на удалённом сервере"""
    request = urllib.request.Request(
        Config.COMPILER_URL,
        data=json.dumps({"code": code}, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=Config.COMPILE_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", "replace"))
