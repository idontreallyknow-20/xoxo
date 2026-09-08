#!/usr/bin/env python3
"""Build today's digest from the scan and send it.

    python scripts/daily_email.py --preview            # write dashboard/_daily_preview.html, send nothing
    python scripts/daily_email.py --edition midday --preview      # the 12:00 edition, from watch_intraday.json
    python scripts/daily_email.py --edition event --send --only-new-alerts   # the half-hourly watch: sends only
                                                       # when a rule fired and that alert has not been emailed
    python scripts/daily_email.py --dry-run            # print who would get what; needs no credential
    python scripts/daily_email.py --send               # send over SMTP with the environment's credential
    python scripts/daily_email.py --send --only-if-alerts   # send only when a written rule fired
    python scripts/daily_email.py --eml some/dir       # write the message as a .eml file instead
    python scripts/daily_email.py --edition close --html-out data/cache/mail/out.html   # for the Gmail connector

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

from an import digest, mail, paths, watch  # noqa: E402

PREVIEW = paths.DASHBOARD_DIR / "_daily_preview.html"   # dashboard/_* is gitignored


def preview_path(edition: str) -> Path:
    return PREVIEW if edition == "morning" else paths.DASHBOARD_DIR / f"_daily_preview_{edition}.html"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--eml", type=Path, default=None, help="write a .eml into this directory instead of sending")
    ap.add_argument("--only-if-alerts", action="store_true", help="skip the send when no written rule fired")
    ap.add_argument("--edition", choices=digest.EDITIONS, default="morning")
    ap.add_argument("--only-new-alerts", action="store_true",
                    help="drop alerts already emailed (data/cache/watch/state.json) and remember the ones sent")
    ap.add_argument("--state", type=Path, default=watch.STATE_PATH)
    ap.add_argument("--date", default=None, help="YYYY-MM-DD, default today")
    ap.add_argument("--inputs", type=Path, default=None, help="directory holding watch.json etc. (default dashboard/)")
    ap.add_argument("--subject-prefix", default="")
    ap.add_argument("--html-out", type=Path, default=None,
                    help="write the rendered HTML here and the subject beside it (PATH.subject.txt), for another sender")
    a = ap.parse_args(argv)
    if not (a.send or a.dry_run or a.preview or a.eml or a.html_out):
        a.preview = True

    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    state = watch.WatchState.load(a.state) if a.only_new_alerts else None
    d = digest.build_digest(digest.load_inputs(a.inputs), today=today, edition=a.edition,
                            seen_alert_keys=set(state.seen_alerts) if state else None)
    html = digest.render_html(d)
    subject = (a.subject_prefix + " " if a.subject_prefix else "") + digest.subject_for(d)
    print(f"{a.edition}: {d['status']}, {d['n_watched']} watched, {d['n_alerts']} lines, {d['n_rule_alerts']} rules fired"
          + (f" ({len(state.seen_alerts)} alert keys already sent)" if state else ""))
    print(f"subject: {subject}")

    if a.html_out:
        a.html_out.parent.mkdir(parents=True, exist_ok=True)
        a.html_out.write_text(html, encoding="utf-8")
        Path(str(a.html_out) + ".subject.txt").write_text(subject + "\n", encoding="utf-8")
        Path(str(a.html_out) + ".txt").write_text(mail.text_from_html(html), encoding="utf-8")
        print(f"wrote {a.html_out} and its subject and text alternative")

    if a.preview:
        out = preview_path(a.edition)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(f"wrote {out} ({len(html):,} chars); open it in a browser")

    if not digest.should_send(d, only_if_alerts=a.only_if_alerts):
        print("no written rule fired" + (" that has not been sent" if a.only_new_alerts else "")
              + f" and the {a.edition} edition sends only on one: not sending")
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
        if state is not None:
            state.remember_alerts(d["alerts"])
            state.save(a.state)
            print(f"remembered {len(d['alerts'])} alert(s) as sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
