"""A small, polite HTTP layer with an injectable transport.

Every remote source in this project is free and rate limited, and the machine this
code was written on could not reach any of them. Both facts point the same way: put
the network behind an interface, make the real one well-behaved, and make the test
one a dictionary.

:class:`HttpTransport` uses ``urllib`` from the standard library rather than
``requests`` so the analysis layer adds no dependency. It carries a token-bucket
rate limiter (the SEC asks for no more than 10 requests a second and means it),
retries on 429 and 5xx with exponential backoff, and honours ``Retry-After``.

:class:`FixtureTransport` maps a URL to a file on disk and raises on anything it
was not given, so a test that accidentally reaches for the network fails loudly.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from .store import FetchError, Offline, RateLimited, is_offline

__all__ = ["Transport", "HttpTransport", "FixtureTransport", "RecordingTransport", "DryRunTransport", "redact"]

_SECRET_KEYS = ("token", "apikey", "api_key", "key", "auth", "password", "secret")


def redact(url: str) -> str:
    """Strip credentials out of a URL so it is safe to print, log, or put in an error."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<unparseable url>"
    if not parts.query:
        return url
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    cleaned = [(k, "***REDACTED***" if k.lower() in _SECRET_KEYS else v) for k, v in pairs]
    query = urllib.parse.urlencode(cleaned, safe="*")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


class Transport(Protocol):
    def get(self, url: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> bytes: ...


class _Bucket:
    """Token bucket. Simple, single-threaded, good enough for a nightly pull."""

    def __init__(self, rate_per_second: float):
        self.rate = float(rate_per_second)
        self._allowance = self.rate
        self._last = time.monotonic()

    def take(self) -> None:
        if self.rate <= 0:
            return
        now = time.monotonic()
        self._allowance = min(self.rate, self._allowance + (now - self._last) * self.rate)
        self._last = now
        if self._allowance < 1.0:
            time.sleep((1.0 - self._allowance) / self.rate)
            self._allowance = 0.0
            self._last = time.monotonic()
        else:
            self._allowance -= 1.0


@dataclass
class HttpTransport:
    user_agent: str
    rate_per_second: float = 8.0
    max_retries: int = 3
    backoff: float = 1.5
    _bucket: _Bucket = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._bucket = _Bucket(self.rate_per_second)

    def get(self, url: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> bytes:
        if is_offline():
            raise Offline(f"DESK_OFFLINE is set, refusing to fetch {redact(url)}")
        h = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate", "Accept": "application/json, text/html;q=0.9"}
        h.update(headers or {})
        last: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._bucket.take()
            req = urllib.request.Request(url, headers=h)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        import gzip

                        raw = gzip.decompress(raw)
                    return raw
            except urllib.error.HTTPError as e:
                status = e.code
                if status == 429:
                    wait = float(e.headers.get("Retry-After") or self.backoff ** (attempt + 1))
                    last = RateLimited(f"429 from {redact(url)}", status=429, url=redact(url))
                    if attempt < self.max_retries:
                        time.sleep(min(wait, 30.0))
                        continue
                    raise last
                if 500 <= status < 600 and attempt < self.max_retries:
                    last = FetchError(f"{status} from {redact(url)}", status=status, url=redact(url))
                    time.sleep(self.backoff ** (attempt + 1))
                    continue
                raise FetchError(f"{status} from {redact(url)}", status=status, url=redact(url)) from e
            except urllib.error.URLError as e:
                last = FetchError(f"{e.reason} for {redact(url)}", url=redact(url))
                if attempt < self.max_retries:
                    time.sleep(self.backoff ** (attempt + 1))
                    continue
                raise last from e
        raise last or FetchError(f"gave up on {redact(url)}")


@dataclass
class FixtureTransport:
    """Serve recorded responses. Anything not recorded raises, so a stray call is loud."""

    mapping: Dict[str, Any] = field(default_factory=dict)
    calls: List[str] = field(default_factory=list)

    def add(self, url: str, payload: Any) -> "FixtureTransport":
        self.mapping[url] = payload
        return self

    def add_file(self, url: str, path: Path) -> "FixtureTransport":
        self.mapping[url] = Path(path).read_bytes()
        return self

    def get(self, url: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> bytes:
        self.calls.append(url)
        if url not in self.mapping:
            raise FetchError(f"no fixture for {redact(url)}", status=404, url=redact(url))
        payload = self.mapping[url]
        if isinstance(payload, bytes):
            return payload
        if isinstance(payload, str):
            return payload.encode("utf-8")
        return json.dumps(payload).encode("utf-8")


@dataclass
class DryRunTransport:
    """Print what would be fetched and return nothing. Used by every ``--dry-run`` CLI."""

    sink: Callable[[str], None] = print
    calls: List[str] = field(default_factory=list)

    def get(self, url: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> bytes:
        safe = redact(url)
        self.calls.append(safe)
        self.sink(f"GET {safe}")
        for k, v in sorted((headers or {}).items()):
            self.sink(f"     {k}: {v}")
        raise Offline("dry run: no request was sent")


@dataclass
class RecordingTransport:
    """Wrap a real transport and save every response as a fixture, for building test data."""

    inner: Transport
    out_dir: Path

    def get(self, url: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> bytes:
        from .store import cache_key

        raw = self.inner.get(url, headers=headers, timeout=timeout)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / f"{cache_key(redact(url))}.bin").write_bytes(raw)
        return raw
