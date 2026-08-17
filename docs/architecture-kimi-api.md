# FinModel AI — архитектура под Kimi API (v2)

Заменяет v1 (`architecture-fin-model-engine.pdf`, ориентирован на Claude). Главный принцип не изменился: **LLM решает _что_ считать, детерминированный код решает _как_ записать в Excel**. Поменялась LLM-платформа и добавилась инфраструктура: развёртывание на Render, хранение без внешней БД, реальный контур файлов.

## 0. Что изменилось относительно v1

| Было (v1) | Стало (v2) | Почему |
|---|---|---|
| Claude, structured output | Kimi API (K3), JSON Mode + function-style схемы в промпте | Выбранный провайдер; JSON Mode гарантирует валидный JSON [^1^] |
| Celery + Redis + очередь | In-process asyncio-задачи + таблица `jobs` в SQLite | Один инстанс на Render; Redis — лишний платный сервис |
| Без конкретики по хранению | Persistent Disk + SQLite (метаданные) + файлы (артефакты) | ФС на Render эфемерна; отдельной БД нет по требованию |
| «Загрузка файлов» абстрактно | Kimi Files API: upload → парсинг → кеш текста у себя | Нельзя ссылаться на `file_id` в диалоге — контент забираем и подкладываем сами [^2^] |
| Ответ целиком | SSE-стриминг всюду | Kimi рекомендует `stream=True`, чтобы не ловить таймауты промежуточных гейтвеев [^2^] |

## 1. Общая схема

```
[Frontend: SPA-чат]  ←─ статика, отдаётся тем же сервисом
      │  POST /api/chat (SSE)   POST /api/files (multipart)
      ▼
[Backend: FastAPI, один инстанс Render]
      │
      ├── Storage: SQLite (метаданные) + /var/data (файлы)
      │
      ├── Kimi Gateway ──► api.moonshot.ai/v1
      │      ├── Files API: upload, get parsed content
      │      └── Chat: JSON Mode, streaming, tool-less
      │
      ├── Extraction Agent  ──► Data Extraction Report (JSON)
      │           ▲
      │     [Экран ревью юзером]   ← правки через чат или таблицу
      │           ▼
      ├── Modelling Agent   ──► Model Spec (JSON)
      │
      ├── Excel Builder (openpyxl + библиотека шаблонов)
      │           ▼
      └── QA Engine (LibreOffice recalc + numpy-financial)
                  ▼
      .xlsx на диск → карточка файла в чат → GET /api/models/{id}/download
```

Ключевое отличие потока от «просто чата»: **файлы в обе стороны ходят через наш бэкенд**. Kimi Files API умеет только принимать файлы и отдавать их распарсенный текст; «прислать файл юзеру» API не умеет в принципе — .xlsx рождает наш Excel Builder, хранит наш диск, отдаёт наш endpoint. Для юзера опыт идентичен веб-Kimi.

## 2. Стек и развёртывание

- **Backend: Python 3.12 + FastAPI.** Выбор над Node обусловлен тем, что Excel Builder (openpyxl), QA (numpy-financial) и парсинг — всё на Python; не плодим второй рантайм.
- **Frontend:** текущий прототип (`index.html`) отдаётся как статика из того же сервиса. Когда вырастет — React/Vite, сборка в `dist/`, отдача тем же FastAPI.
- **Render:** один Web Service (Starter), Docker-деплой, Persistent Disk 5–10 ГБ на `/var/data`.
- **LibreOffice** ставится в Docker-образ (headless-пересчёт в QA).
- Всё конфигурируется через env: `MOONSHOT_API_KEY`, `MOONSHOT_BASE_URL=https://api.moonshot.ai/v1`, `DATA_DIR=/var/data`, `KIMI_MODEL=kimi-k3`.

### render.yaml (blueprint)

