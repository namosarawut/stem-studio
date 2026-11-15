"""File swaps that behave the same on macOS and Windows.

Prism rebuilds files while a browser may still be downloading the old ones, and relies on
a rename to swap the new version in. On macOS (POSIX) that always works: a reader keeps
the old file until it closes it. On Windows a file that is open, or a folder holding an
open file, cannot be replaced, renamed or deleted (PermissionError), so there:

- `replace` retries for a while; a download from 127.0.0.1 finishes quickly.
- `discard` leaves a `<name>.stale` marker when the file cannot go yet, so a cache such
  as stems.zip is rebuilt instead of served out of date (`is_current`).
- `try_unlink` gives up quietly: for leftovers nothing points at any more.

Everything is a plain call through on other systems.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

WINDOWS = sys.platform == "win32"
RETRY_SECONDS = 15.0


def _retry(action):
    deadline = time.monotonic() + RETRY_SECONDS
    delay = 0.02
    while True:
        try:
            return action()
        except PermissionError:
            if not WINDOWS or time.monotonic() > deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.5)


def replace(source: Path | str, target: Path | str) -> None:
    """os.replace (atomic on the same disk), waiting on Windows while the target is open."""
    _retry(lambda: os.replace(source, target))
    _marker(Path(target)).unlink(missing_ok=True)


def _marker(path: Path) -> Path:
    return path.with_name(path.name + ".stale")


def discard(path: Path) -> None:
    """Delete a cached file. If Windows will not let it go yet, mark it stale instead."""
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        _marker(path).touch()
        return
    _marker(path).unlink(missing_ok=True)


def is_current(path: Path) -> bool:
    """The cached file exists and has not been discarded since it was built."""
    return path.exists() and not _marker(path).exists()


def try_unlink(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except PermissionError:
        return False


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def sweep(folder: Path, pattern: str) -> None:
    """Remove leftovers (old folders or temp files) that an open file kept alive before."""
    for old in folder.glob(pattern):
        if old.is_dir():
            remove_tree(old)
        else:
            try_unlink(old)
