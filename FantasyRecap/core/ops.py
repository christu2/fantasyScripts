"""
Operational Reliability Utilities: Atomic Process Locking, Logging & Ops Alerts
===============================================================================
- `ProcessLock`: Uses `fcntl.flock` on macOS/Linux so stale PID files and PID
  recycling can never block or double-fire cron-scheduled orchestrator runs.
- `notify_ops`: Sends structured error/warning/health alerts to an optional
  `DISCORD_WEBHOOK_OPS` channel without polluting league-facing channels.
"""

import fcntl
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
import requests

from FantasyRecap.core.manifest import PROJECT_ROOT

_LEVEL_COLORS = {
    "error": 0xE74C3C,
    "warning": 0xF39C12,
    "info": 0x3498DB,
    "success": 0x2ECC71,
}


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


class ProcessLock:
    """
    Non-blocking atomic OS file lock backed by `fcntl.flock(LOCK_EX | LOCK_NB)`.
    Automatically released by the kernel if the process exits or is killed.
    """

    def __init__(self, lock_path: Path):
        self.lock_path = Path(lock_path)
        self._fp = None
        self.acquired = False

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fp = open(self.lock_path, "a+")
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fp.close()
            self.acquired = False
            return False
        fp.seek(0)
        fp.truncate()
        fp.write(f"{os.getpid()}\n")
        fp.flush()
        self._fp = fp
        self.acquired = True
        return True

    def release(self) -> None:
        if self._fp is not None:
            try:
                fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                self._fp.close()
            except OSError:
                pass
            self._fp = None
        self.acquired = False

    @classmethod
    def is_locked(cls, lock_path: Path) -> bool:
        """Non-destructively checks whether another live process currently holds the lock."""
        p = Path(lock_path)
        if not p.exists():
            return False
        fp = open(p, "a+")
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
        finally:
            fp.close()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


def notify_ops(
    title: str,
    message: str,
    *,
    level: str = "error",
    fields: Optional[List[dict]] = None,
) -> bool:
    """
    Posts an operational alert to `DISCORD_WEBHOOK_OPS` if configured in `.env`.
    Silently no-ops (returns False) when unconfigured so it never breaks local runs
    or leaks stack traces into league-facing Discord channels.
    """
    webhook = os.getenv("DISCORD_WEBHOOK_OPS", "").strip()
    if not webhook:
        return False

    payload = {
        "username": "BFL Bot Ops",
        "embeds": [
            {
                "title": title[:256],
                "description": message[:3900],
                "color": _LEVEL_COLORS.get(level.lower(), 0x95A5A6),
                "fields": fields or [],
                "footer": {"text": f"BFL Ops • {PROJECT_ROOT.name}"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
    }
    try:
        resp = requests.post(webhook, json=payload, timeout=8)
        return resp.status_code in (200, 204)
    except Exception:
        return False
