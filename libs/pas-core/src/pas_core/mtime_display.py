"""Locale-aware file modification time for UI (macOS / Windows user region).

Uses :func:`locale.setlocale` with the process default (``""``) so ``strftime``
follows the user's regional date/time settings where the Python runtime exposes
them. Falls back through common UTF-8 locale names, then a Windows code-page
style name, before giving up to a fixed numeric format.
"""

from __future__ import annotations

import datetime
import locale
import sys
from pathlib import Path
from typing import Optional

_LC_TIME_INITIALIZED = False


def _ensure_lc_time_user_default() -> None:
    """Best-effort: apply OS / user default LC_TIME (macOS, Windows, Linux)."""
    global _LC_TIME_INITIALIZED
    if _LC_TIME_INITIALIZED:
        return
    _LC_TIME_INITIALIZED = True

    candidates: list[str] = [""]
    if sys.platform == "win32":
        # Typical user-default spellings on Windows (CPython accepts one that exists).
        candidates.extend(
            [
                "English_United States.1252",
                "en-US",
                "english-us",
                "C",
            ]
        )
    else:
        candidates.extend(
            [
                "C.UTF-8",
                "UTF-8",
                "en_US.UTF-8",
                "en_US.utf8",
            ]
        )

    for name in candidates:
        try:
            locale.setlocale(locale.LC_TIME, name)
            return
        except locale.Error:
            continue


def relative_mtime_ago(
    dt: datetime.datetime,
    now: Optional[datetime.datetime] = None,
) -> str:
    """English short relative phrase from naive local datetimes."""
    n = now or datetime.datetime.now()
    sec = int((n - dt).total_seconds())
    if sec < 0:
        return "in the future"
    if sec < 45:
        return "just now"
    if sec < 3600:
        m = max(1, sec // 60)
        return f"{m} min ago" if m != 1 else "1 min ago"
    if sec < 86400:
        h = max(1, sec // 3600)
        return f"{h} hrs ago" if h != 1 else "1 hr ago"
    if sec < 86400 * 14:
        d = max(1, sec // 86400)
        return f"{d} days ago" if d != 1 else "1 day ago"
    w = max(1, sec // 604800)
    return f"{w} wks ago" if w != 1 else "1 wk ago"


def format_path_mtime_for_display(
    path: Path,
    *,
    now: Optional[datetime.datetime] = None,
) -> str:
    """Return ``'<locale date/time> · <relative>'`` or ``""`` if unreadable.

    *Local part* uses ``strftime("%c")`` after setting LC_TIME to the user
    default, which tracks regional settings on macOS and Windows when available.
    """
    try:
        ts = path.stat().st_mtime
    except OSError:
        return ""

    dt = datetime.datetime.fromtimestamp(ts)
    n = now or datetime.datetime.now()
    _ensure_lc_time_user_default()
    try:
        local_part = dt.strftime("%c")
    except Exception:
        try:
            local_part = dt.strftime("%x %X")
        except Exception:
            local_part = dt.strftime("%Y-%m-%d %H:%M:%S")

    rel = relative_mtime_ago(dt, n)
    return f"{local_part} · {rel}"
