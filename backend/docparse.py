"""Локальное извлечение текста из документов юзера.

Детерминированно, бесплатно, не зависит от LLM-провайдера:
один и тот же файл одинаково читается для Kimi и MiniMax.
Форматы: txt/md/csv/json/log — как есть; pdf — pypdf; xlsx — openpyxl.
"""
import csv
import io
import json


def extract_text(path: str, filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext in ("txt", "md", "log", "json", "csv", "tsv"):
        return _extract_plain(path, ext)
    if ext == "pdf":
        return _extract_pdf(path)
    if ext in ("xlsx", "xlsm"):
        return _extract_xlsx(path)
    raise ValueError(f"формат .{ext} пока не поддерживается")


def _extract_plain(path: str, ext: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    if ext == "json":
        try:  # красиво развернуть JSON для читабельности моделью
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except Exception:
            pass
    if ext in ("csv", "tsv"):
        try:
            rows = list(csv.reader(io.StringIO(text)))
            text = "\n".join(" | ".join(c.strip() for c in row) for row in rows[:500])
        except Exception:
            pass
    return text


def _extract_pdf(path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    parts = []
    for i, page in enumerate(reader.pages[:60]):  # не более 60 страниц
        t = (page.extract_text() or "").strip()
        if t:
            parts.append(f"--- стр. {i + 1} ---\n{t}")
    if not parts:
        raise ValueError("PDF без текстового слоя (скан) — нужен OCR")
    return "\n\n".join(parts)


def _extract_xlsx(path: str) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    parts = []
    for ws in wb.worksheets[:20]:
        rows = []
        for row in ws.iter_rows(max_row=300, values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(cells):
                rows.append(" | ".join(cells).rstrip(" |"))
            if len(rows) >= 300:
                break
        if rows:
            parts.append(f"=== Лист «{ws.title}» ===\n" + "\n".join(rows))
    wb.close()
    if not parts:
        raise ValueError("Excel-файл пуст")
    return "\n\n".join(parts)
