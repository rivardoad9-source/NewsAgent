"""Monitor GDP yang sadar revisi (OFFLINE: FRED, Binance, Telegram di-mock).

Jalankan:  python -m unittest tests.test_gdp_revision -v
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import build_calibration as bc  # noqa: E402
from scripts import tick  # noqa: E402
from src import revision  # noqa: E402
from src.providers.fred import FredResponse  # noqa: E402

FIRST_PRINT_KEYS = ["ts_utc", "indicator", "label", "obs_date", "release_utc", "actual", "previous",
                    "naive_delta", "verdict", "rationale", "moves", "baseline"]
RELEASE = datetime(2026, 8, 26, 12, 30, tzinfo=timezone.utc)


class RevisionPureTest(unittest.TestCase):
    def test_classify(self):
        self.assertIsNone(revision.classify(3.0, [3.0]))
        self.assertIsNone(revision.classify(3.00001, [3.0]), "beda di bawah 4 desimal = angka sama")
        self.assertEqual(revision.classify(3.0, []), {"revision": False, "estimate_stage": "advance"})
        self.assertEqual(
            revision.classify(3.3, [3.0]),
            {"revision": True, "estimate_stage": "second", "prior_value": 3.0, "revision_delta": 0.3},
        )
        self.assertEqual(revision.classify(2.9, [3.0, 3.3])["estimate_stage"], "third")
        self.assertEqual(revision.classify(2.8, [3.0, 3.3, 2.9])["estimate_stage"], "revisi ke-1 setelah third")

    def test_sentence_is_explicit_and_null_reaction_is_named(self):
        info = revision.classify(3.3, [3.0])
        s = revision.revision_sentence("GDP", "2026-04-01", info, 3.3, {"BTC/USD": {"1h": None}})
        self.assertEqual(s, "GDP Q2 2026 second estimate: REVISI +0.30pp (3.00 -> 3.30), reaksi 1h BTC/USD belum terukur")
        s2 = revision.revision_sentence("GDP", "2026-04-01", info, 3.3, {"BTC/USD": {"1h": -0.42}})
        self.assertIn("reaksi 1h BTC/USD -0.42%", s2)


class TickRevisionAwareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.events = d / "live_events.jsonl"
        self.state_file = d / "live_state.json"
        self.sent: list[dict] = []
        self.value = 3.0

        def series(indicator, limit=60):
            return FredResponse(True, "OK", points=[("2026-01-01", 0.8), ("2026-04-01", self.value)])

        def send(**kw):
            self.sent.append(kw)
            return SimpleNamespace(ok=True, detail="ok", message_preview=f"PREVIEW {kw['event_label']}")

        self.patches = [
            mock.patch.object(tick, "EVENTS_FILE", self.events),
            mock.patch.object(tick, "STATE_FILE", self.state_file),
            mock.patch.object(tick, "DATA_DIR", d),
            mock.patch.object(tick.fred, "get_indicator_series", side_effect=series),
            mock.patch.object(tick.binance, "returns_after", return_value={"15m": 0.1, "1h": None, "4h": None}),
            mock.patch.object(tick.telegram_bot, "send_news_result", side_effect=send),
            mock.patch.object(tick, "hist_context", return_value=""),
            mock.patch.object(tick, "_scheduled_release_moment", return_value=(RELEASE, "19:30", "2026-08-26")),
        ]
        for p in self.patches:
            p.start()
        # Instalasi yang sudah berjalan: GDP punya last_obs kuartal sebelumnya.
        self.state = {"last_obs": {"GDP": "2026-01-01"}}

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def lines(self) -> list[dict]:
        if not self.events.exists():
            return []
        return [json.loads(x) for x in self.events.read_text(encoding="utf-8").splitlines() if x.strip()]

    def run_tick(self):
        return tick.process_revision_aware("GDP", self.state, dry_run=False)

    def test_i_same_obs_same_value_is_not_recorded_twice(self):
        self.assertEqual(self.run_tick()["action"], "sent")
        out = self.run_tick()
        self.assertEqual(out["action"], "idle")
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(len(self.sent), 1)

    def test_ii_same_obs_new_value_is_recorded_and_marked(self):
        self.run_tick()
        self.value = 3.3
        out = self.run_tick()
        self.assertEqual(out["action"], "sent")
        self.assertTrue(out["revision"])
        rows = self.lines()
        self.assertEqual(len(rows), 2)
        rev = rows[1]
        self.assertIs(rev["revision"], True)
        self.assertEqual(rev["prior_value"], 3.0)
        self.assertEqual(rev["revision_delta"], 0.3)
        self.assertEqual(rev["estimate_stage"], "second")
        self.assertIsNone(rev["moves"]["BTC/USD"]["1h"], "belum terjadi = null, bukan 0")
        self.assertIn("REVISI +0.30pp (3.00 -> 3.30)", self.sent[1]["explain"])
        self.assertIn("(REVISI second)", self.sent[1]["event_label"])

    def test_iii_three_estimates_three_entries_and_first_print_shape_is_unchanged(self):
        for v in (3.0, 3.3, 2.9):
            self.value = v
            self.run_tick()
        rows = self.lines()
        self.assertEqual(len(rows), 3)
        self.assertEqual(list(rows[0].keys()), FIRST_PRINT_KEYS, "first print: bentuk baris sama dengan rilis lain")
        self.assertEqual([r.get("revision", False) for r in rows], [False, True, True])
        self.assertEqual([r.get("estimate_stage") for r in rows], [None, "second", "third"])
        self.assertEqual(rows[2]["prior_value"], 3.3)
        self.assertEqual(rows[2]["revision_delta"], -0.4)
        # Nilai kembali ke estimasi lama yang sudah pernah tercatat: bukan informasi baru.
        self.value = 3.0
        self.assertEqual(self.run_tick()["action"], "idle")

    def test_fresh_install_seeds_then_a_revision_uses_the_seed_as_prior(self):
        self.state = {}
        self.assertEqual(self.run_tick()["action"], "seed")
        self.assertEqual(self.lines(), [])
        self.assertEqual(self.run_tick()["action"], "idle")
        self.value = 3.1
        out = self.run_tick()
        self.assertTrue(out["revision"])
        self.assertEqual(self.lines()[0]["prior_value"], 3.0)

    def test_dry_run_prints_the_revision_sentence_and_writes_nothing(self):
        self.run_tick()
        self.value = 3.3
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = tick.process_revision_aware("GDP", self.state, dry_run=True)
        self.assertEqual(out["action"], "dry-run")
        self.assertIn("GDP Q2 2026 second estimate: REVISI +0.30pp", buf.getvalue())
        self.assertEqual(len(self.lines()), 1)

    def test_cpi_path_is_untouched(self):
        self.assertNotIn("GDP", tick.MONITORED)
        self.assertEqual(tick.REVISION_AWARE, ["GDP"])


class CalibrationSplitsRevisionsTest(unittest.TestCase):
    def test_revisions_do_not_enter_first_print_hit_rate(self):
        base = {"indicator": "GDP", "verdict": "BEARISH", "moves": {"BTC/USD": {"1h": -1.0}}}
        events = [dict(base), dict(base, revision=True, moves={"BTC/USD": {"1h": 2.0}})]
        s = bc.live_summary(events, None)
        self.assertEqual((s["total"]["n_events"], s["total"]["hits"]), (1, 1))
        self.assertEqual(s["revisions"]["total"]["n_events"], 1)
        self.assertEqual(s["revisions"]["total"]["hits"], 0)
        self.assertTrue(any("REVISI" in line for line in bc.render_live(s)))

    def test_calibration_json_is_not_written_by_the_live_path(self):
        with tempfile.TemporaryDirectory() as t:
            cal = Path(t) / "calibration.json"
            cal.write_text('{"pairs": {}}', encoding="utf-8")
            before = cal.read_bytes()
            ev = Path(t) / "e.jsonl"
            ev.write_text(json.dumps({"indicator": "GDP", "verdict": "BULLISH", "revision": True,
                                      "moves": {"BTC/USD": {"1h": 1.0}}}) + "\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                bc.write_live(ev, cal, Path(t) / "calibration_live.json")
            self.assertEqual(cal.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