```yaml
services:
  - type: web
    name: finmodel-ai
    runtime: docker
    plan: starter
    healthCheckPath: /healthz
    disk:
      name: finmodel-data
      mountPath: /var/data
      sizeGB: 10
    envVars:
      - key: MOONSHOT_API_KEY
        sync: false        # секрет, задаётся в дашборде
      - key: MOONSHOT_BASE_URL
        value: https://api.moonshot.ai/v1
      - key: DATA_DIR
        value: /var/data
      - key: KIMI_MODEL
        value: kimi-k3
```

### Dockerfile — ключевые моменты

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-calc fonts-dejavu && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DATA_DIR=/var/data
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`requirements.txt`: `fastapi, uvicorn, httpx, openpyxl, numpy-financial, aiosqlite, python-multipart, itsdangerous` (подпись кук).

> Важно: один инстанс с диском **нельзя горизонтально масштабировать** — Render это запретит сам. Для MVP это фича, а не баг.

## 3. Хранение: SQLite + файлы на диске

### Раскладка на Persistent Disk

```
/var/data/
├── app.db                        # SQLite: юзеры, чаты, файлы, джобы, отчёты
└── users/<user_id>/
    ├── uploads/<file_id>.pdf     # оригинал
    ├── uploads/<file_id>.txt     # распарсенный Kimi текст (кеш!)
    ├── reports/<report_id>.xlsx  # готовая модель
    └── reports/<report_id>.qa.json
```

### Схема SQLite (стартовая)

```sql
CREATE TABLE users (
  id TEXT PRIMARY KEY,            -- uuid
  created_at TEXT, last_seen_at TEXT,
  quota_bytes INTEGER DEFAULT 209715200   -- 200 МБ
);
CREATE TABLE chats (
  id TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id),
  title TEXT, extraction_json TEXT, model_spec_json TEXT,  -- версионируемые JSON
  status TEXT DEFAULT 'dialog',   -- dialog | review | built
  created_at TEXT
);
CREATE TABLE messages (
  id TEXT PRIMARY KEY, chat_id TEXT REFERENCES chats(id),
  role TEXT, content TEXT, attachments_json TEXT, created_at TEXT
);
CREATE TABLE files (
  id TEXT PRIMARY KEY, user_id TEXT, chat_id TEXT,
  orig_name TEXT, mime TEXT, size INTEGER,
  path TEXT, parsed_path TEXT, kimi_file_id TEXT, parse_status TEXT,
  created_at TEXT
);
CREATE TABLE jobs (               -- замена Celery+Redis
  id TEXT PRIMARY KEY, chat_id TEXT, type TEXT,   -- extract | build | qa
  status TEXT, progress INTEGER, result_json TEXT, error TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE reports (
  id TEXT PRIMARY KEY, chat_id TEXT, path TEXT,
  qa_json TEXT, kind TEXT,        -- simple | full
  created_at TEXT
);
```

Правила эксплуатации:

- **WAL-режим** SQLite (`PRAGMA journal_mode=WAL`) — чтение не блокирует запись.
- **Атомарные записи файлов**: пишем `*.tmp` → `os.replace()`.
- **Один writer-таск** на процесс для JSON-состояний чата (через `asyncio.Lock` на chat_id достаточно).
- **Квоты**: 25 МБ на файл, 200 МБ на юзера; ночная джоба чистит чаты старше 30 дней без активности (файлы → в корзину на 7 дней).
- **Бэкап**: Litestream реплицирует `app.db` в Backblaze B2; ночной cron пакует `/var/data/users` в tar.gz → туда же. Диск ≠ бэкап.

## 4. Интеграция Kimi API

Базовый URL для международного региона: `https://api.moonshot.ai/v1` (ключи и баланс региональных платформ изолированы; ключ от platform.kimi.com к platform.kimi.ai не подойдёт — 401).[^2^] OpenAI-совместимый SDK, но рекомендую голый `httpx` — полный контроль над стримингом и ретраями.

