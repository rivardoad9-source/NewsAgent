"""
MacroAI Agent - Binance Public Market Data
==========================================

Sumber harga INTRADAY. Ini yang membuka pengukuran T+15m, T+1h, dan T+4h yang
diminta PRD - FMP pada paket saat ini hanya menyediakan EOD harian, dan seluruh
endpoint intraday-nya terkunci (HTTP 402).

Host: data-api.binance.vision (endpoint data publik Binance, TANPA API key).
Diverifikasi 2026-09-07: klines 15m tersedia sejak 2019 untuk BTCUSDT
(candle 2019-01-11 13:30 UTC menutup di 3583.00).

Catatan host: api.binance.com tidak dapat dijangkau dari mesin ini (curl 000),
sedangkan data-api.binance.vision menjawab 200. Jangan diganti tanpa diuji.

CARA MENGUKUR JENDELA
Seluruh horizon dihitung dari candle 15m, bukan dari candle 1h/4h bawaan.
Alasannya: candle 1h dan 4h menempel pada batas jamnya sendiri (14:00, 16:00),
sehingga "1 jam setelah rilis 08:30 ET" tidak sama dengan satu candle 1h. Dengan
15m sebagai satuan, T+15m = 1 candle, T+1h = 4 candle, T+4h = 16 candle dihitung
dari waktu rilis - presisi dan konsisten.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

BASE_URL = "https://data-api.binance.vision/api/v3"
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "binance"

# Simbol Binance untuk aset yang dipantau.
SYMBOLS = {
    "BTC/USD": "BTCUSDT",
    "ETH/USD": "ETHUSDT",
}

# Berapa candle 15m untuk tiap horizon.
HORIZON_CANDLES = {"15m": 1, "1h": 4, "4h": 16}


@dataclass
class KlineResponse:
    ok: bool
    status: str            # OK | NETWORK_ERROR | EMPTY | UNKNOWN_SYMBOL
    candles: list = None   # [[open_ms, o, h, l, c, v, ...]]
    detail: str = ""

    def __post_init__(self):
        if self.candles is None:
            self.candles = []


def _cache_path(symbol: str, start_ms: int, limit: int) -> Path:
    return CACHE_DIR / symbol / f"{start_ms}_{limit}.json"


def get_klines_15m(
    symbol: str,
    start_ms: int,
    limit: int = 20,
    use_cache: bool = True,
    throttle: float = 0.12,
) -> KlineResponse:
    """
    Mengambil candle 15m mulai dari `start_ms` (epoch milidetik UTC).

    Hasil di-cache ke disk karena backtest memanggil endpoint ini ratusan kali
    untuk rentang waktu yang tidak berubah - data historis bersifat tetap, jadi
    mengambilnya ulang hanya membuang kuota.
    """
    path = _cache_path(symbol, start_ms, limit)
    if use_cache and path.exists():
        try:
            cached = json.loads(path.read_text())
            # Cache yang lebih pendek dari `limit` adalah respons PARSIAL (diambil
            # sebelum candle-candle berikutnya terbentuk). Memakainya membuat horizon
            # 1h/4h null selamanya - jadi dianggap miss dan diambil ulang.
            if isinstance(cached, list) and len(cached) >= limit:
                return KlineResponse(True, "OK", cached, "dari cache")
        except (ValueError, OSError):
            pass  # cache rusak - ambil ulang

    url = (
        f"{BASE_URL}/klines?symbol={symbol}&interval=15m"
        f"&startTime={start_ms}&limit={limit}"
    )
    try:
        with urllib.request.urlopen(url, timeout=25) as response:
            candles = json.load(response)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return KlineResponse(False, "NETWORK_ERROR", detail=f"{type(exc).__name__}: {exc}")
    except ValueError as exc:
        return KlineResponse(False, "NETWORK_ERROR", detail=f"JSON tidak valid: {exc}")

    if not candles:
        return KlineResponse(False, "EMPTY", detail="Tidak ada candle pada rentang itu.")

    # Hanya respons LENGKAP yang di-cache: data historis tetap, tapi jendela yang
    # belum selesai (rilis baru) masih akan bertambah candle-nya.
    if use_cache and len(candles) >= limit:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(candles))
        except OSError:
            pass  # cache gagal ditulis bukan alasan menggagalkan permintaan

    if throttle:
        time.sleep(throttle)  # menjaga jarak dari rate limit Binance
    return KlineResponse(True, "OK", candles)


def returns_after(
    asset: str,
    release_utc: datetime,
    horizons: Optional[list] = None,
) -> dict:
    """
    Return persen pada tiap horizon setelah `release_utc`.

    Basis harga adalah CLOSE candle 15m TEPAT SEBELUM rilis - itu harga terakhir
    yang sudah diketahui pasar saat angka keluar. Mengukur dari candle rilis
    sendiri akan menelan sebagian gerakan yang justru ingin diukur.

    Nilai None berarti candle tidak tersedia. Tidak pernah diisi nol: rilis yang
    datanya bolong harus terlihat bolong, bukan tampak sebagai "tidak bergerak".
    """
    horizons = horizons or ["15m", "1h", "4h"]
    symbol = SYMBOLS.get(asset)
    if symbol is None:
        return {h: None for h in horizons}

    # Mulai satu candle sebelum rilis supaya harga basis ikut terambil.
    start = release_utc.replace(second=0, microsecond=0)
    start -= timedelta(minutes=start.minute % 15)
    start_ms = int((start - timedelta(minutes=15)).timestamp() * 1000)

    needed = max(HORIZON_CANDLES[h] for h in horizons) + 2
    res = get_klines_15m(symbol, start_ms, limit=needed)
    if not res.ok or len(res.candles) < 2:
        return {h: None for h in horizons}

    try:
        base = float(res.candles[0][4])  # close candle sebelum rilis
    except (IndexError, TypeError, ValueError):
        return {h: None for h in horizons}
    if not base:
        return {h: None for h in horizons}

    out = {}
    for horizon in horizons:
        idx = HORIZON_CANDLES[horizon]  # candle ke-idx setelah candle basis
        if idx < len(res.candles):
            try:
                out[horizon] = round((float(res.candles[idx][4]) / base - 1.0) * 100.0, 4)
            except (TypeError, ValueError):
                out[horizon] = None
        else:
            out[horizon] = None
    return out


def health_check() -> dict:
    """Cek ketersediaan endpoint publik. Tidak butuh kredensial."""
    now_ms = int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000)
    res = get_klines_15m("BTCUSDT", now_ms, limit=2, use_cache=False, throttle=0)
    return {
        "ok": res.ok,
        "status": res.status,
        "detail": res.detail or f"{len(res.candles)} candle.",
        "requires_key": False,
        "host": "data-api.binance.vision",
    }
