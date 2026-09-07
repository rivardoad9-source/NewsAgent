"""
Tick pipeline MacroAI - dipanggil Hermes cron tiap 15 menit (no_agent, 0 token).

APA YANG DILAKUKAN per indikator (CPI, PPI, NFP, UNEMPLOYMENT):
  1. Baca deret FRED (sudah ditransformasi ke satuan engine).
  2. Observasi baru? (tanggal observasi > state) -> RILIS BARU.
  3. Hitung delta terhadap baseline naive (rilis sebelumnya) - consensus pasar
     tidak tersedia di provider mana pun (lihat CLAUDE.md), baseline selalu
     dilabeli.
  4. Verdict dari Rule Engine (deterministik, tanpa LLM).
  5. Ukur reaksi pasar BTC/ETH (15m/1h/4h) dari MOMEN rilis sebenarnya
     (tanggal rilis FRED + jam konvensi 08:30 ET), bukan dari label bulan
     observasi yang bisa tertinggal ~6 minggu.
  6. Kirim hasil ke Telegram + simpan ke data/live_events.jsonl (feed belajar
     engine) + catat state (anti-duplikat).

KELUARAN untuk cron: DIAM saat tidak ada rilis baru / sukses. Keluar non-zero
hanya saat pengiriman gagal, supaya Hermes cron memunculkan error alert.

PENGGUNAAN:
  python scripts/tick.py                 # mode cron: proses yang due, diam
  python scripts/tick.py --dry-run       # render saja, kirim ke stdout
  python scripts/tick.py --force NFP     # paksa proses ulang (demo/backfill)
  python scripts/tick.py --seed          # catat baseline tanpa kirim apa pun
  python scripts/tick.py --check         # status: rilis terakhir + state
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from src import calendar  # noqa: E402
from src import telegram_bot  # noqa: E402
from src.action import get_stats  # noqa: E402
from src.providers import binance, fred  # noqa: E402
from src.rule_engine import INDICATOR_TABLE, VerdictResult, get_verdict  # noqa: E402

DATA_DIR = ROOT / "data"
STATE_FILE = DATA_DIR / "live_state.json"
EVENTS_FILE = DATA_DIR / "live_events.jsonl"

# Indikator yang dipantau otomatis. GDP TIDAK ada di sini: observasi FRED GDP
# memakai tanggal label kuartal yang TIDAK berubah saat revisi (advance/
# preliminary/final), jadi deteksi "tanggal observasi baru" tidak bisa
# membedakan rilis GDP baru. GDP tetap tampil di dashboard.
MONITORED = ["CPI", "PPI", "NFP", "UNEMPLOYMENT"]

ASSETS = ["BTC/USD", "ETH/USD"]

WIB = ZoneInfo("Asia/Jakarta")

# Edukasi: apa arti indikator ini (1-2 kalimat, bahasa awam).
WHAT_IS = {
    "CPI": (
        "CPI = inflasi konsumen AS (biaya hidup). Rilis bulanan paling ditunggu "
        "pasar karena menentukan arah suku bunga The Fed."
    ),
    "PPI": (
        "PPI = inflasi di tingkat produsen (harga pabrik). Biasanya bergerak "
        "mendahului CPI - kalau produsen naikkan harga, konsumen ikut merasakan "
        "beberapa bulan kemudian."
    ),
    "NFP": (
        "NFP = jumlah lapangan kerja baru AS di luar sektor pertanian. Indikator "
        "utama kesehatan pasar tenaga kerja dan bahan pertimbangan The Fed."
    ),
    "UNEMPLOYMENT": (
        "Unemployment Rate = tingkat pengangguran AS. Dibaca BERSAMA NFP: "
        "NFP kuat + unemployment turun = ekonomi panas."
    ),
}

# Edukasi: mekanisme arah (deterministik per bentuk mapping + arah delta).
def _mechanism(indicator: str, verdict_display: str, delta) -> str:
    spec = INDICATOR_TABLE.get(indicator)
    shape = spec.shape.value if spec else "?"
    if verdict_display in ("BULLISH", "BEARISH"):
        if shape == "MONOTONIC_INVERSE":
            # CPI/PPI/FOMC: angka panas = hawkish = risk-off.
            if delta is not None and delta < 0:
                return (
                    "Angka lebih rendah dari bulan lalu = inflasi mendingin. The Fed "
                    "lebih mungkin melonggar (dovish) -> DXY & yield turun, aset "
                    "berisiko (crypto/tech) cenderung diuntungkan."
                )
            return (
                "Angka lebih tinggi dari bulan lalu = inflasi memanas. The Fed "
                "lebih mungkin menahan/menaikkan suku bunga (hawkish) -> DXY & "
                "yield naik, tekanan ke aset berisiko."
            )
        # BANDED (NFP/unemployment/GDP): terlalu panas ATAU terlalu dingin sama-sama buruk.
        if delta is not None and delta > 0:
            return (
                "Data lebih kuat dari bulan lalu. Buat pasar tenaga kerja, ini "
                "bisa berarti repricing hawkish (The Fed takut ekonomi kepanasan)."
            )
        return (
            "Data lebih lemah dari bulan lalu. Bisa memicu kekhawatiran resesi "
            "(risk-off) atau justru ekspektasi pelonggaran - tergantung konteks."
        )
    return (
        "Delta masih di dalam zona netral (perubahan kecil vs bulan lalu). Pasar "
        "butuh kejutan lebih besar untuk bergerak terarah."
    )


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (ValueError, OSError):
            return {}
    return {}


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def append_event(record: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with EVENTS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def explain_text(indicator: str, verdict_display: str, delta, actual, previous) -> str:
    spec = INDICATOR_TABLE.get(indicator)
    parts = [WHAT_IS.get(indicator, "")]
    if spec and spec.note:
        parts.append(f"Konvensi: {spec.note}")
    parts.append(_mechanism(indicator, verdict_display, delta))
    parts.append(
        "Catatan jujur: backtest 2019-2026 belum menemukan keunggulan arah yang "
        "signifikan; yang terukur andal adalah BESAR pergerakan, bukan arahnya."
    )
    return "\n".join(p for p in parts if p)


def hist_context(indicator: str) -> str:
    """Konteks dari kalibrasi backtest: besar gerak historis, bukan klaim arah."""
    stats = get_stats(indicator, "BTC/USD", "15m")
    if not stats:
        return ""
    avg = stats.get("avg_abs_move_pct")
    align = stats.get("alignment_rate")
    n = stats.get("n_directional")
    edge = stats.get("edge_status", "UNCALIBRATED")
    bits = [f"Backtest {indicator} BTC/USD (n={n})"]
    if avg is not None:
        bits.append(f"|gerak| 15m rata-rata ±{avg:.2f}%")
    if align is not None:
        bits.append(f"alignment arah {align:.1f}%")
    bits.append(f"({edge})")
    return " · ".join(bits)


def _release_moment(indicator: str, obs_date_str: str) -> tuple[datetime, str, str]:
    """Momen rilis sebenarnya (UTC + WIB + tanggal iso) untuk mengukur reaksi pasar."""
    rd = calendar.last_release_date(indicator)
    if rd is None:
        # FRED belum konfirmasi -> pakai tanggal observasi (kurang presisi).
        rd = datetime.fromisoformat(obs_date_str).date()
    release_utc = calendar._to_utc(rd, indicator)
    return release_utc, release_utc.astimezone(WIB).strftime("%H:%M"), rd.isoformat()


def process_indicator(indicator: str, state: dict, dry_run: bool, force: bool = False) -> dict:
    """Proses satu indikator. Mengembalikan dict status aksi."""
    spec = INDICATOR_TABLE.get(indicator)
    label = spec.label if spec else indicator

    series = fred.get_indicator_series(indicator, limit=60)
    if not series.ok or len(series.points) < 2:
        return {"indicator": indicator, "action": "skip", "reason": f"FRED: {series.detail or series.status}"}

    latest_date, actual = series.points[-1]
    obs_str = str(latest_date)
    previous = series.points[-2][1]
    if previous is None or actual is None:
        return {"indicator": indicator, "action": "skip", "reason": "nilai aktual/previous None"}

    last_obs = state.get("last_obs", {}).get(indicator)
    if last_obs is None and not force:
        return {"indicator": indicator, "action": "seed", "reason": f"baseline {obs_str}"}
    if obs_str <= last_obs and not force:
        return {"indicator": indicator, "action": "idle", "reason": f"obs {obs_str} == {last_obs}"}

    delta = float(actual) - float(previous)
    verdict: VerdictResult = get_verdict(indicator, delta)
    display = verdict.display_bias if verdict.verdict is not None else "NO_VERDICT"

    release_utc, wib_hhmm, rel_iso = _release_moment(indicator, obs_str)

    moves = {}
    for asset in ASSETS:
        moves[asset] = binance.returns_after(asset, release_utc)

    explanation = explain_text(indicator, display, delta, actual, previous)
    context = hist_context(indicator)
    unit = spec.unit if spec else ""

    result = telegram_bot.send_news_result(
        event_label=label,
        release_date=rel_iso,
        release_wib=wib_hhmm,
        actual=float(actual),
        previous=float(previous),
        delta=delta,
        verdict_display=display,
        rationale=verdict.rationale or "",
        unit=unit,
        moves=moves,
        explain=explanation,
        hist_context=context,
        dry_run=dry_run,
    )

    if dry_run:
        print(result.message_preview if result.message_preview else "(tidak ada preview)")
        return {"indicator": indicator, "action": "dry-run", "ok": result.ok, "detail": result.detail}

    if result.ok:
        append_event(
            {
                "ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "indicator": indicator,
                "label": label,
                "obs_date": obs_str,
                "release_utc": release_utc.isoformat(timespec="minutes"),
                "actual": actual,
                "previous": previous,
                "naive_delta": round(delta, 4),
                "verdict": display,
                "rationale": verdict.rationale,
                "moves": moves,
                "baseline": "naive-previous",
            }
        )
        state.setdefault("last_obs", {})[indicator] = obs_str
        state.setdefault("last_release", {})[indicator] = release_utc.date().isoformat()
        save_state(state)

    return {"indicator": indicator, "action": "sent" if result.ok else "send-failed", "ok": result.ok, "detail": result.detail}


def main() -> int:
    args = sys.argv[1:]
    force = None
    dry_run = "--dry-run" in args
    seed = "--seed" in args
    check = "--check" in args
    if "--force" in args:
        force = args[args.index("--force") + 1].upper()

    state = load_state()

    if check:
        for row in calendar.last_releases():
            rd = row["release_date"]
            print(f"{row['indicator']:12} rilis terakhir: {rd.isoformat() if rd else 'n/a'}")
        print(f"state: {json.dumps(state.get('last_obs', {}))}")
        return 0

    if seed:
        for ind in MONITORED:
            series = fred.get_indicator_series(ind, limit=60)
            if series.ok and series.points:
                state.setdefault("last_obs", {})[ind] = str(series.points[-1][0])
        save_state(state)
        print("Baseline dicatat:", json.dumps(state.get("last_obs", {})))
        return 0

    indicators = [force] if force else MONITORED
    failures = 0
    for ind in indicators:
        out = process_indicator(ind, state, dry_run, force=bool(force))
        if out["action"] in ("sent", "send-failed"):
            if not out.get("ok"):
                failures += 1
                print(f"[tick] {ind}: GAGAL kirim — {out.get('detail')}", file=sys.stderr)
    # Idle/seed/skip: diam (cron no_agent: stdout kosong = senyap).
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