### 4.1. Файлы: upload → parse → кеш

```
POST /v1/files            (multipart, purpose="file-extract")  → file_id
GET  /v1/files/{id}/content                                   → распарсенный текст
```

- Парсинг на стороне Kimi: текстовые форматы — извлечение текста; сканы и картинки — OCR. PDF «только картинки» тоже уйдут в OCR автоматически.[^2^]
- **Сослаться на файл по `file_id` в диалоге нельзя** — текст забираем и подкладываем в контекст сами.[^2^] Поэтому сразу кешируем: `parsed_path` в SQLite + файл `.txt` на диске. Повторный парсинг того же файла не делаем никогда.
- Загрузка файла бесплатна, но распарсенный текст тарифицируется как входные токены в каждом запросе, где он подложен.[^2^] → см. §7 про бюджет контекста.
- Base64 для документов **не используем** — официально не рекомендовано из-за раздувания токенов.[^2^] Картинки при необходимости — через vision-модель в messages (URL/Base64), но для MVP достаточно OCR через Files API.

### 4.2. Чат: JSON Mode для агентов, стриминг для диалога

```python
payload = {
  "model": "kimi-k3",
  "messages": [...],
  "stream": True,
  "temperature": 0.3,                       # для агентов — детерминизм важнее креатива
  "max_completion_tokens": 16384,
  "response_format": {"type": "json_object"},   # Extraction и Modelling агенты
  "reasoning_effort": "high",               # K3 всегда "думает"; для простых реплик — "low"
}
```

- **JSON Mode** гарантирует парсабельный JSON, но структуру надо описать в промпте (схема + пример).[^1^] Валидация у нас — pydantic-моделью; невалидный ответ → один автоматический retry с сообщением об ошибке схемы, дальше — эскалация юзеру.
- **Стриминг всегда.** Без `stream=True` длинные ответы рискуют умереть по таймауту на промежуточных гейтвеях.[^2^] SSE проксируем до фронта — юзер видит «печатающийся» ответ и прогресс джоб.
- **`finish_reason="length"`** = ответ обрезан по `max_completion_tokens`. Для JSON-ответов агентов это критично (невалидный JSON) — детектим, увеличиваем лимит, повторяем.[^2^]
- **Context Caching** включается автоматически для повторяющегося префикса: системный промпт и описание схем держим **байт-в-байт стабильными** в начале messages — экономия на входных токенах без нашей конфигурации.[^2^]
- **429**: различаем `engine_overloaded_error` (ждём `Retry-After`, экспоненциальный backoff) и `rate_limit_reached_error` (лимит тира — меньше конкурентности). Учитываем, что SDK с авторетраями раздувает счётчик RPM — делаем свои ретраи осознанно.[^2^]
- **Модель не знает дату** и может ошибаться в арифметике — дату кладём в системный промпт, **все** вычисления — только в Excel Builder/QA.[^2^]
- Лимиты вывода: у K3 контекст до 1M токенов, вывод до `1M − prompt_tokens`; у K2.5/K2.6 — 256K.[^2^] Запаса K3 хватает на несколько документов + историю.

### 4.3. Класс-обёртка `KimiGateway`

Единая точка: `upload_file()`, `get_file_content()`, `chat_json(schema_hint, messages)`, `chat_stream(messages)`. Внутри — ретраи, таймауты, логирование `request_id` (без него саппорту Moonshot нечего расследовать).[^2^] Никакой бизнес-логики, чтобы провайдера при желании можно было заменить за день.

## 5. Агентный контур

### 5.1. Extraction Agent

Вход: описание юзера + распарсенные тексты файлов. Выход — строго JSON по схеме:

