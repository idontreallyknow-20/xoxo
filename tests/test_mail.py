"""an.mail: the sending half of the email, without a socket.

The credential comes from the environment only, never reaches a log line, a repr
or an exception, and the provenance file says whether a real message has ever
gone out. The SMTP conversation is checked against a fake server so the call
order (ehlo, starttls, login, send) is pinned without opening port 587.
"""
import json
import smtplib
from pathlib import Path

import pytest

from an import mail

SECRET = "abcd efgh ijkl mnop"
ENV = {
    mail.ENV_USER: "joseph@example.com",
    mail.ENV_PASSWORD: SECRET,
    mail.ENV_TO: "joseph@example.com, other@example.com",
}


def message(**kw):
    base = dict(subject="Desk, Monday", html="<html><body><h1>Hi</h1><p>one &amp; two</p></body></html>",
                to=["joseph@example.com"], sender="joseph@example.com")
    base.update(kw)
    return mail.Message(**base)


# -- settings ------------------------------------------------------------------

def test_settings_come_from_the_environment_and_default_to_gmail():
    s = mail.settings_from_env(ENV)
    assert s.user == "joseph@example.com"
    assert s.to == ["joseph@example.com", "other@example.com"]
    assert s.host == "smtp.gmail.com" and s.port == 587


def test_missing_variable_is_named_and_no_value_is_echoed():
    with pytest.raises(mail.MissingCredential) as e:
        mail.settings_from_env({mail.ENV_USER: "x"})
    assert mail.ENV_PASSWORD in str(e.value)


def test_to_defaults_to_the_sender():
    s = mail.settings_from_env({mail.ENV_USER: "a@b.c", mail.ENV_PASSWORD: "p"})
    assert s.to == ["a@b.c"]


def test_the_password_never_appears_in_a_repr_or_a_description():
    s = mail.settings_from_env(ENV)
    assert SECRET not in repr(s)
    assert SECRET not in s.describe()
    assert SECRET not in repr(mail.SmtpMailer(s))
    assert str(len(SECRET)) in s.describe()


# -- text part -----------------------------------------------------------------

def test_text_part_reads_the_html_in_source_order():
    t = mail.text_from_html("<html><head><style>x{}</style></head><body><p>First &amp; foremost</p>"
                            "<table><tr><td>a</td><td>b</td></tr></table><script>1</script></body></html>")
    assert "First & foremost" in t
    assert "x{}" not in t and "script" not in t
    assert t.index("First") < t.index("a b")


def test_the_email_has_both_parts_in_the_right_order():
    e = message().as_email()
    parts = [p.get_content_type() for p in e.iter_parts()]
    assert parts == ["text/plain", "text/html"]
    assert "one & two" in e.get_body(("plain",)).get_content()


# -- smtp conversation -----------------------------------------------------------

class FakeSmtp:
    made = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls = []
        self.refuse = {}
        self.auth_error = None
        FakeSmtp.made.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.calls.append("quit")

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))
        if self.auth_error:
            raise self.auth_error

    def send_message(self, msg):
        self.calls.append(("send", msg["To"]))
        return self.refuse


def test_the_conversation_is_ehlo_starttls_login_send():
    FakeSmtp.made.clear()
    m = mail.SmtpMailer(mail.settings_from_env(ENV), smtp_factory=FakeSmtp)
    r = m.send(message())
    srv = FakeSmtp.made[-1]
    assert srv.host == "smtp.gmail.com" and srv.port == 587
    assert srv.calls == ["ehlo", "starttls", "ehlo", ("login", "joseph@example.com"),
                         ("send", "joseph@example.com"), "quit"]
    assert r.sent and r.live and r.transport == "SmtpMailer"


def test_a_refused_login_names_the_app_password_and_never_echoes_the_secret():
    def factory(host, port, timeout=None):
        s = FakeSmtp(host, port, timeout)
        s.auth_error = smtplib.SMTPAuthenticationError(535, f"5.7.8 bad AUTH {SECRET}".encode())
        return s

    m = mail.SmtpMailer(mail.settings_from_env(ENV), smtp_factory=factory)
    with pytest.raises(mail.BadCredential) as e:
        m.send(message())
    assert SECRET not in str(e.value)
    assert "app password" in str(e.value)
    assert "REDACTED" in str(e.value)


def test_a_refused_recipient_is_an_error_not_a_silent_success():
    def factory(host, port, timeout=None):
        s = FakeSmtp(host, port, timeout)
        s.refuse = {"other@example.com": (550, b"no such user")}
        return s

    m = mail.SmtpMailer(mail.settings_from_env(ENV), smtp_factory=factory)
    with pytest.raises(mail.SendError):
        m.send(message(to=["joseph@example.com", "other@example.com"]))


def test_a_dead_connection_is_a_send_error_without_the_secret():
    def factory(host, port, timeout=None):
        raise OSError(f"connection refused while sending {SECRET}")

    m = mail.SmtpMailer(mail.settings_from_env(ENV), smtp_factory=factory)
    with pytest.raises(mail.SendError) as e:
        m.send(message())
    assert SECRET not in str(e.value)


# -- dry run and recording -------------------------------------------------------

def test_dry_run_prints_the_plan_and_sends_nothing():
    lines = []
    d = mail.DryRunMailer(sink=lines.append)
    r = d.send(message())
    assert not r.sent and not r.live
    text = "\n".join(lines)
    assert "joseph@example.com" in text and "Desk, Monday" in text and "nothing was sent" in text


def test_recording_writes_an_eml_with_both_parts(tmp_path):
    r = mail.RecordingMailer(tmp_path / "out").send(message())
    assert not r.live and r.path
    raw = Path(r.path).read_bytes()
    assert b"text/html" in raw and b"text/plain" in raw and b"Subject: Desk, Monday" in raw


# -- provenance ----------------------------------------------------------------

def test_provenance_is_false_until_a_live_receipt_is_recorded(tmp_path):
    log = tmp_path / "sent.json"
    assert mail.provenance(log) == {"ever_live": False, "n_sends": 0, "n_live": 0, "last_live": None, "last": None}
    mail.record(mail.DryRunMailer(sink=lambda s: None).send(message()), log)
    assert mail.provenance(log)["ever_live"] is False
    assert mail.provenance(log)["n_sends"] == 1
    FakeSmtp.made.clear()
    live = mail.SmtpMailer(mail.settings_from_env(ENV), smtp_factory=FakeSmtp).send(message())
    mail.record(live, log)
    p = mail.provenance(log)
    assert p["ever_live"] is True and p["n_live"] == 1 and p["last_live"]["transport"] == "SmtpMailer"
    assert SECRET not in log.read_text()


def test_the_send_log_lives_under_the_ignored_cache_directory():
    assert str(mail.SENT_LOG).startswith(str(mail.paths.CACHE_DIR))
    import subprocess
    r = subprocess.run(["git", "check-ignore", "-q", str(mail.SENT_LOG)], cwd=str(mail.paths.ROOT))
    assert r.returncode == 0, "the send log must be gitignored; it holds addresses"


def test_no_literal_credential_anywhere_in_the_module():
    src = (mail.paths.ROOT / "scripts" / "an" / "mail.py").read_text()
    assert "smtplib" in src
    assert not any(k in src for k in ("password=\"", "password='"))
    assert "getpass" not in src, "the password is read from the environment, not prompted for"
