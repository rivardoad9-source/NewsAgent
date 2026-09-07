"""
Mencari TELEGRAM_CHAT_ID.

Jalankan:  python scripts/find_chat_id.py

Bot Telegram tidak bisa memulai percakapan lebih dulu. Chat baru muncul di
getUpdates SETELAH ada orang mengirim pesan ke bot, jadi urutannya wajib:

  1. Tempel token dari @BotFather ke TELEGRAM_BOT_TOKEN di .env
  2. Buka Telegram, kirim pesan apa pun ke bot itu (mis. /start)
  3. Jalankan skrip ini

Catatan bentuk chat_id:
  * chat pribadi  -> angka positif   (mis. 123456789)
  * grup / supergroup -> angka negatif (mis. -1001234567890)
  * channel       -> angka negatif juga

Kalau tujuannya grup: bot harus DIUNDANG ke grup itu dulu. Bila privacy mode
masih aktif (default dari BotFather), bot hanya melihat pesan yang berupa
perintah - kirim `/start@nama_bot` di grup, atau matikan privacy mode lewat
BotFather (/setprivacy -> Disable).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

API = "https://api.telegram.org"


def fail(message: str, code: int = 1) -> None:
    print(f"\n[GAGAL] {message}")
    sys.exit(code)


def main() -> None:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        fail(
            "TELEGRAM_BOT_TOKEN masih kosong di .env.\n"
            "        Ambil token dari @BotFather (bentuknya 123456789:AAE...),\n"
            "        tempel ke .env, lalu jalankan skrip ini lagi."
        )

    # 1. Pastikan tokennya sah dulu, supaya pesan error tidak menyesatkan.
    try:
        me = requests.get(f"{API}/bot{token}/getMe", timeout=15).json()
    except requests.RequestException as exc:
        fail(f"Tidak bisa menghubungi Telegram: {type(exc).__name__}: {exc}")

    if not me.get("ok"):
        fail(f"Token ditolak Telegram: {me.get('description')}")

    bot = me["result"]
    print(f"Bot   : @{bot.get('username')}  ({bot.get('first_name')})")

    # 2. Ambil update terbaru.
    try:
        updates = requests.get(
            f"{API}/bot{token}/getUpdates", params={"limit": 100}, timeout=20
        ).json()
    except requests.RequestException as exc:
        fail(f"getUpdates gagal: {type(exc).__name__}: {exc}")

    if not updates.get("ok"):
        fail(f"getUpdates ditolak: {updates.get('description')}")

    results = updates.get("result", [])
    if not results:
        print(
            "\n[BELUM ADA PESAN]\n"
            f"        Buka Telegram, cari @{bot.get('username')}, lalu kirim /start.\n"
            "        Bot tidak bisa memulai chat sendiri, jadi langkah ini wajib.\n"
            "        Setelah itu jalankan skrip ini lagi.\n\n"
            "        Kalau tujuannya GRUP: undang bot ke grup, lalu kirim\n"
            f"        /start@{bot.get('username')} di dalam grup tersebut."
        )
        sys.exit(2)

    # 3. Kumpulkan chat unik dari semua jenis update.
    chats = {}
    for item in results:
        payload = (
            item.get("message")
            or item.get("edited_message")
            or item.get("channel_post")
            or item.get("my_chat_member")
            or {}
        )
        chat = payload.get("chat")
        if chat and chat.get("id") is not None:
            chats[chat["id"]] = chat

    if not chats:
        fail("Ada update, tetapi tidak satu pun memuat objek chat.", 2)

    print(f"\nDitemukan {len(chats)} chat:\n")
    for chat_id, chat in chats.items():
        kind = chat.get("type", "?")
        name = (
            chat.get("title")
            or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
            or chat.get("username")
            or "(tanpa nama)"
        )
        handle = f" @{chat['username']}" if chat.get("username") else ""
        print(f"  chat_id : {chat_id}")
        print(f"  tipe    : {kind}")
        print(f"  nama    : {name}{handle}")
        print()

    if len(chats) == 1:
        only = next(iter(chats))
        print("Tempel baris ini ke .env:\n")
        print(f"  TELEGRAM_CHAT_ID={only}\n")
    else:
        print("Pilih salah satu id di atas, lalu isi TELEGRAM_CHAT_ID di .env.\n")


if __name__ == "__main__":
    main()
