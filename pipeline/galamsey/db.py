"""Database writes (Supabase Postgres + PostGIS) using the service-role connection string.

The pipeline connects directly with SUPABASE_DB_URL. Row-level security does not
apply to this connection, so keep that URL secret (GitHub Actions secret / .env only).
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import geopandas as gpd
import psycopg

from .config import settings


@contextmanager
def connect():
    if not settings.database_url:
        raise RuntimeError("SUPABASE_DB_URL is not set. Add it to .env (see .env.example).")
    with psycopg.connect(settings.database_url, autocommit=False) as conn:
        yield conn


def _jsonable(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if k == "geometry" or v is None:
            continue
        if isinstance(v, float) and v != v:  # NaN
            continue
        out[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
    return out


def replace_layer(conn, layer_type: str, gdf: gpd.GeoDataFrame, *, source: str, licence: str,
                  verified: bool, retrieved_at: datetime) -> int:
    """Insert a fresh batch for one layer type, then remove the old batch, in one transaction.

    If anything fails, the transaction rolls back and the previous layer stays in place.
    """
    batch = uuid.uuid4()
    gdf = gdf.to_crs("EPSG:4326")
    rows = [
        (layer_type, r.get("name"), r["geometry"].wkb, json.dumps(_jsonable(r)),
         source, retrieved_at, licence, verified, batch)
        for r in gdf.to_dict("records")
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """insert into reference_layers
               (type, name, geom, attributes, source, retrieved_at, licence, verified, batch_id)
               values (%s, %s, st_geomfromwkb(%s, 4326), %s::jsonb, %s, %s, %s, %s, %s)""",
            rows,
        )
        cur.execute("select swap_reference_batch(%s, %s)", (layer_type, batch))
    return len(rows)


def read_layer(conn, layer_type: str) -> gpd.GeoDataFrame:
    return gpd.read_postgis(
        "select id, name, geom from reference_layers where type = %(t)s",
        conn, geom_col="geom", params={"t": layer_type},
    )


def upsert_tiles(conn, grid: gpd.GeoDataFrame) -> int:
    """Insert or update tiles by id. last_processed_at is never touched here."""
    rows = [(r["id"], r["geometry"].wkb, r["priority"]) for r in grid.to_crs("EPSG:4326").to_dict("records")]
    with conn.cursor() as cur:
        cur.executemany(
            """insert into tiles (id, geom, priority)
               values (%s, st_geomfromwkb(%s, 4326), %s::tile_priority)
               on conflict (id) do update set geom = excluded.geom""",
            rows,
        )
        cur.execute("select refresh_tile_attributes()")
    return len(rows)


class RunLog:
    """Writes one row to pipeline_runs per job, updated as the job progresses."""

    def __init__(self, conn, job: str):
        self.conn, self.errors, self.details = conn, [], {}
        with conn.cursor() as cur:
            cur.execute("insert into pipeline_runs (job) values (%s) returning id", (job,))
            self.id = cur.fetchone()[0]
        conn.commit()

    def error(self, message: str) -> None:
        self.errors.append({"at": datetime.now(timezone.utc).isoformat(), "message": message})

    def finish(self, status: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """update pipeline_runs set finished_at = now(), status = %s, errors = %s::jsonb, details = %s::jsonb
                   where id = %s""",
                (status, json.dumps(self.errors), json.dumps(self.details, default=str), self.id),
            )
        self.conn.commit()
