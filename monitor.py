"""
Моніторинг Open Interest ("Позиції") для CXMTUSDT на LBank Futures.
Замість прихованого API скрипт відкриває сторінку в headless-браузері
(Playwright), чекає завантаження даних і зчитує значення "Позиції"
прямо з відрендереної сторінки — так само, як його бачить людина.

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

OI_PATTERN = re.compile(r"Позиц[іi]ї\s*[\r\n]*\s*([\d][\d.,]*)\s*\(CXMT\)", re.IGNORECASE)


def fetch_open_interest() -> float:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ))
        # networkidle тут ніколи не настає (сторінка постійно оновлює котирування),
        # тому чекаємо лише завантаження DOM і далі опитуємо текст самі.
        page.goto(URL, timeout=60000, wait_until="domcontentloaded")

        value = None
        for _ in range(30):
            text = page.inner_text("body")
            match = OI_PATTERN.search(text)
            if match:
                raw = match.group(1).replace(",", "")
                if raw not in ("--", "-", ""):
                    value = float(raw)
                    break
            page.wait_for_timeout(1000)

        browser.close()

        if value is None:
            raise RuntimeError(
                "Не вдалося зчитати значення 'Позиції' зі сторінки "
                "(можливо, змінилась розмітка сайту або значення не встигло завантажитись)"
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

    if prev_oi is not None and current_oi < prev_oi:
        diff = prev_oi - current_oi
        pct = (diff / prev_oi) * 100 if prev_oi else 0
        message = (
            f"📉 CXMTUSDT: Open Interest (Позиції) зменшився\n"
            f"Було: {prev_oi}\n"
            f"Стало: {current_oi}\n"
            f"Зміна: -{diff:.4f} (-{pct:.2f}%)"
        )
        send_telegram_message(message)
        print("Надіслано сповіщення в Telegram.")

    save_state({"open_interest": current_oi, "updated_at": time.time()})


if __name__ == "__main__":
    main()
