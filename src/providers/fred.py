"""
MacroAI Agent - FRED Adapter (Federal Reserve Bank of St. Louis)
================================================================

HASIL VERIFIKASI LANGSUNG pada 2026-09-07 - setiap series di bawah diuji dengan
permintaan sungguhan, bukan disalin dari dokumentasi:

    series             terbaru       total obs   dipakai untuk
    -----------------  ------------  ---------   ----------------------------
    CPIAUCSL           2026-07-01    955         CPI (indeks -> YoY dihitung)
    PPIFIS             2026-07-01    201         PPI (indeks -> YoY dihitung)
    PAYEMS             2026-08-01    1052        NFP (level -> selisih bulanan)
    UNRATE             2026-08-01    944         Unemployment Rate
    DFF                2026-09-03    26363       Fed Funds (rezim kebijakan)
    A191RL1Q225SBEA    2026-04-01    317         GDP growth QoQ annualized
    DTWEXBGS           2026-08-28    5390        Dollar index (broad)
    DGS10              2026-09-03    16873       US 10Y Treasury yield

APA YANG FRED BISA DAN TIDAK BISA:

FRED menyimpan angka AKTUAL yang sudah dirilis, dengan kedalaman puluhan tahun.
FRED TIDAK menerbitkan consensus/forecast sama sekali. Karena itu FRED bukan
pengganti economic-calendar untuk menghitung Surprise Delta - ia sumber jenis
lain. Modul ini dipakai untuk konteks tren jangka panjang, bukan untuk kejutan
jangka pendek.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.stlouisfed.org/fred"
DEFAULT_TIMEOUT = 20


class Transform:
    LEVEL = "LEVEL"        # pakai nilai apa adanya
    YOY_PCT = "YOY_PCT"    # persentase perubahan year-over-year dari indeks
    MOM_DIFF = "MOM_DIFF"  # selisih terhadap bulan sebelumnya


@dataclass(frozen=True)
class SeriesSpec:
    series_id: str
    transform: str
    unit: str
    periods_per_year: int
    label: str


# Pemetaan indikator MacroAI -> series FRED.
SERIES_MAP = {
    "CPI": SeriesSpec("CPIAUCSL", Transform.YOY_PCT, "%", 12, "US CPI YoY"),
    "PPI": SeriesSpec("PPIFIS", Transform.YOY_PCT, "%", 12, "US PPI YoY"),
    "NFP": SeriesSpec("PAYEMS", Transform.MOM_DIFF, "K", 12, "US Non-Farm Payrolls"),
    "UNEMPLOYMENT": SeriesSpec("UNRATE", Transform.LEVEL, "%", 12, "US Unemployment Rate"),
    "FOMC": SeriesSpec("DFF", Transform.LEVEL, "%", 12, "Effective Fed Funds Rate"),
    "GDP": SeriesSpec("A191RL1Q225SBEA", Transform.LEVEL, "%", 4, "US Real GDP Growth"),
}

# Series pendukung untuk konteks lintas aset.
DXY_SERIES = "DTWEXBGS"
US10Y_SERIES = "DGS10"


@dataclass
class FredResponse:
    ok: bool
    status: str          # OK | NOT_CONFIGURED | HTTP_ERROR | NETWORK_ERROR | EMPTY
    points: list = field(default_factory=list)   # [(date, float)] urut naik
    detail: str = ""
    latency_ms: Optional[int] = None

    @property
    def latest(self):
        return self.points[-1] if self.points else None


def get_api_key() -> Optional[str]:
    return os.getenv("FRED_API_KEY") or None


def is_configured() -> bool:
    return bool(get_api_key())


def get_observations(series_id: str, limit: int = 400) -> FredResponse:
    """
    Mengambil observasi terbaru satu series, diurutkan naik menurut tanggal.

    Nilai '.' pada FRED berarti tidak tersedia untuk tanggal itu (hari libur pada
    series harian). Titik seperti itu DIBUANG, bukan diisi nol - mengisi nol akan
    memalsukan pergerakan.
    """
    key = get_api_key()
    if not key:
        return FredResponse(False, "NOT_CONFIGURED", detail="FRED_API_KEY belum diisi di .env.")

    started = datetime.now()
    try:
        response = requests.get(
            f"{BASE_URL}/series/observations",
            params={
                "series_id": series_id,
                "api_key": key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": limit,
            },
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        return FredResponse(False, "NETWORK_ERROR", detail=f"{type(exc).__name__}: {exc}")

    latency_ms = int((datetime.now() - started).total_seconds() * 1000)

    if response.status_code != 200:
        return FredResponse(
            False, "HTTP_ERROR",
            detail=f"HTTP {response.status_code}: {response.text[:150]}",
            latency_ms=latency_ms,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        return FredResponse(False, "HTTP_ERROR", detail=f"JSON tidak valid: {exc}",
                            latency_ms=latency_ms)

    observations = payload.get("observations")
    if not observations:
        return FredResponse(False, "EMPTY", detail="Tidak ada observasi.", latency_ms=latency_ms)

    points = []
    for row in observations:
        raw = row.get("value")
        if raw in (None, ".", ""):
            continue  # tidak tersedia - dibuang, tidak diisi nol
        try:
            points.append((row["date"], float(raw)))
        except (KeyError, TypeError, ValueError):
            continue

    points.sort(key=lambda p: p[0])
    if not points:
        return FredResponse(False, "EMPTY", detail="Semua observasi kosong.", latency_ms=latency_ms)

    return FredResponse(True, "OK", points=points,
                        detail=f"{len(points)} observasi.", latency_ms=latency_ms)


def _apply_transform(points: list, spec: SeriesSpec) -> list:
    """Menerapkan transformasi agar satuannya cocok dengan INDICATOR_TABLE."""
    if spec.transform == Transform.LEVEL:
        return list(points)

    if spec.transform == Transform.YOY_PCT:
        lag = spec.periods_per_year
        out = []
        for i in range(lag, len(points)):
            prev = points[i - lag][1]
            if prev:
                out.append((points[i][0], (points[i][1] / prev - 1.0) * 100.0))
        return out

    if spec.transform == Transform.MOM_DIFF:
        # PAYEMS dilaporkan dalam ribuan pekerja, jadi selisihnya sudah dalam "K".
        return [(points[i][0], points[i][1] - points[i - 1][1]) for i in range(1, len(points))]

    return list(points)


def get_indicator_series(indicator: str, limit: int = 400) -> FredResponse:
    """Deret satu indikator MacroAI, sudah ditransformasi ke satuan yang dipakai engine."""
    spec = SERIES_MAP.get(str(indicator).strip().upper())
    if spec is None:
        return FredResponse(False, "HTTP_ERROR",
                            detail=f"Indikator '{indicator}' tidak dipetakan ke series FRED.")

    raw = get_observations(spec.series_id, limit=limit)
    if not raw.ok:
        return raw

    transformed = _apply_transform(raw.points, spec)
    if not transformed:
        return FredResponse(False, "EMPTY",
                            detail="Observasi tidak cukup untuk transformasi.",
                            latency_ms=raw.latency_ms)

    return FredResponse(True, "OK", points=transformed,
                        detail=f"{len(transformed)} titik setelah {spec.transform}.",
                        latency_ms=raw.latency_ms)


@dataclass
class Trend:
    """Arah pergerakan sebuah indikator selama jendela tertentu."""

    ok: bool
    slope_per_period: Optional[float] = None
    change_total: Optional[float] = None
    latest_value: Optional[float] = None
    latest_date: Optional[str] = None
    window: int = 0
    detail: str = ""

    @property
    def direction(self) -> int:
        """+1 naik, -1 turun, 0 datar/tidak tersedia."""
        if not self.ok or self.change_total is None:
            return 0
        return 1 if self.change_total > 0 else (-1 if self.change_total < 0 else 0)


def compute_trend(indicator: str, window: int = 6) -> Trend:
    """
    Menghitung tren satu indikator selama `window` periode terakhir.

    Memakai regresi linier sederhana pada titik yang tersedia. Bila titiknya
    kurang dari `window`, Trend dikembalikan dengan ok=False - lebih baik tidak
    ada tren daripada tren yang dihitung dari dua titik.
    """
    series = get_indicator_series(indicator, limit=400)
    if not series.ok:
        return Trend(ok=False, detail=series.detail)

    points = series.points[-window:]
    if len(points) < window:
        return Trend(ok=False, window=window,
                     detail=f"Hanya {len(points)} titik tersedia, butuh {window}.")

    values = [v for _, v in points]
    n = len(values)
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    slope = (
        sum((i - mean_x) * (values[i] - mean_y) for i in range(n)) / denominator
        if denominator
        else 0.0
    )

    return Trend(
        ok=True,
        slope_per_period=round(slope, 6),
        change_total=round(values[-1] - values[0], 6),
        latest_value=round(values[-1], 4),
        latest_date=points[-1][0],
        window=window,
        detail=f"{n} periode: {points[0][0]} -> {points[-1][0]}",
    )


def get_policy_rate_trend(window: int = 90) -> Trend:
    """
    Tren Fed Funds harian - dipakai sebagai overlay rezim kebijakan.

    Arah suku bunga acuan menentukan latar jangka panjang bagi seluruh risk
    assets, terlepas dari satu rilis data mana pun.
    """
    raw = get_observations(DFF_SERIES := "DFF", limit=max(window * 2, 200))
    if not raw.ok:
        return Trend(ok=False, detail=raw.detail)

    points = raw.points[-window:]
    if len(points) < 10:
        return Trend(ok=False, window=window, detail="Observasi tidak cukup.")

    values = [v for _, v in points]
    return Trend(
        ok=True,
        change_total=round(values[-1] - values[0], 4),
        latest_value=round(values[-1], 4),
        latest_date=points[-1][0],
        window=len(points),
        detail=f"{len(points)} hari: {points[0][0]} -> {points[-1][0]}",
    )


def health_check() -> dict:
    if not is_configured():
        return {"ok": False, "status": "NOT_CONFIGURED",
                "detail": "FRED_API_KEY belum diisi di .env.", "latency_ms": None}
    probe = get_observations(US10Y_SERIES, limit=1)
    return {
        "ok": probe.ok,
        "status": probe.status,
        "detail": probe.detail,
        "latency_ms": probe.latency_ms,
    }
