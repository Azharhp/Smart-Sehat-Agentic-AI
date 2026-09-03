"""Streamlit UI for SmartSehat — Day 2 (patched).

Patches in this version:
- FIX: Streamlit puts the app's OWN folder on sys.path, not the project
  root — so `from smartsehat import ...` fails when launched via
  `streamlit run smartsehat/app.py`. We force the project root onto
  the import path so the app works regardless of launch directory.
- PATCH 5: HITL card now shows AI confidence (tier-transparency) and
  renders the degraded warning reliably (payload wired via graph.py
  PATCH 3).

Run from the project root (the folder containing config.yaml):
    cd <project root>
    streamlit run smartsehat/app.py
"""
from __future__ import annotations
import sys
from pathlib import Path

# FIX: make the project root importable no matter where Streamlit
# sets the working directory from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

import streamlit as st

from smartsehat import audit
from smartsehat.graph import resume_case, run_case
from smartsehat.router import ModelRouter, load_config
from smartsehat import dawa_graph

st.set_page_config(page_title="SmartSehat • Day 3", layout="wide")

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "assets" / "uploaded"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@st.cache_resource
def _router() -> ModelRouter:
    return ModelRouter()


@st.cache_data
def _cfg() -> dict:
    return load_config()


def _save_upload(uploaded, fixed_name: str) -> str | None:
    if uploaded is None:
        return None
    dest = UPLOAD_DIR / fixed_name
    with open(dest, "wb") as f:
        f.write(uploaded.getbuffer())
    return str(dest)


def _sidebar() -> tuple[str, str]:
    st.sidebar.title("SmartSehat")
    st.sidebar.caption(f"Agents mode: **{_cfg().get('agents', 'stub')}**")

    st.sidebar.subheader("Provider status")
    for name, ok in _router().provider_health().items():
        st.sidebar.write(f"• {name}: {'✅ ready' if ok else '⚠️ not configured'}")

    patients = audit.list_patients()
    if not patients:
        st.sidebar.warning("No patients found. Run `python seed.py` first.")
        st.stop()
    labels = {f"{p['name']} ({p['patient_id']})": p["patient_id"] for p in patients}
    label = st.sidebar.selectbox("Patient", list(labels.keys()))
    patient_id = labels[label]

    st.sidebar.subheader("Recent cases")
    for c in audit.list_cases_for_patient(patient_id)[:8]:
        st.sidebar.caption(f"{c['case_id']} — {c.get('outcome') or 'in-progress'}")

    paramedic_id = st.sidebar.text_input("Paramedic ID", value="PM-2213")
    st.sidebar.divider()
    workflow = st.sidebar.radio("Workflow", ["Consultation", "Dawa Check"], index=0)
    st.session_state["_workflow"] = workflow
    return patient_id, paramedic_id


def _render_timeline(case_id: str) -> None:
    for ev in audit.get_case_timeline(case_id):
        with st.container(border=True):
            cols = st.columns([1, 3])
            cols[0].caption(ev["timestamp"])
            cols[0].markdown(f"**{ev['event_type']}**  \n`{ev.get('provider_used') or ''}`")
            try:
                payload = json.loads(ev["payload_json"] or "{}")
            except Exception:
                payload = {"raw": ev["payload_json"]}
            cols[1].json(payload)


def _latest_gate_payload(result: dict) -> dict:
    ints = result.get("__interrupt__")
    if not ints:
        return {}
    first = ints[0]
    val = getattr(first, "value", None) or (first[0] if isinstance(first, tuple) else None)
    return val or {}


def _hitl_card(case_id: str, gate: dict) -> None:
    tl = gate.get("triage_level") or "—"
    color = {"RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢"}.get(tl, "⚪")
    st.markdown(f"### {color} Triage: **{tl}**")

    # PATCH 5: confidence transparency on the decision screen.
    if gate.get("confidence") is not None:
        try:
            st.caption(f"AI confidence: **{float(gate['confidence']):.2f}**")
        except Exception:
            st.caption(f"AI confidence: {gate.get('confidence')}")

    if gate.get("degraded"):
        st.error(f"⚠️ AI reasoning unavailable — {gate['degraded']}. Referral recommended.")
    if gate.get("ddx"):
        st.markdown("**Differential diagnosis**")
        for d in gate["ddx"]:
            cits = ", ".join(d.get("citations") or []) or "no citations"
            st.markdown(f"- **{d.get('diagnosis', '?')}** ({d.get('probability', '?')}) — _{cits}_")
    if gate.get("prescription_draft"):
        st.markdown("**Prescription draft**")
        st.code(gate["prescription_draft"], language="text")


