"""Runtime configuration, read from environment variables only (never hard-code secrets).

Copy ../.env.example to ../.env and fill in what you have. Every agency source is
optional: leave it blank and the loader falls back to the open dataset.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_dotenv() -> None:
    """Minimal .env reader so local runs work without extra dependencies."""
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if candidate.is_file():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
            return


_load_dotenv()


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    # Database: Supabase → Project Settings → Database → Connection string (URI, session pooler)
    database_url: str | None = field(default_factory=lambda: _env("SUPABASE_DB_URL"))

    # Official agency sources (URL or local file path to GeoJSON / Shapefile zip / GeoPackage).
    # Filled in once each agency grants access. Layers from these are marked verified.
    gss_boundaries_adm1: str | None = field(default_factory=lambda: _env("GSS_BOUNDARIES_ADM1_URL"))
    gss_boundaries_adm2: str | None = field(default_factory=lambda: _env("GSS_BOUNDARIES_ADM2_URL"))
    forestry_commission_reserves: str | None = field(default_factory=lambda: _env("FORESTRY_COMMISSION_RESERVES_URL"))
    wrc_rivers: str | None = field(default_factory=lambda: _env("WRC_RIVERS_URL"))
    minerals_commission_cadastre: str | None = field(default_factory=lambda: _env("MINERALS_COMMISSION_CADASTRE_URL"))

    # Open-data credentials and files
    protected_planet_token: str | None = field(default_factory=lambda: _env("PROTECTED_PLANET_API_TOKEN"))
    hydrorivers_path: str | None = field(default_factory=lambda: _env("HYDRORIVERS_PATH"))
    overpass_url: str = field(default_factory=lambda: _env("OVERPASS_URL") or "https://overpass-api.de/api/interpreter")

    http_timeout_s: int = 180


settings = Settings()
