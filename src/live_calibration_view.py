"""
Data untuk section dashboard "Live vs Backtest" — MURNI dan read-only.

`data/calibration_live.json` ditulis `scripts/build_calibration.py --live-only`. Tidak ada yang
membacanya di UI, jadi angkanya tidak terlihat siapa pun. Modul ini menyiapkan barisnya supaya
dashboard hanya merender, dan supaya jalur "file ada" / "file tidak ada" bisa diuji tanpa
Streamlit.

Batas yang disengaja:
- TIDAK menyentuh `data/calibration.json` maupun `src.action.load_calibration` — itu satu-satunya
  sumber keputusan. Modul ini tidak menulis apa pun.
- Sampel kecil diberi label apa adanya: di bawah MIN_CALIBRATION_N sinyal berarah, angka live
  adalah PEMBANDING, bukan kalibrasi. Hit-rate dari 2 event tidak disajikan setara backtest.
- Nilai yang tidak ada tampil sebagai "—", tidak pernah 0.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
LIVE_CALIBRATION_PATH = ROOT / "data" / "calibration_live.json"

# Selaras dengan ukuran sampel backtest per pasangan (puluhan rilis). Di bawah ini: pembanding.
MIN_CALIBRATION_N = 30


def _fmt(value, suffix: str = "") -> str:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{value}{suffix}"


def load_live_calibration(path: Optional[Path] = None) -> tuple[Optional[dict], Optional[str]]:
    """(isi, alasan kosong). File hilang/rusak = (None, alasan) — bukan exception, bukan 0."""
    path = Path(path or LIVE_CALIBRATION_PATH)
    if not path.exists():
        return None, "belum ada sampel live"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, f"{path.name} tidak terbaca — belum ada sampel live yang bisa ditampilkan"
    if not isinstance(data, dict) or not isinstance(data.get("total"), dict):
        return None, f"{path.name} tidak berbentuk ringkasan live — belum ada sampel live"
    return data, None


def live_calibration_view(data: Optional[dict], empty_reason: Optional[str] = None) -> dict:
    """Bentuk siap-render: status, baris tabel, peringatan, n. MURNI."""
    if data is None or not (data.get("total") or {}).get("n_events"):
        return {"status": "empty", "message": empty_reason or "belum ada sampel live",
                "rows": [], "warning": None, "n": 0, "revision_line": None}

    total = data["total"]
    n = int(total.get("n") or 0)
    rows = []
    for ind, b in list((data.get("by_indicator") or {}).items()) + [("TOTAL", total)]:
        rows.append(
            {
                "IND": ind,
                "EVENT": b.get("n_events", 0),
                "NEUTRAL": b.get("neutral", 0),
                "BELUM TERUKUR": b.get("unmeasured", 0),
                "n": b.get("n", 0),
                "HIT": b.get("hits", 0),
                "LIVE %": _fmt(b.get("hit_rate")),
                "BACKTEST %": _fmt(b.get("backtest_alignment_rate")),
                "DELTA pp": _fmt(b.get("delta_pp")),
            }
        )
    warning = (
        f"n={n} sinyal berarah: pembanding, BUKAN kalibrasi (butuh >= {MIN_CALIBRATION_N}). "
        "Keputusan tetap memakai data/calibration.json (backtest)."
        if n < MIN_CALIBRATION_N
        else f"n={n}: masih sampel live; keputusan tetap memakai data/calibration.json."
    )
    rev = ((data.get("revisions") or {}).get("total")) or {}
    revision_line = (
        f"Revisi (GDP second/third estimate) dipisah: {rev.get('n_events')} event, n={rev.get('n', 0)}, "
        f"hit%={_fmt(rev.get('hit_rate'))} — tidak masuk tabel di atas."
        if rev.get("n_events") else None
    )
    return {
        "status": "ok",
        "message": f"{data.get('rule', '')} · dibuat {data.get('generated_at', '—')}",
        "rows": rows,
        "warning": warning,
        "n": n,
        "revision_line": revision_line,
    }
