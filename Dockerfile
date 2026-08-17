# FinModel AI — all-in-one образ: FastAPI + статика + LibreOffice (для QA-движка)
FROM python:3.12-slim

# LibreOffice Calc нужен QA-движку для headless-пересчёта xlsx (срез 4)
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-calc fonts-dejavu \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY static ./static

ENV DATA_DIR=/var/data
EXPOSE 8000

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
