"""Smoke test del dashboard: carga cada página contra la base real y la demo sin excepciones.

    python dashboard/smoke_test.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from streamlit.testing.v1 import AppTest

DASH = Path(__file__).resolve().parent
PAGES = sorted((DASH / "pages").glob("*.py"))
sys.path.insert(0, str(DASH.parent / "src"))

from qa.config import settings  # noqa: E402


def _query_warnings(at: AppTest) -> list[str]:
    """Consultas que fallaron en silencio (data.query muestra un caption en vez de romper la página)."""
    return [c.value for c in at.caption if "consulta no disponible" in str(c.value)]


def _check(at: AppTest, label: str) -> int:
    if at.exception:
        print(f"FAIL {label}:")
        for e in at.exception:
            print("     ", e.message, "\n", "".join(e.stack_trace[-6:]) if e.stack_trace else "")
        return 1
    qw = _query_warnings(at)
    if qw:
        print(f"FAIL {label}: consultas fallidas {qw}")
        return 1
    print(f"ok   {label}  (st.error blocks: {len(at.error)})")
    return 0


def _with_db_url(url: str | None) -> None:
    if url is None:
        os.environ.pop("QA_DATABASE_URL", None)
    else:
        os.environ["QA_DATABASE_URL"] = url
    settings.cache_clear()


def run() -> int:
    failures = 0
    # app de entrada (navegación + selector de base)
    at = AppTest.from_file(str(DASH / "app.py"), default_timeout=120).run()
    failures += _check(at, "app.py (página por defecto)")
    for db in ("real", "demo"):
        for page in PAGES:
            at = AppTest.from_file(str(page), default_timeout=120)
            at.session_state["db"] = db
            at.run()
            failures += _check(at, f"[{db}] {page.name}")
    # página con interacción: ficha de cada empresa demo
    for cid in ("TEST_ALPHA", "TEST_SLOW", "TEST_BURN"):
        at = AppTest.from_file(str(DASH / "pages" / "4_ficha.py"), default_timeout=120)
        at.session_state["db"] = "demo"
        at.run()
        at.selectbox(key="company_demo").set_value(cid).run()
        failures += _check(at, f"[demo] 4_ficha.py company={cid}")
    # interacciones del mapa: sunburst + red filtrada a bottlenecks + una sola capa
    at = AppTest.from_file(str(DASH / "pages" / "1_mapa.py"), default_timeout=120)
    at.session_state["db"] = "real"
    at.run()
    at.radio[0].set_value("Sunburst").run()
    at.toggle[0].set_value(True).run()
    at.multiselect[0].set_value(["grid"]).run()
    failures += _check(at, "[real] 1_mapa.py sunburst + red filtrada + 1 capa")
    # comparador: el default no puede caer en un bottleneck sin empresas; uno vacío muestra st.info
    at = AppTest.from_file(str(DASH / "pages" / "5_comparador.py"), default_timeout=120)
    at.session_state["db"] = "demo"
    at.run()
    sb = at.selectbox(key="cmp_bn_demo")
    ok = not at.exception and sb.value != "(libre)" and len(at.multiselect) == 1 and bool(at.multiselect[0].value)
    empty = next((i for i, o in enumerate(sb.options) if o.endswith("· 0 emp.")), None)
    if ok and empty is not None:
        sb.select_index(empty).run()
        ok = not at.exception and any("Ninguna empresa" in str(i.value) for i in at.info)
    if not ok:
        print("FAIL [demo] 5_comparador.py: el default no tiene empresas o el bottleneck vacío no muestra st.info")
        failures += 1
    else:
        failures += _check(at, "[demo] 5_comparador.py default con empresas + bottleneck vacío")
    # #44: QA_DATABASE_URL fuera del repo (sqlite existente / inexistente) y URL no-SQLite
    tmp = Path(tempfile.mkdtemp(prefix="qa_dash_"))
    try:
        ext = tmp / "externa.sqlite"
        shutil.copy(DASH.parent / "data" / "demo.sqlite", ext)
        cases = [(f"sqlite:///{ext.as_posix()}", "existente", False),
                 (f"sqlite:///{(tmp / 'no_existe.sqlite').as_posix()}", "inexistente", True),
                 ("postgresql://usuario:secreto@db.example.com/qa", "postgres", True)]
        for url, label, expect_error in cases:
            _with_db_url(url)
            for target in [DASH / "app.py", *PAGES]:
                at = AppTest.from_file(str(target), default_timeout=120)
                at.session_state["db"] = "real"
                at.run()
                name = f"[QA_DATABASE_URL {label}] {target.name}"
                failures += _check(at, name)
                if expect_error and target.name != "app.py" and not at.error:
                    failures += 1
                    print(f"FAIL {name}: se esperaba st.error")
                if any("secreto" in str(e.value) for e in at.error):
                    failures += 1
                    print(f"FAIL {name}: el st.error muestra la contraseña de la URL")
    finally:
        _with_db_url(None)
        shutil.rmtree(tmp, ignore_errors=True)
    print("failures:", failures)
    return failures


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
