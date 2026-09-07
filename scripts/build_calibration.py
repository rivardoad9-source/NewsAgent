"""
Membangun data/calibration.json dari backtest intraday nyata.

Jalankan:  python scripts/build_calibration.py

Berkas hasilnya dipakai src/action.py untuk melampirkan angka terukur ke setiap
sinyal - expected move per timeframe, alignment rate, dan ukuran sampel. Dengan
begitu action layer tidak pernah mengarang statistik, dan tidak perlu memanggil
API saat menghasilkan sinyal.

Jalankan ulang bila: rentang tanggal diperluas, indikator baru ditambahkan, atau
sumber consensus akhirnya tersedia (yang akan mengubah seluruh angka).
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backtest import run_intraday_backtest, summarize_intraday  # noqa: E402

INDICATORS = ["CPI", "PPI", "NFP", "UNEMPLOYMENT"]
ASSETS = ["BTC/USD", "ETH/USD"]
START, END = "2019-01-01", "2026-09-07"

# Ambang signifikansi dua sisi 95%.
Z_CRITICAL = 1.96


def main() -> None:
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


if __name__ == "__main__":
    main()
