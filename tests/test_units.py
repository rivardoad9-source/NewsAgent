"""Test unit ringan MacroAI (offline — tanpa jaringan).

Jalankan:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import calendar  # noqa: E402
from src import telegram_bot  # noqa: E402


class CalendarTest(unittest.TestCase):
    def test_et_to_utc_dst_musim_panas(self):
        # Agustus = EDT (UTC-4): 08:30 ET = 12:30 UTC.
        utc = calendar._to_utc(date(2026, 8, 12), "CPI")
        self.assertEqual(utc, datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc))

    def test_et_to_utc_musim_dingin(self):
        # Januari = EST (UTC-5): 08:30 ET = 13:30 UTC.
        utc = calendar._to_utc(date(2026, 1, 12), "CPI")
        self.assertEqual(utc, datetime(2026, 1, 12, 13, 30, tzinfo=timezone.utc))

    def test_release_ids_dikenal(self):
        for ind in ("CPI", "PPI", "NFP", "UNEMPLOYMENT", "GDP"):
            self.assertIn(ind, calendar.RELEASE_IDS)
        self.assertEqual(calendar.RELEASE_IDS["NFP"], calendar.RELEASE_IDS["UNEMPLOYMENT"])

    def test_impact_lengkap(self):
        for ind in calendar.RELEASE_IDS:
            self.assertIn(ind, calendar.IMPACT)


class TelegramFormatTest(unittest.TestCase):
    def test_format_news_result_unit_k(self):
        text = telegram_bot.format_news_result(
            event_label="US Non-Farm Payrolls",
            release_date="2026-09-04",
            release_wib="19:30",
            actual=162.0,
            previous=21.0,
            delta=141.0,
            verdict_display="BEARISH",
            rationale="Terlalu panas.",
            unit="K",
        )
        self.assertIn("162K", text)
        self.assertIn("21K", text)
        self.assertIn("+141K", text)
        self.assertIn("baseline naive", text)

    def test_format_news_result_unit_persen(self):
        text = telegram_bot.format_news_result(
            event_label="US CPI YoY",
            release_date="2026-08-12",
            release_wib="19:30",
            actual=2.9,
            previous=3.1,
            delta=-0.2,
            verdict_display="BULLISH",
            rationale="Inflasi mendingin.",
            unit="%",
        )
        self.assertIn("2.90%", text)
        self.assertIn("-0.20%", text)

    def test_format_news_result_moves_dan_edukasi(self):
        text = telegram_bot.format_news_result(
            event_label="CPI",
            release_date="2026-08-12",
            release_wib="19:30",
            actual=2.9,
            previous=3.1,
            delta=-0.2,
            verdict_display="BULLISH",
            rationale="x",
            unit="%",
            moves={"BTC/USD": {"15m": 0.4, "1h": 1.1, "4h": None}},
            explain="CPI artinya inflasi.",
        )
        self.assertIn("BTC/USD", text)
        self.assertIn("+0.40%", text)
        self.assertIn("Apa ini & kenapa", text)


if __name__ == "__main__":
    unittest.main()
