"""
Jadwal rilis forward-looking: parse halaman resmi, validasi, dan alarm cakupan. MURNI
(tanpa jaringan) — pengambilan ada di `scripts/fetch_schedule.py`, pemakaian di
`src/calendar.py`.

KENAPA ADA. Jadwal dulu hanya konstanta di `calendar.py` yang "diperbarui setiap awal
tahun". Isinya berhenti di 2026-12-23 dan tidak ada yang tahu: penulis blackout engine
membaca `upcoming()`, dan daftar rilis yang habis menghasilkan file blackout yang kosong
tanpa error — engine bisa membuka posisi tepat di jam CPI/NFP. Modul ini membuat
kegagalan itu BERSUARA: data hasil fetch hanya diterima kalau lolos validasi, dan
cakupan yang menipis menyalakan alarm.

SUMBER (diverifikasi 15 Sep 2026):
- BLS: `https://www.bls.gov/schedule/news_release/bls.ics` — kalender iCalendar resmi,
  satu file untuk semua rilis. Dipilih ketimbang halaman HTML per indikator karena
  strukturnya (SUMMARY + DTSTART) tidak ikut berubah saat tata letak situs berubah.
  www.bls.gov menjawab 403 "Access Denied" untuk User-Agent browser palsu maupun curl,
  tetapi melayani User-Agent yang MENGIDENTIFIKASI dirinya (kebijakan bot BLS).
- FOMC: `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm` — panel
  "YYYY FOMC Meetings", baris bulan + rentang tanggal; hari keputusan = hari ke-2.

ATURAN: tidak ada tanggal yang dikarang. Halaman error/template, indikator yang hilang,
baris yang formatnya tidak dikenal → DITOLAK dengan alasan, bukan di-parse jadi jadwal
kosong yang terlihat sah.
"""
from __future__ import annotations

import re
import statistics
from datetime import date, datetime
from typing import Optional

# SUMMARY ICS BLS -> indikator. Harus sama persis: "Employment Situation of Veterans"
# BUKAN NFP.
BLS_ICS_SUMMARY: dict[str, tuple[str, ...]] = {
    "Consumer Price Index": ("CPI",),
    "Producer Price Index": ("PPI",),
    "Employment Situation": ("NFP", "UNEMPLOYMENT"),
}
BLS_REQUIRED: tuple[str, ...] = ("CPI", "PPI", "NFP")
BLS_RELEASE_HHMM = "083000"  # calendar.RELEASE_TIME_ET mengasumsikan 08:30 ET

# Sama dengan HIGH_IMPACT penulis blackout Hermes (news_blackout_write.py).
HIGH_IMPACT: tuple[str, ...] = ("CPI", "PPI", "NFP", "FOMC", "GDP")

# Validasi jarak rilis bulanan. MEDIAN harus bulanan; per-gap dilonggarkan karena ICS
# RESMI BLS (diunduh 15 Sep 2026) sendiri memuat jarak 16 hari (PPI 14 Jan -> 30 Jan 2026)
# dan 76 hari (NFP/PPI saat shutdown Okt-Nov 2025). Aturan "tiap gap 20-45 hari" menolak
# data resmi; median + batas lebar tetap menolak daftar yang bukan jadwal bulanan.
MONTHLY_MEDIAN_GAP_DAYS = (20, 45)
MONTHLY_ANY_GAP_DAYS = (10, 100)
MONTHLY_PER_YEAR = (10, 13)  # tahun penuh; tahun pertama di file boleh terpotong
FOMC_MAX_PER_YEAR = 9
FOMC_MIN_PER_FULL_YEAR = 6
FOMC_MIN_GAP_DAYS = 20

# Alarm cakupan.
MIN_COVERAGE_DAYS = 60
# Cek "0 entri tahun depan" hanya berlaku saat akhir tahun tinggal <= 120 hari. Tanpa
# batas ini alarm menyala hampir sepanjang tahun (jadwal tahun depan wajar belum terbit
# di bulan Maret), dan alarm yang selalu menyala diabaikan — kegagalan senyap lagi.
NEXT_YEAR_CHECK_DAYS = 120

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july", "august",
         "september", "october", "november", "december"],
        start=1,
    )
}
_MONTH_ABBR = {m[:3]: i for m, i in _MONTHS.items()}


