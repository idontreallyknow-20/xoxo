"""On-disk JSON cache with a TTL, atomic writes, and an explicit offline mode.

Two things this exists for.

1. Every remote source here is rate limited and free. Re-fetching what we already
   have is rude and slow, so a pull is cached by a stable key and only refreshed
   once it is older than its TTL.
2. The machine that generated most of this code had no egress to any market-data
   host. Rather than let a fetcher hang on a proxy that will refuse the CONNECT,
   the transport raises :class:`Offline` immediately when ``DESK_OFFLINE=1``, and
   every caller is written to degrade instead of crash.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

__all__ = ["Offline", "FetchError", "RateLimited", "Cache", "is_offline", "cache_key"]


class Offline(RuntimeError):
    """Raised when a network call is attempted while offline mode is on."""


class FetchError(RuntimeError):
    """A remote call failed in a way the caller is expected to handle."""

    def __init__(self, message: str, *, status: Optional[int] = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


class RateLimited(FetchError):
    """The remote asked us to slow down. Distinct so callers can back off rather than give up."""


def is_offline() -> bool:
    return os.environ.get("DESK_OFFLINE", "") not in ("", "0", "false", "no")


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def cache_key(*parts: Any) -> str:
    """A filesystem-safe, stable key.

    Readable for short keys so a human can find a cached file by eye; hashed tail
    for anything long or awkward, so the name never collides and never blows past
    a filename limit.
    """
    raw = "_".join(str(p) for p in parts)
    safe = _SAFE.sub("-", raw).strip("-")
    if len(safe) <= 96:
        return safe or "key"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{safe[:80]}-{digest}"


@dataclass(frozen=True)
class Entry:
    key: str
    path: Path
    age_seconds: float
    payload: Any


class Cache:
    """A directory of JSON files, each with a ``fetched_at`` stamp.

    ``get_or_fetch`` is the whole interface: it returns the cached payload when it
    is fresh enough, otherwise calls ``fetch`` and stores the result. If ``fetch``
    fails and a stale copy exists, the stale copy is returned rather than nothing,
    because a day-old fundamentals pull is worth far more than an exception.
    """

    def __init__(self, directory: Path, *, default_ttl: float = 86_400.0):
        self.dir = Path(directory)
        self.default_ttl = float(default_ttl)

    # -- plumbing ---------------------------------------------------------

    def path_for(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def read(self, key: str) -> Optional[Entry]:
        p = self.path_for(key)
        if not p.exists():
            return None
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return None
        if not isinstance(blob, dict) or "payload" not in blob:
            return None
        fetched_at = float(blob.get("fetched_at") or 0.0)
        return Entry(key=key, path=p, age_seconds=max(0.0, time.time() - fetched_at), payload=blob["payload"])

    def write(self, key: str, payload: Any, *, meta: Optional[dict] = None) -> Path:
        """Write via a temp file and rename, so a killed process never leaves half a JSON file."""
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.path_for(key)
        tmp = p.with_suffix(".json.tmp")
        blob = {"key": key, "fetched_at": time.time(), "meta": meta or {}, "payload": payload}
        tmp.write_text(json.dumps(blob, indent=None, separators=(",", ":"), default=str), encoding="utf-8")
        os.replace(tmp, p)
        return p

    def delete(self, key: str) -> bool:
        p = self.path_for(key)
        if p.exists():
            p.unlink()
            return True
        return False

    # -- the interface ----------------------------------------------------

    def get_or_fetch(
        self,
        key: str,
        fetch: Callable[[], Any],
        *,
        ttl: Optional[float] = None,
        allow_stale_on_error: bool = True,
        meta: Optional[dict] = None,
    ) -> Any:
        ttl = self.default_ttl if ttl is None else float(ttl)
        entry = self.read(key)
        if entry is not None and entry.age_seconds < ttl:
            return entry.payload
        try:
            payload = fetch()
        except Exception:
            if entry is not None and allow_stale_on_error:
                return entry.payload
            raise
        self.write(key, payload, meta=meta)
        return payload

    def stats(self) -> dict:
        if not self.dir.exists():
            return {"files": 0, "bytes": 0, "dir": str(self.dir)}
        files = list(self.dir.glob("*.json"))
        return {"files": len(files), "bytes": sum(f.stat().st_size for f in files), "dir": str(self.dir)}
