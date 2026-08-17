#!/usr/bin/env python3
"""E2E-тесты FinModel AI (Playwright + системный Chromium).

Сценарии:
  1) логин: неверный пароль → ошибка; верный → вход, юзер в сайдбаре
  2) создание диалога: сообщение уходит, ответ ассистента стримится
  3) загрузка файла: чип появляется, статус доходит до «готово»
  4) получение результата: ответ с учётом файла + счётчик токенов вырос
  5) разлогин → форма входа; логин вторым юзером; изоляция данных

Запуск (сервер уже должен слушать BASE, MOCK_KIMI=1):
  python tests/e2e_test.py
Переменные: BASE (default http://127.0.0.1:8123)
"""
import os
import re
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

BASE = os.getenv("BASE", "http://127.0.0.1:8123")
CHROMIUM = os.getenv("CHROMIUM", "/usr/bin/chromium")
# Пароль тестовых юзеров не храним в репо — передаётся через env при запуске.
E2E_PASSWORD = os.environ["E2E_PASSWORD"]

results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""), flush=True)


def login(page, user: str, password: str) -> None:
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector("#loginOverlay", state="visible", timeout=8000)
    page.fill("#loginUser", user)
    page.fill("#loginPass", password)
    page.click("#loginOverlay button:has-text('Войти')")


