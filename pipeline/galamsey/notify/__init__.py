"""One notify() for WhatsApp, SMS and email (spec 3 and 7).

Safety defaults
- NOTIFY_DRY_RUN=1 (the default) logs messages instead of sending. Set it to 0 only
  after the providers are paid for and approved — SMS and WhatsApp cost money per message.
- The `notifications` table has a unique (alert, channel, recipient) key, so the same
  alert is never sent to the same person twice, even if a run is repeated.
- Failed sends are retried with backoff and the delivery status is recorded.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from . import providers

log = logging.getLogger(__name__)


@dataclass
class Message:
    channel: str            # whatsapp | sms | email
    recipient: str          # E.164 phone or email address
    text: str               # short text (WhatsApp/SMS) or plain-text email body
    subject: str | None = None
    html: str | None = None
    template_params: list[str] | None = None   # WhatsApp business-initiated messages need a template
    attachments: list[dict] | None = None      # email only: [{filename, content (base64), content_id}]


class SendError(Exception):
    pass


def dry_run() -> bool:
    return os.environ.get("NOTIFY_DRY_RUN", "1") != "0"


def _send_once(msg: Message) -> str:
    if dry_run():
        log.info("[dry run] %s → %s: %s", msg.channel, msg.recipient, msg.subject or msg.text[:120])
        return "dry-run"
    if msg.channel == "whatsapp":
        return providers.whatsapp(msg)
    if msg.channel == "sms":
        return providers.sms(msg)
    if msg.channel == "email":
        return providers.email(msg)
    raise SendError(f"Unknown channel {msg.channel}")


def send(msg: Message, attempts: int = 3, backoff_s: float = 2.0) -> tuple[bool, str]:
    """Send with retries. Returns (ok, provider id or last error)."""
    last = ""
    for i in range(attempts):
        try:
            return True, _send_once(msg)
        except Exception as exc:          # provider/network errors: retry, then report
            last = f"{type(exc).__name__}: {exc}"
            log.warning("send %s → %s failed (attempt %d): %s", msg.channel, msg.recipient, i + 1, last)
            time.sleep(backoff_s * (2 ** i))
    return False, last


def notify(conn, alert_id: int | None, msg: Message) -> str:
    """Record-then-send. Returns 'sent', 'failed' or 'duplicate'."""
    with conn.cursor() as cur:
        cur.execute(
            """insert into notifications (alert_id, channel, recipient, status) values (%s, %s, %s, 'queued')
               on conflict (alert_id, channel, recipient) do nothing returning id""",
            (alert_id, msg.channel, msg.recipient))
        row = cur.fetchone()
        if row is None and alert_id is not None:
            return "duplicate"
        nid = row[0] if row else None
    conn.commit()
    ok, info = send(msg)
    with conn.cursor() as cur:
        cur.execute("""update notifications set status = %s, sent_at = case when %s then now() end,
                         attempts = attempts + 1, last_error = %s where id = %s""",
                    ("sent" if ok else "failed", ok, None if ok else info[:500], nid))
    conn.commit()
    return "sent" if ok else "failed"