class ScheduleParseError(ValueError):
    """Halaman tidak bisa dipercaya sebagai jadwal. Pesannya menyebut alasannya."""


# --------------------------------------------------------------------------- BLS (ICS)

def parse_bls_ics(text: str) -> dict[str, list[str]]:
    """ICS resmi BLS -> {indikator: [YYYY-MM-DD, ...]} (urut, unik).

    Menolak: bukan iCalendar (mis. halaman Access Denied), PRODID bukan BLS, event
    tanpa DTSTART terbaca, jam rilis bukan 08:30 ET, atau indikator wajib yang hilang.
    """
    if not isinstance(text, str) or "BEGIN:VCALENDAR" not in text:
        raise ScheduleParseError("BLS: bukan file iCalendar (halaman error/Access Denied?)")
    unfolded = re.sub(r"\r?\n[ \t]", "", text)  # RFC 5545 line folding
    prodid = re.search(r"^PRODID:(.*)$", unfolded, re.M)
    if not prodid or "Bureau of Labor Statistics" not in prodid.group(1):
        raise ScheduleParseError("BLS: PRODID bukan Bureau of Labor Statistics")

    found: dict[str, set[str]] = {}
    for block in unfolded.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        summary = re.search(r"^SUMMARY:(.*)$", block, re.M)
        if not summary:
            continue
        indicators = BLS_ICS_SUMMARY.get(summary.group(1).strip())
        if not indicators:
            continue
        start = re.search(r"^DTSTART(?:;TZID=([^:]+))?:(\d{8})T(\d{6})\s*$", block, re.M)
        if not start:
            raise ScheduleParseError(f"BLS: DTSTART tidak terbaca untuk '{summary.group(1).strip()}'")
        tzid, ymd, hms = start.groups()
        if tzid and "Eastern" not in tzid:
            raise ScheduleParseError(f"BLS: zona waktu tak dikenal '{tzid}'")
        if hms != BLS_RELEASE_HHMM:
            raise ScheduleParseError(
                f"BLS: {indicators[0]} {ymd} dijadwalkan {hms[:2]}:{hms[2:4]} ET, bukan 08:30 — "
                "calendar.RELEASE_TIME_ET akan salah, jadwal ditolak"
            )
        try:
            iso = datetime.strptime(ymd, "%Y%m%d").date().isoformat()
        except ValueError as exc:
            raise ScheduleParseError(f"BLS: tanggal tidak valid {ymd}") from exc
        for ind in indicators:
            found.setdefault(ind, set()).add(iso)

    missing = [ind for ind in BLS_REQUIRED if not found.get(ind)]
    if missing:
        raise ScheduleParseError(f"BLS: indikator tidak ditemukan di ICS: {', '.join(missing)}")
    return {ind: sorted(dates) for ind, dates in sorted(found.items())}


# --------------------------------------------------------------------------- FOMC (HTML)

_PANEL = re.compile(r"<h4>\s*<a[^>]*>\s*(\d{4}) FOMC Meetings\s*</a>\s*</h4>", re.I)
_ROW = re.compile(
    r'fomc-meeting__month[^"]*"[^>]*>\s*<strong>([^<]*)</strong>\s*</div>\s*'
    r'<div class="fomc-meeting__date[^"]*"[^>]*>([^<]*)</div>',
    re.I,
)
# Baris yang BUKAN rapat terjadwal dengan statement 14:00 ET.
_NON_MEETING = re.compile(r"^\d{1,2}\s*\((notation vote|unscheduled|cancelled|conference call)\)$", re.I)


def _month_number(name: str) -> Optional[int]:
    key = name.strip().lower().rstrip(".")
    if not key:
        return None
    return _MONTHS.get(key) or _MONTH_ABBR.get(key[:3])


