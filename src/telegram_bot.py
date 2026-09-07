"""
MacroAI Agent - Telegram Notification Module
============================================

Dua jalur kirim sesuai PRD:

* `send_pre_event_playbook()`  - Feature 2, dikirim pada T-24h dan T-1h.
* `send_post_event_signal()`   - Feature 4, dikirim < 60 detik pasca-rilis.

Prinsip modul ini: **kegagalan pengiriman tidak boleh menjatuhkan pipeline.**
Semua fungsi mengembalikan `DeliveryResult` dan tidak pernah melempar exception
ke pemanggil. Kredensial yang belum diisi diperlakukan sebagai kondisi normal
(NOT_CONFIGURED), bukan error - dashboard harus tetap bisa jalan tanpa token.
"""

from __future__ import annotations

import html
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from dotenv import load_dotenv

from .rule_engine import NO_VERDICT_DISPLAY, VOLATILITY_BUFFER_MINUTES

load_dotenv()

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT_SECONDS = 10

# Peringatan wajib pada setiap sinyal post-event (Constraint 3 di
# prompts/presenter_system_prompt.md).
EXECUTION_WARNING = (
    f"Abaikan pergerakan {VOLATILITY_BUFFER_MINUTES} menit pertama untuk "
    f"menghindari whipsaw/liquidity sweep."
)


# ---------------------------------------------------------------------------
# Hasil pengiriman
# ---------------------------------------------------------------------------

@dataclass
class DeliveryResult:
    ok: bool
    status: str          # SENT | DRY_RUN | NOT_CONFIGURED | HTTP_ERROR | NETWORK_ERROR
    detail: str = ""
    latency_ms: Optional[int] = None
    message_preview: str = ""
    missing_fields: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
            "missing_fields": list(self.missing_fields),
        }


# ---------------------------------------------------------------------------
# Kredensial
# ---------------------------------------------------------------------------

def get_credentials() -> tuple:
    """Membaca token dan chat id dari environment. Mengembalikan (token, chat_id)."""
    return (
        os.getenv("TELEGRAM_BOT_TOKEN") or None,
        os.getenv("TELEGRAM_CHAT_ID") or None,
    )


def is_configured() -> bool:
    token, chat_id = get_credentials()
    return bool(token and chat_id)


def _missing_credentials() -> list:
    token, chat_id = get_credentials()
    missing = []
    if not token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not chat_id:
        missing.append("TELEGRAM_CHAT_ID")
    return missing


# ---------------------------------------------------------------------------
# Pengiriman dasar
# ---------------------------------------------------------------------------

