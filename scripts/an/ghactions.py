"""Ask GitHub Actions to run a workflow, and wait for what it commits.

The desk's container cannot reach a price host, but it can reach ``api.github.com`` with
the token the environment already carries for ``git push`` (``GH_TOKEN`` or
``GITHUB_TOKEN``). So instead of waiting on a scheduled cron that GitHub runs whenever it
gets round to it, the desk fires ``quotes.yml`` itself and polls the ``quotes`` branch for
a new commit. The network is behind one ``opener`` argument so the tests never touch it.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import paths

__all__ = ["token", "repo_slug", "dispatch", "branch_head", "wait_for_new_head", "API"]

API = "https://api.github.com"
Opener = Callable[[urllib.request.Request], Any]


def token() -> Optional[str]:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def repo_slug(root: Optional[Path] = None) -> Optional[str]:
    """``owner/repo`` from ``git remote get-url origin``, or None outside a GitHub clone."""
    r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(root or paths.ROOT), capture_output=True, text=True)
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/"):
        if url.startswith(prefix):
            return url[len(prefix):].removesuffix(".git").strip("/")
    return None


def _request(method: str, path: str, body: Optional[Dict[str, Any]] = None, *, auth: Optional[str] = None,
             opener: Opener = urllib.request.urlopen, timeout: float = 30.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "xoxo-desk",
        **({"Content-Type": "application/json"} if data else {}),
        **({"Authorization": f"Bearer {auth}"} if auth else {})})
    return opener(req, timeout=timeout) if opener is urllib.request.urlopen else opener(req)


def dispatch(slug: str, workflow: str, ref: str, *, auth: Optional[str] = None, opener: Opener = urllib.request.urlopen) -> bool:
    """POST a ``workflow_dispatch``. True on GitHub's 204, False on anything else (never raises)."""
    try:
        with _request("POST", f"/repos/{slug}/actions/workflows/{workflow}/dispatches", {"ref": ref},
                      auth=auth, opener=opener) as r:
            return int(getattr(r, "status", 0) or 0) == 204
    except urllib.error.HTTPError as e:
        return e.code == 204
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def branch_head(slug: str, branch: str, *, auth: Optional[str] = None, opener: Opener = urllib.request.urlopen) -> Optional[str]:
    """The tip commit of ``branch``, or None when it cannot be read."""
    try:
        with _request("GET", f"/repos/{slug}/commits/{branch}", auth=auth, opener=opener) as r:
            blob = json.loads(r.read().decode("utf-8"))
        return blob.get("sha") or None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def wait_for_new_head(slug: str, branch: str, old: Optional[str], *, timeout: float = 600.0, poll: float = 20.0,
                      auth: Optional[str] = None, opener: Opener = urllib.request.urlopen,
                      sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Optional[str]:
    """Poll until ``branch`` points somewhere other than ``old``. Returns the new sha, or None on timeout."""
    t0 = clock()
    while True:
        head = branch_head(slug, branch, auth=auth, opener=opener)
        if head and head != old:
            return head
        if clock() - t0 >= timeout:
            return None
        sleep(poll)
