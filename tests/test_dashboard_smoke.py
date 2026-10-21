"""Carga todas las páginas del dashboard contra la base real y la demo (Streamlit AppTest)."""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_pages_load():
    from qa.cli import cmd_demo, cmd_init
    if not (ROOT / "data" / "qa.sqlite").exists():
        cmd_init(None)
    if not (ROOT / "data" / "demo.sqlite").exists():
        cmd_demo(None)
    spec = importlib.util.spec_from_file_location("smoke", ROOT / "dashboard" / "smoke_test.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.run() == 0
