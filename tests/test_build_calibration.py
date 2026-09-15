"""Test ringkasan feed live di build_calibration.py (offline, tanpa backtest).

Jalankan:  python -m unittest tests.test_build_calibration -v
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import build_calibration as bc  # noqa: E402

BACKTEST = {
    "generated_at": "2026-09-07T06:36:01+00:00",
    "pairs": {
        "PPI|BTC/USD": {"horizons": {"1h": {"alignment_rate": 50.0, "n_directional": 80}}},
        "NFP|BTC/USD": {"horizons": {"1h": {"alignment_rate": 46.0, "n_directional": 90}}},
    },
}


def ev(ind, verdict, btc_1h):
    return {"indicator": ind, "verdict": verdict, "moves": {"BTC/USD": {"15m": 0.1, "1h": btc_1h, "4h": None}}}


class LiveSummaryTest(unittest.TestCase):
    def test_aturan_hit_neutral_dan_null_di_luar_denominator(self):
        events = [
            ev("NFP", "BEARISH", -2.49),   # hit
            ev("PPI", "BEARISH", -1.11),   # hit
            ev("PPI", "BULLISH", -0.5),    # miss
            ev("PPI", "BEARISH", 0.0),     # tepat 0 = miss
            ev("PPI", "NEUTRAL", 3.0),     # tidak dihitung
            ev("CPI", "BEARISH", None),    # unmeasured
        ]
        s = bc.live_summary(events, BACKTEST)
        self.assertEqual(s["source"], "live")
        ppi = s["by_indicator"]["PPI"]
        self.assertEqual((ppi["n_events"], ppi["neutral"], ppi["unmeasured"], ppi["n"], ppi["hits"]), (4, 1, 0, 3, 1))
        self.assertEqual(ppi["hit_rate"], 33.3)
        self.assertEqual(ppi["backtest_alignment_rate"], 50.0)
        self.assertEqual(ppi["delta_pp"], -16.7)
        cpi = s["by_indicator"]["CPI"]
        self.assertEqual((cpi["n"], cpi["unmeasured"]), (0, 1))
        self.assertIsNone(cpi["hit_rate"], "n=0 -> null, bukan 0")
        self.assertIsNone(cpi["delta_pp"])
        self.assertIsNone(cpi["backtest_alignment_rate"], "tidak ada di backtest -> null")
        t = s["total"]
        self.assertEqual((t["n_events"], t["neutral"], t["unmeasured"], t["n"], t["hits"], t["hit_rate"]), (6, 1, 1, 4, 2, 50.0))

    def test_tanpa_backtest_tetap_jalan(self):
        s = bc.live_summary([ev("NFP", "BEARISH", -1.0)], None)
        self.assertEqual(s["by_indicator"]["NFP"]["hit_rate"], 100.0)
        self.assertIsNone(s["by_indicator"]["NFP"]["backtest_alignment_rate"])

    def test_kosong(self):
        s = bc.live_summary([], BACKTEST)
        self.assertEqual(s["total"]["n_events"], 0)
        self.assertIsNone(s["total"]["hit_rate"])
        self.assertEqual(bc.render_live(s), ["LIVE: belum ada event live"])


class WriteLiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_berkas_live_hilang_tetap_menulis_dan_mencetak_belum_ada(self):
        cal = self.dir / "calibration.json"
        cal.write_text(json.dumps(BACKTEST))
        before = cal.read_bytes()
        out = self.dir / "calibration_live.json"
        buf = io.StringIO()
        with redirect_stdout(buf):
            summary = bc.write_live(self.dir / "tidak_ada.jsonl", cal, out)
        self.assertIn("belum ada event live", buf.getvalue())
        self.assertEqual(summary["total"]["n_events"], 0)
        written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(written["source"], "live")
        self.assertIn("generated_at", written)
        self.assertEqual(cal.read_bytes(), before, "calibration.json (backtest) tidak disentuh")

    def test_baris_rusak_dilewati_dan_dihitung(self):
        events = self.dir / "live_events.jsonl"
        events.write_text("{rusak\n" + json.dumps(ev("NFP", "BEARISH", -2.0)) + "\n\n", encoding="utf-8")
        out = self.dir / "calibration_live.json"
        with redirect_stdout(io.StringIO()):
            summary = bc.write_live(events, self.dir / "tidak_ada_cal.json", out)
        self.assertEqual(summary["skipped_malformed_lines"], 1)
        self.assertEqual(summary["total"]["hits"], 1)

    def test_live_only_tidak_menjalankan_backtest(self):
        with mock.patch.object(bc, "build_backtest", side_effect=AssertionError("backtest dijalankan")), \
                mock.patch.object(bc, "write_live") as wl:
            bc.main(["--live-only"])
        wl.assert_called_once()


if __name__ == "__main__":
    unittest.main()
