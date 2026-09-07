"""Send a rendered email over SMTP, with the credential from the environment only.

``scripts/email_picks.py`` renders an email and stops. This is the sending half,
built the way every fetcher in this layer is built: the network sits behind an
injectable object, the dry run prints exactly what the real run would do, and a
file on disk says whether a real message has ever gone out.

Credentials. Gmail wants an app password (Google account, Security, 2-Step
Verification, App passwords), sixteen characters, which is a full credential for
the mailbox. It lives in ``DESK_MAIL_PASSWORD`` and nowhere else: not in a flag
(shell history), not in a file (``tests/test_privacy.py`` scans every tracked
file for exactly that), and never in a log line or an exception. ``SmtpMailer``
keeps the password out of its own ``repr`` and the error it raises on a refused
login quotes the server's reply with the password stripped out, because SMTP
servers occasionally echo the AUTH string back.

Where it runs. ``setup.ps1 -InstallTask`` stores the four variables as
user-scope environment variables on Windows (the registry, not a file in this
repo) so the scheduled task can read them without a shell open.

Nothing here has sent a real message. The machine it was written on could not
open port 587 to anything.
"""
from __future__ import annotations

import datetime as dt
import html as htmllib
import json
import os
import re
import smtplib
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from . import paths

__all__ = [
    "ENV_USER", "ENV_PASSWORD", "ENV_TO", "ENV_HOST", "ENV_PORT",
    "MailSettings", "Message", "Receipt", "Mailer",
    "SmtpMailer", "DryRunMailer", "RecordingMailer",
    "MissingCredential", "BadCredential", "SendError",
    "settings_from_env", "text_from_html", "provenance", "record", "redact_secret",
    "MAIL_CACHE",
]

ENV_USER = "DESK_MAIL_USER"
ENV_PASSWORD = "DESK_MAIL_PASSWORD"
ENV_TO = "DESK_MAIL_TO"
ENV_HOST = "DESK_MAIL_HOST"
ENV_PORT = "DESK_MAIL_PORT"

DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587

MAIL_CACHE = paths.CACHE_DIR / "mail"
SENT_LOG = MAIL_CACHE / "sent.json"


class MissingCredential(RuntimeError):
    """A required environment variable is not set. The message names the variable, never a value."""


class BadCredential(RuntimeError):
    """The server refused the login. The message carries the server's reply with the secret stripped."""


class SendError(RuntimeError):
    """The server accepted the login and then refused the message, or the connection failed."""


@dataclass(frozen=True)
class MailSettings:
    user: str
    password: str = field(repr=False)
    to: List[str]
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    def describe(self) -> str:
        """One line for a dry run. The password is described by its length only."""
        return (f"from {self.user} to {', '.join(self.to)} via {self.host}:{self.port} "
                f"(password: {len(self.password)} characters, from {ENV_PASSWORD})")


def settings_from_env(env: Optional[Dict[str, str]] = None) -> MailSettings:
    """Read the five variables. Raises :class:`MissingCredential` naming the first one absent."""
    e = os.environ if env is None else env
    user = (e.get(ENV_USER) or "").strip()
    password = e.get(ENV_PASSWORD) or ""
    to_raw = (e.get(ENV_TO) or "").strip()
    for name, value in ((ENV_USER, user), (ENV_PASSWORD, password)):
        if not value:
            raise MissingCredential(f"{name} is not set. It is read from the environment and from nowhere else.")
    to = [t.strip() for t in re.split(r"[,;\s]+", to_raw) if t.strip()] or [user]
    host = (e.get(ENV_HOST) or DEFAULT_HOST).strip()
    try:
        port = int((e.get(ENV_PORT) or DEFAULT_PORT))
    except ValueError as err:
        raise MissingCredential(f"{ENV_PORT} must be an integer") from err
    return MailSettings(user=user, password=password, to=to, host=host, port=port)


def redact_secret(text: str, secret: str) -> str:
    """Strip a secret from a string that might echo it (an SMTP reply, an exception)."""
    if not secret:
        return text
    return text.replace(secret, "***REDACTED***")


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


def text_from_html(html: str) -> str:
    """A plain-text part for clients that will not render HTML. Crude on purpose:
    the HTML is written so its reading order is its source order."""
    s = re.sub(r"(?is)<(style|script|head)\b.*?</\1>", "", html)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(td|th)>", " ", s)
    s = re.sub(r"(?i)</(p|div|tr|h[1-6]|li|table)>", "\n", s)
    s = _TAG.sub("", s)
    s = htmllib.unescape(s)
    s = "\n".join(_WS.sub(" ", ln).strip() for ln in s.splitlines())
    return _BLANKS.sub("\n\n", s).strip() + "\n"


@dataclass(frozen=True)
class Message:
    subject: str
    html: str
    to: List[str]
    sender: str
    text: Optional[str] = None

    def as_email(self) -> EmailMessage:
        m = EmailMessage()
        m["Subject"] = self.subject
        m["From"] = self.sender
        m["To"] = ", ".join(self.to)
        m["Date"] = formatdate(localtime=True)
        m["Message-ID"] = make_msgid(domain="desk.local")
        m.set_content(self.text if self.text is not None else text_from_html(self.html))
        m.add_alternative(self.html, subtype="html")
        return m


