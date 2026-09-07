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
}

EVENT_LABEL: dict[str, str] = {
    "CPI": "US CPI YoY",
    "PPI": "US PPI YoY",
    "NFP": "US Non-Farm Payrolls",
    "UNEMPLOYMENT": "US Unemployment Rate",
    "GDP": "US GDP Growth",
}

IMPACT: dict[str, str] = {
    "CPI": "HIGH",
    "PPI": "HIGH",
    "NFP": "HIGH",
    "UNEMPLOYMENT": "HIGH",
    "GDP": "MED",
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
    """Rilis masa depan yang SUDAH dikonfirmasi FRED (sering kosong - wajar).

    FRED mengonfirmasi ~2-4 minggu sebelum hari-H. Dashboard menampilkan hasil
    fungsi ini apa adanya plus catatan "belum dikonfirmasi" untuk sisanya.
    """
    now = now or datetime.now(timezone.utc)
    end = now.date() + timedelta(days=horizon_days)
    out: list[ScheduledRelease] = []
    for indicator in RELEASE_IDS:
        dates = _release_dates(indicator, now.date(), end)
        for rd in dates:
            if rd >= now.date():
                out.append(
                    ScheduledRelease(
                        indicator=indicator,
                        release_date=rd,
                        release_utc=_to_utc(rd, indicator),
                        confirmed=True,
                    )
                )
                break  # hanya rilis terdekat per indikator
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
