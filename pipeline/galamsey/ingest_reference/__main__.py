"""Reference-data loader.

Usage (from the pipeline/ folder):

  python -m galamsey.ingest_reference all                    # everything, into Supabase
  python -m galamsey.ingest_reference all --out ./out        # dry run: write GeoJSON files, no database
  python -m galamsey.ingest_reference boundaries|forests|rivers|concessions|priority-zones|grid
  python -m galamsey.ingest_reference import-concessions mines.geojson --source "analyst:jane" --date 2026-10-01

Each step is independent and safe to re-run: a layer is only replaced once its new
version has loaded completely.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import yaml

from .. import grid as gridmod
from ..config import CONFIG_DIR
from ..geo import buffer_metres, clean
from .layers import boundary_sources, concession_sources, forest_reserve_sources, river_sources
from .priority import match_districts, normalise_name
from .sources import LayerData, SourceUnavailable, first_available

log = logging.getLogger("ingest_reference")


# ---------------------------------------------------------------------------
# Where results go: the database, or GeoJSON files for a dry run
# ---------------------------------------------------------------------------

class FileStore:
    def __init__(self, out: Path):
        self.out = out
        out.mkdir(parents=True, exist_ok=True)

    def write(self, layer_type: str, data: LayerData) -> int:
        gdf = data.gdf.to_crs("EPSG:4326").copy()
        gdf["source"], gdf["licence"], gdf["verified"] = data.source, data.licence, data.verified
        gdf["retrieved_at"] = data.retrieved_at.isoformat()
        gdf.to_file(self.out / f"{layer_type}.geojson", driver="GeoJSON")
        return len(gdf)

    def read(self, layer_type: str) -> gpd.GeoDataFrame:
        path = self.out / f"{layer_type}.geojson"
        if not path.exists():
            raise SourceUnavailable(f"{path} not found — run that step first")
        return gpd.read_file(path)

    def write_tiles(self, grid: gpd.GeoDataFrame) -> int:
        grid.to_file(self.out / "tiles.geojson", driver="GeoJSON")
        return len(grid)

    def commit(self) -> None: ...
    def run_error(self, message: str) -> None: ...


class DbStore:
    def __init__(self):
        from .. import db
        self.db = db
        self._cm = db.connect()
        self.conn = self._cm.__enter__()
        self.run = db.RunLog(self.conn, "reference_refresh")

    def write(self, layer_type: str, data: LayerData) -> int:
        n = self.db.replace_layer(self.conn, layer_type, data.gdf, source=data.source, licence=data.licence,
                                  verified=data.verified, retrieved_at=data.retrieved_at)
        self.conn.commit()
        self.run.details[layer_type] = {"features": n, "source": data.source}
        return n

    def read(self, layer_type: str) -> gpd.GeoDataFrame:
        gdf = self.db.read_layer(self.conn, layer_type)
        if gdf.empty:
            raise SourceUnavailable(f"layer '{layer_type}' is empty — run that step first")
        return gdf.rename_geometry("geometry")

    def write_tiles(self, grid: gpd.GeoDataFrame) -> int:
        n = self.db.upsert_tiles(self.conn, grid)
        self.conn.commit()
        self.run.details["tiles"] = {"tiles": n}
        return n

    def commit(self) -> None:
        self.conn.commit()

    def run_error(self, message: str) -> None:
        self.conn.rollback()
        self.run.error(message)

    def close(self, ok: bool) -> None:
        self.run.finish("success" if ok and not self.run.errors else "partial" if ok else "failed")
        self._cm.__exit__(None, None, None)


def load_config() -> dict:
    return yaml.safe_load((CONFIG_DIR / "reference.yaml").read_text())


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def step_boundaries(store) -> None:
    for level, layer in (("ADM1", "admin1"), ("ADM2", "admin2")):
        data, _ = first_available(boundary_sources(level))
        log.info("%s: %d features from %s", layer, store.write(layer, data), data.source)
        if level == "ADM1":
            # National outline = all regions merged (saves a separate download).
            country = gpd.GeoDataFrame({"name": ["Ghana"]}, geometry=[data.gdf.union_all()], crs=data.gdf.crs)
            store.write("admin0", LayerData(country, data.source, data.licence, data.verified, data.retrieved_at))


def step_forests(store) -> None:
    data, _ = first_available(forest_reserve_sources())
    log.info("forest_reserve: %d features from %s", store.write("forest_reserve", data), data.source)


def select_major_rivers(rivers: gpd.GeoDataFrame, names: list[str]) -> gpd.GeoDataFrame:
    """Keep river features whose name is one of the configured major rivers; tag them with that name."""
    wanted = {normalise_name(n): n for n in names}
    keys = rivers["name"].fillna("").map(lambda n: normalise_name(n).removesuffix(" river"))
    out = rivers[keys.isin(wanted)].copy()
    out["river"] = keys[keys.isin(wanted)].map(wanted)
    return out


def step_rivers(store) -> None:
    names = load_config()["major_rivers"]
    data, _ = first_available(river_sources(names))
    log.info("river: %d features from %s", store.write("river", data), data.source)

    major = select_major_rivers(data.gdf, names)
    if major.empty:
        # e.g. HydroRIVERS has no names — buffer everything it gave us (already filtered to main rivers).
        major = data.gdf.assign(river=data.gdf["name"])
    for metres in (100, 500):
        buf = buffer_metres(major[["river", "geometry"]], metres, dissolve_by="river").rename(columns={"river": "name"})
        layer = f"river_buffer_{metres}m"
        derived = LayerData(buf, f"{data.source} (+{metres} m buffer)", data.licence, data.verified, data.retrieved_at)
        log.info("%s: %d river buffers", layer, store.write(layer, derived))


def step_concessions(store) -> None:
    try:
        data, _ = first_available(concession_sources())
    except SourceUnavailable:
        log.warning("concession: no official cadastre configured yet. Import analyst files with "
                    "'import-concessions' until MINERALS_COMMISSION_CADASTRE_URL is available.")
        return
    log.info("concession: %d features from %s", store.write("concession", data), data.source)


def import_concessions(store, path: str, source: str, date: str, licence: str, verified: bool) -> None:
    if verified and not source.startswith("minerals_commission"):
        sys.exit("--verified is only allowed for files that came from the Minerals Commission "
                 "(use --source minerals_commission:<what>).")
    gdf = gpd.read_file(path)
    if "name" not in gdf.columns:
        for guess in ("NAME", "Name", "LICENCE_NO", "Licence", "Holder", "COMPANY"):
            if guess in gdf.columns:
                gdf = gdf.rename(columns={guess: "name"})
                break
    gdf = clean(gdf)
    when = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    n = store.write("concession", LayerData(gdf, source, licence, verified, when))
    log.info("concession: imported %d features from %s (verified=%s)", n, path, verified)


def step_priority_zones(store) -> None:
    districts = store.read("admin2")
    zones, missing = match_districts(districts, load_config()["priority_districts"])
    if missing:
        log.warning("Priority districts not found in boundary data (fix spelling in config/reference.yaml): %s",
                    ", ".join(missing))
    if zones.empty:
        raise SourceUnavailable("no priority districts matched")
    data = LayerData(zones[["name", "region_hint", "geometry"]], "galamsey_watch:priority_districts",
                     "Derived from district boundaries; editable", False, datetime.now(timezone.utc))
    log.info("priority_zone: %d districts", store.write("priority_zone", data))


def step_grid(store) -> None:
    country = store.read("admin0")
    try:
        zones = store.read("priority_zone")
    except SourceUnavailable:
        zones = None
    grid = gridmod.flag_priority(gridmod.build_grid(country), zones)
    n = store.write_tiles(grid)
    log.info("tiles: %d (%d high priority)", n, int((grid["priority"] == "high").sum()))


STEPS = {
    "boundaries": step_boundaries,
    "forests": step_forests,
    "rivers": step_rivers,
    "concessions": step_concessions,
    "priority-zones": step_priority_zones,
    "grid": step_grid,
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m galamsey.ingest_reference")
    p.add_argument("step", choices=["all", *STEPS, "import-concessions"])
    p.add_argument("file", nargs="?", help="file to import (import-concessions only)")
    p.add_argument("--out", type=Path, help="write GeoJSON here instead of the database (dry run)")
    p.add_argument("--source", default="analyst_upload", help="import-concessions: where the file came from")
    p.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat(), help="import-concessions: data date")
    p.add_argument("--licence", default="Unknown — confirm with data owner")
    p.add_argument("--verified", action="store_true", help="import-concessions: official Minerals Commission file")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    store = FileStore(args.out) if args.out else DbStore()
    ok = True
    try:
        if args.step == "import-concessions":
            if not args.file:
                p.error("import-concessions needs a file")
            import_concessions(store, args.file, args.source, args.date, args.licence, args.verified)
        else:
            for name in (STEPS if args.step == "all" else [args.step]):
                try:
                    STEPS[name](store)
                except SourceUnavailable as exc:
                    # Never block the whole refresh because one source is down; log and continue.
                    log.error("%s failed: %s", name, exc)
                    store.run_error(f"{name}: {exc}")
                    # Missing optional layers give a 'partial' run; boundaries and grid are essential.
                    if args.step != "all" or name in ("boundaries", "grid"):
                        ok = False
    finally:
        if isinstance(store, DbStore):
            store.close(ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