```json
{
  "business_model": "saas_subscription",
  "parameters": [
    {"id": "p1", "category": "revenue", "name": "ARPU",
     "value": 12000, "unit": "RUB/month",
     "status": "SOURCE | DERIVED | ASSUMPTION | MISSING",
     "source": {"file_id": "f1", "quote": "..."},
     "confidence": 0.92}
  ],
  "conflicts": [{"parameter": "churn", "values": [{"v": 0.04, "src": "chat"}, {"v": 0.07, "src": "f2"}]}],
  "missing": [{"name": "tax_regime", "why_needed": "налог на выручку", "suggested_default": "USN6"}]
}
```

Обязательные свойства: `quote` для SOURCE (traceability — фича доверия), `confidence` для сортировки экрана ревью, `MISSING` только с `suggested_default`. Агенту **запрещено** додумывать значения вне `ASSUMPTION` с явным флагом (тот самый принцип NO INVENTED DATA из v1).

### 5.2. Ревью юзером

Extraction Report сохраняется в `chats.extraction_json`, отдаётся на фронт: редактируемая таблица + чат («налог УСН 15%, поправь»). Каждая правка — новая версия JSON, старые не затираем.

### 5.3. Modelling Agent

Вход: подтверждённый Extraction Report. Выход — Model Spec: **какие шаблоны применить и с какими параметрами, а не текст формул**:

```json
{
  "horizon_months": 36,
  "revenue_engine": {"template": "customers_arpu_churn",
                     "params": {"arpu": "p1", "churn": "p3", "new_customers": "p4"}},
  "sheets": ["Assumptions", "Revenue", "OPEX", "PnL", "CashFlow", "DCF", "Dashboard"],
  "scenarios": ["base", "pessimistic"],
  "modules": [{"template": "npv_irr", "params": {"discount_rate": "p9"}},
              {"template": "working_capital", "params": {"dso": 30, "dio": 0, "dpo": 45}}]
}
```

Валидация: все `template` — из реестра шаблонов, все `params` ссылаются на существующие `p*` из Extraction Report. Неизвестный шаблон → отказ с честным «пока не поддерживаем», а не импровизация.

### 5.4. Excel Builder

Детерминированный: Model Spec → openpyxl-сборка из библиотеки шаблонов. Каждый шаблон — класс с `render(workbook, params)` и **золотым тестом** (фикстура → xlsx → LibreOffice recalc → сверка значений) в CI. Циркулярные ссылки запрещены на уровне шаблонов (проценты считаем на начало периода). Все допущения — только на листе Assumptions, формулы ссылаются туда, не содержат констант.

### 5.5. QA Engine

1. Headless LibreOffice пересчитывает файл; ловим `#REF!`, `#DIV/0!`, `#VALUE!`.
2. Независимый пересчёт NPV/IRR через numpy-financial, допуск < 0,1%.
3. Структурные чеки: баланс сходится, roll-forward ОС, суммы по годам консистентны.
4. Результат — `qa.json` рядом с файлом + строка в чате («14 проверок пройдено»).

## 6. Backend API (FastAPI)

```
POST   /api/session                    # анонимная сессия, подписанная httpOnly-кука
POST   /api/files                      # multipart → диск → Kimi Files API → parse → кеш
GET    /api/files/{id}                 # метаданные + статус парсинга
DELETE /api/files/{id}
POST   /api/chats/{id}/messages        # SSE-стрим: реплика агента (диалог/интервью)
POST   /api/chats/{id}/extract         # запуск Extraction Agent (job)
GET    /api/chats/{id}/extraction      # Data Extraction Report (+ версии)
PUT    /api/chats/{id}/extraction      # правки юзера (новая версия)
POST   /api/chats/{id}/build           # Modelling Agent + Excel Builder + QA (job)
GET    /api/jobs/{id}                  # статус/progress (или SSE-канал)
GET    /api/reports/{id}/download      # отдача .xlsx
GET    /healthz
```

- Все длинные операции — `jobs`: создание джобы возвращает `job_id` сразу, прогресс стримится. HTTP-запрос никогда не висит минуту.
- SSE, а не WebSocket: односторонний поток, проще через прокси Render.

