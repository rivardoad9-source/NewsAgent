"""Test offline jalur cepat BLS (`src/bls_fast_release.py`).

Tanpa jaringan: semua fungsi hitung/murni diuji dengan payload tiruan, dan
`detect_fast_release` diuji dengan fetcher + resolver tanggal rilis yang diinjeksi.

Angka yang dipakai di beberapa test adalah angka ASLI rilis 10 Sep 2026 (PPI Agustus
WPSFD4 = 157.411, Juli = 156.784) dan NFP Agustus (159.075K vs 158.913K), supaya
aritmetika di modul ini tervalidasi terhadap sumber, bukan cuma terhadap dirinya sendiri.

Jalankan:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import io
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import bls_fast_release as fast  # noqa: E402


class FakeResponse(io.BytesIO):
    """Context manager minimal yang meniru respons `urllib.request.urlopen`."""

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
        return None


def payload(series_id: str, rows: list[tuple[str, str, str, str]]) -> dict:
    """Payload BLS minimal: (year, period, periodName, value)."""
    return {
        "status": "REQUEST_SUCCEEDED",
        "Results": {
            "series": [
                {
                    "seriesID": series_id,
                    "data": [
                        {"year": y, "period": p, "periodName": n, "value": v}
                        for (y, p, n, v) in rows
                    ],
                }
            ]
        },
    }


PPI_REAL = payload(
    "WPSFD4",
    [
        ("2026", "M08", "August", "157.411"),
        ("2026", "M07", "July", "156.784"),
        ("2026", "M06", "June", "156.667"),
        ("2025", "M08", "August", "149.3"),
        ("2025", "M07", "July", "149.4"),
    ],
)


class ParseObservationsTest(unittest.TestCase):
    def test_orders_newest_first_and_normalises_period(self):
        obs = fast.parse_observations(PPI_REAL)
        self.assertEqual(
            [o["period"] for o in obs], ["2026-08", "2026-07", "2026-06", "2025-08", "2025-07"]
        )
        self.assertAlmostEqual(obs[0]["value"], 157.411)

    def test_skips_annual_average_m13(self):
        obs = fast.parse_observations(
            payload("X", [("2026", "M13", "Annual", "150.0"), ("2026", "M08", "August", "157.4")])
        )
        self.assertEqual([o["period"] for o in obs], ["2026-08"])

    def test_skips_values_that_are_not_numbers(self):
        # BLS memakai "-" untuk data yang tidak tersedia: itu bukan rilis.
        obs = fast.parse_observations(
            payload("X", [("2026", "M08", "August", "-"), ("2026", "M07", "July", "156.7")])
        )
        self.assertEqual([o["period"] for o in obs], ["2026-07"])

    def test_empty_or_malformed_payload_is_empty(self):
        for bad in [{}, {"Results": {}}, {"Results": {"series": []}}, None]:
            self.assertEqual(fast.parse_observations(bad), [])


class ComputeHeadlineTest(unittest.TestCase):
    def test_index_uses_yoy_and_reports_mom(self):
        obs = fast.parse_observations(
            payload(
                "X",
                [
                    ("2026", "M08", "August", "110"),
                    ("2026", "M07", "July", "105"),
                    ("2025", "M08", "August", "100"),
                    ("2025", "M07", "July", "100"),
                ],
            )
        )
        head = fast.compute_headline("index", obs)
        assert head is not None
        self.assertEqual(head["period"], "2026-08")
        self.assertAlmostEqual(head["actual"], 10.0)  # 110/100 - 1
        self.assertAlmostEqual(head["previous"], 5.0)  # 105/100 - 1
        self.assertAlmostEqual(head["delta"], 5.0)
        self.assertAlmostEqual(head["mom"], 4.76, places=2)  # 110/105 - 1

    def test_index_returns_none_without_year_ago_data(self):
        obs = fast.parse_observations(
            payload("X", [("2026", "M08", "August", "110"), ("2026", "M07", "July", "105")])
        )
        self.assertIsNone(fast.compute_headline("index", obs))

    def test_rate_is_level_with_point_delta(self):
        obs = fast.parse_observations(
            payload(
                "X",
                [
                    ("2026", "M08", "August", "4.2"),
                    ("2026", "M07", "July", "4.1"),
                ],
            )
        )
        head = fast.compute_headline("rate", obs)
        assert head is not None
        self.assertAlmostEqual(head["actual"], 4.2)
        self.assertAlmostEqual(head["previous"], 4.1)
        self.assertAlmostEqual(head["delta"], 0.1, places=6)

    def test_level_delta_matches_the_real_nfp_release(self):
        # Rilis asli: 159.075K (Agu) vs 158.913K (Jul) vs 158.892K (Jun)
        # -> perubahan +162K, sebelumnya +21K, selisih +141K (sama dgn baris feed 4 Sep).
        obs = fast.parse_observations(
            payload(
                "CES0000000001",
                [
                    ("2026", "M08", "August", "159075"),
                    ("2026", "M07", "July", "158913"),
                    ("2026", "M06", "June", "158892"),
                ],
            )
        )
        head = fast.compute_headline("level_delta", obs)
        assert head is not None
        self.assertAlmostEqual(head["actual"], 162.0)
        self.assertAlmostEqual(head["previous"], 21.0)
        self.assertAlmostEqual(head["delta"], 141.0)

    def test_real_ppi_mom_matches_the_bls_headline(self):
        # Headline BLS 10 Sep 2026: final demand naik 0,4% MoM. Ini yang harus dibaca
        # modul dari seri yang sama — kalau tidak, sumbernya salah seri.
        head = fast.compute_headline("index", fast.parse_observations(PPI_REAL))
        assert head is not None
        self.assertAlmostEqual(head["mom"], 0.4, places=1)

    def test_not_enough_data_is_none(self):
        self.assertIsNone(fast.compute_headline("index", []))
        self.assertIsNone(fast.compute_headline("rate", [{"period": "2026-08", "value": 4.2}]))

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            fast.compute_headline(
                "nonsense",
                [{"period": "2026-08", "value": 1.0}, {"period": "2026-07", "value": 1.0}],
            )


class PeriodMatchesReleaseTest(unittest.TestCase):
    def test_one_month_lag_is_the_normal_case(self):
        # Rilis 10 Sep 2026 membawa data Agustus 2026.
        self.assertTrue(fast.period_matches_release("2026-08", date(2026, 9, 10)))

    def test_two_month_lag_is_tolerated_for_a_shifted_release(self):
        self.assertTrue(fast.period_matches_release("2026-07", date(2026, 9, 10)))

    def test_same_month_or_too_old_is_rejected(self):
        # Periode yang sama dengan bulan rilis = belum mungkin; 3 bulan terlalu basi
        # (itu data lama, bukan rilis hari ini).
        self.assertFalse(fast.period_matches_release("2026-09", date(2026, 9, 10)))
        self.assertFalse(fast.period_matches_release("2026-06", date(2026, 9, 10)))

    def test_garbage_period_is_rejected(self):
        self.assertFalse(fast.period_matches_release("M08", date(2026, 9, 10)))


class LatestScheduledDateTest(unittest.TestCase):
    """Jadwal forward, bukan tanggal yang dikonfirmasi FRED.

    Ini bug yang tertangkap saat uji pertama: `calendar.last_release_date()` menjawab
    13 Agustus pada 10 September, sehingga jalur cepat diam di hari rilis PPI.
    """

    SCHEDULE = ["2026-08-13", "2026-09-10", "2026-10-15"]

    def test_picks_the_most_recent_past_date(self):
        self.assertEqual(
            fast.latest_scheduled_date(self.SCHEDULE, date(2026, 9, 10)), date(2026, 9, 10)
        )
        self.assertEqual(
            fast.latest_scheduled_date(self.SCHEDULE, date(2026, 9, 30)), date(2026, 9, 10)
        )
        self.assertEqual(
            fast.latest_scheduled_date(self.SCHEDULE, date(2026, 10, 15)), date(2026, 10, 15)
        )

    def test_before_the_first_entry_is_none(self):
        self.assertIsNone(fast.latest_scheduled_date(self.SCHEDULE, date(2026, 1, 1)))

    def test_unreadable_entries_are_skipped_not_guessed(self):
        self.assertEqual(
            fast.latest_scheduled_date(["nonsense", "2026-09-10"], date(2026, 9, 30)),
            date(2026, 9, 10),
        )

    def test_empty_schedule_is_none(self):
        self.assertIsNone(fast.latest_scheduled_date([], date(2026, 9, 30)))


class ReleaseWindowTest(unittest.TestCase):
    def setUp(self):
        self.release = datetime(2026, 9, 10, 12, 30, tzinfo=timezone.utc)  # 19:30 WIB

    def test_inside_the_window(self):
        self.assertTrue(fast.within_release_window(self.release, self.release))
        self.assertTrue(
            fast.within_release_window(self.release + timedelta(minutes=45), self.release)
        )
        self.assertTrue(
            fast.within_release_window(
                self.release + timedelta(minutes=fast.FAST_RELEASE_HORIZON_MIN), self.release
            )
        )

    def test_before_the_release_and_after_the_horizon(self):
        self.assertFalse(
            fast.within_release_window(self.release - timedelta(minutes=1), self.release)
        )
        self.assertFalse(
            fast.within_release_window(
                self.release + timedelta(minutes=fast.FAST_RELEASE_HORIZON_MIN, seconds=1),
                self.release,
            )
        )


class DetectFastReleaseTest(unittest.TestCase):
    def setUp(self):
        self.release = datetime(2026, 9, 10, 12, 30, tzinfo=timezone.utc)
        self.now = self.release + timedelta(minutes=25)
        self.release_date = date(2026, 9, 10)

    def detect(self, state=None, now=None, rows=None, indicator="PPI", release_date=None):
        fetched = rows if rows is not None else PPI_REAL
        return fast.detect_fast_release(
            indicator,
            state or {},
            now or self.now,
            fetcher=lambda series_id, moment: fast.parse_observations(fetched),
            release_date_for=lambda ind, moment: (
                self.release_date if release_date is None else release_date
            ),
        )

    def test_fires_right_after_the_release(self):
        hit = self.detect()
        assert hit is not None
        self.assertEqual(hit["series"], "WPSFD4")
        self.assertEqual(hit["release_date"], date(2026, 9, 10))
        self.assertEqual(hit["headline"]["period"], "2026-08")

    def test_does_not_fire_twice_for_the_same_period(self):
        self.assertIsNone(self.detect(state={"fast_sent": {"PPI": "2026-08"}}))

    def test_fires_again_for_a_new_period(self):
        self.assertIsNotNone(self.detect(state={"fast_sent": {"PPI": "2026-07"}}))

    def test_outside_the_window_is_silent(self):
        self.assertIsNone(self.detect(now=self.release + timedelta(hours=5)))
        self.assertIsNone(self.detect(now=self.release - timedelta(hours=1)))

    def test_stale_period_is_rejected(self):
        # API mengembalikan data Juli padahal rilisnya 15 Nov: jangan kirim sebagai "baru".
        self.assertIsNone(self.detect(rows=PPI_REAL, release_date=date(2026, 11, 15)))

    def test_unknown_indicator_is_ignored(self):
        self.assertIsNone(self.detect(indicator="GDP"))

    def test_missing_release_date_is_silent(self):
        # Kalender tidak tahu ada rilis -> jalur cepat diam, bukan menebak.
        self.assertIsNone(
            fast.detect_fast_release(
                "PPI",
                {},
                self.now,
                fetcher=lambda s, m: fast.parse_observations(PPI_REAL),
                release_date_for=lambda ind, moment: None,
            )
        )

    def test_fetch_failure_propagates_as_bls_error(self):
        def boom(series_id, moment):
            raise fast.BlsFetchError("status BLS REQUEST_NOT_PROCESSED")

        with self.assertRaises(fast.BlsFetchError):
            fast.detect_fast_release(
                "PPI",
                {},
                self.now,
                fetcher=boom,
                release_date_for=lambda ind, moment: self.release_date,
            )


class FetchObservationsTest(unittest.TestCase):
    def test_raises_when_bls_reports_failure_status(self):
        body = b'{"status": "REQUEST_NOT_PROCESSED", "message": "Series does not exist"}'
        with self.assertRaises(fast.BlsFetchError):
            fast.fetch_observations(
                "NOPE",
                datetime(2026, 9, 10, tzinfo=timezone.utc),
                opener=lambda *a, **k: FakeResponse(body),
            )

    def test_parses_a_successful_response(self):
        body = (
            b'{"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"data": '
            b'[{"year": "2026", "period": "M08", "periodName": "August", "value": "157.411"}]}]}}'
        )
        obs = fast.fetch_observations(
            "WPSFD4",
            datetime(2026, 9, 10, tzinfo=timezone.utc),
            opener=lambda *a, **k: FakeResponse(body),
        )
        self.assertEqual(obs[0]["period"], "2026-08")
        self.assertAlmostEqual(obs[0]["value"], 157.411)


if __name__ == "__main__":
    unittest.main()
