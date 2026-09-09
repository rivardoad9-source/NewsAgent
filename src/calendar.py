"""
Kalender rilis makro AS berbasis FRED Release Calendar (endpoint
`/release/dates` - singular, yang TERBUKTI bersih; versi plural `/releases/dates`
mengembalikan noise harian untuk release yang punya banyak seri).

FRED hanya mengonfirmasi rilis ~2-4 minggu sebelumnya: `upcoming()` sering
kosong sampai BLS/Dept. of Labor mengunci tanggal. Itu bukan bug - dashboard
harus menampilkannya sebagai "belum dikonfirmasi FRED".

Jam rilis mengikuti konvensi per indikator (08:30 ET CPI/PPI/NFP/Unemployment/GDP,
14:00 ET FOMC) dan Eastern mengikuti DST - simpan & hitung dalam UTC, konversi
hanya untuk tampilan (lihat CLAUDE.md "Penanganan Waktu").

FOMC tidak ada di kalender ini: FRED tidak menerbitkan tanggal keputusan FOMC
sebagai release dengan observasi (DFF adalah seri harian). Jadwal FOMC ditangani
terpisah bila diperlukan.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()  # modul dipakai CLI & dashboard; dotenv tidak auto-load oleh Python

FRED_BASE_URL = "https://api.stlouisfed.org/fred"
DEFAULT_TIMEOUT = 20
ET = ZoneInfo("America/New_York")

# Release FRED per indikator (id sama dengan yang dipakai backtest + GDP).
RELEASE_IDS: dict[str, int] = {
    "CPI": 10,   # Consumer Price Index
    "PPI": 46,   # Producer Price Index
    "NFP": 50,   # Employment Situation
    "UNEMPLOYMENT": 50,  # Employment Situation (rilis yang sama dengan NFP)
    "GDP": 53,   # Gross Domestic Product
}

# Konvensi jam rilis (ET). GDP memakai 08:30 ET seperti rilis BEA lainnya.
RELEASE_TIME_ET: dict[str, time] = {
    "CPI": time(8, 30),
    "PPI": time(8, 30),
    "NFP": time(8, 30),
    "UNEMPLOYMENT": time(8, 30),
    "GDP": time(8, 30),
    "FOMC": time(14, 0),  # statement 14:00 ET (press conference 14:30)
}

# Jadwal rilis RESMI BLS 2026 (sumber: https://www.bls.gov/schedule/ — BLS
# menerbitkan jadwal SETAHUN PENUH di muka, diperbarui bila perlu).
#
# MENGAPA INI ADA: FRED `/release/dates` ternyata HANYA memuat rilis yang SUDAH
# TERJADI (database historis) — terbukti 9 Sep 2026: PPI 10 Sep & CPI 11 Sep
# (data Agustus) sudah diumumkan BLS berhari-hari sebelumnya tetapi TIDAK muncul
# di FRED. `upcoming()` yang hanya mengandalkan FRED karena itu SELALU kosong
# menjelang rilis (false negative H-1 yang berbahaya). Jadwal BLS = sumber
# kebenaran forward-looking; FRED hanya cadangan/verifikasi historis.
#
# NOTE: perbarui setiap awal tahun dari bls.gov/schedule (open work: auto-fetch).
BLS_SCHEDULE: dict[str, list[str]] = {
    # CPI 2026 (data -> rilis): Jan 13, Feb 13, Mar 11, Apr 10, May 12, Jun 10,
    # Jul 14, Aug 12, Sep 11, Oct 14, Nov 10, Dec 10
    "CPI": [
        "2026-01-13", "2026-02-13", "2026-03-11", "2026-04-10",
        "2026-05-12", "2026-06-10", "2026-07-14", "2026-08-12",
        "2026-09-11", "2026-10-14", "2026-11-10", "2026-12-10",
    ],
    # PPI 2026: Jun 11, Jul 15, Aug 13, Sep 10, Oct 15, Nov 13, Dec 15
    "PPI": [
        "2026-06-11", "2026-07-15", "2026-08-13", "2026-09-10",
        "2026-10-15", "2026-11-13", "2026-12-15",
    ],
    # Employment Situation (NFP + Unemployment, rilis yang sama): Jumat pertama.
    # Jan 9, Feb 6, Mar 6, Apr 3, May 8, Jun 5, Jul 2, Aug 7, Sep 4,
    # Oct 2, Nov 6, Dec 4
    "NFP": [
        "2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03",
        "2026-05-08", "2026-06-05", "2026-07-02", "2026-08-07",
        "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
    ],
    "UNEMPLOYMENT": [
        "2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03",
        "2026-05-08", "2026-06-05", "2026-07-02", "2026-08-07",
        "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
    ],
    # GDP: jadwal BEA (bukan BLS) — advance/second/third estimate per kuartal.
    # Sumber: https://www.bea.gov/news/schedule (2026; Q4'25 sempat di-reschedule
    # dari 29 Jan ke 20 Feb krn ketersediaan data — BEA mengumumkan per kuartal).
    # Catatan: yg paling market-moving = ADVANCE estimate (30 hari setelah kuartal
    # berakhir); second/third = revisi, dampak lebih kecil.
    "GDP": [
        "2026-02-20",  # Q4'25 Advance (rescheduled)
        "2026-03-13",  # Q4'25 Second
        "2026-04-09",  # Q4'25 Third
        "2026-04-30",  # Q1'26 Advance
        "2026-05-28",  # Q1'26 Second
        "2026-06-25",  # Q1'26 Third
        "2026-07-30",  # Q2'26 Advance
        "2026-08-26",  # Q2'26 Second
        "2026-09-30",  # Q2'26 Third
        "2026-10-29",  # Q3'26 Advance  <-- market-moving
        "2026-11-25",  # Q3'26 Second
        "2026-12-23",  # Q3'26 Third
    ],
}

# Jadwal FOMC 2026 (sumber: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm).
# Tanggal di sini = hari KEPUTUSAN (hari ke-2 meeting), statement 14:00 ET,
# press conference 14:30 ET. Meeting yg ada (*) menyertakan SEP + dot plot:
# Mar 17-18, Jun 16-17, Sep 15-16, Dec 8-9. FOMC TIDAK ada di FRED (bukan
# release-with-data) → murni kalender, tidak di-auto-monitor tick.py.
FOMC_SCHEDULE: list[str] = [
    "2026-01-28",  # Jan 27-28
    "2026-03-18",  # Mar 17-18  (SEP)
    "2026-04-29",  # Apr 28-29
    "2026-06-17",  # Jun 16-17  (SEP)
    "2026-07-29",  # Jul 28-29
    "2026-09-16",  # Sep 15-16  (SEP)  <-- berikutnya
    "2026-10-28",  # Oct 27-28
    "2026-12-09",  # Dec 8-9    (SEP)
]

# Indikator kalender yang TIDAK punya FRED release id (tidak di auto-monitor
# tick.py, murni jadwal). Dipakai upcoming() untuk tahu kandidat mana yang
# dilayani oleh FOMC_SCHEDULE.
FOMC_INDICATOR_KEYS: tuple[str, ...] = ("FOMC",)

EVENT_LABEL: dict[str, str] = {
    "CPI": "US CPI YoY",
    "PPI": "US PPI YoY",
    "NFP": "US Non-Farm Payrolls",
    "UNEMPLOYMENT": "US Unemployment Rate",
    "GDP": "US GDP Growth",
    "FOMC": "FOMC Rate Decision",
}

IMPACT: dict[str, str] = {
    "CPI": "HIGH",
    "PPI": "HIGH",
    "NFP": "HIGH",
    "UNEMPLOYMENT": "HIGH",
    "GDP": "MED",
    "FOMC": "HIGH",
}

# Seri FRED yang dipakai untuk memverifikasi "data baru sudah keluar".
SERIES_BY_INDICATOR: dict[str, str] = {
    "CPI": "CPIAUCSL",
    "PPI": "PPIFIS",
    "NFP": "PAYEMS",
    "UNEMPLOYMENT": "UNRATE",
    "GDP": "A191RL1Q225SBEA",
}

WIB = ZoneInfo("Asia/Jakarta")


@dataclass(frozen=True)
class ScheduledRelease:
    indicator: str
    release_date: date
    release_utc: datetime
    confirmed: bool  # True = tanggal dikonfirmasi FRED; False = estimasi


def _api_key() -> Optional[str]:
    return os.getenv("FRED_API_KEY") or None


def _release_dates(indicator: str, start: date, end: date) -> list[date]:
    """Tanggal publikasi SESUNGGUHNYA (endpoint /release/dates, pola backtest).

    Mengembalikan list kosong saat gagal / tidak dikonfigurasi - kalender adalah
    fasilitas, bukan penghambat.
    """
    key = _api_key()
    release_id = RELEASE_IDS.get(str(indicator).strip().upper())
    if not key or release_id is None:
        return []
    try:
        resp = requests.get(
            f"{FRED_BASE_URL}/release/dates",
            params={
                "release_id": str(release_id),
                "api_key": key,
                "file_type": "json",
                "realtime_start": start.isoformat(),
                "realtime_end": end.isoformat(),
                "limit": 1000,
                "sort_order": "asc",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        return sorted(
            {
                date.fromisoformat(row["date"])
                for row in resp.json().get("release_dates", [])
                if row.get("date")
            }
        )
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return []


def last_release_date(indicator: str, now: Optional[datetime] = None) -> Optional[date]:
    """Tanggal rilis terakhir yang sudah dikonfirmasi FRED untuk indikator.

    Inilah tanggal publikasi data terbaru - dipakai tick.py untuk mengukur
    pergerakan harga "saat news" dari momen yang benar (bukan dari label bulan
    observasi FRED, yang untuk CPI bisa tertinggal ~6 minggu).
    """
    now = now or datetime.now(timezone.utc)
    # Cari 400 hari ke belakang: menutupi GDP kuartalan sekalipun ada jeda rilis.
    start = now.date() - timedelta(days=400)
    dates = _release_dates(indicator, start, now.date())
    return dates[-1] if dates else None


def last_releases(now: Optional[datetime] = None) -> list[dict]:
    """Ringkasan rilis terakhir tiap indikator untuk dashboard (tanpa fetch seri)."""
    out = []
    for indicator in RELEASE_IDS:
        rd = last_release_date(indicator, now)
        out.append(
            {
                "indicator": indicator,
                "label": EVENT_LABEL.get(indicator, indicator),
                "release_date": rd,
                "impact": IMPACT.get(indicator),
                "confirmed": rd is not None,
            }
        )
    return out


def upcoming(horizon_days: int = 90, now: Optional[datetime] = None) -> list[ScheduledRelease]:
    """Rilis masa depan yang SUDAH dikonfirmasi (sering kosong - wajar).

    Sumber GANDA (lihat BLS_SCHEDULE di atas untuk konteks kenapa):
    1. `BLS_SCHEDULE` — jadwal resmi BLS, diterbitkan setahun penuh di muka.
       Ini sumber UTAMA untuk rilis mendatang (CPI/PPI/NFP/UNEMPLOYMENT).
    2. `_release_dates()` (FRED) — fallback: FRED hanya memuat rilis yang SUDAH
       TERJADI, jadi praktis tidak pernah menyumbang untuk masa depan, tapi
       dipertahankan sebagai jaring pengaman bila jadwal BLS belum diisi
       (mis. GDP, atau tahun berjalan yang belum di-update).
    """
    now = now or datetime.now(timezone.utc)
    end = now.date() + timedelta(days=horizon_days)
    out: list[ScheduledRelease] = []

    # Gabungkan semua indikator: RELEASE_IDS (BLS+BEA via BLS_SCHEDULE, punya FRED
    # id) + FOMC (murni kalender Fed, tanpa FRED id).
    all_indicators = set(RELEASE_IDS) | set(FOMC_INDICATOR_KEYS)

    for indicator in sorted(all_indicators):
        candidates: set[date] = set()
        # Sumber 1: jadwal resmi (BLS/BEA + FOMC), forward-looking.
        for iso in BLS_SCHEDULE.get(indicator, []):
            try:
                candidates.add(date.fromisoformat(iso))
            except ValueError:
                continue
        if indicator == "FOMC":
            for iso in FOMC_SCHEDULE:
                try:
                    candidates.add(date.fromisoformat(iso))
                except ValueError:
                    continue
        # Sumber 2: FRED (historis — jarang menyumbang untuk masa depan).
        candidates.update(_release_dates(indicator, now.date(), end))

        future = sorted(rd for rd in candidates if rd >= now.date() and rd <= end)
        if not future:
            continue
        # Hanya rilis TERDEKAT per indikator (perilaku lama dipertahankan).
        rd = future[0]
        out.append(
            ScheduledRelease(
                indicator=indicator,
                release_date=rd,
                release_utc=_to_utc(rd, indicator),
                confirmed=True,  # jadwal resmi (BLS/BEA/Fed); FRED = sudah terjadi
            )
        )
    return sorted(out, key=lambda r: r.release_utc)


def _to_utc(release_date: date, indicator: str) -> datetime:
    when_et = datetime.combine(
        release_date, RELEASE_TIME_ET.get(indicator, time(8, 30)), tzinfo=ET
    )
    return when_et.astimezone(timezone.utc)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()  # CLI berjalan di luar Streamlit; dotenv tidak auto-load

    print("== RILIS TERAKHIR (dikonfirmasi FRED) ==")
    for row in last_releases():
        rd = row["release_date"]
        print(f"  {row['indicator']:12} {rd.isoformat() if rd else '(belum ada)'}")
    print("== RILIS BERIKUTNYA (hanya yang sudah dikonfirmasi FRED) ==")
    for rel in upcoming():
        wib = rel.release_utc.astimezone(WIB)
        print(
            f"  {rel.indicator:12} {rel.release_date}  "
            f"UTC {rel.release_utc:%Y-%m-%d %H:%M}  WIB {wib:%Y-%m-%d %H:%M}"
        )
    if not upcoming():
        print("  (FRED belum konfirmasi rilis mendatang - normal 2-4 minggu sebelum H-1)")
