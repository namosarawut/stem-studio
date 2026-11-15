"""Deleted songs, imported files and setlists wait here for a week before they are gone.

A song can hold hours of hand work (a corrected metronome, cues, a tempo lock), so a
delete only moves it aside. Each entry is a folder data/trash/<entry>/ holding the item
(`item`, a folder or a file) and `trash.json` describing what it was and where it lived.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path

from . import fsutil

KEEP_DAYS = 7
ENTRY_ID = re.compile(r"^[a-z]+-[0-9a-f]{6,16}-[0-9a-f]{8}$")
KINDS = ("job", "import", "playlist")


class TrashError(ValueError):
    """Shown to the person as is."""


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def move_in(trash: Path, kind: str, item_id: str, source: Path, title: str, extra: dict | None = None) -> dict:
    """Move `source` (a folder or a file) into the trash. Returns the entry's description."""
    if kind not in KINDS:
        raise TrashError("ชนิดไม่ถูกต้อง")
    entry_id = f"{kind}-{item_id}-{uuid.uuid4().hex[:8]}"
    folder = trash / entry_id
    folder.mkdir(parents=True)
    info = {
        "entry": entry_id, "kind": kind, "id": item_id, "title": title, "deleted_at": time.time(),
        "size_mb": round(_size(source) / 1e6, 1), "file": source.name if source.is_file() else None, **(extra or {}),
    }
    (folder / "trash.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    fsutil.replace(source, folder / "item")  # a rename on the same disk: instant and atomic
    return info


def entries(trash: Path) -> list[dict]:
    found = []
    for meta in trash.glob("*/trash.json"):
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        info["expires_at"] = info["deleted_at"] + KEEP_DAYS * 86400
        found.append(info)
    return sorted(found, key=lambda e: e["deleted_at"], reverse=True)


def _entry(trash: Path, entry_id: str) -> Path:
    if not ENTRY_ID.match(entry_id) or not (trash / entry_id / "trash.json").exists():
        raise TrashError("ไม่พบรายการนี้ในถังขยะ")
    return trash / entry_id


def take_out(trash: Path, entry_id: str, target: Path) -> dict:
    """Move an entry's item back to `target`. Refuses when something is already there."""
    folder = _entry(trash, entry_id)
    info = json.loads((folder / "trash.json").read_text(encoding="utf-8"))
    if target.exists():
        raise TrashError("มีรายการชื่อเดียวกันอยู่แล้ว กู้คืนทับไม่ได้")
    target.parent.mkdir(parents=True, exist_ok=True)
    fsutil.replace(folder / "item", target)
    shutil.rmtree(folder, ignore_errors=True)
    return info


def info_of(trash: Path, entry_id: str) -> dict:
    return json.loads((_entry(trash, entry_id) / "trash.json").read_text(encoding="utf-8"))


def remove(trash: Path, entry_id: str) -> None:
    shutil.rmtree(_entry(trash, entry_id), ignore_errors=True)


def purge(trash: Path, now: float | None = None) -> int:
    """Delete entries older than KEEP_DAYS. Returns how many went."""
    now = now or time.time()
    gone = 0
    for info in entries(trash):
        if info["expires_at"] <= now:
            shutil.rmtree(trash / info["entry"], ignore_errors=True)
            gone += 1
    return gone