def _light_emoji(light: str) -> str:
    return {"RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢"}.get(light, "⚪")


def _dawa_tab(paramedic_id: str) -> None:
    st.title("Dawa Check — prescription / medicine-bag audit")
    ss = st.session_state
    ss.setdefault("dawa_case_id", None)
    ss.setdefault("dawa_thread_id", None)
    ss.setdefault("dawa_stage", "idle")   # idle | confirm | review | done
    ss.setdefault("dawa_gate", {})

    rx_upload = st.file_uploader(
        "Rx / medicine-bag image (.jpg/.png)",
        type=["jpg", "jpeg", "png"], key="dawa_rx",
    )
    if st.button("Extract medicines", type="primary", disabled=(rx_upload is None)):
        rx_path = _save_upload(rx_upload, "dawa_rx.jpg")
        cid, tid, first = dawa_graph.run_dawa_case(
            rx_image_path=rx_path or "", paramedic_id=paramedic_id,
        )
        ss.dawa_case_id, ss.dawa_thread_id = cid, tid
        ss.dawa_gate = _latest_gate_payload(first)
        ss.dawa_stage = "confirm" if ss.dawa_gate.get("gate") == "dawa_confirm" else "done"

    if not ss.dawa_case_id:
        st.info("Upload an Rx or medicine-bag photo, then click **Extract medicines**.")
        return

    st.subheader(f"Dawa case {ss.dawa_case_id}")

    if ss.dawa_stage == "confirm":
        st.warning("🎤 Confirm medicine list — paramedic review")
        if ss.dawa_gate.get("degraded"):
            st.error(f"⚠️ Vision extraction unavailable — {ss.dawa_gate['degraded']}. Type medicines manually below.")
        meds = list(ss.dawa_gate.get("medicines") or [])
        if not meds:
            meds = [{"name": "", "strength": ""}]
        rows = []
        for i, m in enumerate(meds):
            c1, c2 = st.columns([3, 2])
            n = c1.text_input(f"Medicine {i+1}", value=m.get("name", ""),
                              key=f"dawa_m_{i}")
            s = c2.text_input(f"Strength {i+1}", value=m.get("strength") or "",
                              key=f"dawa_s_{i}")
            if n.strip():
                rows.append({"name": n.strip(),
                             "strength": s.strip() or None})
        if st.button("+ add row"):
            meds.append({"name": "", "strength": ""})
            st.rerun()

        c1, c2 = st.columns(2)
        pregnant = c1.checkbox("Pregnant", value=False)
        age = c2.number_input("Age", min_value=0, max_value=120, value=30, step=1)
        current_meds = st.text_input(
            "Current meds / context (comma separated, optional)", value="")
        indication = st.text_input(
            "Indication (optional — helps stewardship checks)", value="")

        if st.button("Confirm & check", type="primary"):
            patient = {
                "pregnant": bool(pregnant),
                "age": int(age),
                "current_meds": [m.strip() for m in current_meds.split(",") if m.strip()],
                "indication": indication.strip(),
            }
            r = dawa_graph.resume_dawa_case(
                ss.dawa_thread_id, medicines=rows, patient=patient,
            )
            ss.dawa_gate = _latest_gate_payload(r)
            ss.dawa_stage = "review" if ss.dawa_gate.get("gate") == "dawa_review" else "done"
            st.rerun()

    elif ss.dawa_stage == "review":
        grid = ss.dawa_gate.get("grid") or []
        st.markdown("### Traffic-light grid")
        for row in grid:
            with st.container(border=True):
                header = f"{_light_emoji(row.get('light', 'GREEN'))} **{row['name']}**"
                sub = []
                if row.get("generic"):
                    sub.append(f"generic: `{row['generic']}`")
                if row.get("class"):
                    sub.append(f"class: `{row['class']}`")
                st.markdown(header + (" — " + " · ".join(sub) if sub else ""))
                for f in row.get("flags") or []:
                    st.markdown(
                        f"- **{f.get('code')}** ({f.get('severity')}) — {f.get('reason')} "
                        + (f"  \n  _cite:_ `{f['citation']}`" if f.get('citation') else "")
                    )
                    with st.expander(f"Dismiss flag {f.get('code')} on {row['name']}"):
                        reason = st.text_input(
                            "Reason", key=f"dawa_dismiss_{row['name']}_{f.get('code')}")
                        if st.button(
                            "Dismiss",
                            key=f"btn_dismiss_{row['name']}_{f.get('code')}",
                        ):
                            r = dawa_graph.resume_dawa_case(
                                ss.dawa_thread_id, action="dismiss",
                                medicine=row["name"], flag_code=f.get("code"),
                                reason=reason,
                            )
                            ss.dawa_gate = _latest_gate_payload(r)
                            ss.dawa_stage = "review" if ss.dawa_gate.get("gate") == "dawa_review" else "done"
                            st.rerun()
        if st.button("Finalize", type="primary"):
            r = dawa_graph.resume_dawa_case(ss.dawa_thread_id, action="finalize")
            ss.dawa_gate = _latest_gate_payload(r)
            ss.dawa_stage = "done"
            st.rerun()

    else:  # done
        row = audit.get_case(ss.dawa_case_id)
        if row and row.get("completed_at"):
            st.success(f"Dawa case complete — outcome: {row.get('outcome')}")

    st.divider()
    st.subheader("Ledger timeline")
    _render_timeline(ss.dawa_case_id)


