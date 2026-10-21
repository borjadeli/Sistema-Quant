"""Smoke test del dashboard: carga cada página contra la base real y la demo sin excepciones.

    python dashboard/smoke_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

DASH = Path(__file__).resolve().parent
PAGES = sorted((DASH / "pages").glob("*.py"))


def run() -> int:
    failures = 0
    # app de entrada (navegación + selector de base)
    at = AppTest.from_file(str(DASH / "app.py"), default_timeout=120).run()
    if at.exception:
        print("FAIL app.py:", [e.message for e in at.exception])
        failures += 1
    else:
        print("ok   app.py (página por defecto)")
    for db in ("real", "demo"):
        for page in PAGES:
            at = AppTest.from_file(str(page), default_timeout=120)
            at.session_state["db"] = db
            at.run()
            if at.exception:
                failures += 1
                print(f"FAIL [{db}] {page.name}:")
                for e in at.exception:
                    print("     ", e.message, "\n", "".join(e.stack_trace[-6:]) if e.stack_trace else "")
            else:
                n_err = len(at.error)
                print(f"ok   [{db}] {page.name}  (st.error blocks: {n_err})")
    # página con interacción: ficha de cada empresa demo
    for cid in ("TEST_ALPHA", "TEST_SLOW", "TEST_BURN"):
        at = AppTest.from_file(str(DASH / "pages" / "4_ficha.py"), default_timeout=120)
        at.session_state["db"] = "demo"
        at.run()
        at.selectbox(key="company_demo").set_value(cid).run()
        status = "FAIL" if at.exception else "ok  "
        failures += bool(at.exception)
        print(f"{status} [demo] 4_ficha.py company={cid}", [e.message for e in at.exception] if at.exception else "")
    # interacciones del mapa: sunburst + red filtrada a bottlenecks
    at = AppTest.from_file(str(DASH / "pages" / "1_mapa.py"), default_timeout=120)
    at.session_state["db"] = "real"
    at.run()
    at.radio[0].set_value("Sunburst").run()
    at.toggle[0].set_value(True).run()
    failures += bool(at.exception)
    print("FAIL" if at.exception else "ok  ", "[real] 1_mapa.py sunburst + red filtrada",
          [e.message for e in at.exception] if at.exception else "")
    print("failures:", failures)
    return failures


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
