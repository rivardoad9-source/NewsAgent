"""
Identitas RILIS vs identitas ANGKA — untuk indikator yang direvisi dengan obs_date sama (GDP).

MASALAHNYA. Dedupe feed belajar memakai (indicator, obs_date). GDP punya advance / second /
third estimate untuk kuartal yang SAMA: obs_date FRED (`A191RL1Q225SBEA`) tidak berubah,
hanya nilainya. Dengan kunci itu revisi terlihat "sudah tercatat" dan hilang tanpa error.
(Sebelum modul ini GDP bahkan tidak dipantau tick sama sekali, justru karena itu.)

PILIHAN KUNCI: (b) sidik-jari nilai — (indicator, obs_date, nilai dibulatkan 4 desimal).
- FRED hanya menyajikan vintage TERBARU lewat `series/observations` yang dipakai provider;
  tahap estimasi (advance/second/third) tidak ada di respons. Kunci (a) berbasis tahap
  berarti MENEBAK tahap dari kalender, dan satu tebakan salah = revisi yang tertelan lagi.
- Nilai adalah satu-satunya fakta yang terbaca langsung: nilai sama = tidak ada informasi
  baru (dedupe jalan), nilai beda = revisi (dicatat).
- Konsekuensi yang disengaja: estimasi berikutnya yang nilainya PERSIS sama dengan
  sebelumnya TIDAK dicatat. Tidak ada angka baru, jadi tidak ada surprise untuk diukur.
- Label tahap (`estimate_stage`) diturunkan dari URUTAN nilai berbeda yang teramati, bukan
  dari BEA — itu keterangan, bukan kunci, dan disebut basisnya.

`prior_value` = nilai terakhir yang TERCATAT untuk (indicator, obs_date) di feed belajar,
atau nilai baseline yang disimpan `--seed`/tick pertama di state. Bukan dari vintage FRED
(provider tidak mengambil ALFRED/realtime), jadi revisi yang terjadi saat tick tidak
berjalan dibandingkan dengan nilai terakhir yang engine LIHAT, bukan estimasi resmi
sebelumnya.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

STAGES = ("advance", "second", "third")
STAGE_BASIS = "urutan nilai berbeda yang teramati untuk obs_date ini (bukan label BEA)"
DECIMALS = 4


def _norm(value) -> Optional[float]:
    try:
        return round(float(value), DECIMALS)
    except (TypeError, ValueError):
        return None


def recorded_values(records: Iterable[dict], indicator: str, obs_date: str) -> list[float]:
    """Nilai `actual` yang sudah tercatat untuk (indicator, obs_date), urut kemunculan, unik."""
    out: list[float] = []
    for rec in records:
        if rec.get("indicator") != indicator or rec.get("obs_date") != obs_date:
            continue
        v = _norm(rec.get("actual"))
        if v is not None and v not in out:
            out.append(v)
    return out


def stage_label(index: int) -> str:
    return STAGES[index] if index < len(STAGES) else f"revisi ke-{index - len(STAGES) + 1} setelah third"


def classify(actual, known_values: list[float]) -> Optional[dict]:
    """None = angka ini sudah dikenal (jangan catat). Selain itu: keterangan rilis. MURNI."""
    value = _norm(actual)
    if value is None:
        return None
    known = [v for v in (_norm(k) for k in known_values) if v is not None]
    if value in known:
        return None
    if not known:
        return {"revision": False, "estimate_stage": stage_label(0)}
    prior = known[-1]
    return {
        "revision": True,
        "estimate_stage": stage_label(len(known)),
        "prior_value": prior,
        "revision_delta": round(value - prior, DECIMALS),
    }


def quarter_label(obs_date: str) -> str:
    try:
        d = date.fromisoformat(str(obs_date)[:10])
    except ValueError:
        return str(obs_date)
    return f"Q{(d.month - 1) // 3 + 1} {d.year}"


def revision_sentence(indicator: str, obs_date: str, info: dict, actual, moves: Optional[dict],
                      asset: str = "BTC/USD", horizon: str = "1h", unit: str = "%") -> str:
    """Kalimat eksplisit untuk revisi; tidak pernah menyamarkan revisi jadi rilis biasa."""
    delta = info["revision_delta"]
    suffix = "pp" if unit == "%" else f" {unit}".rstrip()
    move = ((moves or {}).get(asset) or {}).get(horizon)
    reaction = (
        f"reaksi {horizon} {asset} belum terukur"
        if not isinstance(move, (int, float)) or isinstance(move, bool)
        else f"reaksi {horizon} {asset} {move:+.2f}%"
    )
    return (
        f"{indicator} {quarter_label(obs_date)} {info['estimate_stage']} estimate: REVISI "
        f"{delta:+.2f}{suffix} ({info['prior_value']:.2f} -> {float(actual):.2f}), {reaction}"
    )