def main() -> None:
    patient_id, paramedic_id = _sidebar()
    workflow = st.session_state.get("_workflow", "Consultation")
    if workflow == "Dawa Check":
        _dawa_tab(paramedic_id)
        return
    st.title("Clinical case runner — Day 3")

    ss = st.session_state
    ss.setdefault("case_id", None)
    ss.setdefault("thread_id", None)
    ss.setdefault("stage", "idle")   # idle | transcript | hitl | done
    ss.setdefault("gate", {})

    with st.expander("Attach patient inputs (optional in stub mode)", expanded=True):
        c1, c2 = st.columns(2)
        audio = c1.file_uploader("Patient audio (.wav)", type=["wav"])
        image = c2.file_uploader("Chest X-ray or Rx image (.jpg/.png)",
                                 type=["jpg", "jpeg", "png"])

    if st.button("Start new case", type="primary"):
        audio_path = _save_upload(audio, "case_audio.wav")
        image_path = _save_upload(image, "case_image.jpg")
        case_id, thread_id, result = run_case(
            patient_id, paramedic_id,
            audio_path=audio_path, image_path=image_path,
        )
        ss.case_id = case_id
        ss.thread_id = thread_id
        ss.gate = _latest_gate_payload(result)
        ss.stage = "transcript" if ss.gate.get("gate") == "transcript" else "hitl"

    if not ss.case_id:
        st.info("Pick a patient, optionally attach audio/image, and click **Start new case**.")
        return

    st.subheader(f"Case {ss.case_id}")
    _render_timeline(ss.case_id)

    if ss.stage == "transcript":
        st.warning("🎤 Confirm transcript — paramedic review")
        edited = st.text_area("Transcript (Urdu)",
                              value=ss.gate.get("transcript", ""),
                              height=120)
        if st.button("Confirm transcript"):
            r = resume_case(ss.thread_id, action="confirm", transcript=edited)
            ss.gate = _latest_gate_payload(r)
            ss.stage = "hitl" if "__interrupt__" in r else "done"
            st.rerun()

    elif ss.stage == "hitl":
        st.warning("🩺 Human-in-the-loop gate — paramedic decision required")
        _hitl_card(ss.case_id, ss.gate)
        c1, c2, c3 = st.columns(3)
        if c1.button("✅ Approve"):
            r = resume_case(ss.thread_id, action="approve")
            ss.gate = _latest_gate_payload(r)
            ss.stage = "hitl" if "__interrupt__" in r else "done"
            st.rerun()
        with c2.expander("✏️ Modify"):
            modified = st.text_area("Modified prescription", key="mod_txt")
            if st.button("Submit modification"):
                r = resume_case(ss.thread_id, action="modify", modified_content=modified)
                ss.gate = _latest_gate_payload(r)
                ss.stage = "hitl" if "__interrupt__" in r else "done"
                st.rerun()
        with c3.expander("❌ Reject"):
            reason = st.text_input("Rejection reason", key="rej_txt")
            if st.button("Submit rejection"):
                r = resume_case(ss.thread_id, action="reject", reason=reason)
                ss.gate = _latest_gate_payload(r)
                ss.stage = "hitl" if "__interrupt__" in r else "done"
                st.rerun()

    else:  # done
        row = audit.get_case(ss.case_id)
        if row and row.get("completed_at"):
            st.success(f"Case complete — outcome: {row.get('outcome')} "
                       f"(HITL: {row.get('hitl_outcome')})")


if __name__ == "__main__":
    main()