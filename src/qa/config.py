"""Carga de configuración y rutas del proyecto."""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("QA_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_PATH = Path(os.environ.get("QA_CONFIG", ROOT / "config" / "settings.yaml"))
DATA_DIR = ROOT / "data"
MAP_PATH = DATA_DIR / "map" / "industry_map.yaml"
UNIVERSE_PATH = DATA_DIR / "universe" / "companies.yaml"
RELATIONSHIPS_PATH = DATA_DIR / "universe" / "relationships.yaml"
BOTTLENECKS_PATH = DATA_DIR / "bottlenecks" / "bottlenecks.yaml"
RESEARCH_DIR = DATA_DIR / "research"
FIXTURES_DIR = DATA_DIR / "fixtures"


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    ua = os.environ.get("QA_SEC_USER_AGENT")
    if ua:
        cfg["providers"]["sec_user_agent"] = ua
    db = os.environ.get("QA_DATABASE_URL")
    if db:
        cfg["database_url"] = db
    return cfg


def config_hash() -> str:
    """Hash del archivo de configuración: se registra en cada análisis para reproducibilidad."""
    return hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()[:12]


def database_url() -> str:
    url = settings()["database_url"]
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        rel = url.removeprefix("sqlite:///")
        url = f"sqlite:///{(ROOT / rel).as_posix()}"
    return url
