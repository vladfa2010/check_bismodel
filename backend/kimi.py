"""KimiGateway — единая точка общения с Kimi API (Moonshot).

Вся бизнес-логика снаружи, здесь только транспорт: ретраи, таймауты,
mock-режим для разработки без ключа. Смена провайдера = правка одного файла.
"""
import asyncio
import json
import uuid

import httpx

from . import config

MAX_RETRIES = 3


def _mock_reply(user_text: str, docs: list[str]) -> str:
    files_line = ""
    if docs:
        files_line = f"\n\nЯ получил и разобрал {len(docs)} документ(а) — в боевом режиме извлеку из них параметры модели."
    return (
        "Принял! Это ответ бэкенда в mock-режиме (MOONSHOT_API_KEY не задан, "
        "запрос к Kimi не уходил)."
        f"{files_line}\n\n"
        "В боевом режиме на этом месте Kimi обсудит с вами бизнес-модель, "
        "задаст вопросы о недостающих параметрах (цена, клиенты, churn, налоги) "
        "и подготовит Data Extraction Report для сборки Excel-модели."
    )


class KimiGateway:
    def __init__(self) -> None:
        self.base = config.MOONSHOT_BASE_URL
        self.key = config.MOONSHOT_API_KEY
        self.mock = config.MOCK_KIMI

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"}

    # ---------- файлы ----------
    async def upload_file(self, path: str, filename: str) -> str:
        """POST /v1/files (purpose=file-extract) → file_id."""
        if self.mock:
            await asyncio.sleep(0.5)
            return "mock-" + uuid.uuid4().hex[:10]
        async with httpx.AsyncClient(timeout=120) as client:
            with open(path, "rb") as f:
                resp = await client.post(
                    f"{self.base}/files",
                    headers=self._headers(),
                    files={"file": (filename, f)},
                    data={"purpose": "file-extract"},
                )
            resp.raise_for_status()
            return resp.json()["id"]

    async def file_content(self, file_id: str) -> str:
        """GET /v1/files/{id}/content → распарсенный текст (текст / OCR)."""
        if self.mock:
            await asyncio.sleep(0.5)
            return "(демо-текст документа: в боевом режиме здесь распарсенное Kimi содержимое файла)"
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(f"{self.base}/files/{file_id}/content", headers=self._headers())
            resp.raise_for_status()
            try:
                data = resp.json()
                return data.get("content") or resp.text
            except Exception:
                return resp.text

    # ---------- чат ----------
    async def chat_stream(self, messages: list[dict], json_mode: bool = False):
        """Асинхронный генератор текстовых дельт (SSE-стрим Kimi → наружу)."""
        if self.mock:
            user_text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            text = _mock_reply(user_text, [m for m in messages if "Документ «" in m.get("content", "")])
            # имитация стриминга по словам
            for word in text.split(" "):
                await asyncio.sleep(0.03)
                yield word + " "
            return

        payload = {
            "model": config.KIMI_MODEL,
            "messages": messages,
            "stream": True,
            "temperature": 0.3,
            "max_completion_tokens": 4096,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as client:
                    async with client.stream(
                        "POST", f"{self.base}/chat/completions",
                        headers=self._headers(), json=payload,
                    ) as resp:
                        if resp.status_code == 429 and attempt < MAX_RETRIES - 1:
                            retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                            await asyncio.sleep(min(retry_after, 30))
                            continue
                        if resp.status_code != 200:
                            body = (await resp.aread()).decode(errors="replace")[:400]
                            raise RuntimeError(f"Kimi API {resp.status_code}: {body}")
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                return
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            choices = chunk.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            if delta.get("content"):
                                yield delta["content"]
                        return
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
        if last_error:
            raise last_error


kimi = KimiGateway()
