"""
MacroAI Agent - Dual-Horizon Thesis
===================================

Menyusun tesis untuk satu event makro pada DUA horizon yang sumbernya berbeda:

  JANGKA PENDEK (jam sampai hari)
      Dari Surprise Delta terhadap consensus. Yang menggerakkan harga di menit
      pertama adalah selisih rilis dengan yang sudah diperhitungkan pasar -
      bukan bagus atau buruknya angka itu secara absolut.

  JANGKA PANJANG (minggu sampai bulan)
      Dari TREN indikator itu sendiri plus arah suku bunga acuan, memakai
      histori FRED. Satu rilis tunggal hampir tidak pernah menentukan arah
      multi-bulan; yang menentukan adalah lintasan inflasi, tenaga kerja, dan
      kebijakan. Karena itu horizon ini sengaja TIDAK dihitung dari surprise.

Keduanya memakai INDICATOR_TABLE yang sama sebagai sumber arah, sehingga tidak
mungkin saling bertentangan karena definisi yang berbeda.

CATATAN TENTANG KEYAKINAN
Modul ini tidak pernah mengeluarkan angka "win rate". Yang tersedia hanya
`alignment_rate` dari `src/backtest.py`, yang diukur terhadap forecast naif pada
resolusi harian - bukan terhadap consensus pasar pada T+15m. Menyebutnya win
rate akan menyesatkan pemakainya.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .providers import fred
from .rule_engine import (
    INDICATOR_TABLE,
    NO_VERDICT_DISPLAY,
    VOLATILITY_BUFFER_MINUTES,
    Verdict,
    calculate_surprise_delta,
    get_playbook,
    get_verdict,
)

# Jendela tren default per horizon (dalam periode rilis).
LONG_WINDOW = 6      # 6 rilis bulanan ~ setengah tahun
POLICY_WINDOW = 90   # 90 hari Fed Funds

# Ambang tren dianggap datar, per indikator, dalam satuan indikator.
FLAT_THRESHOLD = {
    "CPI": 0.20,
    "PPI": 0.30,
    "NFP": 40.0,
    "UNEMPLOYMENT": 0.20,
    "GDP": 0.40,
    "FOMC": 0.25,
}


@dataclass
class HorizonView:
    horizon: str
    verdict: Optional[str]
    rationale: str
    basis: str
    evidence: dict = field(default_factory=dict)

    @property
    def display(self) -> str:
        return self.verdict or NO_VERDICT_DISPLAY


@dataclass
class Thesis:
    indicator: str
    label: str
    short_term: HorizonView
    long_term: HorizonView
    playbook: dict = field(default_factory=dict)
    caveats: list = field(default_factory=list)
    confidence_note: str = ""

    def to_dict(self) -> dict:
        return {
            "indicator": self.indicator,
            "label": self.label,
            "short_term": {
                "verdict": self.short_term.verdict,
                "display": self.short_term.display,
                "rationale": self.short_term.rationale,
                "basis": self.short_term.basis,
                "evidence": self.short_term.evidence,
            },
            "long_term": {
                "verdict": self.long_term.verdict,
                "display": self.long_term.display,
                "rationale": self.long_term.rationale,
                "basis": self.long_term.basis,
                "evidence": self.long_term.evidence,
            },
            "playbook": self.playbook,
            "caveats": self.caveats,
            "confidence_note": self.confidence_note,
            "volatility_buffer_minutes": VOLATILITY_BUFFER_MINUTES,
        }


# ---------------------------------------------------------------------------
# Jangka pendek
# ---------------------------------------------------------------------------

def build_short_term(indicator: str, actual=None, consensus=None) -> HorizonView:
    """
    Bias jangka pendek dari Surprise Delta.

    Bila consensus tidak tersedia - keadaan normal saat ini, karena tidak satu
    pun provider kita menyediakannya - horizon ini mengembalikan verdict None
    dan menyebutkan penyebabnya. Ia TIDAK jatuh ke NEUTRAL, karena NEUTRAL
    adalah kesimpulan analitis yang sah dan tidak boleh dipakai menyamarkan
    data yang hilang.
    """
    spec = INDICATOR_TABLE.get(str(indicator).strip().upper())
    if spec is None:
        return HorizonView("SHORT", None, f"Indikator '{indicator}' tidak dikenal.", "n/a")

    if consensus is None:
        return HorizonView(
            horizon="SHORT",
            verdict=None,
            rationale=(
                "Consensus tidak tersedia, sehingga Surprise Delta tidak dapat dihitung. "
                "Tanpa itu tidak ada dasar untuk bias jangka pendek."
            ),
            basis="Surprise Delta = Actual - Consensus",
            evidence={"missing": ["consensus"]},
        )

    delta = calculate_surprise_delta(actual, consensus)
    if delta is None:
        return HorizonView(
            horizon="SHORT",
            verdict=None,
            rationale="Nilai Actual belum rilis, jadi Surprise Delta belum ada.",
            basis="Surprise Delta = Actual - Consensus",
            evidence={"consensus": consensus, "missing": ["actual"]},
        )

    result = get_verdict(spec.key, delta)
    return HorizonView(
        horizon="SHORT",
        verdict=result.verdict.value if result.verdict else None,
        rationale=result.rationale,
        basis="Surprise Delta terhadap consensus",
        evidence={
            "actual": actual,
            "consensus": consensus,
            "surprise_delta": delta,
            "surprise_display": spec.format_delta(delta),
            "neutral_buffer": spec.neutral_buffer,
            "mapping_shape": spec.shape.value,
        },
    )


# ---------------------------------------------------------------------------
# Jangka panjang
# ---------------------------------------------------------------------------

def build_long_term(indicator: str, window: int = LONG_WINDOW) -> HorizonView:
    """
    Bias jangka panjang dari tren indikator + arah kebijakan.

    Arah tren dinormalkan lewat `heat_sign` di INDICATOR_TABLE, sehingga
    "ekonomi memanas" selalu bertanda positif apa pun indikatornya. Untuk risk
    assets, memanas berarti tekanan hawkish (bearish), mendingin berarti ruang
    pelonggaran (bullish) - kecuali pendinginannya begitu tajam sampai terbaca
    sebagai resesi, yang ditangani lewat overlay kebijakan.
    """
    key = str(indicator).strip().upper()
    spec = INDICATOR_TABLE.get(key)
    if spec is None:
        return HorizonView("LONG", None, f"Indikator '{indicator}' tidak dikenal.", "n/a")

    if key not in fred.SERIES_MAP:
        return HorizonView(
            "LONG", None,
            f"'{key}' belum dipetakan ke series FRED, jadi trennya tidak dapat dihitung.",
            "Tren historis FRED",
        )

    trend = fred.compute_trend(key, window=window)
    if not trend.ok:
        return HorizonView(
            "LONG", None,
            f"Tren tidak dapat dihitung: {trend.detail}",
            "Tren historis FRED",
        )

    policy = fred.get_policy_rate_trend(POLICY_WINDOW)
    flat = FLAT_THRESHOLD.get(key, 0.2)
    change = trend.change_total or 0.0

    evidence = {
        "window_periods": window,
        "change_total": change,
        "change_display": spec.format_delta(change),
        "latest_value": trend.latest_value,
        "latest_date": trend.latest_date,
        "flat_threshold": flat,
        "range": trend.detail,
    }
    if policy.ok:
        evidence["fed_funds_latest"] = policy.latest_value
        evidence["fed_funds_change_90d"] = policy.change_total

    if abs(change) <= flat:
        return HorizonView(
            "LONG", Verdict.NEUTRAL.value,
            (
                f"Selama {window} rilis terakhir {spec.label} hanya bergerak "
                f"{spec.format_delta(change)}, di dalam ambang datar "
                f"{spec.format_level(flat)}. Belum ada lintasan yang jelas untuk "
                f"disandarkan pada horizon menengah."
            ),
            "Tren historis FRED",
            evidence,
        )

    # heat_sign menormalkan arah: positif = ekonomi lebih panas dari sebelumnya.
    heat = change * spec.heat_sign
    policy_easing = policy.ok and policy.change_total is not None and policy.change_total < -0.05
    policy_tightening = policy.ok and policy.change_total is not None and policy.change_total > 0.05

    if heat > 0:
        verdict = Verdict.BEARISH.value
        reason = (
            f"{spec.label} bergerak {spec.format_delta(change)} selama {window} rilis "
            f"terakhir (terbaru {spec.format_level(trend.latest_value)} per "
            f"{trend.latest_date}), menandakan ekonomi memanas dibanding awal jendela. "
            f"Lintasan seperti ini menahan ruang pelonggaran dan menekan risk assets "
            f"pada horizon menengah."
        )
        if policy_easing:
            verdict = Verdict.NEUTRAL.value
            reason += (
                f" Namun Fed Funds justru turun {policy.change_total:+.2f} pp dalam "
                f"{POLICY_WINDOW} hari terakhir — data dan kebijakan bergerak berlawanan, "
                f"sehingga arah menengahnya tidak jelas."
            )
    else:
        verdict = Verdict.BULLISH.value
        reason = (
            f"{spec.label} bergerak {spec.format_delta(change)} selama {window} rilis "
            f"terakhir (terbaru {spec.format_level(trend.latest_value)} per "
            f"{trend.latest_date}), menandakan ekonomi mendingin. Lintasan ini membuka "
            f"ruang pelonggaran moneter, yang secara historis menopang risk assets."
        )
        if policy_tightening:
            verdict = Verdict.NEUTRAL.value
            reason += (
                f" Namun Fed Funds masih naik {policy.change_total:+.2f} pp dalam "
                f"{POLICY_WINDOW} hari terakhir — kebijakan belum mengikuti data, "
                f"sehingga arah menengahnya belum terkonfirmasi."
            )

    return HorizonView("LONG", verdict, reason, "Tren historis FRED + arah Fed Funds", evidence)


# ---------------------------------------------------------------------------
# Tesis gabungan
# ---------------------------------------------------------------------------

def build_thesis(indicator: str, actual=None, consensus=None, window: int = LONG_WINDOW) -> Thesis:
    """Tesis lengkap satu event: jangka pendek, jangka panjang, playbook, dan caveat."""
    key = str(indicator).strip().upper()
    spec = INDICATOR_TABLE.get(key)
    label = spec.label if spec else str(indicator)

    short = build_short_term(key, actual=actual, consensus=consensus)
    long_ = build_long_term(key, window=window)
    playbook = get_playbook(key, consensus) if consensus is not None else {}

    caveats = [
        f"Abaikan pergerakan {VOLATILITY_BUFFER_MINUTES} menit pertama pasca-rilis "
        f"untuk menghindari whipsaw/liquidity sweep.",
    ]
    if spec and not spec.calibrated:
        caveats.append("Threshold indikator ini masih placeholder, belum terkalibrasi backtest.")
    if short.verdict and long_.verdict and short.verdict != long_.verdict:
        caveats.append(
            f"Kedua horizon berbeda arah (pendek {short.verdict}, panjang {long_.verdict}). "
            f"Ini normal — satu rilis bisa melawan tren — tetapi jangan dipakai "
            f"membenarkan posisi yang ditahan melewati horizonnya."
        )

    confidence_note = (
        "Tidak ada win rate yang dapat dilampirkan pada tesis ini. Ukuran yang tersedia "
        "hanya alignment rate terhadap forecast naif pada resolusi harian (lihat "
        "src/backtest.py), dan pada 2019-2026 angkanya tidak berbeda signifikan dari "
        "50% untuk seluruh indikator yang diuji. Win rate yang sesungguhnya menuntut "
        "consensus historis dan harga intraday, yang keduanya belum tersedia."
    )

    return Thesis(
        indicator=key,
        label=label,
        short_term=short,
        long_term=long_,
        playbook=playbook,
        caveats=caveats,
        confidence_note=confidence_note,
    )
