"""
MacroAI Agent - Financial Modeling Prep (FMP) Adapter
=====================================================

HASIL VERIFIKASI LANGSUNG terhadap key pada 2026-09-07. Bukan tebakan dari
dokumentasi - setiap baris di bawah diuji dengan permintaan sungguhan.

    endpoint                     status   rentang yang dikembalikan
    --------------------------   ------   ---------------------------------
    economic-calendar            402      DIBLOKIR - butuh paket berbayar
    api/v3/economic_calendar     403      mati (legacy, sejak 2025-08-31)
    economic-indicators          200      2025-09-08 s/d 2025-12-05  (basi)
    treasury-rates               200      2026-06-09 s/d 2026-09-04  (62 entri)
    historical-price-eod/light   200      2019-01-01 s/d 2026-09-07  (2807 entri)
    quote                        200      live

KONSEKUENSI YANG HARUS DIPAHAMI SEBELUM MEMAKAI MODUL INI:

1. Tidak ada CONSENSUS di tier ini. Consensus hanya ada di economic-calendar
   yang diblokir. Tanpa consensus, Surprise Delta = Actual - Consensus tidak
   dapat dihitung, sehingga Rule Engine TIDAK BISA menghasilkan verdict dari
   data FMP saja. Ini blocker untuk Feature 1 dan Feature 4.

2. `economic-indicators` tertinggal ~9 bulan dari hari berjalan. Jangan sekali
   pun dipakai sebagai nilai Actual untuk sinyal live - modul ini menolak
   mengembalikannya tanpa peringatan basi (lihat `staleness_days`).

3. Harga historis hanya EOD harian. PRD mengukur reaksi pada T+15m, dan bar
   harian tidak bisa mengukur jendela 15 menit. Feature 3 tetap membutuhkan
   sumber data intraday.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://financialmodelingprep.com/stable"
DEFAULT_TIMEOUT = 20

# Ambang kewajaran umur data untuk indikator makro bulanan. Di atas ini, angka
# tidak boleh dipakai sebagai nilai live.
MAX_ACCEPTABLE_STALENESS_DAYS = 45


@dataclass
class FMPResponse:
    """Hasil satu panggilan. Tidak pernah melempar exception ke pemanggil."""

    ok: bool
    status: str          # OK | NOT_CONFIGURED | RESTRICTED | HTTP_ERROR | NETWORK_ERROR | EMPTY
    data: list = field(default_factory=list)
    detail: str = ""
    http_code: Optional[int] = None
    latency_ms: Optional[int] = None
    staleness_days: Optional[int] = None

    @property
    def is_stale(self) -> bool:
        return (
            self.staleness_days is not None
            and self.staleness_days > MAX_ACCEPTABLE_STALENESS_DAYS
        )


def get_api_key() -> Optional[str]:
    return os.getenv("FMP_API_KEY") or None


def is_configured() -> bool:
    return bool(get_api_key())


def _request(path: str, params: Optional[dict] = None) -> FMPResponse:
    key = get_api_key()
    if not key:
        return FMPResponse(
            ok=False,
            status="NOT_CONFIGURED",
            detail="FMP_API_KEY belum diisi di .env.",
        )

    query = dict(params or {})
    query["apikey"] = key
    started = datetime.now(timezone.utc)
    try:
        response = requests.get(f"{BASE_URL}/{path}", params=query, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        return FMPResponse(
            ok=False,
            status="NETWORK_ERROR",
            detail=f"{type(exc).__name__}: {exc}",
        )

    latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    # 402 dipakai FMP untuk endpoint di luar paket langganan. Ini bukan bug dan
    # bukan kesalahan kredensial - dibedakan supaya pesannya tidak menyesatkan.
    if response.status_code == 402:
        return FMPResponse(
            ok=False,
            status="RESTRICTED",
            detail="Endpoint tidak termasuk paket langganan FMP saat ini.",
            http_code=402,
            latency_ms=latency_ms,
        )

    if response.status_code != 200:
        return FMPResponse(
            ok=False,
            status="HTTP_ERROR",
            detail=f"HTTP {response.status_code}: {response.text[:160]}",
            http_code=response.status_code,
            latency_ms=latency_ms,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        return FMPResponse(
            ok=False,
            status="HTTP_ERROR",
            detail=f"Response bukan JSON valid: {exc}",
            http_code=200,
            latency_ms=latency_ms,
        )

    if isinstance(payload, dict):
        message = payload.get("Error Message") or payload.get("message") or str(payload)[:160]
        return FMPResponse(
            ok=False,
            status="HTTP_ERROR",
            detail=message,
            http_code=200,
            latency_ms=latency_ms,
        )

    if not payload:
        return FMPResponse(
            ok=False,
            status="EMPTY",
            detail="Tidak ada data untuk parameter tersebut.",
            http_code=200,
            latency_ms=latency_ms,
        )

    staleness = None
    dates = [row.get("date") for row in payload if isinstance(row, dict) and row.get("date")]
    if dates:
        try:
            newest = max(datetime.fromisoformat(str(d)[:10]).date() for d in dates)
            staleness = (date.today() - newest).days
        except ValueError:
            staleness = None

    return FMPResponse(
        ok=True,
        status="OK",
        data=payload,
        detail=f"{len(payload)} entri.",
        http_code=200,
        latency_ms=latency_ms,
        staleness_days=staleness,
    )


# ---------------------------------------------------------------------------
# Endpoint yang terbukti tersedia
# ---------------------------------------------------------------------------

def get_quote(symbol: str) -> FMPResponse:
    """Harga live. Terverifikasi untuk AAPL dan BTCUSD."""
    return _request("quote", {"symbol": symbol})


def get_treasury_rates(date_from: str, date_to: str) -> FMPResponse:
    """
    Kurva imbal hasil Treasury AS. Kolom `year10` adalah US 10Y yang dipakai
    untuk konfirmasi lintas aset.

    Catatan tier: hanya sekitar 62 entri terakhir yang dikembalikan, jadi cukup
    untuk konfirmasi live tetapi TIDAK cukup untuk backtest multi-tahun.
    """
    return _request("treasury-rates", {"from": date_from, "to": date_to})


def get_us10y_change(date_from: str, date_to: str) -> Optional[float]:
    """
    Perubahan US 10Y dalam persentase poin antara dua observasi terakhir.
    Mengembalikan None bila data tidak cukup - pemanggil harus memperlakukan
    None sebagai 'konfirmasi tidak tersedia', bukan sebagai nol.
    """
    res = get_treasury_rates(date_from, date_to)
    if not res.ok or len(res.data) < 2:
        return None
    rows = sorted(res.data, key=lambda r: r.get("date", ""))
    try:
        return round(float(rows[-1]["year10"]) - float(rows[-2]["year10"]), 4)
    except (KeyError, TypeError, ValueError):
        return None


def get_economic_indicator(name: str, date_from: Optional[str] = None, date_to: Optional[str] = None) -> FMPResponse:
    """
    Nilai historis satu indikator makro.

    Nama yang terverifikasi hidup: CPI, inflationRate, unemploymentRate,
    federalFunds, retailSales, consumerSentiment, GDP.

    PERINGATAN: pada tier ini datanya tertinggal jauh dari hari berjalan.
    Periksa `staleness_days` / `is_stale` sebelum memakai nilainya untuk apa pun
    yang bersifat live.
    """
    params = {"name": name}
    if date_from:
        params["from"] = date_from
    if date_to:
        params["to"] = date_to
    return _request("economic-indicators", params)


def get_historical_prices(symbol: str, date_from: str, date_to: str) -> FMPResponse:
    """
    Harga penutupan harian. Kedalaman penuh 2019-2026 terverifikasi untuk BTCUSD.

    Granularitasnya harian, sehingga TIDAK dapat mengukur reaksi T+15m yang
    diminta PRD. Berguna untuk konteks harian dan pengembangan, bukan untuk
    kalibrasi jendela 15 menit.
    """
    return _request(
        "historical-price-eod/light",
        {"symbol": symbol, "from": date_from, "to": date_to},
    )


# ---------------------------------------------------------------------------
# Yang diblokir - tetap diekspos supaya kegagalannya eksplisit, bukan senyap
# ---------------------------------------------------------------------------

def get_economic_calendar(date_from: str, date_to: str) -> FMPResponse:
    """
    Kalender ekonomi berisi jadwal DAN consensus.

    Pada key saat ini endpoint ini mengembalikan 402 RESTRICTED. Fungsi tetap
    disediakan agar kegagalannya terlihat sebagai status terstruktur, dan agar
    langsung berfungsi begitu paket dinaikkan - tanpa perlu ubah kode pemanggil.
    """
    return _request("economic-calendar", {"from": date_from, "to": date_to})


# ---------------------------------------------------------------------------
# Health check untuk dashboard
# ---------------------------------------------------------------------------

def health_check() -> dict:
    """Ringkasan kesehatan koneksi untuk Tab System Health."""
    if not is_configured():
        return {
            "ok": False,
            "status": "NOT_CONFIGURED",
            "detail": "FMP_API_KEY belum diisi di .env.",
            "latency_ms": None,
            "calendar_available": False,
        }

    probe = get_quote("AAPL")
    calendar = get_economic_calendar(str(date.today()), str(date.today()))
    return {
        "ok": probe.ok,
        "status": probe.status,
        "detail": probe.detail if probe.ok else probe.detail,
        "latency_ms": probe.latency_ms,
        "calendar_available": calendar.ok,
        "calendar_detail": calendar.detail,
    }