def parse_fomc_html(text: str) -> list[str]:
    """Halaman kalender FOMC -> [YYYY-MM-DD hari keputusan, ...] (urut, unik).

    Menolak halaman tanpa panel tahun (error/template), panel tanpa baris, dan baris
    yang formatnya tidak dikenal. Notation vote / unscheduled dilewati secara eksplisit.
    """
    if not isinstance(text, str):
        raise ScheduleParseError("FOMC: halaman kosong")
    parts = _PANEL.split(text)
    if len(parts) < 3:
        raise ScheduleParseError("FOMC: tidak ada panel 'YYYY FOMC Meetings' (halaman error/template?)")
    out: set[str] = set()
    for i in range(1, len(parts), 2):
        year = int(parts[i])
        rows = _ROW.findall(parts[i + 1].split("panel panel-default")[0])
        if not rows:
            raise ScheduleParseError(f"FOMC {year}: panel tanpa baris rapat")
        for month_raw, date_raw in rows:
            label = " ".join(date_raw.split()).rstrip("*").strip()
            if _NON_MEETING.match(label):
                continue
            m = re.fullmatch(r"(\d{1,2})-(\d{1,2})\*?", label)
            months = [p for p in month_raw.split("/") if p.strip()]
            if not m or not months or len(months) > 2:
                raise ScheduleParseError(f"FOMC {year}: baris tidak dikenal '{month_raw.strip()} {date_raw.strip()}'")
            month = _month_number(months[-1])
            if month is None:
                raise ScheduleParseError(f"FOMC {year}: bulan tidak dikenal '{month_raw.strip()}'")
            day1, day2 = int(m.group(1)), int(m.group(2))
            if len(months) == 1 and day2 != day1 + 1:
                raise ScheduleParseError(f"FOMC {year}: rentang tidak wajar '{month_raw.strip()} {label}'")
            try:
                out.add(date(year, month, day2).isoformat())
            except ValueError as exc:
                raise ScheduleParseError(f"FOMC {year}: tanggal tidak valid '{month_raw.strip()} {label}'") from exc
    if not out:
        raise ScheduleParseError("FOMC: tidak ada rapat terjadwal yang terbaca")
    return sorted(out)


# --------------------------------------------------------------------------- validasi

def _parse_iso_list(name: str, values: object, errors: list[str]) -> list[date]:
    if not isinstance(values, list) or not values:
        errors.append(f"{name}: harus list tanggal yang tidak kosong")
        return []
    parsed: list[date] = []
    for raw in values:
        if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            errors.append(f"{name}: format tanggal bukan YYYY-MM-DD: {raw!r}")
            return []
        try:
            parsed.append(date.fromisoformat(raw))
        except ValueError:
            errors.append(f"{name}: tanggal tidak valid: {raw!r}")
            return []
    if any(b <= a for a, b in zip(parsed, parsed[1:])):
        errors.append(f"{name}: tanggal tidak urut naik / ada duplikat")
    return parsed


