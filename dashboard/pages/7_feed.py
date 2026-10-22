"""Research Feed: eventos que pueden cambiar tesis + propuestas pendientes de cambios al mapa."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import data as D  # noqa: E402

st.title("📰 Research Feed")
D.banner()


def last_ts(sql: str) -> str | None:
    df = D.query(sql)
    if df.empty or df.iloc[0, 0] is None or pd.isna(df.iloc[0, 0]):
        return None
    return D.fmt_dt(df.iloc[0, 0])


# para distinguir "nunca se ejecutó" de "se ejecutó y no hubo nada nuevo"
# (la investigación semilla de `qa init` es search_summary: no cuenta como ingesta)
last_ingest = last_ts("SELECT MAX(t) FROM (SELECT MAX(retrieved_at) AS t FROM datapoints WHERE verification != "
                      "'search_summary' UNION ALL SELECT MAX(retrieved_at) FROM prices "
                      "UNION ALL SELECT MAX(as_of) FROM market_snapshots)")
last_curate = last_ts("SELECT MAX(created_at) FROM agent_runs WHERE agent = 'map_curator'")
last_discover = last_ts("SELECT MAX(created_at) FROM agent_runs WHERE agent = 'discovery'")


def ran(label: str, ts: str | None, cmd: str) -> str:
    return f"{label}: última ejecución {ts}" if ts else f"{label}: nunca ejecutado (`{cmd}`)"


st.caption(" · ".join([ran("Ingesta", last_ingest, "qa ingest"), ran("Map Curator", last_curate, "qa curate-map"),
                       ran("Discovery", last_discover, "qa discover")]))

ev = D.query("SELECT * FROM feed_events ORDER BY event_date DESC, created_at DESC")
st.subheader("Eventos")
if ev.empty:
    if last_ingest or last_curate:
        st.info("Sin eventos nuevos: la última ingesta / curaduría no detectó filings nuevos, movimientos de precio "
                "> 15% ni cambios relevantes. Volver a correr `qa ingest` o `qa curate-map` para actualizar.")
    else:
        st.info("Sin eventos: todavía no se ejecutó la ingesta. Se generan con `qa ingest` (nuevos filings, "
                "movimientos de precio > 15%) y con el agente Map Curator (`qa curate-map`).")
else:
    all_types = sorted(ev["event_type"].unique())
    c1, c2, c3 = st.columns(3)
    with c1:
        types = st.multiselect("Tipo", all_types, default=[], placeholder="Todos los tipos",
                               format_func=lambda x: D.es(D.EVENT_TYPE_ES, x))
    with c2:
        only_review = st.toggle("Solo los que disparan revisión", value=False,
                                help="Eventos marcados para re-analizar la tesis de la empresa en la próxima corrida.")
    with c3:
        cid = st.text_input("Empresa (ticker)", "", placeholder="p.ej. TEST_ALPHA")
    v = ev[ev["event_type"].isin(types or all_types)]
    if only_review:
        v = v[v["triggers_review"] == 1]
    if cid:
        v = v[v["company_id"].fillna("").str.upper().str.contains(cid.strip().upper(), regex=False)]
    n_rev = int((ev["triggers_review"] == 1).sum())
    st.caption(f"{len(v)} de {len(ev)} eventos · {n_rev} disparan revisión · "
               f"{int((ev['processed'] == 0).sum())} sin procesar")
    v = v.assign(event_type=v["event_type"].map(lambda x: D.es(D.EVENT_TYPE_ES, x)),
                 impact=v["impact"].map(lambda x: D.es(D.IMPACT_ES, x)),
                 verification=v["verification"].map(lambda x: D.es(D.VERIFICATION_ES, x)),
                 triggers_review=v["triggers_review"].astype("boolean"),
                 processed=v["processed"].astype("boolean"),
                 event_date=pd.to_datetime(v["event_date"], errors="coerce"))
    st.dataframe(v[["event_date", "company_id", "event_type", "title", "impact", "triggers_review", "detail",
                    "bottleneck_id", "verification", "processed", "url"]], hide_index=True, width="stretch", placeholder="—",
                 column_config=D.colcfg(
                     v, event_date=st.column_config.DateColumn("fecha", format="YYYY-MM-DD"),
                     title=st.column_config.TextColumn("título", width="large"),
                     detail=st.column_config.TextColumn("detalle", width="medium"),
                     url=st.column_config.LinkColumn("fuente", display_text="abrir"),
                     triggers_review=st.column_config.CheckboxColumn("dispara revisión"),
                     processed=st.column_config.CheckboxColumn("procesado")))

st.subheader("Propuestas de cambios al mapa / universo")
props = D.query("SELECT * FROM map_change_proposals ORDER BY created_at DESC")
if props.empty:
    st.info("Sin propuestas. Las generan los agentes Map Curator (`qa curate-map`) y Discovery (`qa discover`); "
            "se revisan con `qa proposals --approve ID` / `--reject ID` (nunca se aplican solas).")
else:
    pend = props[props["status"] == "pending"]
    st.caption(f"{len(pend)} pendientes de revisión de {len(props)} propuestas.")
    pv = props.assign(change_type=props["change_type"].map(lambda x: D.es(D.CHANGE_TYPE_ES, x)),
                      status=props["status"].map(lambda x: D.es(D.PROPOSAL_STATUS_ES, x)),
                      created_at=pd.to_datetime(props["created_at"], utc=True, errors="coerce"))
    st.dataframe(pv[["id", "created_at", "change_type", "status", "rationale", "reviewed_by"]], hide_index=True,
                 width="stretch", placeholder="—", column_config=D.colcfg(
                     pv, id=st.column_config.NumberColumn("#", format="%d"),
                     created_at=st.column_config.DatetimeColumn("fecha", format="YYYY-MM-DD HH:mm"),
                     rationale=st.column_config.TextColumn("fundamento", width="large")))
    for _, p in pend.iterrows():
        with st.expander(f"#{p['id']} · {D.es(D.CHANGE_TYPE_ES, p['change_type'])} · {str(p['rationale'])[:90]}"):
            st.json(D.js(p["payload"]))
            evd = D.js(p["evidence"]) or []
            for e in evd:
                st.markdown(f"- {e.get('claim')} — {D.es(D.VERIFICATION_ES, e.get('verification'))} · "
                            f"{e.get('source_url') or 'sin fuente'}")
            st.code(f"qa proposals --approve {p['id']}   # o --reject {p['id']}", language="bash")
            st.caption(json.dumps({"agent_run_id": p["agent_run_id"]}))
