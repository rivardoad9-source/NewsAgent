"""Auto-fetch jadwal BLS + FOMC: parse, validasi, fallback, alarm cakupan (OFFLINE).

Fixture di tests/fixtures/ adalah salinan halaman resmi yang diunduh 15 Sep 2026
(ICS BLS utuh; halaman FOMC dipangkas ke panel 2025-2027; halaman Access Denied BLS
yang sungguh dikembalikan ke curl). Tidak ada test di sini yang menyentuh jaringan.

Jalankan:  python -m unittest tests.test_schedule -v
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src import calendar  # noqa: E402
from src import schedule  # noqa: E402
import fetch_schedule  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
ICS = (FIX / "bls_2026-09-15.ics").read_text(encoding="utf-8")
FOMC = (FIX / "fomccalendars_2026-09-15.html").read_text(encoding="utf-8")
DENIED = (FIX / "bls_access_denied.html").read_text(encoding="utf-8")
FOMC_TEMPLATE = (FIX / "fomccalendars_template_no_panels.html").read_text(encoding="utf-8")
TODAY = date(2026, 9, 15)
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def monthly(year: int, day: int = 12, months=range(1, 13)) -> list[str]:
    return [date(year, m, day).isoformat() for m in months]


def good_data() -> dict:
    return {"BLS": schedule.parse_bls_ics(ICS), "FOMC": schedule.parse_fomc_html(FOMC)}


class ParseOfficialPagesTest(unittest.TestCase):
    def test_bls_ics_yields_the_three_releases_at_0830(self):
        bls = schedule.parse_bls_ics(ICS)
        self.assertEqual(sorted(bls), ["CPI", "NFP", "PPI", "UNEMPLOYMENT"])
        self.assertEqual(bls["NFP"], bls["UNEMPLOYMENT"])
        # Tanggal resmi yang juga ada di konstanta calendar.py.
        for iso in ("2026-09-11", "2026-10-14", "2026-12-10"):
            self.assertIn(iso, bls["CPI"])
        self.assertIn("2026-09-10", bls["PPI"])
        self.assertIn("2026-12-04", bls["NFP"])
        # "Employment Situation of Veterans" bukan NFP: hanya satu NFP per bulan.
        self.assertEqual(len([d for d in bls["NFP"] if d.startswith("2026-")]), 12)
        # Kondisi hari ini: ICS resmi belum memuat 2027.
        self.assertFalse(any(d.startswith("2027") for v in bls.values() for d in v))

    def test_fomc_decision_day_is_the_second_day_and_notation_votes_are_skipped(self):
        fomc = schedule.parse_fomc_html(FOMC)
        self.assertIn("2026-09-16", fomc)      # Sep 15-16
        self.assertIn("2027-12-08", fomc)      # Dec 7-8 (panel 2027 ada di bawah halaman)
        self.assertNotIn("2025-08-22", fomc)   # "22 (notation vote)"
        self.assertEqual(len([d for d in fomc if d.startswith("2027")]), 8)
        self.assertEqual(fomc, sorted(set(fomc)))

    def test_cross_month_meeting_uses_the_second_month(self):
        html = FOMC.replace("<strong>April</strong>", "<strong>Apr/May</strong>", 1).replace(">28-29<", ">30-1<", 1)
        self.assertIn("2026-05-01", schedule.parse_fomc_html(html))

    def test_access_denied_page_is_rejected_not_parsed_as_empty(self):
        with self.assertRaisesRegex(schedule.ScheduleParseError, "bukan file iCalendar"):
            schedule.parse_bls_ics(DENIED)

    def test_fomc_template_without_panels_is_rejected(self):
        with self.assertRaisesRegex(schedule.ScheduleParseError, "tidak ada panel"):
            schedule.parse_fomc_html(FOMC_TEMPLATE)

    def test_ics_from_someone_else_is_rejected(self):
        with self.assertRaisesRegex(schedule.ScheduleParseError, "PRODID"):
            schedule.parse_bls_ics(ICS.replace("Bureau of Labor Statistics", "Somebody Else"))

    def test_partial_ics_missing_an_indicator_is_rejected(self):
        partial = ICS.replace("SUMMARY:Producer Price Index", "SUMMARY:Something Else")
        with self.assertRaisesRegex(schedule.ScheduleParseError, "PPI"):
            schedule.parse_bls_ics(partial)

    def test_release_time_other_than_0830_is_rejected(self):
        moved = ICS.replace("DTSTART;TZID=US-Eastern:20261210T083000", "DTSTART;TZID=US-Eastern:20261210T100000")
        self.assertNotEqual(moved, ICS)
        with self.assertRaisesRegex(schedule.ScheduleParseError, "bukan 08:30"):
            schedule.parse_bls_ics(moved)

    def test_unknown_fomc_row_is_rejected_not_guessed(self):
        odd = FOMC.replace(">27-28<", ">TBD<", 1)
        with self.assertRaisesRegex(schedule.ScheduleParseError, "baris tidak dikenal"):
            schedule.parse_fomc_html(odd)


class ValidateScheduleTest(unittest.TestCase):
    def test_the_official_data_passes(self):
        self.assertEqual(schedule.validate_schedule(good_data(), TODAY), [])

    def test_wrong_shape(self):
        self.assertTrue(schedule.validate_schedule([], TODAY))
        self.assertTrue(schedule.validate_schedule({"BLS": {}}, TODAY))

    def test_bad_iso_format(self):
        d = good_data()
        d["BLS"]["CPI"] = d["BLS"]["CPI"][:-1] + ["12/10/2026"]
        self.assertTrue(any("YYYY-MM-DD" in e for e in schedule.validate_schedule(d, TODAY)))
        d = good_data()
        d["BLS"]["CPI"] = d["BLS"]["CPI"][:-1] + ["2026-02-30"]
        self.assertTrue(any("tidak valid" in e for e in schedule.validate_schedule(d, TODAY)))

    def test_not_ascending(self):
        d = good_data()
        d["BLS"]["PPI"] = list(reversed(d["BLS"]["PPI"]))
        self.assertTrue(any("urut" in e for e in schedule.validate_schedule(d, TODAY)))

    def test_weekly_garbage_is_not_a_monthly_schedule(self):
        d = good_data()
        d["BLS"]["CPI"] = [date.fromordinal(date(2026, 1, 5).toordinal() + 7 * i).isoformat() for i in range(52)]
        errors = schedule.validate_schedule(d, TODAY)
        self.assertTrue(any("median" in e for e in errors), errors)

    def test_a_missing_quarter_is_rejected(self):
        d = good_data()
        d["BLS"]["CPI"] = [x for x in d["BLS"]["CPI"] if not ("2026-04" <= x < "2026-08")]
        self.assertTrue(any("jarak rilis di luar" in e for e in schedule.validate_schedule(d, TODAY)))

    def test_too_few_per_full_year(self):
        d = good_data()
        d["BLS"]["NFP"] = monthly(2025) + monthly(2026, months=range(1, 13, 2)) + ["2027-01-08"]
        d["BLS"]["UNEMPLOYMENT"] = d["BLS"]["NFP"]
        errors = schedule.validate_schedule(d, TODAY)
        self.assertTrue(any("tanggal di 2026" in e for e in errors), errors)

    def test_all_dates_in_the_past(self):
        d = good_data()
        self.assertTrue(any("sudah lewat" in e for e in schedule.validate_schedule(d, date(2027, 1, 5))))

    def test_fomc_too_many_per_year(self):
        d = good_data()
        extra = [f"2026-{m:02d}-02" for m in (2, 5, 8, 11)]
        d["FOMC"] = sorted(set(d["FOMC"]) | set(extra))
        errors = schedule.validate_schedule(d, TODAY)
        self.assertTrue(any("rapat di 2026" in e or "terlalu dekat" in e for e in errors), errors)

    def test_missing_required_indicator(self):
        d = good_data()
        del d["BLS"]["CPI"]
        self.assertIn("BLS.CPI: tidak ada", schedule.validate_schedule(d, TODAY))


class CalendarFallbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "schedule.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_falls_back_to_constants(self):
        eff = calendar.effective_schedule(NOW, self.path)
        self.assertEqual(eff["BLS"], calendar.BLS_SCHEDULE)
        self.assertEqual(eff["FOMC"], calendar.FOMC_SCHEDULE)
        self.assertEqual(set(eff["sources"].values()), {"constants"})
        self.assertTrue(eff["file_errors"])

    def test_corrupt_file_falls_back_to_constants(self):
        self.path.write_text("{ not json", encoding="utf-8")
        eff = calendar.effective_schedule(NOW, self.path)
        self.assertEqual(eff["BLS"], calendar.BLS_SCHEDULE)
        self.assertIn("tidak terbaca", eff["file_errors"][0])

    def test_invalid_file_falls_back_to_constants(self):
        d = good_data()
        d["BLS"]["CPI"] = ["2026-09-11"]
        self.path.write_text(json.dumps(d), encoding="utf-8")
        eff = calendar.effective_schedule(NOW, self.path)
        self.assertEqual(eff["BLS"], calendar.BLS_SCHEDULE)
        self.assertTrue(eff["file_errors"])

    def test_valid_file_is_primary_and_gdp_stays_on_constants(self):
        d = good_data()
        self.path.write_text(json.dumps(d), encoding="utf-8")
        eff = calendar.effective_schedule(NOW, self.path)
        self.assertEqual(eff["BLS"]["CPI"], d["BLS"]["CPI"])
        self.assertEqual(eff["FOMC"], d["FOMC"])
        self.assertEqual(eff["BLS"]["GDP"], calendar.BLS_SCHEDULE["GDP"])
        self.assertEqual(eff["sources"]["CPI"], "file")
        self.assertEqual(eff["sources"]["GDP"], "constants")

    def test_upcoming_reads_the_file_with_the_same_signature(self):
        d = good_data()
        d["FOMC"] = [x if x != "2026-09-16" else "2026-09-17" for x in d["FOMC"]]  # penanda
        self.path.write_text(json.dumps(d), encoding="utf-8")
        original = calendar.SCHEDULE_PATH
        calendar.SCHEDULE_PATH = self.path
        calendar._release_dates, saved = (lambda *a, **k: []), calendar._release_dates  # tanpa FRED
        try:
            rows = {r.indicator: r for r in calendar.upcoming(horizon_days=7, now=NOW)}
        finally:
            calendar.SCHEDULE_PATH = original
            calendar._release_dates = saved
        self.assertEqual(rows["FOMC"].release_date, date(2026, 9, 17))
        self.assertEqual(rows["FOMC"].release_utc, datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc))


class CoverageAlarmTest(unittest.TestCase):
    def merged(self, data: dict) -> dict:
        m = dict(data["BLS"])
        m["GDP"] = calendar.BLS_SCHEDULE["GDP"]
        m["FOMC"] = data["FOMC"]
        return m

    def test_alarm_fires_today_because_2027_is_missing(self):
        cov = schedule.assess_coverage(self.merged(good_data()), TODAY)
        self.assertFalse(cov["ok"])
        for ind in ("CPI", "PPI", "NFP", "GDP"):
            self.assertIn(f"{ind}: 0 entri untuk 2027", cov["reason"])
        self.assertNotIn("FOMC: 0 entri", cov["reason"])  # Fed sudah menerbitkan 2027
        self.assertEqual(cov["last_date"], "2026-12-04")

    def test_alarm_fires_when_horizon_under_60_days(self):
        d = good_data()
        cov = schedule.assess_coverage(self.merged(d), date(2026, 5, 1),
                                       indicators=("CPI",))
        self.assertTrue(cov["ok"])  # Mei: >120 hari ke akhir tahun, cakupan jauh
        short = {"CPI": ["2026-06-10", "2026-06-20"]}
        cov = schedule.assess_coverage(short, date(2026, 5, 1), indicators=("CPI",))
        self.assertFalse(cov["ok"])
        self.assertIn("< 60", cov["reason"])

    def test_no_alarm_when_next_year_is_published(self):
        d = good_data()
        m = self.merged(d)
        for ind in ("CPI", "PPI", "NFP", "GDP"):
            m[ind] = m[ind] + [date(2027, 1, 13).isoformat(), date(2027, 2, 11).isoformat()]
        cov = schedule.assess_coverage(m, TODAY)
        self.assertTrue(cov["ok"], cov["reason"])

    def test_constants_alone_also_alarm_today(self):
        m = dict(calendar.BLS_SCHEDULE)
        m["FOMC"] = calendar.FOMC_SCHEDULE
        cov = schedule.assess_coverage(m, TODAY)
        self.assertFalse(cov["ok"])
        self.assertEqual(cov["per_indicator"]["GDP"]["last_date"], "2026-12-23")


class FetchScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.schedule_path = self.dir / "schedule.json"
        self.status_path = self.dir / "schedule_status.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, ics=lambda: ICS, fomc=lambda: FOMC, dry_run=False):
        return fetch_schedule.run(NOW, schedule_path=self.schedule_path, status_path=self.status_path,
                                  dry_run=dry_run, ics_loader=ics, fomc_loader=fomc)

    def test_official_pages_are_written_and_the_alarm_exits_non_zero_today(self):
        code, status = self.run_script()
        self.assertEqual(code, 2)
        self.assertTrue(status["fetch_accepted"])
        written = json.loads(self.schedule_path.read_text(encoding="utf-8"))
        self.assertEqual(written["FOMC"], schedule.parse_fomc_html(FOMC))
        self.assertIn("generated_at", written)
        on_disk = json.loads(self.status_path.read_text(encoding="utf-8"))
        self.assertFalse(on_disk["ok"])
        self.assertIn("0 entri untuk 2027", on_disk["reason"])
        self.assertEqual(on_disk["per_indicator"]["CPI"]["source"], "file")

    def test_error_page_is_rejected_and_the_old_file_is_untouched(self):
        self.schedule_path.write_text('{"sentinel": true}', encoding="utf-8")
        code, status = self.run_script(ics=lambda: DENIED)
        self.assertEqual(code, 3)
        self.assertFalse(status["fetch_accepted"])
        self.assertEqual(self.schedule_path.read_text(encoding="utf-8"), '{"sentinel": true}')
        self.assertIn("DITOLAK", json.loads(self.status_path.read_text(encoding="utf-8"))["reason"])

    def test_network_failure_is_rejected_not_written(self):
        def boom():
            raise OSError("connection reset")
        code, status = self.run_script(fomc=boom)
        self.assertEqual(code, 3)
        self.assertFalse(self.schedule_path.exists())
        self.assertTrue(any("FOMC: fetch gagal" in e for e in status["fetch_errors"]))
        # Status tetap ditulis, dari jadwal efektif (konstanta).
        self.assertEqual(json.loads(self.status_path.read_text(encoding="utf-8"))["per_indicator"]["CPI"]["source"], "constants")

    def test_dry_run_writes_nothing(self):
        code, status = self.run_script(dry_run=True)
        self.assertEqual(code, 2)
        self.assertFalse(self.schedule_path.exists())
        self.assertFalse(self.status_path.exists())
        self.assertEqual(status["per_indicator"]["CPI"]["source"], "fetch (dry-run)")

    def test_cli_with_fixture_files(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = fetch_schedule.main([
                "--dry-run", "--now", "2026-09-15T12:00:00+00:00",
                "--ics-file", str(FIX / "bls_2026-09-15.ics"),
                "--fomc-file", str(FIX / "fomccalendars_2026-09-15.html"),
                "--schedule-path", str(self.schedule_path), "--status-path", str(self.status_path),
            ])
        self.assertEqual(code, 2)
        self.assertIn("ALARM CAKUPAN", out.getvalue())
        self.assertIn("0 entri untuk 2027", err.getvalue())


if __name__ == "__main__":
    unittest.main()
