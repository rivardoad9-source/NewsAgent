"""
FRED release-calendar watcher — kirim via @HermsNews_Bot, untuk Hermes cron no_agent.

Diam (stdout kosong, exit 0) selama tidak ada jadwal rilis baru yang dikonfirmasi
FRED. Begitu FRED mengunci tanggal yang belum pernah diberitakan (state watermark
di data/calendar_watch_state.json), kirim pesan Telegram VIA @HermsNews_Bot.

PENTING (dotenv trap): token dibaca LANGSUNG dari file .env NewsAgent, BUKAN via
load_dotenv() — load_dotenv tidak menimpa variabel yang sudah ada di environment
(bot MainHermes), sehingga bisa salah kirim ke bot utama. Parse manual = selalu
dapat token milik NewsAgent.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

REPO = "/home/ubuntu/NewsAgent"
sys.path.insert(0, REPO)

from src import calendar  # noqa: E402

STATE_FILE = os.path.join(REPO, "data", "calendar_watch_state.json")
WIB = ZoneInfo("Asia/Jakarta")
HORIZON_DAYS = 120
CHAT_ID = "6678941282"  # fallback; utama dari .env


def read_env(key: str) -> str:
    """Baca satu key LANGSUNG dari file .env (anti dotenv trap)."""
    try:
        with open(os.path.join(REPO, ".env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


def telegram_send(token: str, chat_id: str, text: str) -> bool:
    """Kirim via Telegram Bot API. Return True kalau terkirim."""
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r).get("ok", False)
    except Exception:
        return False


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"notified": []}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def main() -> int:
    now = datetime.now(timezone.utc)
    upcoming = calendar.upcoming(horizon_days=HORIZON_DAYS, now=now)
    if not upcoming:
        return 0  # FRED belum mengunci apa pun — diam.

    state = load_state()
    notified = {(item["indicator"], item["date"]) for item in state.get("notified", [])}

    fresh = [r for r in upcoming if (r.indicator, r.release_date.isoformat()) not in notified]
    if not fresh:
        return 0  # semua sudah pernah diberitakan — diam.

    fresh.sort(key=lambda r: r.release_utc)
    lines = ["📅 FRED ngunci jadwal rilis baru (via HermesNews):"]
    for r in fresh:
        wib = r.release_utc.astimezone(WIB)
        lines.append(
            f"• {r.indicator}: {wib.strftime('%a, %d %b %Y %H:%M WIB')}"
        )
    msg = "\n".join(lines)

    token = read_env("TELEGRAM_BOT_TOKEN")
    chat_id = read_env("TELEGRAM_CHAT_ID") or CHAT_ID
    if not token:
        # Bot token tidak terbaca — fallback ke stdout supaya cron no_agent
        # tetap mengangkat pesan (lebih baik sampai daripada hilang).
        print(msg)
        return 0

    if not telegram_send(token, chat_id, msg):
        # Gagal kirim: keluar non-zero supaya cron naikkan error alert.
        print(msg, file=sys.stderr)
        return 1

    for r in fresh:
        notified.add((r.indicator, r.release_date.isoformat()))
    state["notified"] = [{"indicator": ind, "date": d} for ind, d in sorted(notified)]
    save_state(state)
    return 0  # stdout kosong = cron diam, tidak dobel kirim.


if __name__ == "__main__":
    sys.exit(main())
