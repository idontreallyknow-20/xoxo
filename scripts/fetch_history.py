#!/usr/bin/env python3
"""Fetch the public daily-price history, parse it, audit it, write the provenance.

    python scripts/fetch_history.py --dry-run        # every URL and destination, nothing fetched
    python scripts/fetch_history.py                  # fetch what is missing, parse, audit, write manifest
    python scripts/fetch_history.py --audit          # re-run the audit on the cache only
    python scripts/fetch_history.py --make-fixture   # cut tests/fixtures/history/ from the cache
    python scripts/fetch_history.py --check          # accepted for build_all; does nothing

Everything lands under data/cache/history/ (gitignored). The manifest records, per source,
the URL, sha256, byte count, rows, first and last date, and when it was fetched. The audit
gate is written into the manifest and the paper-trading CLI refuses to run live on FAIL.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import history  # noqa: E402

USER_AGENT = "xoxo research desk (fetch_history.py)"


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    bundle = os.environ.get("SSL_CERT_FILE") or "/root/.ccr/ca-bundle.crt"
    if Path(bundle).exists():
        try:
            ctx.load_verify_locations(bundle)
        except ssl.SSLError:
            pass
    return ctx


def fetch(url: str, *, timeout: float = 120.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return resp.read()


def _describe(src: history.Source, data: bytes) -> Dict[str, Any]:
    info: Dict[str, Any] = {"url": src.url, "sha256": history.sha256_of(data), "bytes": len(data), "kind": src.kind,
                            "role": src.role}
    try:
        if src.kind == "plotly_long":
            df = pd.read_csv(Path(history.RAW_DIR / src.filename), usecols=["date", "Name"])
            info.update(rows=int(len(df)), first=str(df["date"].min()), last=str(df["date"].max()),
                        n_tickers=int(df["Name"].nunique()))
        elif src.kind == "lean_zip":
            df = history.load_lean_zip(data, src.filename.split(".")[0])
            info.update(rows=int(len(df)), first=df.index[0].date().isoformat(), last=df.index[-1].date().isoformat())
        elif src.kind == "lean_factor":
            df = history.load_lean_factor_file(data.decode("utf-8"))
            info.update(rows=int(len(df)))
        elif src.kind == "stocknet_csv":
            df = history.load_stocknet_csv(data.decode("utf-8"))
            info.update(rows=int(len(df)), first=df.index[0].date().isoformat(), last=df.index[-1].date().isoformat())
        elif src.kind == "membership_csv":
            m = history.load_membership(data.decode("utf-8"))
            info.update(rows=sum(len(v) for v in m.intervals.values()), n_tickers=len(m.intervals))
    except Exception as e:  # noqa: BLE001 - provenance must still be written
        info["parse_error"] = f"{type(e).__name__}: {e}"
    return info


def build(root: Path, *, do_fetch: bool, verbose: bool = True) -> Dict[str, Any]:
    raw = root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    sources: Dict[str, Dict[str, Any]] = {}
    for src in history.SOURCES:
        p = raw / src.filename
        if not p.exists() or p.stat().st_size < 20:
            if not do_fetch:
                sources[src.key] = {"url": src.url, "missing": True}
                continue
            if verbose:
                print(f"  fetching {src.key}: {src.url}")
            data = fetch(src.url)
            p.write_bytes(data)
        data = p.read_bytes()
        sources[src.key] = _describe(src, data)
        if verbose:
            d = sources[src.key]
            print(f"  {src.key:14s} sha256 {d['sha256'][:12]}… {d['bytes']:>10,d} B  rows {d.get('rows', '?')}  "
                  f"{d.get('first', '')}..{d.get('last', '')}{'  PARSE ERROR: ' + d['parse_error'] if 'parse_error' in d else ''}")

    # parse the universe
    panel = history.load_plotly_long(str(raw / "all_stocks_5yr.csv"))
    bench = {}
    factors = {}
    for b in history.BENCHMARKS:
        zp, fp = raw / f"{b.lower()}.zip", raw / f"factor_{b.lower()}.csv"
        if zp.exists() and fp.exists():
            fac = history.load_lean_factor_file(fp.read_text(encoding="utf-8"))
            adj = history.apply_lean_factors(history.load_lean_zip(zp.read_bytes(), b.lower()), fac, dividends=False)
            bench[b] = adj["close"]
            factors[b] = fac
    panel.benchmarks = pd.DataFrame({b: s.reindex(panel.close.index) for b, s in bench.items()},
                                    index=panel.close.index).reindex(columns=list(history.BENCHMARKS))

    membership = None
    mp = raw / "sp500_ticker_start_end.csv"
    if mp.exists():
        membership = history.load_membership(mp.read_text(encoding="utf-8"))

    # cross-checks: plotly vs Lean (split-only adjusted) and vs stocknet Close
    cross = []
    for t in ("AAPL", "IBM", "BAC", "AIG"):
        zp, fp = raw / f"{t.lower()}.zip", raw / f"factor_{t.lower()}.csv"
        if zp.exists() and fp.exists():
            fac = history.load_lean_factor_file(fp.read_text(encoding="utf-8"))
            adj = history.apply_lean_factors(history.load_lean_zip(zp.read_bytes(), t.lower()), fac, dividends=False)
            cross.append((t, "lean split-adjusted", adj["close"], "plotly"))
    for t in ("AAPL", "MSFT"):
        sp = raw / f"stocknet_{t}.csv"
        if sp.exists() and sp.stat().st_size > 100:
            cross.append((t, "stocknet close", history.load_stocknet_csv(sp.read_text(encoding="utf-8"))["close"], "plotly"))

    report = history.audit(panel, membership=membership, cross=cross, benchmark_factors=factors)
    if report.dataset_verdict == "unadjusted":
        panel = history.adjust_for_splits(panel, report.findings)
        report = history.audit(panel, membership=membership, cross=cross, benchmark_factors=factors)

    written = history.write_panel(panel, root / "panel")
    manifest = {
        "fetched_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "sources": sources,
        "panel": {"source": "plotly all_stocks_5yr + QuantConnect Lean SPY/QQQ (split-only adjusted)",
                  "sessions": panel.sessions, "tickers": len(panel.tickers), "first": panel.first, "last": panel.last,
                  "basis": "price returns; no dividends on either side", "files": written,
                  "adjustments_applied": [a.to_json() for a in panel.adjustments]},
        "audit": report.to_json(),
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def print_audit(a: Dict[str, Any]) -> None:
    print(f"\n  dataset adjustment verdict: {a['dataset_verdict']}   gate: {a['gate']}")
    for f in a["findings"]:
        if f["verdict"] == "candidate":
            continue
        print(f"    {f['ticker']:6s} {f['date']}  ratio seen {f['ratio_seen']}  known {f['ratio_known']}  "
              f"volume x{None if f['volume_multiple'] is None else round(f['volume_multiple'], 1)}  -> {f['verdict']}")
    cands = [f for f in a["findings"] if f["verdict"] == "candidate"]
    if cands:
        print(f"    {len(cands)} split-like moves found, none applied: "
              + ", ".join(f"{c['ticker']} {c['date']} x{c['ratio_seen']} (volume x{None if c['volume_multiple'] is None else round(c['volume_multiple'], 1)})" for c in cands[:10]))
    if a.get("suspect_tickers"):
        print(f"    suspected data artefacts, excluded from the eligible universe: {', '.join(a['suspect_tickers'])}")
    for c in a["cross_checks"]:
        print(f"    cross-check {c['ticker']:5s} {c['a']} vs {c['b']}: n={c['n_overlap']} daily-return |diff| median "
              f"{c['median_abs_rel_diff']} max {c['max_abs_rel_diff']}, level ratio {c['level_ratio']} -> "
              f"{'ok' if c['passed'] else 'FAIL'}")
    m = a["membership"]
    print(f"    membership: {m['members_on_first_session']} of {m['n_tickers']} were in the index on the first session, "
          f"{m['members_on_last_session']} on the last")
    print(f"    benchmark splits inside the window: {a['benchmark_splits_in_window']}")
    if a["thin_tickers"]:
        print(f"    thin tickers (<500 sessions): {', '.join(a['thin_tickers'][:20])}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--audit", action="store_true", help="re-audit the cache, fetch nothing")
    ap.add_argument("--make-fixture", action="store_true")
    ap.add_argument("--root", default=str(history.HISTORY_CACHE))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    root = Path(a.root)

    if a.check:
        print("fetch_history.py: nothing to do in --check mode")
        return 0
    if a.dry_run:
        for s in history.SOURCES:
            print(f"  {s.key:14s} {s.role:11s} {s.url}\n{'':30s}-> {root / 'raw' / s.filename}")
        return 0
    if a.make_fixture:
        cached = history.load_cached(root)
        if cached is None:
            print("no cache to cut a fixture from; run without flags first", file=sys.stderr)
            return 1
        panel, mem, manifest = cached
        parent = {k: v.get("sha256") for k, v in manifest.get("sources", {}).items() if isinstance(v, dict) and v.get("sha256")}
        files = history.make_fixture(panel, mem, parent=parent)
        history.FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (history.FIXTURE_DIR / name).write_text(text, encoding="utf-8")
            print(f"  wrote {history.FIXTURE_DIR / name} ({len(text):,d} B)")
        return 0

    manifest = build(root, do_fetch=not a.audit)
    print_audit(manifest["audit"])
    print(f"\n  panel: {manifest['panel']['sessions']} sessions x {manifest['panel']['tickers']} tickers, "
          f"{manifest['panel']['first']}..{manifest['panel']['last']}; manifest at {root / 'manifest.json'}")
    return 0 if manifest["audit"]["gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
