"""
Membangun data/calibration.json dari backtest intraday nyata.

Jalankan:  python scripts/build_calibration.py

Berkas hasilnya dipakai src/action.py untuk melampirkan angka terukur ke setiap
sinyal - expected move per timeframe, alignment rate, dan ukuran sampel. Dengan
begitu action layer tidak pernah mengarang statistik, dan tidak perlu memanggil
API saat menghasilkan sinyal.

Jalankan ulang bila: rentang tanggal diperluas, indikator baru ditambahkan, atau
sumber consensus akhirnya tersedia (yang akan mengubah seluruh angka).

FEED LIVE (sumber `live`)
    python scripts/build_calibration.py --live-only

Statistik dari data/live_events.jsonl ditulis ke berkas TERPISAH,
data/calibration_live.json (dengan `generated_at` + `source: "live"`), dan
dibandingkan dengan angka backtest (`source: "backtest"`) yang dibaca dari
data/calibration.json. Alasan berkas terpisah:
- calibration.json dibaca src/action.py (`pairs`); menambah/menimpa di sana berisiko
  mengubah perilaku sinyal lewat jalur yang tidak diuji, dan angka backtest tidak
  boleh tertimpa.
- `--live-only` tidak menjalankan backtest (jaringan, menit-an); sampel live masih
  sangat kecil, jadi yang ditambahkan ke rencana hanyalah pembanding, bukan kalibrasi.
Tanpa `--live-only`, backtest dibangun ulang seperti sebelumnya lalu ringkasan live
ikut ditulis.

Aturan hit-rate (mengikuti deskripsi na_scorecard.py di work order; berkas itu ada
di server Hermes dan TIDAK terbaca dari repo ini): arah verdict vs tanda gerak
BTC/USD horizon 1h. BULLISH kena bila gerak > 0, BEARISH kena bila gerak < 0.
NEUTRAL tidak masuk denominator (tidak pernah ambil sisi). Gerak 1h null tidak masuk
denominator dan dihitung `unmeasured`. Gerak tepat 0 = MISS: verdict mengambil sisi
dan pasar tidak mengonfirmasinya.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

INDICATORS = ["CPI", "PPI", "NFP", "UNEMPLOYMENT"]
ASSETS = ["BTC/USD", "ETH/USD"]
START, END = "2019-01-01", "2026-09-07"

CALIBRATION_PATH = ROOT / "data" / "calibration.json"
LIVE_EVENTS_PATH = ROOT / "data" / "live_events.jsonl"
LIVE_OUTPUT_PATH = ROOT / "data" / "calibration_live.json"
LIVE_ASSET = "BTC/USD"
LIVE_HORIZON = "1h"

# Ambang signifikansi dua sisi 95%.
Z_CRITICAL = 1.96


def read_events(path: Path) -> tuple[list[dict], int]:
    """Baris valid + jumlah baris rusak yang dilewati. Berkas hilang = ([], 0)."""
    if not path.exists():
        return [], 0
    events, bad = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(row, dict):
            events.append(row)
        else:
            bad += 1
    return events, bad


def _bucket() -> dict:
    return {"n_events": 0, "neutral": 0, "unmeasured": 0, "n": 0, "hits": 0}


def _finish(b: dict) -> dict:
    b["hit_rate"] = round(100.0 * b["hits"] / b["n"], 1) if b["n"] else None
    return b


def live_summary(events: Iterable[dict], backtest: Optional[dict] = None) -> dict:
    """Hit-rate verdict live per indikator + total, dengan kolom backtest pembanding.

    Fungsi murni: tanpa I/O. `backtest` = isi calibration.json (boleh None/kosong).
    """
    pairs = (backtest or {}).get("pairs", {})
    by_ind: dict[str, dict] = {}
    total = _bucket()
    for ev in events:
        ind = str(ev.get("indicator") or "?").upper()
        b = by_ind.setdefault(ind, _bucket())
        for acc in (b, total):
            acc["n_events"] += 1
        verdict = str(ev.get("verdict") or "").upper()
        if verdict not in ("BULLISH", "BEARISH"):
            for acc in (b, total):
                acc["neutral"] += 1
            continue
        move = ((ev.get("moves") or {}).get(LIVE_ASSET) or {}).get(LIVE_HORIZON)
        if not isinstance(move, (int, float)) or isinstance(move, bool):
            for acc in (b, total):
                acc["unmeasured"] += 1
            continue
        hit = move > 0 if verdict == "BULLISH" else move < 0
        for acc in (b, total):
            acc["n"] += 1
            acc["hits"] += int(hit)

    for ind, b in by_ind.items():
        _finish(b)
        bt = pairs.get(f"{ind}|{LIVE_ASSET}", {}).get("horizons", {}).get(LIVE_HORIZON, {})
        rate = bt.get("alignment_rate")
        b["backtest_alignment_rate"] = rate
        b["backtest_n_directional"] = bt.get("n_directional")
        b["delta_pp"] = round(b["hit_rate"] - rate, 1) if b["hit_rate"] is not None and rate is not None else None
    _finish(total)
    return {
        "source": "live",
        "compared_with": "backtest",
        "backtest_generated_at": (backtest or {}).get("generated_at"),
        "rule": (
            f"verdict vs tanda gerak {LIVE_ASSET} {LIVE_HORIZON}; NEUTRAL & gerak null di luar "
            "denominator; gerak tepat 0 = miss"
        ),
        "by_indicator": dict(sorted(by_ind.items())),
        "total": total,
    }


def render_live(summary: dict) -> list[str]:
    if summary["total"]["n_events"] == 0:
        return ["LIVE: belum ada event live"]
    fmt = lambda v, s="": "—" if v is None else f"{v}{s}"  # noqa: E731
    out = [f"LIVE vs BACKTEST ({summary['rule']})",
           f"  {'IND':13} {'event':>5} {'neutral':>7} {'unmeas':>6} {'n':>3} {'hit':>3} "
           f"{'live%':>6} {'backtest%':>9} {'delta pp':>8}"]
    rows = list(summary["by_indicator"].items()) + [("TOTAL", summary["total"])]
    for ind, b in rows:
        out.append(
            f"  {ind:13} {b['n_events']:>5} {b['neutral']:>7} {b['unmeasured']:>6} {b['n']:>3} {b['hits']:>3} "
            f"{fmt(b['hit_rate']):>6} {fmt(b.get('backtest_alignment_rate')):>9} {fmt(b.get('delta_pp')):>8}"
        )
    out.append("  Sampel live sangat kecil: angka ini pembanding, BUKAN kalibrasi.")
    return out


def write_live(events_path: Path = LIVE_EVENTS_PATH, calibration_path: Path = CALIBRATION_PATH,
               output_path: Path = LIVE_OUTPUT_PATH) -> dict:
    events, bad = read_events(events_path)
    backtest = None
    if calibration_path.exists():
        try:
            backtest = json.loads(calibration_path.read_text(encoding="utf-8"))
        except ValueError:
            backtest = None
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **live_summary(events, backtest),
        "events_file": str(events_path.relative_to(ROOT)) if events_path.is_relative_to(ROOT) else str(events_path),
        "skipped_malformed_lines": bad,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    for line in render_live(summary):
        print(line)
    print(f"Tersimpan: {output_path}")
    return summary


def build_backtest() -> None:
    from src.backtest import run_intraday_backtest, summarize_intraday  # jaringan; hanya mode penuh

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "period": f"{START} s/d {END}",
        "methodology": {
            "surprise_baseline": "naive random walk (nilai rilis sebelumnya)",
            "surprise_baseline_warning": (
                "BUKAN consensus pasar. Consensus tidak tersedia di sumber mana pun "
                "yang dipakai proyek ini, sehingga edge yang diukur di sini mungkin "
                "berbeda jauh dari edge terhadap ekspektasi pasar sesungguhnya."
            ),
            "price_source": "Binance klines 15m (data-api.binance.vision)",
            "price_basis": "close candle 15m tepat sebelum rilis",
            "release_times": "08:30 ET (CPI/PPI/NFP/Unemployment), 14:00 ET (FOMC)",
        },
        "pairs": {},
    }

    for asset in ASSETS:
        for indicator in INDICATORS:
            print(f"  {indicator:14} {asset:9} ...", end=" ", flush=True)
            result = run_intraday_backtest(indicator, asset, START, END)
            if not result.ok:
                print(f"GAGAL ({result.status})")
                continue

            summary = summarize_intraday(result)
            entry = {"n_releases": summary["n_total"], "period": summary["period"],
                     "horizons": {}}

            for horizon, stats in summary["horizons"].items():
                n = stats["n_directional"]
                rate = stats["alignment_rate"]
                if not n or rate is None:
                    entry["horizons"][horizon] = {"status": "INSUFFICIENT_SAMPLE"}
                    continue

                se = math.sqrt(0.25 / n) * 100
                z = (rate - 50) / se
                entry["horizons"][horizon] = {
                    "alignment_rate": rate,
                    "n_directional": n,
                    "avg_abs_move_pct": stats["avg_abs_move"],
                    "std_error_pp": round(se, 2),
                    "z_score": round(z, 2),
                    # Status inilah yang dibaca action layer untuk memutuskan
                    # apakah arah boleh dipercaya sama sekali.
                    "edge_status": (
                        "EDGE_DETECTED" if abs(z) > Z_CRITICAL else "NO_EDGE_DETECTED"
                    ),
                }

            out["pairs"][f"{indicator}|{asset}"] = entry
            rates = [
                f"{h}={entry['horizons'][h].get('alignment_rate', '-')}%"
                for h in ("15m", "1h", "4h")
            ]
            print(f"n={summary['n_total']:<4} " + "  ".join(rates))

    target = ROOT / "data" / "calibration.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nTersimpan: {target}  ({len(out['pairs'])} pasangan)")


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="Bangun calibration.json (backtest) + calibration_live.json (live)")
    parser.add_argument("--live-only", action="store_true",
                        help="hanya ringkasan feed live; backtest tidak dijalankan, calibration.json tidak disentuh")
    args = parser.parse_args(argv)
    if not args.live_only:
        build_backtest()
    write_live()


if __name__ == "__main__":
    main()
