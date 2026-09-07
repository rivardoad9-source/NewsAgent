"""
MacroAI Agent - Action Layer (BUY / SELL / HOLD)
================================================

Menerjemahkan bias Rule Engine menjadi aksi per timeframe: 15m, 1h, 4h.

PRINSIP MODUL INI
Setiap aksi dikirim bersama bukti terukurnya - alignment rate, ukuran sampel,
dan status edge dari `data/calibration.json`. Angka itu tidak opsional dan tidak
bisa dilepas dari sinyalnya. Alasannya sederhana: pada kalibrasi 2019-2026,
TIDAK SATU PUN dari 24 kombinasi indikator x aset x timeframe menunjukkan edge
arah yang signifikan secara statistik. Sinyal BUY/SELL yang dikirim tanpa angka
itu akan terbaca jauh lebih meyakinkan daripada yang sebenarnya didukung data.

Dua lapis pengaman yang selalu aktif:

  1. VOLATILITY BUFFER. Selama 15 menit pertama pasca-rilis, aksi selalu HOLD
     apa pun biasnya - mitigasi whipsaw/liquidity sweep sesuai PRD.

  2. STRICT MODE (opsional). Bila dinyalakan, timeframe yang berstatus
     NO_EDGE_DETECTED diturunkan menjadi HOLD. Default mati, sehingga pemakai
     tetap menerima arah yang diminta, tetapi opsinya tersedia.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

from .rule_engine import (
    INDICATOR_TABLE,
    NO_VERDICT_DISPLAY,
    VOLATILITY_BUFFER_MINUTES,
    Verdict,
)

CALIBRATION_PATH = Path(__file__).resolve().parent.parent / "data" / "calibration.json"
TIMEFRAMES = ["15m", "1h", "4h"]

# Pengali terhadap pergerakan rata-rata terukur untuk menentukan batas
# invalidasi. Stop yang lebih rapat dari pergerakan normal akan tersapu oleh
# noise biasa, bukan oleh salahnya tesis.
INVALIDATION_MULTIPLIER = 1.5


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    PENDING = "PENDING"   # ditampilkan sebagai "—", menunggu data tertentu


# Berapa menit tiap horizon berlangsung - dipakai untuk memeriksa apakah ada
# rilis lain yang jatuh DI DALAM jendela posisi.
HORIZON_MINUTES = {"15m": 15, "1h": 60, "4h": 240}

# Peringkat dampak. Diturunkan dari pergerakan rata-rata TERUKUR di kalibrasi,
# bukan dari penilaian subjektif. FOMC belum punya kalibrasi (release_id-nya
# belum dipetakan), jadi diberi nilai tertinggi secara konvensi - keputusan suku
# bunga secara empiris adalah event makro paling menggerakkan pasar.
FALLBACK_IMPACT = {"FOMC": 99.0, "CPI": 0.79, "NFP": 0.51, "PPI": 0.30,
                   "UNEMPLOYMENT": 0.51, "GDP": 0.40}


_ACTION_FROM_VERDICT = {
    Verdict.BULLISH.value: Action.BUY,
    Verdict.BEARISH.value: Action.SELL,
    Verdict.NEUTRAL.value: Action.HOLD,
}


@dataclass
class TimeframeAction:
    timeframe: str
    action: Action
    reason: str
    expected_move_pct: Optional[float] = None
    invalidation_pct: Optional[float] = None
    measured_alignment: Optional[float] = None
    sample_size: Optional[int] = None
    edge_status: str = "UNCALIBRATED"
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "action": self.action.value,
            "reason": self.reason,
            "expected_move_pct": self.expected_move_pct,
            "invalidation_pct": self.invalidation_pct,
            "measured_alignment": self.measured_alignment,
            "sample_size": self.sample_size,
            "edge_status": self.edge_status,
            "warnings": list(self.warnings),
        }


@dataclass
class ActionPlan:
    indicator: str
    asset: str
    bias: str
    timeframes: list = field(default_factory=list)
    global_warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "indicator": self.indicator,
            "asset": self.asset,
            "bias": self.bias,
            "volatility_buffer_minutes": VOLATILITY_BUFFER_MINUTES,
            "timeframes": [t.to_dict() for t in self.timeframes],
            "global_warnings": list(self.global_warnings),
        }

    def get(self, timeframe: str) -> Optional[TimeframeAction]:
        return next((t for t in self.timeframes if t.timeframe == timeframe), None)


def impact_score(indicator: str, asset: str = "BTC/USD") -> float:
    """
    Seberapa besar sebuah indikator menggerakkan pasar, dalam persen.

    Diambil dari pergerakan rata-rata 15m hasil kalibrasi bila tersedia, sehingga
    peringkatnya berdasarkan pengukuran, bukan asumsi.
    """
    stats = get_stats(str(indicator).strip().upper(), asset, "15m")
    if stats.get("avg_abs_move_pct") is not None:
        return float(stats["avg_abs_move_pct"])
    return FALLBACK_IMPACT.get(str(indicator).strip().upper(), 0.0)


def find_blocking_event(
    indicator: str,
    asset: str,
    horizon: str,
    now,
    upcoming_events,
) -> Optional[dict]:
    """
    Mencari rilis lain yang jatuh DI DALAM jendela horizon ini dan dampaknya
    setara atau lebih besar.

    Menahan posisi terarah melewati rilis yang lebih besar berarti hasilnya
    ditentukan oleh event itu, bukan oleh tesis yang sedang dijalankan. Dalam
    kondisi begini aksi menjadi PENDING - bukan HOLD, karena bedanya penting:
    HOLD berarti "sudah dinilai, tidak ada peluang", PENDING berarti
    "belum bisa dinilai, sedang menunggu sesuatu".
    """
    if not upcoming_events or now is None:
        return None

    minutes = HORIZON_MINUTES.get(horizon)
    if minutes is None:
        return None

    window_end = now + timedelta(minutes=minutes)
    current_impact = impact_score(indicator, asset)

    for event in upcoming_events:
        when = event.get("scheduled_utc")
        other = str(event.get("event_type", "")).strip().upper()
        if when is None or not other or other == str(indicator).strip().upper():
            continue
        if not (now < when <= window_end):
            continue
        if impact_score(other, asset) >= current_impact:
            return {
                "event_type": other,
                "event_name": event.get("event_name", other),
                "scheduled_utc": when,
                "impact": impact_score(other, asset),
            }
    return None


_cache: Optional[dict] = None


def load_calibration(force: bool = False) -> dict:
    """Memuat data/calibration.json. Kosong bila belum dibangun."""
    global _cache
    if _cache is not None and not force:
        return _cache
    try:
        _cache = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _cache = {}
    return _cache


def get_stats(indicator: str, asset: str, timeframe: str) -> dict:
    """Statistik terukur untuk satu kombinasi. Kosong bila belum dikalibrasi."""
    calibration = load_calibration()
    pair = calibration.get("pairs", {}).get(f"{indicator}|{asset}")
    if not pair:
        return {}
    stats = pair.get("horizons", {}).get(timeframe, {})
    return dict(stats) if stats.get("alignment_rate") is not None else {}


def build_action_plan(
    indicator: str,
    asset: str,
    verdict_display: str,
    minutes_since_release: Optional[float] = None,
    strict_mode: bool = False,
    missing_fields: Optional[list] = None,
    upcoming_events: Optional[list] = None,
    now=None,
) -> ActionPlan:
    """
    Menyusun rencana aksi per timeframe.

    `verdict_display` harus datang apa adanya dari Rule Engine
    (`VerdictResult.display_bias`). Modul ini tidak pernah menghitung ulang bias
    dan tidak pernah mengganti verdict yang hilang dengan NEUTRAL.

    `minutes_since_release=None` berarti pre-event - buffer belum berlaku, dan
    rencana yang dihasilkan bersifat persiapan skenario.
    """
    key = str(indicator).strip().upper()
    spec = INDICATOR_TABLE.get(key)
    plan = ActionPlan(indicator=key, asset=asset, bias=verdict_display)

    engine_down = verdict_display == NO_VERDICT_DISPLAY
    in_buffer = (
        minutes_since_release is not None
        and 0 <= minutes_since_release < VOLATILITY_BUFFER_MINUTES
    )

    waiting_for = ", ".join(missing_fields) if missing_fields else "data rilis"
    if engine_down:
        plan.global_warnings.append(
            f"Rule Engine belum menghasilkan verdict - menunggu {waiting_for}. "
            f"Seluruh timeframe berstatus PENDING, bukan HOLD: belum dinilai, "
            f"bukan sudah dinilai dan ditolak."
        )
    if in_buffer:
        plan.global_warnings.append(
            f"Masih di dalam volatility buffer "
            f"({minutes_since_release:.0f} dari {VOLATILITY_BUFFER_MINUTES} menit). "
            f"Seluruh timeframe HOLD untuk menghindari whipsaw/liquidity sweep."
        )
    if spec and not spec.calibrated:
        plan.global_warnings.append(
            "Threshold indikator ini masih placeholder, belum terkalibrasi backtest."
        )

    for timeframe in TIMEFRAMES:
        stats = get_stats(key, asset, timeframe)
        expected = stats.get("avg_abs_move_pct")
        alignment = stats.get("alignment_rate")
        sample = stats.get("n_directional")
        edge = stats.get("edge_status", "UNCALIBRATED")

        warnings = []
        invalidation = (
            round(expected * INVALIDATION_MULTIPLIER, 3) if expected is not None else None
        )

        # --- Urutan penentuan aksi: pengaman dulu, arah belakangan ---------
        blocker = find_blocking_event(key, asset, timeframe, now, upcoming_events)

        if engine_down:
            action = Action.PENDING
            reason = f"Menunggu {waiting_for} - belum ada dasar untuk menilai arah."
        elif blocker is not None:
            action = Action.PENDING
            when = blocker["scheduled_utc"]
            stamp = when.strftime("%d %b %H:%M UTC") if hasattr(when, "strftime") else str(when)
            reason = (
                f"Menunggu {blocker['event_name']} pada {stamp}, yang jatuh di dalam "
                f"jendela {timeframe} ini dan dampaknya setara atau lebih besar. "
                f"Posisi terarah yang ditahan melewatinya akan ditentukan oleh event itu, "
                f"bukan oleh tesis ini."
            )
            warnings.append(f"Event penghalang: {blocker['event_name']}.")
        elif in_buffer:
            action = Action.HOLD
            reason = (
                f"Volatility buffer {VOLATILITY_BUFFER_MINUTES} menit belum lewat. "
                f"Pergerakan di jendela ini historisnya {expected}% dan sering berbalik."
                if expected is not None
                else f"Volatility buffer {VOLATILITY_BUFFER_MINUTES} menit belum lewat."
            )
        elif verdict_display == Verdict.NEUTRAL.value:
            action = Action.HOLD
            reason = (
                "Bias NEUTRAL - rilis sesuai ekspektasi atau metrik saling bertentangan. "
                "Tidak ada edge arah untuk diambil."
            )
        elif edge == "UNCALIBRATED":
            # Prinsip modul ini: bukti menempel pada sinyal. Kombinasi yang belum
            # pernah diukur tidak punya bukti apa pun, sehingga mengeluarkan arah
            # di sini akan melanggar prinsip itu sendiri.
            action = Action.PENDING
            reason = (
                f"Menunggu kalibrasi untuk {key} pada {asset}. Kombinasi ini belum "
                f"pernah di-backtest, jadi tidak ada pergerakan rata-rata maupun "
                f"akurasi arah yang bisa dilampirkan. Jalankan "
                f"scripts/build_calibration.py."
            )
        elif strict_mode and edge == "NO_EDGE_DETECTED":
            action = Action.HOLD
            reason = (
                f"Strict mode: pada {timeframe}, akurasi arah terukur {alignment}% "
                f"dari {sample} sampel - tidak berbeda signifikan dari lemparan koin."
            )
        else:
            action = _ACTION_FROM_VERDICT.get(verdict_display, Action.HOLD)
            reason = (
                f"Bias {verdict_display} dari Rule Engine diterjemahkan menjadi "
                f"{action.value} pada horizon {timeframe}."
            )

        # --- Bukti terukur selalu menempel pada sinyal ---------------------
        if edge == "NO_EDGE_DETECTED":
            warnings.append(
                f"TIDAK ADA EDGE TERUKUR: akurasi arah {alignment}% dari {sample} rilis "
                f"(2019-2026) - secara statistik tidak berbeda dari 50%."
            )
        elif edge == "UNCALIBRATED":
            warnings.append(
                "Kombinasi ini belum dikalibrasi. Jalankan scripts/build_calibration.py."
            )

        if expected is not None:
            warnings.append(
                f"Pergerakan rata-rata {timeframe} adalah {expected}%. Batas invalidasi "
                f"di bawah {invalidation}% berisiko tersapu noise biasa, bukan oleh "
                f"salahnya tesis."
            )

        plan.timeframes.append(
            TimeframeAction(
                timeframe=timeframe,
                action=action,
                reason=reason,
                expected_move_pct=expected,
                invalidation_pct=invalidation,
                measured_alignment=alignment,
                sample_size=sample,
                edge_status=edge,
                warnings=warnings,
            )
        )

    calibration = load_calibration()
    if calibration:
        plan.global_warnings.append(
            "Seluruh angka kalibrasi diukur terhadap forecast naif, bukan consensus "
            "pasar. Consensus belum tersedia di sumber mana pun yang dipakai proyek ini."
        )
    return plan


def format_plan_text(plan: ActionPlan) -> str:
    """Rendering teks untuk notifikasi Telegram dan konsol."""
    lines = [f"AKSI — {plan.indicator} / {plan.asset}", f"Bias: {plan.bias}", ""]
    for tf in plan.timeframes:
        head = f"  {tf.timeframe:>4}  {tf.action.value:<7}"
        if tf.expected_move_pct is not None:
            head += f"  exp move {tf.expected_move_pct}%  invalidasi {tf.invalidation_pct}%"
        if tf.measured_alignment is not None:
            head += f"  akurasi {tf.measured_alignment}% (n={tf.sample_size})"
        lines.append(head)
    if plan.global_warnings:
        lines.append("")
        lines += [f"  ! {w}" for w in plan.global_warnings]
    return "\n".join(lines)
