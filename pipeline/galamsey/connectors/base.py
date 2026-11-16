"""Government data connectors (spec 5.8 / M10).

Every agency adapter has the same shape:
    fetch()      → raw records from the agency (API, file export, or MOCK data)
    normalise()  → our standard records
    load(conn)   → write them to the database
    health()     → can we reach it right now?

Rules
- A connector that is down, not configured or not yet agreed is skipped and logged;
  it never blocks the daily run.
- Status (connected / stale / off) is written to the `connectors` table for the admin panel.
- Agency data stays off the public map unless connectors.public_allowed is true.

Each connector reads its settings from env variables named <ID>_URL and <ID>_TOKEN,
e.g. MINERALS_COMMISSION_EQUIPMENT_URL. Setting <ID>_MOCK=1 uses the bundled mock
data instead, for testing the whole flow before an agency agreement exists.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)
MOCK_DIR = Path(__file__).parent / "mock"


class NotConfigured(Exception):
    pass


class Connector(ABC):
    id: str = ""
    agency: str = ""
    stale_after = timedelta(days=2)

    def __init__(self):
        prefix = self.id.upper()
        self.url = os.environ.get(f"{prefix}_URL")
        self.token = os.environ.get(f"{prefix}_TOKEN")
        self.mock = os.environ.get(f"{prefix}_MOCK") == "1"

    # -- the four standard methods -------------------------------------------
    def fetch(self) -> list[dict]:
        if self.mock:
            return json.loads((MOCK_DIR / f"{self.id}.json").read_text())
        if not self.url:
            raise NotConfigured(f"{self.id}: no URL configured and mock mode off")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        r = requests.get(self.url, headers=headers, timeout=120)
        r.raise_for_status()
        data = r.json()
        return data.get("features") or data.get("data") or data if isinstance(data, dict) else data

    @abstractmethod
    def normalise(self, raw: list[dict]) -> list[dict]: ...

    @abstractmethod
    def load(self, conn, records: list[dict]) -> int: ...

    def health(self) -> bool:
        if self.mock:
            return True
        if not self.url:
            return False
        try:
            return requests.head(self.url, timeout=15).status_code < 500
        except requests.RequestException:
            return False

    # -- orchestration -------------------------------------------------------
    def run(self, conn) -> dict:
        try:
            records = self.normalise(self.fetch())
            n = self.load(conn, records)
            self._status(conn, "connected", None)
            conn.commit()
            return {"connector": self.id, "loaded": n}
        except NotConfigured as exc:
            conn.rollback()
            self._status(conn, "off", str(exc))
            conn.commit()
            return {"connector": self.id, "skipped": str(exc)}
        except Exception as exc:          # agency feed errors must never stop the daily run
            conn.rollback()
            log.warning("connector %s failed: %s", self.id, exc)
            self._status(conn, "stale", f"{type(exc).__name__}: {exc}"[:500])
            conn.commit()
            return {"connector": self.id, "error": str(exc)}

    def _status(self, conn, status: str, error: str | None):
        with conn.cursor() as cur:
            if status == "connected":
                cur.execute("update connectors set status='connected', last_success_at=now(), last_error=null where id=%s",
                            (self.id,))
            else:
                # 'stale' only if it worked recently; otherwise it is effectively off
                cur.execute("""update connectors set last_error=%s,
                                 status = case when %s = 'off' then 'off'::connector_status
                                               when last_success_at > now() - %s then 'stale'::connector_status
                                               else 'off'::connector_status end
                               where id=%s""", (error, status, self.stale_after, self.id))


def parse_time(value) -> datetime:
    if isinstance(value, datetime):
        return value
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