def _per_year(dates: list[date]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for d in dates:
        counts[d.year] = counts.get(d.year, 0) + 1
    return counts


def validate_schedule(data: object, today: date) -> list[str]:
    """Daftar alasan penolakan; list kosong = diterima. MURNI.

    Diterapkan SEBELUM hasil fetch ditulis, dan lagi saat `calendar.py` memuat file.
    """
    errors: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("BLS"), dict) or "FOMC" not in data:
        return ["bentuk harus {\"BLS\": {indikator: [...]}, \"FOMC\": [...]}"]

    bls = data["BLS"]
    for ind in BLS_REQUIRED:
        if ind not in bls:
            errors.append(f"BLS.{ind}: tidak ada")
    for ind, values in bls.items():
        dates = _parse_iso_list(f"BLS.{ind}", values, errors)
        if len(dates) < 2:
            if dates:
                errors.append(f"BLS.{ind}: hanya {len(dates)} tanggal")
            continue
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        med = statistics.median(gaps)
        if not MONTHLY_MEDIAN_GAP_DAYS[0] <= med <= MONTHLY_MEDIAN_GAP_DAYS[1]:
            errors.append(f"BLS.{ind}: median jarak rilis {med} hari, bukan bulanan")
        bad = [g for g in gaps if not MONTHLY_ANY_GAP_DAYS[0] <= g <= MONTHLY_ANY_GAP_DAYS[1]]
        if bad:
            errors.append(f"BLS.{ind}: jarak rilis di luar {MONTHLY_ANY_GAP_DAYS[0]}-{MONTHLY_ANY_GAP_DAYS[1]} hari: {bad[:3]}")
        years = _per_year(dates)
        first = min(years)
        for y, n in years.items():
            if n > MONTHLY_PER_YEAR[1] or (y != first and n < MONTHLY_PER_YEAR[0]):
                errors.append(f"BLS.{ind}: {n} tanggal di {y} (harus {MONTHLY_PER_YEAR[0]}-{MONTHLY_PER_YEAR[1]})")
        if dates[-1] < today:
            errors.append(f"BLS.{ind}: semua tanggal sudah lewat (terakhir {dates[-1]})")
    if "UNEMPLOYMENT" in bls and "NFP" in bls and bls["UNEMPLOYMENT"] != bls["NFP"]:
        errors.append("BLS.UNEMPLOYMENT harus sama dengan BLS.NFP (rilis Employment Situation yang sama)")

    fomc = _parse_iso_list("FOMC", data["FOMC"], errors)
    if fomc:
        years = _per_year(fomc)
        first = min(years)
        for y, n in years.items():
            if n > FOMC_MAX_PER_YEAR or (y != first and n < FOMC_MIN_PER_FULL_YEAR):
                errors.append(f"FOMC: {n} rapat di {y} (maks {FOMC_MAX_PER_YEAR}, min {FOMC_MIN_PER_FULL_YEAR} per tahun penuh)")
        close = [(a, b) for a, b in zip(fomc, fomc[1:]) if (b - a).days < FOMC_MIN_GAP_DAYS]
        if close:
            errors.append(f"FOMC: dua rapat terlalu dekat: {close[0][0]} & {close[0][1]}")
        if fomc[-1] < today:
            errors.append(f"FOMC: semua tanggal sudah lewat (terakhir {fomc[-1]})")
    return errors


# --------------------------------------------------------------------------- alarm cakupan

def assess_coverage(
    schedule: dict[str, list[str]],
    today: date,
    sources: Optional[dict[str, str]] = None,
    indicators: tuple[str, ...] = HIGH_IMPACT,
) -> dict:
    """Seberapa jauh ke depan jadwal efektif menjangkau, per indikator high-impact. MURNI.

    `schedule` = {indikator: [ISO,...]} (BLS + GDP + "FOMC"). Alarm (`ok: False`) bila
    tanggal terjauh sebuah indikator < MIN_COVERAGE_DAYS dari hari ini, atau bila akhir
    tahun tinggal <= NEXT_YEAR_CHECK_DAYS dan indikator itu punya 0 entri tahun depan.
    """
    sources = sources or {}
    year_end_days = (date(today.year, 12, 31) - today).days
    check_next_year = year_end_days <= NEXT_YEAR_CHECK_DAYS
    per: dict[str, dict] = {}
    reasons: list[str] = []
    for ind in indicators:
        parsed = []
        for raw in schedule.get(ind, []):
            try:
                parsed.append(date.fromisoformat(str(raw)))
            except ValueError:
                continue
        last = max(parsed) if parsed else None
        days_left = (last - today).days if last else None
        next_year = sum(1 for d in parsed if d.year == today.year + 1)
        per[ind] = {
            "last_date": last.isoformat() if last else None,
            "days_left": days_left,
            f"entries_{today.year + 1}": next_year,
            "source": sources.get(ind, "unknown"),
        }
        if last is None:
            reasons.append(f"{ind}: tidak ada jadwal sama sekali")
        elif days_left < MIN_COVERAGE_DAYS:
            reasons.append(f"{ind}: tanggal terakhir {last} tinggal {days_left} hari (< {MIN_COVERAGE_DAYS})")
        if check_next_year and next_year == 0:
            reasons.append(f"{ind}: 0 entri untuk {today.year + 1} (akhir tahun tinggal {year_end_days} hari)")
    lasts = [v["last_date"] for v in per.values() if v["last_date"]]
    earliest_last = min(lasts) if lasts else None
    return {
        "ok": not reasons,
        "checked_on": today.isoformat(),
        "last_date": earliest_last,  # tanggal terakhir yang PALING DEKAT di antara indikator
        "days_left": (date.fromisoformat(earliest_last) - today).days if earliest_last else None,
        "reason": "; ".join(reasons) if reasons else None,
        "per_indicator": per,
    }


