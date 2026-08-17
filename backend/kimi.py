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
        "Принял! Это ответ бэкенда в mock-режиме (у выбранной модели нет API-ключа, "
        "запрос к LLM не уходил)."
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
    async def chat_stream(self, messages: list[dict], json_mode: bool = False,
                          model: str | None = None):
        """Асинхронный генератор событий: ("delta", текст) и в конце ("usage", dict).

        Мультипровайдерно: model выбирает запись из config.available_models()
        (moonshot / minimax — оба OpenAI-совместимых). Модель без ключа
        (или MOCK_KIMI=1) отвечает заглушкой. Usage просим через
        stream_options.include_usage; если провайдер его не прислал —
        считаем грубую оценку, чтобы учёт токенов не проваливался.
        """
        spec = config.model_spec(model) or {
            "id": model or "unknown", "title": model, "provider": "mock",
            "base_url": "", "key": "", "mock": True,
        }
        if spec["mock"]:
            user_text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            docs = [m for m in messages if "Документ «" in m.get("content", "")]
            text = _mock_reply(user_text, docs)
            in_tokens = sum(len(m.get("content", "")) // 3 for m in messages)
            out_tokens = 0
            for word in text.split(" "):
                await asyncio.sleep(0.03)
                out_tokens += 1
                yield ("delta", word + " ")
            yield ("usage", {
                "prompt_tokens": in_tokens,
                "completion_tokens": out_tokens,
                "total_tokens": in_tokens + out_tokens,
            })
            return

        payload = {
            "model": spec["id"],
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            # temperature не передаём: kimi-k3 принимает только 1, у MiniMax дефолт адекватный
            "max_completion_tokens": 4096,
        }
        if spec["provider"] == "minimax":
            # мысли M3 прилетают отдельным полем reasoning_details — контент остаётся чистым
            payload["reasoning_split"] = True
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {spec['key']}"}
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as client:
                    async with client.stream(
                        "POST", f"{spec['base_url']}/chat/completions",
                        headers=headers, json=payload,
                    ) as resp:
                        if resp.status_code == 429 and attempt < MAX_RETRIES - 1:
                            retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                            await asyncio.sleep(min(retry_after, 30))
                            continue
                        if resp.status_code != 200:
                            body = (await resp.aread()).decode(errors="replace")[:400]
                            raise RuntimeError(f"{spec['provider']} API {resp.status_code}: {body}")
                        usage = None
                        acc_len = 0
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            if chunk.get("usage"):
                                usage = chunk["usage"]
                            choices = chunk.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            if delta.get("content"):
                                acc_len += len(delta["content"])
                                yield ("delta", delta["content"])
                        if not usage:
                            # провайдер не вернул usage — грубая оценка, помечаем estimated
                            in_est = sum(len(m.get("content", "")) for m in messages) // 4
                            out_est = acc_len // 4
                            usage = {"prompt_tokens": in_est, "completion_tokens": out_est,
                                     "total_tokens": in_est + out_est, "estimated": True}
                        yield ("usage", usage)
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
