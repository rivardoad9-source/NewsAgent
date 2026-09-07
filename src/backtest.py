"""
MacroAI Agent - Backtest Engine (Feature 3)
===========================================

Menghitung reaksi pasar terhadap rilis makro AS 2019-2026 dari DATA NYATA:

  * tanggal rilis  -> FRED release/dates  (tanggal publikasi sesungguhnya,
                      bukan tanggal periode acuan)
  * nilai aktual   -> FRED observations
  * harga          -> FMP historical-price-eod

========================= BATASAN YANG WAJIB DIBACA =========================

Modul ini BUKAN backtest yang diminta PRD, dan tidak boleh disebut demikian.
Ada dua penyimpangan metodologis yang mengubah arti angkanya:

1. SURPRISE DIUKUR TERHADAP FORECAST STATISTIK, BUKAN CONSENSUS PASAR.
   Consensus tidak tersedia di satu pun sumber yang kita punya (economic-calendar
   FMP terkunci di paket berbayar; FRED tidak menerbitkan forecast sama sekali).
   Sebagai pengganti dipakai forecast naif - random walk, yaitu nilai rilis
   sebelumnya. Ini ukuran yang berbeda: pasar sudah memperhitungkan banyak hal
   yang tidak diketahui model naif, sehingga "surprise" di sini cenderung lebih
   besar dan sinyalnya lebih lemah daripada surprise terhadap consensus.

2. RESOLUSI HARIAN, BUKAN T+15m.
   PRD mengukur reaksi pada T+15m. Harga yang tersedia hanya EOD harian, dan bar
   harian tidak bisa mengukur jendela 15 menit. Angka di sini adalah return
   hari-rilis penuh, yang mencampur reaksi rilis dengan seluruh berita lain
   sepanjang hari itu.

Konsekuensinya: hasil modul ini adalah BATAS BAWAH yang kasar untuk menguji
apakah arah pemetaan indikator masuk akal. Ia TIDAK boleh dipakai sebagai
win rate produk, dan tidak boleh dipakai untuk menetapkan ukuran posisi.
=============================================================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

from .providers import fmp, fred
from .rule_engine import INDICATOR_TABLE, Verdict, get_verdict

load_dotenv()

FRED_BASE = "https://api.stlouisfed.org/fred"

# Indikator -> release_id FRED (diverifikasi 2026-09-07).
RELEASE_IDS = {
    "CPI": 10,           # Consumer Price Index
    "PPI": 46,           # Producer Price Index
    "NFP": 50,           # Employment Situation
    "UNEMPLOYMENT": 50,  # Employment Situation (rilis yang sama dengan NFP)
}

ASSET_SYMBOLS = {
    "BTC/USD": "BTCUSD",
    "ETH/USD": "ETHUSD",
    "NASDAQ": "^IXIC",
}


@dataclass
class BacktestResult:
    ok: bool
    status: str
    rows: list = field(default_factory=list)
    detail: str = ""
    indicator: Optional[str] = None
    asset: Optional[str] = None

    @property
    def n(self) -> int:
        return len(self.rows)


def get_release_dates(indicator: str, start: str, end: str) -> list:
    """Tanggal publikasi sesungguhnya untuk satu indikator, urut naik."""
    key = fred.get_api_key()
    release_id = RELEASE_IDS.get(str(indicator).strip().upper())
    if not key or release_id is None:
        return []

    try:
        response = requests.get(
            f"{FRED_BASE}/release/dates",
            params={
                "release_id": release_id,
                "api_key": key,
                "file_type": "json",
                "realtime_start": start,
                "realtime_end": end,
                "limit": 1000,
                "sort_order": "asc",
            },
            timeout=25,
        )
        if response.status_code != 200:
            return []
        return [row["date"] for row in response.json().get("release_dates", [])]
    except (requests.RequestException, ValueError, KeyError):
        return []


def _price_map(symbol: str, start: str, end: str) -> dict:
    """{tanggal: harga penutupan} dari FMP."""
    res = fmp.get_historical_prices(symbol, start, end)
    if not res.ok:
        return {}
    out = {}
    for row in res.data:
        d, p = row.get("date"), row.get("price")
        if d and p is not None:
            out[str(d)[:10]] = float(p)
    return out


def _return_on(prices: dict, day: str) -> Optional[float]:
    """
    Return persen pada hari `day` terhadap hari perdagangan sebelumnya.

    Mengembalikan None bila harga hari itu atau pembandingnya tidak ada -
    misalnya rilis jatuh di hari libur bursa. Tidak pernah menebak dengan nol.
    """
    days = sorted(prices)
    if day not in prices:
        return None
    i = days.index(day)
    if i == 0:
        return None
    prev = prices[days[i - 1]]
    if not prev:
        return None
    return round((prices[day] / prev - 1.0) * 100.0, 4)


def run_backtest(
    indicator: str,
    asset: str = "BTC/USD",
    start: str = "2019-01-01",
    end: str = "2026-09-07",
) -> BacktestResult:
    """
    Menjalankan backtest satu indikator terhadap satu aset.

    Setiap baris hasil memuat: tanggal rilis, nilai aktual, forecast naif,
    surprise, verdict Rule Engine, return hari-rilis, dan apakah arah verdict
    sejalan dengan arah return.
    """
    key = str(indicator).strip().upper()
    spec = INDICATOR_TABLE.get(key)
    if spec is None:
        return BacktestResult(False, "UNKNOWN_INDICATOR",
                              detail=f"'{indicator}' tidak ada di interpretation table.")

    symbol = ASSET_SYMBOLS.get(asset)
    if symbol is None:
        return BacktestResult(False, "UNKNOWN_ASSET", detail=f"Aset '{asset}' tidak dikenal.")

    releases = get_release_dates(key, start, end)
    if not releases:
        return BacktestResult(False, "NO_RELEASE_DATES",
                              detail="Tanggal rilis tidak dapat diambil dari FRED.",
                              indicator=key, asset=asset)

    series = fred.get_indicator_series(key, limit=400)
    if not series.ok:
        return BacktestResult(False, "NO_SERIES", detail=series.detail,
                              indicator=key, asset=asset)

    prices = _price_map(symbol, start, end)
    if not prices:
        return BacktestResult(False, "NO_PRICES",
                              detail=f"Harga {symbol} tidak dapat diambil dari FMP.",
                              indicator=key, asset=asset)

    points = series.points  # [(periode, nilai)] urut naik
    rows = []

    for release_date in releases:
        # Observasi yang dipublikasikan pada tanggal ini adalah periode terakhir
        # yang berakhir SEBELUM tanggal rilis.
        published = [(d, v) for d, v in points if d < release_date]
        if len(published) < 2:
            continue

        period, actual = published[-1]
        forecast = published[-2][1]  # forecast naif: random walk (nilai sebelumnya)
        surprise = round(actual - forecast, 6)

        verdict_result = get_verdict(key, surprise)
        if verdict_result.verdict is None:
            continue

        ret = _return_on(prices, release_date)
        if ret is None:
            continue  # rilis jatuh di hari tanpa harga - dibuang, bukan diisi nol

        verdict = verdict_result.verdict.value
        if verdict == Verdict.NEUTRAL.value:
            aligned = None  # NEUTRAL tidak punya arah untuk dinilai
        else:
            aligned = (verdict == Verdict.BULLISH.value and ret > 0) or (
                verdict == Verdict.BEARISH.value and ret < 0
            )

        rows.append(
            {
                "release_date": release_date,
                "period": period,
                "actual": round(actual, 4),
                "naive_forecast": round(forecast, 4),
                "surprise": surprise,
                "verdict": verdict,
                "return_pct": ret,
                "aligned": aligned,
            }
        )

    if not rows:
        return BacktestResult(False, "NO_OVERLAP",
                              detail="Tidak ada tanggal rilis yang beririsan dengan data harga.",
                              indicator=key, asset=asset)

    return BacktestResult(True, "OK", rows=rows,
                          detail=f"{len(rows)} rilis dievaluasi.",
                          indicator=key, asset=asset)


def summarize(result: BacktestResult) -> dict:
    """
    Ringkasan statistik. `alignment_rate` SENGAJA tidak dinamai win rate:
    ia mengukur kesesuaian arah terhadap forecast naif pada resolusi harian,
    bukan performa strategi terhadap consensus pasar.
    """
    if not result.ok or not result.rows:
        return {
            "ok": False,
            "detail": result.detail,
            "alignment_rate": None,
            "n_directional": 0,
            "n_total": 0,
        }

    rows = result.rows
    directional = [r for r in rows if r["aligned"] is not None]
    hits = sum(1 for r in directional if r["aligned"])
    returns = [abs(r["return_pct"]) for r in rows]

    by_verdict = {}
    for verdict in ("BULLISH", "BEARISH", "NEUTRAL"):
        subset = [r for r in rows if r["verdict"] == verdict]
        sub_dir = [r for r in subset if r["aligned"] is not None]
        by_verdict[verdict] = {
            "n": len(subset),
            "alignment_rate": (
                round(sum(1 for r in sub_dir if r["aligned"]) / len(sub_dir) * 100, 1)
                if sub_dir else None
            ),
            "avg_abs_return": round(sum(abs(r["return_pct"]) for r in subset) / len(subset), 3)
            if subset else None,
        }

    return {
        "ok": True,
        "indicator": result.indicator,
        "asset": result.asset,
        "n_total": len(rows),
        "n_directional": len(directional),
        "alignment_rate": round(hits / len(directional) * 100, 1) if directional else None,
        "avg_abs_return": round(sum(returns) / len(returns), 3),
        "period": f"{rows[0]['release_date']} s/d {rows[-1]['release_date']}",
        "by_verdict": by_verdict,
        "methodology": (
            "Surprise diukur terhadap forecast naif (random walk), BUKAN consensus pasar. "
            "Return diukur pada resolusi harian (EOD), BUKAN T+15m. Angka ini batas bawah "
            "kasar untuk menguji arah pemetaan - bukan win rate produk."
        ),
    }

# ===========================================================================
# BACKTEST INTRADAY - jendela T+15m / T+1h / T+4h yang diminta PRD
# ===========================================================================

from datetime import datetime as _dt, time as _time, timezone as _timezone  # noqa: E402
from zoneinfo import ZoneInfo as _ZoneInfo  # noqa: E402

from .providers import binance as _binance  # noqa: E402

_ET = _ZoneInfo("America/New_York")

# Jam rilis resmi menurut zona Eastern. BLS merilis CPI, PPI, dan Employment
# Situation pukul 08:30 ET; FOMC mengumumkan keputusan pukul 14:00 ET.
# Disimpan dalam ET, BUKAN UTC: Eastern mengikuti DST, sehingga jam UTC-nya
# bergeser antara 12:30 dan 13:30 sepanjang tahun.
RELEASE_TIMES_ET = {
    "CPI": _time(8, 30),
    "PPI": _time(8, 30),
    "NFP": _time(8, 30),
    "UNEMPLOYMENT": _time(8, 30),
    "GDP": _time(8, 30),
    "FOMC": _time(14, 0),
}

HORIZONS = ["15m", "1h", "4h"]


def release_datetime_utc(indicator: str, release_date: str):
    """Menggabungkan tanggal rilis FRED dengan jam rilis resmi, lalu ke UTC."""
    t = RELEASE_TIMES_ET.get(str(indicator).strip().upper())
    if t is None:
        return None
    y, m, d = (int(x) for x in release_date.split("-"))
    return _dt(y, m, d, t.hour, t.minute, tzinfo=_ET).astimezone(_timezone.utc)


def run_intraday_backtest(
    indicator: str,
    asset: str = "BTC/USD",
    start: str = "2019-01-01",
    end: str = "2026-09-07",
) -> BacktestResult:
    """
    Backtest pada jendela intraday sesungguhnya.

    Sama seperti run_backtest untuk sisi makronya - surprise masih diukur
    terhadap forecast naif, karena consensus tetap tidak tersedia. Yang berubah
    adalah sisi harga: return diukur pada T+15m, T+1h, dan T+4h dari candle 15m
    Binance, bukan pada penutupan harian.
    """
    key = str(indicator).strip().upper()
    spec = INDICATOR_TABLE.get(key)
    if spec is None:
        return BacktestResult(False, "UNKNOWN_INDICATOR", detail=f"'{indicator}' tidak dikenal.")
    if asset not in _binance.SYMBOLS:
        return BacktestResult(False, "UNKNOWN_ASSET",
                              detail=f"Aset '{asset}' tidak ada di Binance ({list(_binance.SYMBOLS)}).")

    releases = get_release_dates(key, start, end)
    if not releases:
        return BacktestResult(False, "NO_RELEASE_DATES", detail="Tanggal rilis tidak terambil.",
                              indicator=key, asset=asset)

    series = fred.get_indicator_series(key, limit=400)
    if not series.ok:
        return BacktestResult(False, "NO_SERIES", detail=series.detail, indicator=key, asset=asset)

    points = series.points
    rows = []

    for release_date in releases:
        published = [(d, v) for d, v in points if d < release_date]
        if len(published) < 2:
            continue

        period, actual = published[-1]
        forecast = published[-2][1]
        surprise = round(actual - forecast, 6)

        verdict_result = get_verdict(key, surprise)
        if verdict_result.verdict is None:
            continue

        moment = release_datetime_utc(key, release_date)
        if moment is None:
            continue

        rets = _binance.returns_after(asset, moment, HORIZONS)
        if all(v is None for v in rets.values()):
            continue

        verdict = verdict_result.verdict.value
        row = {
            "release_date": release_date,
            "release_utc": moment.strftime("%Y-%m-%d %H:%M"),
            "period": period,
            "actual": round(actual, 4),
            "naive_forecast": round(forecast, 4),
            "surprise": surprise,
            "verdict": verdict,
        }
        for horizon in HORIZONS:
            ret = rets.get(horizon)
            row[f"ret_{horizon}"] = ret
            if ret is None or verdict == Verdict.NEUTRAL.value:
                row[f"aligned_{horizon}"] = None
            else:
                row[f"aligned_{horizon}"] = (
                    verdict == Verdict.BULLISH.value and ret > 0
                ) or (verdict == Verdict.BEARISH.value and ret < 0)
        rows.append(row)

    if not rows:
        return BacktestResult(False, "NO_OVERLAP", detail="Tidak ada rilis dengan data candle.",
                              indicator=key, asset=asset)

    return BacktestResult(True, "OK", rows=rows, detail=f"{len(rows)} rilis dievaluasi.",
                          indicator=key, asset=asset)


def summarize_intraday(result: BacktestResult) -> dict:
    """Ringkasan per horizon. Tetap dinamai alignment_rate, bukan win rate."""
    if not result.ok or not result.rows:
        return {"ok": False, "detail": result.detail}

    rows = result.rows
    out = {
        "ok": True,
        "indicator": result.indicator,
        "asset": result.asset,
        "n_total": len(rows),
        "period": f"{rows[0]['release_date']} s/d {rows[-1]['release_date']}",
        "horizons": {},
        "methodology": (
            "Surprise terhadap forecast naif (random walk), BUKAN consensus pasar. "
            "Harga intraday dari candle 15m Binance; basis = close candle sebelum rilis."
        ),
    }
    for horizon in HORIZONS:
        directional = [r for r in rows if r.get(f"aligned_{horizon}") is not None]
        hits = sum(1 for r in directional if r[f"aligned_{horizon}"])
        moves = [abs(r[f"ret_{horizon}"]) for r in rows if r.get(f"ret_{horizon}") is not None]
        out["horizons"][horizon] = {
            "n_directional": len(directional),
            "alignment_rate": round(hits / len(directional) * 100, 1) if directional else None,
            "avg_abs_move": round(sum(moves) / len(moves), 3) if moves else None,
        }
    return out