def main() -> int:
    print(f"E2E против {BASE}\n", flush=True)

    # тестовый документ
    doc = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    doc.write("SaaS: 25 новых клиентов/мес, ARPU 12000 руб, churn 4%/мес.")
    doc.close()

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM, headless=True,
                                    args=["--no-sandbox"])
        ctx = browser.new_context()
        page = ctx.new_page()
        page.set_default_timeout(15000)

        # ===== 1. Логин =====
        print("1) Логин", flush=True)
        login(page, "vlad", "wrong-password")
        page.wait_for_selector("#loginError", state="visible")
        err = page.inner_text("#loginError")
        check("неверный пароль → ошибка в форме", "Неверный логин" in err, err)

        login(page, "vlad", E2E_PASSWORD)
        page.wait_for_selector("#loginOverlay", state="hidden")
        page.wait_for_selector("#sbUser")
        who = page.inner_text("#sbUser")
        check("верный пароль → вход, юзер в сайдбаре", who == "vlad", who)

        # ===== 2. Создание диалога =====
        print("2) Создание диалога", flush=True)
        page.fill("#input", "Привет! Хочу финмодель для SaaS.")
        page.press("#input", "Enter")
        # ответ стримится в .streamText; в mock-режиме содержит маркер
        page.wait_for_selector(".streamText", timeout=10000)
        page.wait_for_function(
            "() => [...document.querySelectorAll('.streamText')].some(e => e.textContent.length > 40)",
            timeout=20000,
        )
        reply = page.inner_text(".streamText")
        check("сообщение отправлено, ответ ассистента получен (стрим)", len(reply) > 40,
              reply[:60] + "…")

        # ===== 3. Загрузка файла =====
        print("3) Загрузка файла", flush=True)
        page.set_input_files("#fileInput", doc.name)
        page.wait_for_selector("#attachChips > div")
        page.wait_for_function(
            "() => document.querySelector('#attachChips').textContent.includes('готово')",
            timeout=30000,
        )
        chip = page.inner_text("#attachChips")
        check("файл загружен, парсинг завершён («готово»)", "готово" in chip, chip.replace("\n", " "))

        # ===== 4. Получение результата =====
        print("4) Получение результата", flush=True)
        usage_before = page.inner_text("#sbUsage")
        page.fill("#input", "Учитывай мой документ в модели.")
        page.press("#input", "Enter")
        page.wait_for_function(
            "() => document.querySelectorAll('.streamText').length >= 2",
            timeout=10000,
        )
        page.wait_for_function(
            "() => document.querySelectorAll('.streamText')[1].textContent.length > 40",
            timeout=20000,
        )
        replies = page.eval_on_selector_all(".streamText", "els => els.map(e => e.textContent)")
        check("второй ответ получен (результат диалога)", len(replies) >= 2 and len(replies[1]) > 40,
              replies[-1][:60] + "…")
        # ждём обновления счётчика после стрима — сигнал, что usage записан в БД
        req = lambda s: int(re.search(r"запросов: (\d+)", s).group(1))
        expected = req(usage_before) + 1
        page.wait_for_function(
            f"document.querySelector('#sbUsage').textContent.includes('запросов: {expected}')",
            timeout=15000,
        )
        page.reload(wait_until="networkidle")
        page.wait_for_selector("#sbUser")
        time.sleep(1.5)  # loadUsage догружает /api/usage
        usage_after = page.inner_text("#sbUsage")
        check("счётчик токенов вырос после диалога",
              req(usage_after) == expected,
              f"{usage_before!r} → {usage_after!r}")

        # файл сохранился на сервере у vlad
        files = page.request.get(f"{BASE}/api/files").json()["files"]
        check("файл vlad лежит в его хранилище на сервере", len(files) == 1,
              files[0]["orig_name"] if files else "пусто")

        # ===== 5. Логаут / логин / изоляция =====
        print("5) Логаут / логин victor / изоляция", flush=True)
        page.click("button[title='Выйти']")
        page.wait_for_selector("#loginOverlay", state="visible")
        check("разлогин → форма входа снова показана", True)

        # API без сессии должен быть закрыт
        r = page.request.get(f"{BASE}/api/files")
        check("API без сессии → 401", r.status == 401, f"HTTP {r.status}")

        login(page, "victor", E2E_PASSWORD)
        page.wait_for_selector("#loginOverlay", state="hidden")
        page.wait_for_selector("#sbUser")
        who = page.inner_text("#sbUser")
        check("victor входит", who == "victor", who)
        files = page.request.get(f"{BASE}/api/files").json()["files"]
        check("victor НЕ видит файлы vlad (изоляция)", len(files) == 0, f"files={len(files)}")

        # ===== 6. Анти-брутфорс: 5 неудач → блок 429 =====
        print("6) Анти-брутфорс", flush=True)
        locked = False
        for i in range(7):
            r = page.request.post(f"{BASE}/api/auth/login",
                                  data={"username": "brute_test", "password": f"wrong{i}"})
            if r.status == 429:
                locked = True
                break
        check("после 5 неверных паролей логин блокируется (429)", locked,
              f"статус на попытке {i + 1}: {r.status}")

        # ===== 7. Множественные диалоги =====
        # (victor уже залогинен после сценария 5)
        print("7) Диалоги: создание, история, переключение", flush=True)
        page.wait_for_selector("#historyList .chat-open", timeout=8000)
        check("после входа диалог создан и виден в сайдбаре", True,
              page.inner_text("#historyList"))

        page.fill("#input", "Первый диалог: SaaS с подпиской")
        page.press("#input", "Enter")
        page.wait_for_selector(".streamText", timeout=90000)
        page.wait_for_function("document.querySelectorAll('.streamText').length >= 1 && document.querySelector('.streamText').textContent.length > 20", timeout=90000)
        page.wait_for_timeout(1500)  # тайтл обновится после ответа
        page.reload(wait_until="networkidle")
        page.wait_for_selector("#historyList .chat-open", timeout=8000)
        title1 = page.inner_text("#historyList .chat-open >> nth=0")
        check("тайтл первого диалога из первого сообщения", "SaaS" in title1, title1)

        page.click("text=Новый чат")
        page.wait_for_selector("#emptyState:not(.hidden)", timeout=5000)
        check("новый чат: пустое состояние показано", True)

        page.fill("#input", "Второй диалог: кофейня у метро")
        page.press("#input", "Enter")
        page.wait_for_selector(".streamText", timeout=90000)
        page.wait_for_timeout(1500)
        page.reload(wait_until="networkidle")
        page.wait_for_selector("#historyList .chat-open", timeout=8000)
        n = page.locator("#historyList .chat-open").count()
        check("в сайдбаре два диалога", n == 2, f"buttons={n}")

        page.locator("#historyList .chat-open").nth(1).click()  # первый (старый) диалог
        page.wait_for_timeout(800)
        body = page.inner_text("#chatContainer")
        check("переключение: в старом диалоге его сообщения", "SaaS" in body and "кофейня" not in body,
              body[:80].replace("\n", " "))
        page.locator("#historyList .chat-open").nth(0).click()  # второй (свежий)
        page.wait_for_timeout(800)
        body = page.inner_text("#chatContainer")
        check("переключение: в новом диалоге его сообщения", "кофейня" in body and "SaaS" not in body,
              body[:80].replace("\n", " "))

        # ===== 8. Меню «⋯»: переименовать / закрепить / удалить =====
        print("8) Меню диалога: rename / pin / delete", flush=True)
        page.on("dialog", lambda d: d.accept("Тестовый диалог"))

        page.locator("#historyList .chat-open").nth(0).hover()
        page.locator("#historyList .chat-menubtn").nth(0).click()
        page.wait_for_selector(".chat-menu:not(.hidden)", timeout=3000)
        check("меню «⋯» открывается", True)

        page.click(".chat-menu:not(.hidden) [data-act='rename']")
        page.wait_for_timeout(1000)
        t = page.inner_text("#historyList")
        check("переименование сохранилось", "Тестовый диалог" in t, t[:60].replace("\n", " "))

        page.locator("#historyList .chat-menubtn").nth(0).click()
        page.click(".chat-menu:not(.hidden) [data-act='pin']")
        page.wait_for_timeout(1000)
        first_title = page.locator("#historyList .chat-open").nth(0).inner_text()
        first_has_pin = page.locator("#historyList .chat-open svg").count() > 0
        check("закреплённый поднялся наверх с иконкой", "Тестовый" in first_title and first_has_pin,
              first_title)

        page.locator("#historyList .chat-menubtn").nth(0).click()
        page.click(".chat-menu:not(.hidden) [data-act='delete']")
        page.wait_for_timeout(1200)
        t = page.inner_text("#historyList")
        check("диалог удалён", "Тестовый диалог" not in t, t[:60].replace("\n", " "))

        # ===== 9. Выбор модели =====
        print("9) Выбор модели (Kimi K3 / MiniMax M3)", flush=True)
        opts = page.locator("#modelSelect option").all_inner_texts()
        check("в селекторе две модели", len(opts) == 2, str(opts))
        mm = [o for o in opts if "MiniMax" in o][0]
        page.select_option("#modelSelect", label=mm)
        page.wait_for_timeout(500)
        page.fill("#input", "Проверка переключения на MiniMax")
        page.press("#input", "Enter")
        page.wait_for_selector(".streamText", timeout=90000)
        page.wait_for_timeout(800)
        page.reload(wait_until="networkidle")
        page.wait_for_selector("#historyList .chat-open", timeout=8000)
        page.wait_for_timeout(1200)
        val = page.eval_on_selector("#modelSelect", "el => el.value")
        check("модель диалога запомнилась после перезагрузки", "MiniMax" in val, val)

        # ===== 10. Мобильная вёрстка: сайдбар-шторка =====
        print("10) Мобильная вёрстка: сайдбар-шторка", flush=True)
        mctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
        mp = mctx.new_page()
        mp.set_default_timeout(15000)
        login(mp, "vlad", E2E_PASSWORD)
        mp.wait_for_selector("#loginOverlay", state="hidden")
        mp.wait_for_selector("#historyList .chat-open", timeout=8000)
        hidden_cls = mp.eval_on_selector("#sidebar", "el => el.className")
        check("на мобиле сайдбар скрыт за экраном", "-translate-x-full" in hidden_cls)

        mp.click("button[aria-label='Меню']")
        mp.wait_for_timeout(400)  # анимация выезда
        shown = mp.eval_on_selector("#sidebar", "el => !el.classList.contains('-translate-x-full')")
        backdrop = mp.eval_on_selector("#sbBackdrop", "el => !el.classList.contains('hidden')")
        check("гамбургер выезжает шторку с подложкой", shown and backdrop)

        mp.locator("#historyList .chat-open").nth(0).click()
        mp.wait_for_timeout(600)
        closed_cls = mp.eval_on_selector("#sidebar", "el => el.className")
        check("выбор диалога закрывает шторку", "-translate-x-full" in closed_cls)
        mctx.close()

        browser.close()

    os.unlink(doc.name)
    failed = [r for r in results if not r[1]]
    print(f"\nИтог: {len(results) - len(failed)}/{len(results)} PASS", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
