"""
Ambil jadwal rilis resmi (BLS + FOMC), validasi, tulis data/schedule.json, dan nyalakan
alarm cakupan. Untuk cron Hermes — script ini TIDAK memasang cron sendiri.

Jalankan:
    python scripts/fetch_schedule.py              # fetch + tulis + status
    python scripts/fetch_schedule.py --dry-run    # fetch + validasi + status di stdout, tanpa menulis
    python scripts/fetch_schedule.py --ics-file X --fomc-file Y   # input dari file (offline)

Exit code (supaya cron bisa melaporkan):
    0  jadwal diterima DAN cakupan aman
    2  cakupan menipis (alarm) — jadwal mungkin tetap diterima
    3  hasil fetch ditolak/gagal — data/schedule.json lama TIDAK disentuh

Kenapa JSON, bukan file .py hasil generate: isi berasal dari halaman web. JSON adalah data
yang divalidasi ulang setiap kali `src/calendar.py` memuatnya; .py hasil generate adalah
kode yang dieksekusi saat import, dan satu halaman aneh bisa merusak import semua konsumen
kalender (dashboard, tick, penulis blackout).

Tidak ada tanggal yang dikarang: kalau salah satu sumber gagal atau meragukan, SELURUH
hasil ditolak (tidak ditulis sebagian), dan alarm tetap dihitung dari jadwal efektif
(file lama yang masih valid, atau konstanta).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import calendar  # noqa: E402
from src.schedule import (  # noqa: E402
    ScheduleParseError,
    assess_coverage,
    parse_bls_ics,
    parse_fomc_html,
    validate_schedule,
)

BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
# www.bls.gov menolak UA browser palsu dan curl (403 "Access Denied", diuji 15 Sep 2026) tapi
# melayani UA yang mengidentifikasi diri — sesuai kebijakan bot BLS. Jangan diganti UA browser.
USER_AGENT = "NewsAgent-schedule-fetch/1.0 (macro release calendar; operator contact on file)"
STATUS_PATH = ROOT / "data" / "schedule_status.json"


def fetch_text(url: str, opener: Callable[..., Any] = urllib.request.urlopen) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener(request, timeout=30) as response:
        status = getattr(response, "status", 200)
        body = response.read().decode("utf-8", errors="replace")
    if status != 200:
        raise ScheduleParseError(f"HTTP {status} dari {url}")
    return body


def build_schedule(ics_text: str, fomc_text: str, today) -> tuple[Optional[dict], list[str]]:
    """(data, alasan penolakan). MURNI. data None = ditolak."""
    errors: list[str] = []
    bls = fomc = None
    try:
        bls = parse_bls_ics(ics_text)
    except ScheduleParseError as exc:
        errors.append(str(exc))
    try:
        fomc = parse_fomc_html(fomc_text)
    except ScheduleParseError as exc:
        errors.append(str(exc))
    if errors:
        return None, errors
    data = {"BLS": bls, "FOMC": fomc}
    errors = validate_schedule(data, today)
    return (None, errors) if errors else (data, [])


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run(
    now: datetime,
    *,
    schedule_path: Path,
    status_path: Path,
    dry_run: bool = False,
    ics_loader: Callable[[], str],
    fomc_loader: Callable[[], str],
) -> tuple[int, dict]:
    today = now.date()
    fetch_errors: list[str] = []
    ics_text = fomc_text = ""
    try:
        ics_text = ics_loader()
    except Exception as exc:  # noqa: BLE001 - apa pun sebabnya, sumber ini tidak terbaca
        fetch_errors.append(f"BLS: fetch gagal: {type(exc).__name__}: {exc}"[:300])
    try:
        fomc_text = fomc_loader()
    except Exception as exc:  # noqa: BLE001
        fetch_errors.append(f"FOMC: fetch gagal: {type(exc).__name__}: {exc}"[:300])

    data, rejected = (None, fetch_errors) if fetch_errors else build_schedule(ics_text, fomc_text, today)
    accepted = data is not None
    if accepted and not dry_run:
        write_json_atomic(
            schedule_path,
            {
                "generated_at": now.isoformat(timespec="seconds"),
                "sources": {"BLS": BLS_ICS_URL, "FOMC": FOMC_URL},
                "BLS": data["BLS"],
                "FOMC": data["FOMC"],
            },
        )

    # Cakupan dihitung dari jadwal yang BENAR-BENAR akan dipakai. Pada dry-run yang lolos,
    # itu hasil fetch ini; selain itu, file di disk (kalau valid) atau konstanta.
    effective = calendar.effective_schedule(now, schedule_path)
    if accepted and dry_run:
        effective["BLS"].update(data["BLS"])
        effective["FOMC"] = data["FOMC"]
        effective["sources"].update({k: "fetch (dry-run)" for k in list(data["BLS"]) + ["FOMC"]})
    merged = dict(effective["BLS"])
    merged["FOMC"] = effective["FOMC"]
    coverage = assess_coverage(merged, today, effective["sources"])

    status = {
        **coverage,
        "checked_at": now.isoformat(timespec="seconds"),
        "fetch_accepted": accepted,
        "fetch_errors": rejected,
        "schedule_file_errors": [] if accepted else effective["file_errors"],
        "dry_run": dry_run,
    }
    if not accepted:
        status["ok"] = False
        status["reason"] = "; ".join(filter(None, ["hasil fetch DITOLAK: " + " | ".join(rejected), coverage["reason"]]))
    if not dry_run:
        write_json_atomic(status_path, status)
    code = 3 if not accepted else (0 if coverage["ok"] else 2)
    return code, status


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="validasi & cetak status, tanpa menulis file")
    parser.add_argument("--ics-file", help="baca ICS BLS dari file, bukan jaringan")
    parser.add_argument("--fomc-file", help="baca halaman FOMC dari file, bukan jaringan")
    parser.add_argument("--now", help="override waktu (ISO 8601, UTC) — untuk uji")
    parser.add_argument("--schedule-path", default=str(calendar.SCHEDULE_PATH))
    parser.add_argument("--status-path", default=str(STATUS_PATH))
    args = parser.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    def loader(path: Optional[str], url: str) -> Callable[[], str]:
        if path:
            return lambda: Path(path).read_text(encoding="utf-8")
        return lambda: fetch_text(url)

    code, status = run(
        now,
        schedule_path=Path(args.schedule_path),
        status_path=Path(args.status_path),
        dry_run=args.dry_run,
        ics_loader=loader(args.ics_file, BLS_ICS_URL),
        fomc_loader=loader(args.fomc_file, FOMC_URL),
    )
    verdict = {0: "OK", 2: "ALARM CAKUPAN", 3: "FETCH DITOLAK"}[code]
    print(f"[schedule] {verdict} · diterima={status['fetch_accepted']} · tanggal terakhir terdekat {status['last_date']} ({status['days_left']} hari)")
    for ind, row in status["per_indicator"].items():
        extra = {k: v for k, v in row.items() if k.startswith("entries_")}
        print(f"  {ind:5} terakhir {row['last_date']} ({row['days_left']} hari) · {extra} · sumber {row['source']}")
    if status["reason"]:
        print(f"[schedule] alasan: {status['reason']}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
