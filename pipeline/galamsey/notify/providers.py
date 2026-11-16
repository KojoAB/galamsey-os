"""Provider adapters. Each takes a Message and returns the provider's message id.

| Channel  | Provider                    | Env variables |
|----------|-----------------------------|---------------|
| WhatsApp | Meta WhatsApp Cloud API     | WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_ALERT_TEMPLATE |
| SMS      | Africa's Talking (default)  | SMS_PROVIDER=africastalking, AT_USERNAME, AT_API_KEY, AT_SENDER_ID |
| SMS      | Hubtel                      | SMS_PROVIDER=hubtel, HUBTEL_CLIENT_ID, HUBTEL_CLIENT_SECRET, HUBTEL_SENDER_ID |
| Email    | Resend                      | RESEND_API_KEY, ALERT_EMAIL_FROM |

WhatsApp rule: messages WE start (alerts) must use a template pre-approved by Meta.
Create a template called e.g. `galamsey_alert` with one body parameter {{1}} holding
the alert line. Replies to tipsters within 24 h can be free text (see web/ tip webhook).
"""

from __future__ import annotations

import os

import requests

GRAPH = "https://graph.facebook.com/v21.0"


def _need(*names: str) -> list[str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")
    return [os.environ[n] for n in names]


def whatsapp(msg) -> str:
    token, phone_id, template = _need("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ALERT_TEMPLATE")
    body = {
        "messaging_product": "whatsapp",
        "to": msg.recipient.lstrip("+"),
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": os.environ.get("WHATSAPP_TEMPLATE_LANG", "en")},
            "components": [{"type": "body", "parameters": [
                {"type": "text", "text": p} for p in (msg.template_params or [msg.text])]}],
        },
    }
    r = requests.post(f"{GRAPH}/{phone_id}/messages", json=body, timeout=30,
                      headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()["messages"][0]["id"]


def sms(msg) -> str:
    provider = os.environ.get("SMS_PROVIDER", "africastalking")
    text = msg.text[:459]                     # 3 SMS segments max
    if provider == "hubtel":
        cid, secret, sender = _need("HUBTEL_CLIENT_ID", "HUBTEL_CLIENT_SECRET", "HUBTEL_SENDER_ID")
        r = requests.get("https://smsc.hubtel.com/v1/messages/send", timeout=30,
                         params={"clientid": cid, "clientsecret": secret, "from": sender,
                                 "to": msg.recipient.lstrip("+"), "content": text})
        r.raise_for_status()
        return str(r.json().get("messageId", ""))
    username, key = _need("AT_USERNAME", "AT_API_KEY")
    data = {"username": username, "to": msg.recipient, "message": text}
    if os.environ.get("AT_SENDER_ID"):
        data["from"] = os.environ["AT_SENDER_ID"]
    r = requests.post("https://api.africastalking.com/version1/messaging", data=data, timeout=30,
                      headers={"apiKey": key, "Accept": "application/json"})
    r.raise_for_status()
    rec = r.json()["SMSMessageData"]["Recipients"][0]
    if rec.get("status") != "Success":
        raise RuntimeError(f"Africa's Talking: {rec.get('status')}")
    return rec.get("messageId", "")


def email(msg) -> str:
    key, sender = _need("RESEND_API_KEY", "ALERT_EMAIL_FROM")
    r = requests.post("https://api.resend.com/emails", timeout=30,
                      headers={"Authorization": f"Bearer {key}"},
                      json={"from": sender, "to": [msg.recipient], "subject": msg.subject or "Galamsey Watch",
                            "text": msg.text, **({"html": msg.html} if msg.html else {}),
                            **({"attachments": msg.attachments} if msg.attachments else {})})
    r.raise_for_status()
    return r.json().get("id", "")
