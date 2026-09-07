"""
MacroAI Agent - Rule Engine
===========================

Mesin verdict deterministik. Tidak ada LLM di jalur ini, sesuai NFR *Objectivity*
di PRD: seluruh Final Bias lahir dari kalkulasi, bukan dari penalaran model.

Dua konsep yang menjadi inti modul ini:

1. INDICATOR_TABLE adalah *sumber kebenaran tunggal* untuk arah per indikator.
   Jalur pre-event (playbook) dan post-event (verdict) membaca tabel yang sama,
   sehingga keduanya tidak mungkin saling bertentangan.

2. Pemetaan arah TIDAK seragam antar indikator. Asumsi populer
   "Actual < Consensus = Bullish" hanya sahih untuk indikator inflasi. Untuk NFP,
   GDP, dan Unemployment Rate, kedua ekor distribusi sama-sama Risk-Off: terlalu
   panas memicu repricing hawkish, terlalu dingin memicu kekhawatiran resesi.
   Karena itu ada dua bentuk pemetaan: MONOTONIC_INVERSE dan BANDED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Tipe dasar
# ---------------------------------------------------------------------------

class Verdict(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class EngineStatus(str, Enum):
    OK = "OK"
    INCOMPLETE_DATA = "INCOMPLETE_DATA"


class Confirmation(str, Enum):
    CONFIRMED = "CONFIRMED"        # DXY & yield bergerak sesuai arah verdict
    CONTRADICTED = "CONTRADICTED"  # bergerak berlawanan -> verdict diturunkan
    UNAVAILABLE = "UNAVAILABLE"    # data lintas aset tidak tersedia


class MappingShape(str, Enum):
    MONOTONIC_INVERSE = "MONOTONIC_INVERSE"  # makin rendah Actual, makin bullish
    BANDED = "BANDED"                        # kedua ekor bearish, tengah bullish


# Teks yang WAJIB dipakai ketika engine tidak menghasilkan verdict.
# Sengaja BUKAN "NEUTRAL": NEUTRAL adalah hasil analisis yang sah
# ("in-line, potensi whipsaw, stay cash"), sedangkan ini adalah kegagalan
# sistem. Trader harus bisa membedakan keduanya.
NO_VERDICT_DISPLAY = "N/A - RULE ENGINE UNAVAILABLE"

# Volatility buffer standar pasca-rilis (menit). Dipakai bersama oleh notifikasi,
# dashboard, dan interval pengukuran backtest T+15m.
VOLATILITY_BUFFER_MINUTES = 15


# ---------------------------------------------------------------------------
# Indicator interpretation table - SUMBER KEBENARAN TUNGGAL UNTUK ARAH
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IndicatorSpec:
    """Definisi satu indikator makro dan cara membaca arahnya."""

    key: str
    label: str
    unit: str                                    # satuan delta, mis. "%" atau "K"
    shape: MappingShape
    neutral_buffer: float                        # |delta| <= buffer -> in-line
    band: Optional[tuple] = None                 # hanya untuk BANDED
    calibrated: bool = False                     # True jika berasal dari backtest F3
    note: str = ""

    # --- Deteksi kontradiksi antar indikator -------------------------------
    # `domain` mengelompokkan indikator yang mengukur sisi ekonomi yang sama.
    # `heat_sign` menormalkan arah: setelah dikalikan, delta positif SELALU
    # berarti "ekonomi lebih panas dari perkiraan".
    #
    # Ini yang membuat NFP tinggi + Unemployment naik terdeteksi sebagai
    # kontradiksi. Keduanya bisa sama-sama menghasilkan verdict BEARISH, tetapi
    # lewat kanal yang berlawanan (terlalu panas vs pasar kerja memburuk), dan
    # PRD mensyaratkan divergensi semacam itu diklasifikasikan NEUTRAL.
    domain: str = ""
    heat_sign: int = 1

    def format_delta(self, delta: float) -> str:
        sign = "+" if delta > 0 else ""
        if self.unit == "K":
            return f"{sign}{delta:,.0f}K"
        return f"{sign}{delta:.2f}{self.unit}"

    def format_level(self, value: float) -> str:
        if self.unit == "K":
            return f"{value:,.0f}K"
        return f"{value:.2f}{self.unit}"


# CATATAN KALIBRASI: seluruh neutral_buffer dan band di bawah masih PLACEHOLDER
# yang ditetapkan konservatif. Angka final harus datang dari Historical
# Backtesting Engine (Feature 3 di PRD). Field `calibrated` menandai statusnya
# supaya tidak ada yang mengira angka ini sudah tervalidasi.
INDICATOR_TABLE = {
    "CPI": IndicatorSpec(
        key="CPI",
        domain="INFLATION",
        heat_sign=1,
        label="US CPI YoY",
        unit="%",
        shape=MappingShape.MONOTONIC_INVERSE,
        neutral_buffer=0.05,
        note="Inflasi mendingin menekan DXY dan membuka ruang pelonggaran.",
    ),
    "PPI": IndicatorSpec(
        key="PPI",
        domain="INFLATION",
        heat_sign=1,
        label="US PPI YoY",
        unit="%",
        shape=MappingShape.MONOTONIC_INVERSE,
        neutral_buffer=0.10,
        note="Tekanan harga produsen mendahului CPI; reaksi pasar lebih redam.",
    ),
    "FOMC": IndicatorSpec(
        key="FOMC",
        domain="POLICY",
        heat_sign=1,
        label="FOMC Rate Decision",
        unit="%",
        shape=MappingShape.MONOTONIC_INVERSE,
        neutral_buffer=0.0,
        note=(
            "Setiap deviasi dari ekspektasi bersifat material. Jika rate sesuai "
            "ekspektasi, arah ditentukan Dot Plot dan statement - bukan modul ini."
        ),
    ),
    "NFP": IndicatorSpec(
        key="NFP",
        domain="LABOR",
        heat_sign=1,
        label="US Non-Farm Payrolls",
        unit="K",
        shape=MappingShape.BANDED,
        neutral_buffer=25.0,
        band=(-75.0, 75.0),
        note=(
            "Non-monotonik. Terlalu panas memicu repricing hawkish; terlalu dingin "
            "memicu kekhawatiran resesi. Zona tengah adalah goldilocks."
        ),
    ),
    "UNEMPLOYMENT": IndicatorSpec(
        key="UNEMPLOYMENT",
        domain="LABOR",
        heat_sign=-1,
        label="US Unemployment Rate",
        unit="%",
        shape=MappingShape.BANDED,
        neutral_buffer=0.1,
        band=(-0.2, 0.2),
        note=(
            "Kenaikan tipis dibaca sebagai pendinginan yang dovish; lonjakan tajam "
            "dibaca sebagai sinyal resesi."
        ),
    ),
    "GDP": IndicatorSpec(
        key="GDP",
        domain="GROWTH",
        heat_sign=1,
        label="US GDP Growth Rate",
        unit="%",
        shape=MappingShape.BANDED,
        neutral_buffer=0.2,
        band=(-0.5, 0.5),
        note="Pertumbuhan terlalu kuat memicu hawkish; terlalu lemah memicu resesi.",
    ),
}

SUPPORTED_EVENTS = tuple(INDICATOR_TABLE.keys())


# ---------------------------------------------------------------------------
# Hasil
# ---------------------------------------------------------------------------

@dataclass
class VerdictResult:
    """Hasil evaluasi Rule Engine untuk satu rilis."""

    verdict: Optional[Verdict]
    status: EngineStatus
    rationale: str
    event_type: Optional[str] = None
    surprise_delta: Optional[float] = None
    mapping_shape: Optional[MappingShape] = None
    confirmation: Confirmation = Confirmation.UNAVAILABLE
    missing_fields: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def display_bias(self) -> str:
        """Teks bias siap tampil. Tidak pernah menyamarkan kegagalan jadi NEUTRAL."""
        if self.verdict is None:
            return NO_VERDICT_DISPLAY
        return self.verdict.value

    @property
    def is_actionable(self) -> bool:
        return self.verdict is not None and self.status is EngineStatus.OK

    def to_dict(self) -> dict:
        """Kontrak keluaran untuk konsumen hilir (mis. hermes)."""
        return {
            "verdict": self.verdict.value if self.verdict else None,
            "display_bias": self.display_bias,
            "status": self.status.value,
            "event_type": self.event_type,
            "surprise_delta": self.surprise_delta,
            "mapping_shape": self.mapping_shape.value if self.mapping_shape else None,
            "confirmation": self.confirmation.value,
            "missing_fields": list(self.missing_fields),
            "rationale": self.rationale,
            "notes": list(self.notes),
            "volatility_buffer_minutes": VOLATILITY_BUFFER_MINUTES,
        }


# ---------------------------------------------------------------------------
# Kalkulasi
# ---------------------------------------------------------------------------

def calculate_surprise_delta(actual, consensus):
    """
    Surprise Delta = Actual - Consensus.

    Mengembalikan None jika salah satu input hilang atau bukan angka. Sengaja
    tidak melempar exception dan tidak mengasumsikan nilai pengganti: data yang
    hilang harus tetap terlihat hilang sampai ke output.
    """
    if actual is None or consensus is None:
        return None
    try:
        return round(float(actual) - float(consensus), 6)
    except (TypeError, ValueError):
        return None


def _incomplete(missing, reason) -> VerdictResult:
    return VerdictResult(
        verdict=None,
        status=EngineStatus.INCOMPLETE_DATA,
        rationale=reason,
        missing_fields=list(missing),
    )


def get_verdict(event_type, surprise_delta) -> VerdictResult:
    """
    Menentukan Final Bias untuk satu indikator.

    Fail-safe: jika `event_type` tidak dikenal atau `surprise_delta` bernilai
    None, engine TIDAK menebak dan TIDAK mengembalikan NEUTRAL. Ia mengembalikan
    status INCOMPLETE_DATA dengan verdict None, sehingga lapisan penyaji
    menampilkan NO_VERDICT_DISPLAY - bukan sinyal yang terlihat sah.
    """
    missing = []
    if event_type is None:
        missing.append("event_type")
    if surprise_delta is None:
        missing.append("surprise_delta")
    if missing:
        return _incomplete(
            missing,
            "Rule Engine tidak dapat menghitung verdict: input wajib tidak lengkap.",
        )

    key = str(event_type).strip().upper()
    spec = INDICATOR_TABLE.get(key)
    if spec is None:
        return _incomplete(
            ["event_type"],
            f"Indikator '{event_type}' tidak ada di interpretation table. "
            f"Didukung: {', '.join(SUPPORTED_EVENTS)}.",
        )

    delta = float(surprise_delta)
    shown = spec.format_delta(delta)

    # In-line dengan ekspektasi -> NEUTRAL yang sah (bukan kegagalan sistem).
    if abs(delta) <= spec.neutral_buffer:
        return VerdictResult(
            verdict=Verdict.NEUTRAL,
            status=EngineStatus.OK,
            event_type=key,
            surprise_delta=delta,
            mapping_shape=spec.shape,
            rationale=(
                f"Surprise Delta {shown} berada dalam neutral buffer "
                f"+/-{spec.format_level(spec.neutral_buffer)}. Rilis sesuai "
                f"ekspektasi pasar; risiko whipsaw tinggi."
            ),
            notes=[] if spec.calibrated else ["Neutral buffer belum terkalibrasi backtest."],
        )

    if spec.shape is MappingShape.MONOTONIC_INVERSE:
        if delta < 0:
            verdict = Verdict.BULLISH
            reason = (
                f"Actual di bawah consensus ({shown}) menandakan tekanan harga lebih "
                f"rendah dari perkiraan. Ekspektasi kebijakan bergeser dovish: DXY dan "
                f"US 10Y cenderung melemah, likuiditas ke risk assets menguat."
            )
        else:
            verdict = Verdict.BEARISH
            reason = (
                f"Actual di atas consensus ({shown}) menandakan inflasi lebih persisten "
                f"dari perkiraan. Ekspektasi bergeser hawkish: DXY dan US 10Y cenderung "
                f"menguat, tekanan risk-off pada aset berisiko."
            )
    else:  # BANDED
        low, high = spec.band
        if delta > high:
            verdict = Verdict.BEARISH
            reason = (
                f"Surprise Delta {shown} melewati batas atas band "
                f"({spec.format_level(high)}). Data terlalu kuat memicu repricing "
                f"hawkish - yield naik, risk assets tertekan meski ekonomi solid."
            )
        elif delta < low:
            verdict = Verdict.BEARISH
            reason = (
                f"Surprise Delta {shown} jatuh di bawah batas bawah band "
                f"({spec.format_level(low)}). Pelemahan sebesar ini dibaca sebagai "
                f"sinyal resesi, bukan sebagai alasan pelonggaran - risk-off."
            )
        else:
            verdict = Verdict.BULLISH
            reason = (
                f"Surprise Delta {shown} berada di zona goldilocks "
                f"({spec.format_level(low)} s/d {spec.format_level(high)}): cukup "
                f"melunak untuk mendukung pelonggaran, belum cukup lemah untuk memicu "
                f"kekhawatiran resesi."
            )

    notes = [] if spec.calibrated else ["Threshold belum terkalibrasi backtest (placeholder)."]
    return VerdictResult(
        verdict=verdict,
        status=EngineStatus.OK,
        event_type=key,
        surprise_delta=delta,
        mapping_shape=spec.shape,
        rationale=reason,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Rekonsiliasi & konfirmasi lintas aset
# ---------------------------------------------------------------------------

def reconcile(results) -> VerdictResult:
    """
    Menggabungkan beberapa indikator yang rilis bersamaan (mis. NFP + Unemployment).

    Aturan fail-safe PRD: divergensi antar metrik menghasilkan NEUTRAL, bukan
    salah satu arah yang dipilih sepihak.
    """
    results = list(results)
    if not results:
        return _incomplete(["results"], "Tidak ada hasil indikator untuk direkonsiliasi.")

    if any(r.status is EngineStatus.INCOMPLETE_DATA for r in results):
        missing = sorted({f for r in results for f in r.missing_fields})
        return _incomplete(missing, "Sebagian indikator dalam rilis ini tidak dapat dievaluasi.")

    # --- Lapis 1: kontradiksi di level pembacaan ekonomi -------------------
    # Dijalankan SEBELUM perbandingan verdict. Dua indikator dalam satu domain
    # bisa menghasilkan verdict yang sama namun lewat kanal yang berlawanan -
    # NFP tinggi (pasar kerja terlalu panas) bersama Unemployment naik (pasar
    # kerja memburuk) sama-sama BEARISH, tetapi datanya saling meniadakan.
    # Inilah kasus "Conflicting Macro Metrics" yang dicontohkan PRD.
    by_domain: dict = {}
    for r in results:
        spec = INDICATOR_TABLE.get(r.event_type or "")
        if spec is None or r.surprise_delta is None:
            continue
        if abs(r.surprise_delta) <= spec.neutral_buffer:
            continue  # in-line: tidak memberi arah, tidak ikut dinilai
        heat = 1 if (r.surprise_delta * spec.heat_sign) > 0 else -1
        by_domain.setdefault(spec.domain, []).append((r.event_type, heat))

    for domain, readings in by_domain.items():
        if len({h for _, h in readings}) > 1:
            detail = ", ".join(
                f"{name} menunjuk ekonomi lebih {'panas' if h > 0 else 'dingin'}"
                for name, h in readings
            )
            return VerdictResult(
                verdict=Verdict.NEUTRAL,
                status=EngineStatus.OK,
                rationale=(
                    f"Metrik dalam domain {domain} saling bertentangan ({detail}). "
                    f"Data yang saling meniadakan tidak menghasilkan edge arah - "
                    f"diklasifikasikan NEUTRAL untuk melindungi modal."
                ),
                notes=[
                    "Konflik terdeteksi di level pembacaan ekonomi, bukan di level verdict.",
                ],
            )

    # --- Lapis 2: kontradiksi di level verdict -----------------------------
    directional = {r.verdict for r in results if r.verdict is not Verdict.NEUTRAL}
    labels = ", ".join(f"{r.event_type}={r.display_bias}" for r in results)

    if len(directional) > 1:
        return VerdictResult(
            verdict=Verdict.NEUTRAL,
            status=EngineStatus.OK,
            rationale=(
                f"Indikator dalam rilis ini saling bertentangan ({labels}). Divergensi "
                f"multi-metrik diklasifikasikan NEUTRAL untuk melindungi modal - "
                f"potensi whipsaw tinggi."
            ),
            notes=["Verdict ditetapkan oleh aturan konflik, bukan oleh satu indikator."],
        )

    lead = next((r for r in results if r.verdict is not Verdict.NEUTRAL), results[0])
    extra = [f"Konsisten lintas indikator ({labels})."] if len(results) > 1 else []
    return VerdictResult(
        verdict=lead.verdict,
        status=EngineStatus.OK,
        event_type=lead.event_type,
        surprise_delta=lead.surprise_delta,
        mapping_shape=lead.mapping_shape,
        rationale=lead.rationale,
        notes=list(lead.notes) + extra,
    )


def apply_cross_asset_confirmation(
    result: VerdictResult,
    dxy_change,
    us10y_change,
    downgrade_on_contradiction: bool = True,
) -> VerdictResult:
    """
    Memverifikasi verdict terhadap pergerakan DXY dan US 10Y.

    BULLISH untuk risk assets mengharapkan DXY dan yield MELEMAH; BEARISH
    mengharapkan keduanya MENGUAT. Jika pasar bergerak berlawanan, verdict
    diturunkan ke NEUTRAL - sesuai PRD, verdict tanpa konfirmasi lintas aset
    belum tervalidasi.
    """
    if result.verdict is None or result.verdict is Verdict.NEUTRAL:
        return result

    if dxy_change is None or us10y_change is None:
        missing = [
            name
            for name, val in (("dxy_change", dxy_change), ("us10y_change", us10y_change))
            if val is None
        ]
        result.confirmation = Confirmation.UNAVAILABLE
        result.missing_fields = sorted(set(result.missing_fields) | set(missing))
        result.notes.append("Konfirmasi lintas aset tidak tersedia - verdict belum tervalidasi.")
        return result

    expected_negative = result.verdict is Verdict.BULLISH
    agrees = (
        (float(dxy_change) < 0) == expected_negative
        and (float(us10y_change) < 0) == expected_negative
    )

    if agrees:
        result.confirmation = Confirmation.CONFIRMED
        result.notes.append(
            f"Terkonfirmasi lintas aset: DXY {dxy_change:+.2f}%, US10Y {us10y_change:+.3f} pp."
        )
        return result

    result.confirmation = Confirmation.CONTRADICTED
    if downgrade_on_contradiction:
        original = result.verdict.value
        result.verdict = Verdict.NEUTRAL
        result.rationale = (
            f"Verdict awal {original} tidak terkonfirmasi pasar (DXY {dxy_change:+.2f}%, "
            f"US10Y {us10y_change:+.3f} pp bergerak berlawanan). Diturunkan ke NEUTRAL "
            f"untuk menghindari fake-out."
        )
        result.notes.append(f"Downgrade otomatis dari {original} karena kontradiksi lintas aset.")
    return result


# ---------------------------------------------------------------------------
# Playbook pre-event - dibaca dari tabel yang sama dengan verdict
# ---------------------------------------------------------------------------

def get_playbook(event_type, consensus) -> dict:
    """
    Menghasilkan trigger pre-event dari INDICATOR_TABLE.

    Seluruh `condition` disusun di sini, bukan di lapisan penyaji - memenuhi
    Constraint 5 pada `prompts/presenter_system_prompt.md`: operator dan arah
    tidak boleh dikarang oleh LLM.
    """
    key = str(event_type).strip().upper() if event_type is not None else None
    spec = INDICATOR_TABLE.get(key) if key else None

    if spec is None or consensus is None:
        missing = []
        if spec is None:
            missing.append("event_type")
        if consensus is None:
            missing.append("consensus")
        return {
            "status": EngineStatus.INCOMPLETE_DATA.value,
            "missing_fields": missing,
            "bullish_condition": "[MISSING]",
            "bearish_condition": "[MISSING]",
            "neutral_condition": "[MISSING]",
        }

    c = float(consensus)
    buf = spec.neutral_buffer
    neutral_condition = (
        f"Actual = {spec.format_level(c)} +/- {spec.format_level(buf)}"
        if buf > 0
        else f"Actual = {spec.format_level(c)}"
    )

    if spec.shape is MappingShape.MONOTONIC_INVERSE:
        payload = {
            "bullish_condition": f"Actual < {spec.format_level(c - buf)}",
            "bullish_rationale": "Tekanan harga melunak -> DXY melemah, ekspansi likuiditas risk-on.",
            "bearish_condition": f"Actual > {spec.format_level(c + buf)}",
            "bearish_rationale": "Inflasi persisten -> ekspektasi hawkish, DXY dan yield menguat.",
        }
    else:
        low, high = spec.band
        payload = {
            "bullish_condition": (
                f"Actual antara {spec.format_level(c + low)} dan {spec.format_level(c + high)}, "
                f"di luar neutral buffer"
            ),
            "bullish_rationale": "Zona goldilocks: melunak tanpa memicu kekhawatiran resesi.",
            "bearish_condition": (
                f"Actual > {spec.format_level(c + high)} (repricing hawkish) ATAU "
                f"Actual < {spec.format_level(c + low)} (kekhawatiran resesi)"
            ),
            "bearish_rationale": "Kedua ekor sama-sama risk-off, lewat kanal yang berbeda.",
        }

    payload.update(
        {
            "status": EngineStatus.OK.value,
            "event_type": key,
            "label": spec.label,
            "unit": spec.unit,
            "mapping_shape": spec.shape.value,
            "consensus": c,
            "neutral_condition": neutral_condition,
            "neutral_rationale": "Sesuai ekspektasi pasar - potensi whipsaw, tidak ada edge arah.",
            "calibrated": spec.calibrated,
            "note": spec.note,
            "volatility_buffer_minutes": VOLATILITY_BUFFER_MINUTES,
        }
    )
    return payload
