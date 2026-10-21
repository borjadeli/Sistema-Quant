"""Research Feed: eventos que pueden cambiar tesis + propuestas pendientes de cambios al mapa."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

import data as D  # noqa: E402

st.title("📰 Research Feed")
D.banner()

ev = D.query("SELECT * FROM feed_events ORDER BY created_at DESC")
if ev.empty:
    st.info("Sin eventos. Se generan con `qa ingest` (nuevos filings, movimientos de precio > 15%), con el agente "
            "Map Curator (`qa curate-map`) o cargándolos manualmente.")
else:
    c1, c2, c3 = st.columns(3)
    with c1:
        types = st.multiselect("Tipo", sorted(ev["event_type"].unique()), default=sorted(ev["event_type"].unique()))
    with c2:
        only_review = st.toggle("Solo los que disparan revisión", value=False)
    with c3:
        cid = st.text_input("Empresa (ticker)", "")
    v = ev[ev["event_type"].isin(types)]
    if only_review:
        v = v[v["triggers_review"] == 1]
    if cid:
        v = v[v["company_id"].fillna("").str.upper() == cid.upper()]
    st.dataframe(v[["event_date", "company_id", "bottleneck_id", "event_type", "title", "detail", "impact",
                    "verification", "triggers_review", "processed", "url"]], hide_index=True, width="stretch",
                 column_config={"url": st.column_config.LinkColumn("fuente"),
                                "triggers_review": st.column_config.CheckboxColumn("revisión")})

st.subheader("Propuestas pendientes de cambios al mapa / universo")
props = D.query("SELECT * FROM map_change_proposals ORDER BY created_at DESC")
if props.empty:
    st.write("Ninguna. Las generan los agentes Map Curator y Discovery; se aprueban con "
             "`qa proposals --approve ID` (nunca se aplican solas).")
else:
    st.dataframe(props[["id", "created_at", "change_type", "status", "rationale", "reviewed_by"]], hide_index=True,
                 width="stretch")
    for _, p in props[props["status"] == "pending"].iterrows():
        with st.expander(f"#{p['id']} · {p['change_type']} · {str(p['rationale'])[:90]}"):
            st.json(D.js(p["payload"]))
            evd = D.js(p["evidence"]) or []
            for e in evd:
                st.markdown(f"- {e.get('claim')} — {e.get('verification')} · {e.get('source_url') or 'sin fuente'}")
            st.code(f"qa proposals --approve {p['id']}   # o --reject {p['id']}", language="bash")
            st.caption(json.dumps({"agent_run_id": p["agent_run_id"]}))
