"""Section dashboard Live vs Backtest: data murni, tanpa Streamlit, tanpa jaringan.

Jalankan:  python -m unittest tests.test_live_calibration_view -v
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import live_calibration_view as view  # noqa: E402

SUMMARY = {
    "generated_at": "2026-09-15T14:00:00+00:00",
    "source": "live",
    "rule": "verdict vs tanda gerak BTC/USD 1h",
    "by_indicator": {
        "NFP": {"n_events": 1, "neutral": 0, "unmeasured": 0, "n": 1, "hits": 1, "hit_rate": 100.0,
                "backtest_alignment_rate": 47.0, "delta_pp": 53.0},
        "CPI": {"n_events": 1, "neutral": 0, "unmeasured": 1, "n": 0, "hits": 0, "hit_rate": None,
                "backtest_alignment_rate": None, "delta_pp": None},
    },
    "total": {"n_events": 2, "neutral": 0, "unmeasured": 1, "n": 1, "hits": 1, "hit_rate": 100.0},
    "revisions": {"by_indicator": {}, "total": {"n_events": 1, "n": 0, "hits": 0, "hit_rate": None}},
}


class LiveCalibrationViewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_absent_is_an_empty_state_not_an_error_and_not_zero(self):
        data, reason = view.load_live_calibration(self.dir / "calibration_live.json")
        self.assertIsNone(data)
        v = view.live_calibration_view(data, reason)
        self.assertEqual(v["status"], "empty")
        self.assertEqual(v["message"], "belum ada sampel live")
        self.assertEqual(v["rows"], [])

    def test_corrupt_file_is_empty_state(self):
        p = self.dir / "calibration_live.json"
        p.write_text("{rusak", encoding="utf-8")
        v = view.live_calibration_view(*view.load_live_calibration(p))
        self.assertEqual(v["status"], "empty")
        self.assertIn("belum ada sampel live", v["message"])

    def test_file_present_renders_rows_with_small_sample_warning(self):
        p = self.dir / "calibration_live.json"
        p.write_text(json.dumps(SUMMARY), encoding="utf-8")
        v = view.live_calibration_view(*view.load_live_calibration(p))
        self.assertEqual(v["status"], "ok")
        self.assertEqual([r["IND"] for r in v["rows"]], ["NFP", "CPI", "TOTAL"])
        self.assertIn("pembanding, BUKAN kalibrasi", v["warning"])
        self.assertIn("n=1", v["warning"])
        cpi = v["rows"][1]
        self.assertEqual((cpi["LIVE %"], cpi["BACKTEST %"], cpi["DELTA pp"]), ("—", "—", "—"), "null tampil —, bukan 0")
        self.assertIn("Revisi", v["revision_line"])

    def test_no_writes_and_no_decision_path(self):
        src = (ROOT / "src" / "live_calibration_view.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"write_text|open\([^)]*['\"]w|json\.dump\(", src))
        self.assertNotIn("from src.action", src)
        dash = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn("live_calibration_view(*load_live_calibration())", dash)
        self.assertIn('section("F3b"', dash)


if __name__ == "__main__":
    unittest.main()
