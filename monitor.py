"""
Моніторинг Open Interest ("Позиції") для CXMTUSDT на LBank Futures.
Відкриває сторінку в headless-браузері (Playwright), чекає завантаження
даних і зчитує значення "Позиції"/"Positions" прямо з відрендереної
сторінки.

Надсилає повідомлення в Telegram, коли значення зменшується порівняно
з попередньою перевіркою. Стан зберігається у state.json.
"""

import json
import os
import re
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

URL = "https://www.lbank.com/uk/futures/CXMTUSDT"
STATE_FILE = "state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Пробуємо і українську, і англійську версію тексту (сайт може
# перенаправити на іншу мову залежно від геолокації/заголовків сервера).
# Значення може мати суфікс K/M/B (напр. "260.359K (CXMT)").
OI_PATTERNS = [
    re.compile(r"Позиц[іi]ї\s*[\r\n]*\s*([\d][\d.,]*)\s*([KMB]?)\s*\(CXMT\)", re.IGNORECASE),
    re.compile(r"Positions?\s*[\r\n]*\s*([\d][\d.,]*)\s*([KMB]?)\s*\(CXMT\)", re.IGNORECASE),
]

SUFFIX_MULTIPLIER = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def parse_value(raw_number: str, suffix: str) -> float:
    number = float(raw_number.replace(",", ""))
    return number * SUFFIX_MULTIPLIER.get(suffix.upper(), 1)


def fetch_open_interest() -> float:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8"},
        )
        page.goto(URL, timeout=60000, wait_until="domcontentloaded")

        value = None
        last_text = ""
        for _ in range(30):
            text = page.inner_text("body")
            last_text = text
            for pattern in OI_PATTERNS:
                match = pattern.search(text)
                if match:
                    raw = match.group(1)
                    if raw not in ("--", "-", ""):
                        value = parse_value(raw, match.group(2))
                        break
            if value is not None:
                break
            page.wait_for_timeout(1000)

        final_url = page.url
        browser.close()

        if value is None:
            snippet = last_text[:1500].replace("\n", " | ")
            raise RuntimeError(
                "Не вдалося зчитати значення 'Позиції' зі сторінки.\n"
                f"Фінальний URL після завантаження: {final_url}\n"
                f"Перші 1500 символів тексту сторінки: {snippet}"
            )
        return value


def send_telegram_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "Не задані TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (env variables / GitHub secrets)"
        )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def main() -> None:
    state = load_state()
    prev_oi = state.get("open_interest")

    try:
        current_oi = fetch_open_interest()
    except Exception as e:
        print(f"Помилка отримання Open Interest: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] CXMTUSDT Позиції = {current_oi}")

    # Мінімальне падіння (в абсолютних одиницях OI), при якому надсилаємо
    # сповіщення. Змінити поріг можна тут.
    MIN_DROP_THRESHOLD = 1000

    if prev_oi is not None and current_oi < prev_oi:
        diff = prev_oi - current_oi
        if diff >= MIN_DROP_THRESHOLD:
            pct = (diff / prev_oi) * 100 if prev_oi else 0
            message = (
                f"📉 CXMTUSDT: Open Interest (Позиції) зменшився\n"
                f"Було: {prev_oi}\n"
                f"Стало: {current_oi}\n"
                f"Зміна: -{diff:.4f} (-{pct:.2f}%)"
            )
            send_telegram_message(message)
            print(f"Падіння {diff:.4f} >= порогу {MIN_DROP_THRESHOLD}. Надіслано сповіщення в Telegram.")
        else:
            print(f"Падіння {diff:.4f} менше порогу {MIN_DROP_THRESHOLD}. Сповіщення не надіслано.")

    save_state({"open_interest": current_oi, "updated_at": time.time()})


if __name__ == "__main__":
    main()
