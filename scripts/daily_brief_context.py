"""
Daily-brief context collector — untuk Hermes cron agent (data 0-token, LLM hanya
untuk judgment/rekap). Output = konteks terstruktur yang di-inject ke prompt agent.

Cetak: hari ini, rilis yang sudah dikonfirmasi FRED (7 hari ke depan, WIB), rilis
terakhir yang sudah diproses, dan event live terbaru.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

REPO = "/home/ubuntu/NewsAgent"
sys.path.insert(0, REPO)

from src import calendar  # noqa: E402

WIB = ZoneInfo("Asia/Jakarta")


def main() -> int:
    now = datetime.now(timezone.utc)
    now_wib = now.astimezone(WIB)

    print(f"HARI INI: {now_wib.strftime('%A, %d %B %Y %H:%M WIB')}")
    print(f"KONTEKS: sekarang {now_wib.strftime('%H:%M')} WIB = {now.strftime('%H:%M')} UTC.")
    print("RILIS AS 08:30 ET = 19:30 WIB (EDT) / 20:30 WIB (EST).")

    # Rilis terkonfirmasi 7 hari ke depan
    upcoming = calendar.upcoming(horizon_days=7, now=now)
    if not upcoming:
        print("\nUPCOMING_7D: KOSONG")
        print("Catatan: FRED hanya mengunci ~2-4 minggu sebelumnya; kosong = normal,"
              " bukan berarti tidak ada rilis. Pola reguler: NFP = Jumat pertama,"
              " CPI = sekitar tgl 10-13, PPI = sehari setelah CPI, GDP = akhir bulan.")
    else:
        print("\nUPCOMING_7D:")
        for r in sorted(upcoming, key=lambda r: r.release_utc):
            wib = r.release_utc.astimezone(WIB)
            days = (r.release_date - now.date()).days
            label = "HARI INI" if days == 0 else ("BESOK (H-1)" if days == 1 else f"H-{days}")
            print(f"  [{label}] {r.indicator}: {wib.strftime('%a %d %b %Y %H:%M WIB')}")

    # Rilis terakhir yang sudah diproses agent
    state_file = os.path.join(REPO, "data", "live_state.json")
    try:
        with open(state_file) as f:
            state = json.load(f)
        lr = state.get("last_release", {})
        if lr:
            print("\nRILIS_TERAKHIR_DIPROSES:")
            for ind, d in sorted(lr.items()):
                print(f"  {ind}: {d}")
        else:
            print("\nRILIS_TERAKHIR_DIPROSES: belum ada")
    except FileNotFoundError:
        print("\nRILIS_TERAKHIR_DIPROSES: state belum ada (seed belum jalan?)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
