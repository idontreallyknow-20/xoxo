#!/usr/bin/env python3
"""Build today's digest from the scan and send it.

    python scripts/daily_email.py --preview            # write dashboard/_daily_preview.html, send nothing
    python scripts/daily_email.py --dry-run            # print who would get what; needs no credential
    python scripts/daily_email.py --send               # send over SMTP with the environment's credential
    python scripts/daily_email.py --send --only-if-alerts   # send only when a written rule fired
    python scripts/daily_email.py --eml some/dir       # write the message as a .eml file instead

The credential is read from the environment and from nowhere else:

    DESK_MAIL_USER      the Gmail address that sends
    DESK_MAIL_PASSWORD  a Gmail app password, sixteen characters
    DESK_MAIL_TO        who receives it (defaults to the sender)
    DESK_MAIL_HOST / DESK_MAIL_PORT   default smtp.gmail.com:587

Run scripts/scan.py first; this reads dashboard/watch.json, tracker.json and
positioning.json and never fetches anything itself. setup.ps1 -Daily does both.

Nothing here has sent a real message. The machine it was written on could not
open port 587.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import digest, mail, paths  # noqa: E402

PREVIEW = paths.DASHBOARD_DIR / "_daily_preview.html"   # dashboard/_* is gitignored


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--eml", type=Path, default=None, help="write a .eml into this directory instead of sending")
    ap.add_argument("--only-if-alerts", action="store_true", help="skip the send when no written rule fired")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD, default today")
    ap.add_argument("--inputs", type=Path, default=None, help="directory holding watch.json etc. (default dashboard/)")
    ap.add_argument("--subject-prefix", default="")
    a = ap.parse_args(argv)
    if not (a.send or a.dry_run or a.preview or a.eml):
        a.preview = True

    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    d = digest.build_digest(digest.load_inputs(a.inputs), today=today)
    html = digest.render_html(d)
    subject = (a.subject_prefix + " " if a.subject_prefix else "") + digest.subject_for(d)
    print(f"digest: {d['status']}, {d['n_watched']} watched, {d['n_alerts']} lines, {d['n_rule_alerts']} rules fired")
    print(f"subject: {subject}")

    if a.preview:
        PREVIEW.parent.mkdir(parents=True, exist_ok=True)
        PREVIEW.write_text(html, encoding="utf-8")
        print(f"wrote {PREVIEW} ({len(html):,} chars); open it in a browser")

    if not digest.should_send(d, only_if_alerts=a.only_if_alerts):
        print("no written rule fired and --only-if-alerts is set: not sending")
        return 0

    if a.eml:
        r = mail.RecordingMailer(a.eml).send(mail.Message(subject=subject, html=html, to=["preview@localhost"],
                                                          sender="desk@localhost"))
        mail.record(r)
        print(f"wrote {r.path}")

    if a.dry_run or a.send:
        try:
            settings = mail.settings_from_env()
        except mail.MissingCredential as e:
            if a.send:
                print(f"cannot send: {e}", file=sys.stderr)
                return 2
            print(f"dry run without a credential: {e}")
            settings = None
        message = mail.Message(subject=subject, html=html,
                               to=settings.to if settings else ["<DESK_MAIL_TO>"],
                               sender=settings.user if settings else "<DESK_MAIL_USER>")
        if a.dry_run:
            if settings:
                print(f"would send {settings.describe()}")
            r = mail.DryRunMailer().send(message)
            mail.record(r)
            p = mail.provenance()
            print(f"ever sent a real message from this checkout: {'yes, ' + p['last_live']['at'] if p['ever_live'] else 'no, never'}")
            return 0
        try:
            r = mail.SmtpMailer(settings).send(message)
        except (mail.BadCredential, mail.SendError) as e:
            print(f"send failed: {e}", file=sys.stderr)
            return 1
        mail.record(r)
        print(f"sent to {', '.join(r.to)} at {r.at} via {r.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
