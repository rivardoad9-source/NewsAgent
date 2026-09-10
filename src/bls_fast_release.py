"""Jalur CEPAT hasil rilis makro dari API resmi BLS.

MASALAH YANG DISELESAIKAN (10 Sep 2026). Alert Format-3 normal dipicu oleh observasi
BARU di FRED, dan FRED sering telat: pada rilis PPI 19:30 WIB, FRED masih mengembalikan
observasi Juli 46 menit setelah rilis, jadi operator menunggu tanpa kabar
("hasil news belum dikirim ke gw") padahal angkanya sudah publik sejak menit pertama.

SUMBERNYA API RESMI BLS, BUKAN SCRAPING. www.bls.gov mengembalikan 403 dari server ini
(halaman rilis maupun RSS-nya; diuji 10 Sep 2026), sementara api.bls.gov/publicAPI/v1
melayani seri yang sama tanpa API key dan SUDAH memuat periode baru saat rilis — PPI
Agustus 2026 (WPSFD4) terbaca 157.411 tepat setelah rilis, 157.411/156.784 − 1 = +0,4%
MoM, sama dengan headline BLS. Jadi ini sumber primer yang sama, bukan pihak ketiga.

MELENGKAPI JALUR FRED, TIDAK MENGGANTIKANNYA. Modul ini hanya MENDETEKSI dan menghitung;
pengiriman dan pencatatan tetap di `scripts/tick.py` supaya bentuk alert, penjelasan dan
feed belajar tidak punya dua implementasi:
  - jalur cepat mengirim dalam ~5 menit (tick cron 5 menit) dan mencatat event dengan
    penanda `source: "bls-api-fast"` + seri asalnya;
  - jalur FRED tetap jadi jaring pengaman: kalau API BLS telat/gagal, alert tetap keluar
    begitu FRED menyusul. `process_indicator` melewati event yang sudah dicatat jalur
    cepat (dedupe per indikator + obs_date) supaya tidak ada dua alert untuk satu rilis.

SEMUA FUNGSI DI ATAS `fetch_observations` MURNI (tanpa jaringan, tanpa jam global) supaya
setiap cabangnya bisa diuji offline di `tests/test_bls_fast_release.py`.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

BLS_API_ROOT = "https://api.bls.gov/publicAPI/v1/timeseries/data"

# BLS memblokir User-Agent bawaan urllib dengan 403; UA browser biasa dilayani.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0 Safari/537.36"
)

# Seberapa lama setelah momen rilis jalur cepat masih boleh menembak. Rilis BLS 08:30 ET
# dan tick cron jalan tiap 5 menit, jadi kondisi normal = < 5 menit. 120 menit memberi
# ruang untuk API yang telat atau server yang baru hidup, tanpa membuat rilis kemarin
# ikut terkirim ulang sebagai "baru".
FAST_RELEASE_HORIZON_MIN = 120

# Berapa tahun ke belakang diambil. Dua tahun cukup untuk YoY bulan ini DAN YoY bulan
# sebelumnya (yang keduanya dibutuhkan untuk delta).
FETCH_YEARS_BACK = 2


@dataclass(frozen=True)
class SeriesSpec:
    """Seri BLS + cara membacanya, per indikator yang dipantau."""

    series_id: str
    # "index"       -> headline dihitung YoY (CPI/PPI), MoM ikut dilaporkan sebagai info
    # "level_delta" -> headline = selisih level bulan ini vs bulan lalu (NFP, ribuan)
    # "rate"        -> headline = levelnya sendiri (tingkat pengangguran, %)
    kind: str
    label: str
    unit: str


SERIES: dict[str, SeriesSpec] = {
    "CPI": SeriesSpec("CUSR0000SA0", "index", "US CPI (SA)", "% YoY"),
    "PPI": SeriesSpec("WPSFD4", "index", "US PPI Final Demand (SA)", "% YoY"),
    "NFP": SeriesSpec("CES0000000001", "level_delta", "US Non-Farm Payrolls", "ribu"),
    "UNEMPLOYMENT": SeriesSpec("LNS14000000", "rate", "US Unemployment Rate", "%"),
}


class BlsFetchError(RuntimeError):
    """API BLS tidak melayani seri yang diminta."""


def _minus_year(period: str) -> str:
    """`2026-08` -> `2025-08`."""
    year, month = period.split("-")
    return f"{int(year) - 1}-{month}"


def parse_observations(payload: Any) -> list[dict]:
    """Payload BLS -> observasi bulanan terurut dari yang terbaru.

    MURNI. Melewati periode yang bukan bulanan (BLS memakai `M13` untuk rata-rata
    tahunan) dan nilai yang bukan angka (BLS memakai `-` untuk data yang tidak
    tersedia) — keduanya bukan rilis, dan menebak nilainya adalah cara sebuah modul
    mengirim angka karangan.
    """
    series = ((payload or {}).get("Results") or {}).get("series") or []
    if not series:
        return []

    rows: list[dict] = []
    for record in series[0].get("data") or []:
        period = str(record.get("period") or "")
        year = str(record.get("year") or "")
        if not period.startswith("M") or not year.isdigit():
            continue
        if not period[1:].isdigit() or not 1 <= int(period[1:]) <= 12:
            continue
        try:
            value = float(record["value"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(
            {
                "period": f"{year}-{int(period[1:]):02d}",
                "value": value,
                "latest": str(record.get("latest", "")).lower() == "true",
            }
        )

    rows.sort(key=lambda row: row["period"], reverse=True)
    return rows


def compute_headline(kind: str, observations: list[dict]) -> Optional[dict]:
    """Angka headline dari observasi. MURNI; None kalau datanya belum cukup.

    `delta` selalu selisih headline bulan ini vs bulan lalu, persis semantik jalur FRED
    (`actual - previous`), karena nilai itulah yang masuk ke `get_verdict`.
    """
    if len(observations) < 2:
        return None

    latest, prior = observations[0], observations[1]

    if kind == "rate":
        return {
            "period": latest["period"],
            "actual": latest["value"],
            "previous": prior["value"],
            "delta": round(latest["value"] - prior["value"], 2),
            "mom": None,
        }

    if kind == "level_delta":
        if len(observations) < 3:
            return None
        prior_prior = observations[2]
        actual = latest["value"] - prior["value"]
        previous = prior["value"] - prior_prior["value"]
        return {
            "period": latest["period"],
            "actual": actual,
            "previous": previous,
            "delta": round(actual - previous, 4),
            "mom": None,
        }

    if kind != "index":
        raise ValueError(f"kind tidak dikenal: {kind!r}")

    # YoY butuh bulan yang sama setahun sebelumnya, untuk bulan ini DAN bulan lalu.
    year_ago = next((r for r in observations if r["period"] == _minus_year(latest["period"])), None)
    prior_year_ago = next(
        (r for r in observations if r["period"] == _minus_year(prior["period"])), None
    )
    if year_ago is None or prior_year_ago is None:
        return None

    yoy_latest = (latest["value"] / year_ago["value"] - 1) * 100
    yoy_prior = (prior["value"] / prior_year_ago["value"] - 1) * 100
    mom = (latest["value"] / prior["value"] - 1) * 100

    return {
        "period": latest["period"],
        "actual": round(yoy_latest, 2),
        "previous": round(yoy_prior, 2),
        "delta": round(yoy_latest - yoy_prior, 2),
        # MoM bukan yang masuk verdict (jalur FRED memakai YoY), tapi ini angka yang
        # dibandingkan pasar, jadi ikut dilaporkan di teks.
        "mom": round(mom, 2),
    }


def period_matches_release(period: str, release_date: date) -> bool:
    """Apakah `period` (YYYY-MM) memang periode yang dirilis pada `release_date`?

    Rilis BLS membawa data BULAN SEBELUMNYA (rilis 10 Sep 2026 = data Agustus), jadi
    perbedaannya harus 1 bulan — 2 bulan masih diterima untuk rilis yang tergeser
    (libur/penundaan). Ini yang menahan modul mengirim ulang data lama ketika API
    memuat periode yang sudah lewat: periode yang jauh dari tanggal rilis ditolak.
    """
    try:
        year, month = (int(part) for part in period.split("-"))
    except ValueError:
        return False

    diff = (release_date.year - year) * 12 + (release_date.month - month)
    return 1 <= diff <= 2


def within_release_window(
    now: datetime, release_utc: datetime, horizon_min: int = FAST_RELEASE_HORIZON_MIN
) -> bool:
    """Apakah `now` berada di (momen rilis, momen rilis + horizon]. MURNI."""
    return release_utc <= now <= release_utc + timedelta(minutes=horizon_min)


def latest_scheduled_date(schedule: list[str], on_date: date) -> Optional[date]:
    """Tanggal rilis TERAKHIR yang sudah dijadwalkan pada atau sebelum `on_date`.

    MURNI. Jadwal forward (BLS_SCHEDULE) yang dipakai, BUKAN tanggal yang sudah
    dikonfirmasi FRED: pada rilis PPI 10 Sep 2026, `calendar.last_release_date()` masih
    menjawab 13 Agustus (rilis terakhir yang diakui FRED) — persis keterlambatan yang
    jalur cepat ini ada untuk menutupinya. Entri jadwal yang tidak terbaca dilewati,
    bukan ditebak.
    """
    parsed: list[date] = []
    for raw in schedule:
        try:
            parsed.append(date.fromisoformat(str(raw).strip()))
        except ValueError:
            continue
    past = [d for d in parsed if d <= on_date]
    return max(past) if past else None


def fetch_observations(
    series_id: str,
    now: datetime,
    opener: Callable[..., Any] = urllib.request.urlopen,
    years_back: int = FETCH_YEARS_BACK,
) -> list[dict]:
    """Ambil observasi bulanan seri BLS. Melempar `BlsFetchError` kalau gagal.

    Melempar, bukan mengembalikan [] — pemanggil harus bisa membedakan "API tidak
    melayani" dari "seri kosong", dan tick mencetak alasannya.
    """
    url = (
        f"{BLS_API_ROOT}/{series_id}"
        f"?startyear={now.year - years_back}&endyear={now.year}"
    )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=30) as response:
            payload = json.load(response)
    except Exception as exc:  # noqa: BLE001 - apa pun kegagalannya, ini bukan rilis
        raise BlsFetchError(f"{type(exc).__name__}: {exc}") from exc

    status = str(payload.get("status") or "")
    if status and status.upper() != "REQUEST_SUCCEEDED":
        raise BlsFetchError(f"status BLS {status}: {payload.get('message') or ''}".strip())

    return parse_observations(payload)


def detect_fast_release(
    indicator: str,
    state: dict,
    now: Optional[datetime] = None,
    *,
    fetcher: Callable[[str, datetime], list[dict]] = fetch_observations,
    release_date_for: Optional[Callable[[str, datetime], Optional[date]]] = None,
) -> Optional[dict]:
    """Deteksi rilis yang belum pernah dikirim jalur cepat, siap dipakai tick.

    Mengembalikan dict berisi headline + momen rilis + seri asal, atau None. Semua
    syarat harus lolos: seri dikenal, datanya cukup, periodenya memang periode yang
    dirilis, momen rilisnya baru saja terjadi (dalam horizon), dan belum pernah
    dikirim (`state["fast_sent"]`).
    """
    spec = SERIES.get(indicator)
    if spec is None:
        return None

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # Diimpor di dalam fungsi supaya modul ini tetap bisa diuji tanpa menyentuh FRED /
    # dotenv; `src.calendar` juga yang memegang jadwal BLS forward-looking.
    from src import calendar as _calendar  # noqa: PLC0415

    if release_date_for is None:

        def _default_resolver(ind: str, moment: datetime) -> Optional[date]:
            today = moment.astimezone(timezone.utc).date()
            scheduled = latest_scheduled_date(_calendar.BLS_SCHEDULE.get(ind, []), today)
            if scheduled is not None:
                return scheduled
            # Cadangan: kalau indikatornya tidak ada di jadwal BLS (mis. sumber lain),
            # pakai tanggal rilis terakhir yang dikonfirmasi FRED.
            return _calendar.last_release_date(ind, moment)

        resolve = _default_resolver
    else:
        resolve = release_date_for

    release_date = resolve(indicator, now)
    if release_date is None:
        return None

    release_utc = _calendar._to_utc(release_date, indicator)
    if release_utc.tzinfo is None:
        release_utc = release_utc.replace(tzinfo=timezone.utc)
    if not within_release_window(now, release_utc):
        return None

    observations = fetcher(spec.series_id, now)
    headline = compute_headline(spec.kind, observations)
    if headline is None:
        return None

    if not period_matches_release(headline["period"], release_date):
        return None

    if state.get("fast_sent", {}).get(indicator) == headline["period"]:
        return None

    return {
        "indicator": indicator,
        "label": spec.label,
        "unit": spec.unit,
        "series": spec.series_id,
        "headline": headline,
        "release_date": release_date,
        "release_utc": release_utc,
    }