## 7. Бюджет контекста и стоимость

Главная статья расходов — **входные токены**: распарсенные документы тарифицируются каждый раз, когда подложены в запрос.[^2^] Отсюда правила:

1. Системный промпт + JSON-схемы — стабильный префикс (бесплатное попадание в Context Caching).[^2^]
2. В диалоговые реплики сырые тексты документов **не тащим**: после extraction в контекст идёт только Extraction JSON (~1–2K токенов) + последние N реплик.
3. Сырой текст подкладывается один раз — в Extraction Agent.
4. Учёт расхода: логируем `usage` каждого запроса в SQLite (`jobs.result_json`) — это и основа будущего ценообразования (себестоимость одной модели).

Грубая смета одной модели на K3: документы ~50–150K входных (однократно, дальше кеш/JSON), extraction ~4K выходных, modelling ~4K, диалог — мелочь. При этом K3 открывается после пополнения от $1.[^2^]

## 8. Безопасность и приватность

- Секреты — только env (`sync: false` в Render), никогда в git (токен GitHub из `bis model cred.txt` — тоже не в репо).
- Подписанные куки сессий (`itsdangerous`), `httpOnly`, `Secure`, `SameSite=Lax`.
- Антивирус/санитизация файлов на MVP: лимит размера + белый список MIME; ClamAV — позже.
- Дисклеймер в чате и на листе Assumptions: «модель — инструмент оценки, не финансовая рекомендация».
- В privacy policy прямо пишем: документы уходят на обработку в Kimi API (третья сторона). Для B2B-клиентов это будет первый вопрос — отвечаем ссылкой на data security политику платформы.[^3^]

## 9. MVP-дорожная карта (срез за срезом)

1. **Сквозной скелет (1–2 недели)**: FastAPI + диск + SQLite + KimiGateway + загрузка файла → парсинг → показ текста во фронте. Уже на этом этапе чат из прототипа подключается к живому бэкенду.
2. **Extraction + ревью (2 недели)**: Extraction Agent с JSON Mode, экран ревью (таблица из прототипа), версии правок.
3. **Simple Model (2 недели)**: Modelling Agent, 2–3 шаблона (Volume×Price, SaaS, Real Estate), Excel Builder, скачивание.
4. **QA (1 неделя)**: LibreOffice recalc + numpy-financial, qa.json, строка в чате.
5. **Полировка**: интервью-агент для MISSING, what-if контур («подними churn до 7%» → пересборка), бэкапы Litestream.

Eval-харнес с первого дня: 10–15 реальных комплектов «документы → эталонная модель», прогон в CI на каждый коммит в агенты/шаблоны.

## 10. Открытые вопросы (нужны решения основателей)

1. **Регион Kimi**: platform.kimi.ai (международный, api.moonshot.ai) vs platform.kimi.com (Китай) — ключи и баланс изолированы.[^2^] Для РФ-клиентов — проверить стабильность доступа из РФ; в справке честно написано, что доступ из неподдерживаемых регионов не гарантирован.[^2^]
2. **Налоговые режимы РФ** (УСН 6/15, НДС на УСН) — отдельный модуль шаблонов или параметр? Решает продукт, кодируется один раз.
3. **Google Sheets** как второй формат выдачи — спрос с консультантов.
4. **Лимит бесплатных моделей** на юзера до подключения платёжки.

---

[^1^]: [Kimi Help Center — Model capabilities (JSON Mode, OCR)](https://www.kimi.com/help/kimi-api/api-model-capabilities)
[^2^]: [Kimi Help Center — API troubleshooting (файлы, стриминг, 429, кеширование, лимиты, регионы)](https://www.kimi.com/help/kimi-api/api-troubleshooting)
[^3^]: [Kimi Help Center — Data security](https://www.kimi.com/help/kimi-api/api-data-security)