@dataclass(frozen=True)
class Receipt:
    sent: bool
    live: bool
    transport: str
    to: List[str]
    subject: str
    at: str
    detail: str = ""
    path: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return {"sent": self.sent, "live": self.live, "transport": self.transport, "to": self.to,
                "subject": self.subject, "at": self.at, "detail": self.detail, "path": self.path}


class Mailer(Protocol):
    def send(self, message: Message) -> Receipt: ...


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


@dataclass
class SmtpMailer:
    """STARTTLS on 587, the shape Gmail documents. ``smtp_factory`` exists so a test can
    hand in a fake server and check the call sequence without a socket."""

    settings: MailSettings
    timeout: float = 30.0
    smtp_factory: Callable[..., Any] = field(default=smtplib.SMTP, repr=False)

    def __repr__(self) -> str:  # never the password
        s = self.settings
        return f"SmtpMailer(host={s.host!r}, port={s.port}, user={s.user!r}, to={s.to!r})"

    def send(self, message: Message) -> Receipt:
        s = self.settings
        email = message.as_email()
        try:
            with self.smtp_factory(s.host, s.port, timeout=self.timeout) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                try:
                    server.login(s.user, s.password)
                except smtplib.SMTPAuthenticationError as err:
                    reply = redact_secret(str(getattr(err, "smtp_error", b"") or err), s.password)
                    raise BadCredential(
                        f"{s.host} refused the login for {s.user}: {reply}. For Gmail this needs an app "
                        f"password, not the account password, in {ENV_PASSWORD}.") from None
                refused = server.send_message(email)
        except (BadCredential, SendError):
            raise
        except (smtplib.SMTPException, OSError) as err:
            raise SendError(redact_secret(f"{type(err).__name__}: {err}", s.password)) from None
        if refused:
            raise SendError(f"{s.host} refused {len(refused)} recipient(s): "
                            + ", ".join(redact_secret(str(k), s.password) for k in refused))
        return Receipt(sent=True, live=True, transport="SmtpMailer", to=list(message.to),
                       subject=message.subject, at=_now(), detail=f"{s.host}:{s.port}")


@dataclass
class DryRunMailer:
    """Print what would be sent and send nothing. The password never reaches this class."""

    sink: Callable[[str], None] = print
    calls: List[Message] = field(default_factory=list)

    def send(self, message: Message) -> Receipt:
        self.calls.append(message)
        self.sink(f"DRY RUN  would send to {', '.join(message.to)}")
        self.sink(f"         from {message.sender}")
        self.sink(f"         subject {message.subject}")
        self.sink(f"         {len(message.html):,} chars of HTML, "
                  f"{len(message.text or text_from_html(message.html)):,} chars of text")
        self.sink("         nothing was sent")
        return Receipt(sent=False, live=False, transport="DryRunMailer", to=list(message.to),
                       subject=message.subject, at=_now(), detail="dry run")


@dataclass
class RecordingMailer:
    """Write the message as a ``.eml`` file instead of sending it. For previews and tests."""

    out_dir: Path

    def send(self, message: Message) -> Receipt:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", message.subject).strip("-")[:60] or "message"
        p = self.out_dir / f"{stamp}-{safe}.eml"
        p.write_bytes(message.as_email().as_bytes())
        return Receipt(sent=False, live=False, transport="RecordingMailer", to=list(message.to),
                       subject=message.subject, at=_now(), detail="written to disk", path=str(p))


# ---------------------------------------------------------------------------
# provenance: has a real message ever been sent from this checkout?
# ---------------------------------------------------------------------------

def record(receipt: Receipt, log: Optional[Path] = None) -> Path:
    """Append the receipt to the send log. The log holds addresses and subjects, never a credential."""
    p = log or SENT_LOG
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = _read_log(p)
    blob["sends"].append(receipt.to_json())
    blob["sends"] = blob["sends"][-200:]
    blob["ever_live"] = blob["ever_live"] or receipt.live
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(blob, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return p


def _read_log(p: Path) -> Dict[str, Any]:
    if p.exists():
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(blob, dict) and isinstance(blob.get("sends"), list):
                blob.setdefault("ever_live", False)
                return blob
        except (json.JSONDecodeError, OSError):
            pass
    return {"ever_live": False, "sends": []}


def provenance(log: Optional[Path] = None) -> Dict[str, Any]:
    """``ever_live`` is true only if an :class:`SmtpMailer` receipt was recorded."""
    blob = _read_log(log or SENT_LOG)
    live = [s for s in blob["sends"] if s.get("live")]
    return {
        "ever_live": bool(blob["ever_live"]),
        "n_sends": len(blob["sends"]),
        "n_live": len(live),
        "last_live": live[-1] if live else None,
        "last": blob["sends"][-1] if blob["sends"] else None,
    }
