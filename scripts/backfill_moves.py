"""
Menambal horizon reaksi (15m/1h/4h) yang masih null di data/live_events.jsonl.

Jalankan:  python scripts/backfill_moves.py [--dry-run] [--now 2026-09-15T10:00:00+00:00]

MENGAPA: tick.py menghitung `moves` lewat `binance.returns_after` SAAT pesan dikirim,
beberapa menit setelah rilis - horizon 1h dan 4h belum terjadi pada saat itu, jadi
tersimpan null. Tidak ada yang pernah menambalnya (na_scorecard.py menghitung ulang
live setiap kali, tapi tidak menyimpan). Script ini menambal nilai yang SUDAH bisa
dihitung, di baris yang sama.

Aturan:
- Hanya nilai null yang diisi. Nilai yang sudah ada TIDAK pernah ditimpa.
- Horizon yang belum jatuh tempo (release_utc + horizon + 30 menit > sekarang) dibiarkan
  null TANPA catatan: itu keadaan sementara yang normal, bukan kegagalan, dan catatan
  di sana hanya perlu dihapus lagi di run berikutnya.
- Horizon jatuh tempo yang tetap tidak bisa dihitung (candle tidak tersedia) tetap null
  dan diberi `moves_note["<asset>|<horizon>"]`. Tidak pernah diisi 0, tidak dikira-kira.
  Catatan menyimpan waktu percobaan PERTAMA dan tidak diubah di run berikutnya, supaya
  run berulang tanpa hasil baru tidak menulis ulang berkas. Kalau kemudian terisi,
  catatannya dihapus.
- Idempoten: urutan baris, jumlah baris, field lain dan urutan key dipertahankan; baris
  yang tidak bisa di-parse ditulis ulang apa adanya.
- Penulisan: salin ke live_events.jsonl.bak, tulis file temp di folder yang sama, lalu
  os.replace (atomik). Kalau tidak ada yang berubah, tidak ada yang ditulis (termasuk .bak).
- Balapan dengan tick.py (yang meng-APPEND): isi berkas dibaca ulang tepat sebelum
  replace; kalau berubah sejak dibaca, run ini batal menulis (exit 0) dan run berikutnya
  mengulang - lebih baik terlambat 30 menit daripada menghapus event yang baru ditulis.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVENTS_FILE = ROOT / "data" / "live_events.jsonl"
HORIZON_MINUTES = {"15m": 15, "1h": 60, "4h": 240}
GRACE = timedelta(minutes=30)
NOTE_UNAVAILABLE = "candle tidak tersedia (percobaan pertama {ts})"

ReturnsFn = Callable[[str, datetime, list], dict]


def _parse_utc(raw: object) -> Optional[datetime]:
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def plan_line(record: dict, now: datetime, returns_fn: ReturnsFn) -> tuple[dict, list[dict]]:
    """Mengembalikan (record baru, daftar perubahan). Record input tidak dimutasi."""
    release = _parse_utc(record.get("release_utc"))
    moves = record.get("moves")
    if release is None or not isinstance(moves, dict):
        return record, []

    new = json.loads(json.dumps(record))  # salinan dalam, urutan key terjaga
    new_moves = new["moves"]
    notes = dict(new.get("moves_note") or {})
    changes: list[dict] = []
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    for asset, per_h in new_moves.items():
        if not isinstance(per_h, dict):
            continue
        due = [
            h for h, v in per_h.items()
            if v is None and h in HORIZON_MINUTES
            and release + timedelta(minutes=HORIZON_MINUTES[h]) + GRACE <= now
        ]
        if not due:
            continue
        # Selalu minta ketiga horizon: memakai cache 18-candle yang sama dengan tick.py.
        computed = returns_fn(asset, release, list(HORIZON_MINUTES)) or {}
        for h in due:
            value = computed.get(h)
            key = f"{asset}|{h}"
            if value is None:
                if key not in notes:
                    notes[key] = NOTE_UNAVAILABLE.format(ts=stamp)
                    changes.append({"asset": asset, "horizon": h, "from": None, "to": None, "note": notes[key]})
                continue
            per_h[h] = value
            notes.pop(key, None)
            changes.append({"asset": asset, "horizon": h, "from": None, "to": value})

    if notes:
        new["moves_note"] = notes
    else:
        new.pop("moves_note", None)
    if new == record:
        return record, []
    return new, changes


def backfill(
    path: Path,
    now: datetime,
    returns_fn: ReturnsFn,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    if not path.exists():
        log(f"[backfill] {path} tidak ada - tidak ada yang ditambal")
        return {"changed_lines": 0, "changes": [], "written": False}

    original = path.read_bytes()
    lines = original.decode("utf-8").splitlines(keepends=True)
    out_lines: list[str] = []
    all_changes: list[dict] = []
    changed_lines = 0

    for idx, line in enumerate(lines):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        try:
            record = json.loads(body) if body.strip() else None
        except ValueError:
            record = None
        if not isinstance(record, dict):
            out_lines.append(line)  # baris kosong/rusak: apa adanya
            continue
        new, changes = plan_line(record, now, returns_fn)
        if not changes:
            out_lines.append(line)
            continue
        changed_lines += 1
        for c in changes:
            c.update({"line": idx, "indicator": record.get("indicator"), "release_utc": record.get("release_utc")})
            all_changes.append(c)
            target = c["note"] if "note" in c else c["to"]
            log(f"[backfill] baris {idx} {c['indicator']} {c['release_utc']} {c['asset']} {c['horizon']}: null -> {target}")
        out_lines.append(json.dumps(new, ensure_ascii=False) + (ending or "\n"))

    if not all_changes:
        log("[backfill] tidak ada perubahan")
        return {"changed_lines": 0, "changes": [], "written": False}
    if dry_run:
        log(f"[backfill] --dry-run: {len(all_changes)} perubahan di {changed_lines} baris, TIDAK ditulis")
        return {"changed_lines": changed_lines, "changes": all_changes, "written": False}

    if path.read_bytes() != original:
        log("[backfill] berkas berubah selama run (tick.py menulis?) - batal menulis, diulang run berikutnya")
        return {"changed_lines": changed_lines, "changes": all_changes, "written": False}

    shutil.copyfile(path, path.with_name(path.name + ".bak"))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write("".join(out_lines))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log(f"[backfill] ditulis: {len(all_changes)} perubahan di {changed_lines} baris (cadangan {path.name}.bak)")
    return {"changed_lines": changed_lines, "changes": all_changes, "written": True}


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--dry-run", action="store_true", help="cetak rencana, jangan tulis")
    parser.add_argument("--now", help="override waktu sekarang (ISO, UTC) - untuk test")
    parser.add_argument("--file", default=str(EVENTS_FILE), help="berkas jsonl (default data/live_events.jsonl)")
    args = parser.parse_args(argv)

    now = _parse_utc(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print(f"[backfill] --now tidak valid: {args.now}", file=sys.stderr)
        return 2
    from src.providers import binance  # import di sini: test menyuntik returns_fn sendiri

    backfill(Path(args.file), now, binance.returns_after, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