def _send(text: str, dry_run: bool = False) -> DeliveryResult:
    """
    Mengirim satu pesan HTML ke Telegram.

    `dry_run=True` memformat pesan tanpa memanggil jaringan - dipakai oleh tombol
    uji di dashboard dan oleh test.
    """
    if dry_run:
        return DeliveryResult(
            ok=True,
            status="DRY_RUN",
            detail="Pesan diformat tanpa dikirim.",
            message_preview=text,
        )

    missing = _missing_credentials()
    if missing:
        return DeliveryResult(
            ok=False,
            status="NOT_CONFIGURED",
            detail=(
                "Kredensial Telegram belum diisi. Tambahkan "
                + " dan ".join(missing)
                + " ke file .env."
            ),
            message_preview=text,
            missing_fields=missing,
        )

    token, chat_id = get_credentials()
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    started = time.perf_counter()
    try:
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return DeliveryResult(
            ok=False,
            status="NETWORK_ERROR",
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=int((time.perf_counter() - started) * 1000),
            message_preview=text,
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    if response.status_code != 200:
        return DeliveryResult(
            ok=False,
            status="HTTP_ERROR",
            detail=f"HTTP {response.status_code}: {response.text[:200]}",
            latency_ms=latency_ms,
            message_preview=text,
        )

    return DeliveryResult(
        ok=True,
        status="SENT",
        detail="Terkirim.",
        latency_ms=latency_ms,
        message_preview=text,
    )


def check_connection(timeout: int = 5) -> DeliveryResult:
    """Health check ringan lewat endpoint getMe. Dipakai Tab System Health."""
    missing = _missing_credentials()
    if missing:
        return DeliveryResult(
            ok=False,
            status="NOT_CONFIGURED",
            detail="Token belum diisi.",
            missing_fields=missing,
        )

    token, _ = get_credentials()
    started = time.perf_counter()
    try:
        response = requests.get(f"{TELEGRAM_API_BASE}/bot{token}/getMe", timeout=timeout)
    except requests.RequestException as exc:
        return DeliveryResult(
            ok=False,
            status="NETWORK_ERROR",
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    if response.status_code != 200:
        return DeliveryResult(
            ok=False,
            status="HTTP_ERROR",
            detail=f"HTTP {response.status_code}",
            latency_ms=latency_ms,
        )

    name = ""
    try:
        name = response.json().get("result", {}).get("username", "")
    except ValueError:
        pass
    return DeliveryResult(
        ok=True,
        status="SENT",
        detail=f"Terhubung sebagai @{name}" if name else "Terhubung.",
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# Format pesan
# ---------------------------------------------------------------------------

def _esc(value) -> str:
    """Escape HTML. `None` menjadi penanda [MISSING], bukan string kosong."""
    if value is None:
        return "[MISSING]"
    return html.escape(str(value))


def format_pre_event_playbook(
    event_name: str,
    scheduled_wib: str,
    consensus,
    previous,
    playbook: dict,
) -> str:
    """Menyusun teks Format 1 (Pre-Event Scenario Playbook)."""
    incomplete = (
        playbook.get("status") != "OK"
        or consensus is None
        or event_name is None
        or scheduled_wib is None
    )
    lines = []
    if incomplete:
        missing = list(playbook.get("missing_fields", []))
        if consensus is None:
            missing.append("consensus")
        lines.append("⚠️ <b>STATUS: INCOMPLETE_DATA</b>")
        if missing:
            lines.append(f"<i>Field hilang: {_esc(', '.join(sorted(set(missing))))}</i>")
        lines.append("")

    lines += [
        f"\U0001f514 <b>[EVENT REMINDER] {_esc(event_name)}</b>",
        f"\U0001f552 {_esc(scheduled_wib)}",
        f"Consensus: <b>{_esc(consensus)}</b> | Previous: <b>{_esc(previous)}</b>",
        "",
        "<b>PRE-EVENT SCENARIO PLAYBOOK</b>",
        f"\U0001f4c8 <b>Bullish:</b> {_esc(playbook.get('bullish_condition'))}",
        f"     <i>{_esc(playbook.get('bullish_rationale', ''))}</i>",
        f"\U0001f4c9 <b>Bearish:</b> {_esc(playbook.get('bearish_condition'))}",
        f"     <i>{_esc(playbook.get('bearish_rationale', ''))}</i>",
        f"➖ <b>Neutral:</b> {_esc(playbook.get('neutral_condition'))}",
        f"     <i>{_esc(playbook.get('neutral_rationale', ''))}</i>",
    ]

    if playbook.get("calibrated") is False:
        lines += ["", "<i>Threshold masih placeholder - belum terkalibrasi backtest.</i>"]
    return "\n".join(lines)


def format_post_event_signal(
    event_name: str,
    actual,
    consensus,
    previous,
    surprise_delta,
    verdict_display: str,
    rationale: str,
    dxy_change=None,
    us10y_change=None,
    confirmation: str = "UNAVAILABLE",
    historical_stats: Optional[str] = None,
) -> str:
    """Menyusun teks Format 2 (Post-Event Signal Report)."""
    engine_down = verdict_display == NO_VERDICT_DISPLAY
    missing = [
        name
        for name, value in (
            ("actual", actual),
            ("consensus", consensus),
            ("surprise_delta", surprise_delta),
            ("dxy_change", dxy_change),
            ("us10y_change", us10y_change),
        )
        if value is None
    ]

    lines = []
    if engine_down or missing:
        lines.append("⚠️ <b>STATUS: INCOMPLETE_DATA</b>")
        if missing:
            lines.append(f"<i>Field hilang: {_esc(', '.join(missing))}</i>")
        lines.append("")

    lines += [
        f"\U0001f4ca <b>[SIGNAL] {_esc(event_name)}</b>",
        f"Actual: <b>{_esc(actual)}</b> | Consensus: {_esc(consensus)} | Previous: {_esc(previous)}",
        f"Surprise Delta: <b>{_esc(surprise_delta)}</b>",
        "",
        "<b>CROSS-ASSET</b>",
        f"DXY: {_esc(dxy_change)} | US 10Y: {_esc(us10y_change)}",
        f"Konfirmasi: <b>{_esc(confirmation)}</b>",
    ]
    if historical_stats:
        lines.append(f"Backtest T+15m: {_esc(historical_stats)}")

    lines += [
        "",
        f"<b>FINAL BIAS: {_esc(verdict_display)}</b>",
        f"<i>{_esc(rationale)}</i>",
        "",
        f"⚠️ {_esc(EXECUTION_WARNING)}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# API publik
# ---------------------------------------------------------------------------

def send_pre_event_playbook(
    event_name: str,
    scheduled_wib: str,
    consensus,
    previous,
    playbook: dict,
    dry_run: bool = False,
) -> DeliveryResult:
    """Mengirim Pre-Event Scenario Playbook (T-24h / T-1h)."""
    try:
        text = format_pre_event_playbook(
            event_name=event_name,
            scheduled_wib=scheduled_wib,
            consensus=consensus,
            previous=previous,
            playbook=playbook or {},
        )
    except Exception as exc:  # format tidak boleh menjatuhkan pipeline
        return DeliveryResult(
            ok=False,
            status="FORMAT_ERROR",
            detail=f"{type(exc).__name__}: {exc}",
        )
    return _send(text, dry_run=dry_run)


def send_post_event_signal(
    event_name: str,
    actual,
    consensus,
    previous,
    surprise_delta,
    verdict_display: str,
    rationale: str,
    dxy_change=None,
    us10y_change=None,
    confirmation: str = "UNAVAILABLE",
    historical_stats: Optional[str] = None,
    dry_run: bool = False,
) -> DeliveryResult:
    """
    Mengirim Post-Event Signal Report.

    `verdict_display` harus datang apa adanya dari Rule Engine
    (`VerdictResult.display_bias`). Modul ini tidak pernah mengganti nilai yang
    hilang dengan NEUTRAL - kegagalan engine harus terlihat sebagai kegagalan.
    """
    try:
        text = format_post_event_signal(
            event_name=event_name,
            actual=actual,
            consensus=consensus,
            previous=previous,
            surprise_delta=surprise_delta,
            verdict_display=verdict_display,
            rationale=rationale,
            dxy_change=dxy_change,
            us10y_change=us10y_change,
            confirmation=confirmation,
            historical_stats=historical_stats,
        )
    except Exception as exc:
        return DeliveryResult(
            ok=False,
            status="FORMAT_ERROR",
            detail=f"{type(exc).__name__}: {exc}",
        )
    return _send(text, dry_run=dry_run)


def send_test_alert(dry_run: bool = False) -> DeliveryResult:
    """Dry-run test yang dipanggil tombol 'Send Test Alert' di sidebar dashboard."""
    text = (
        "\U0001f9ea <b>[TEST] MacroAI Agent</b>\n"
        "Uji koneksi pipeline notifikasi.\n"
        f"Volatility buffer aktif: <b>{VOLATILITY_BUFFER_MINUTES} menit</b>.\n"
        "<i>Pesan ini tidak mengandung sinyal posisi.</i>"
    )
    return _send(text, dry_run=dry_run)
