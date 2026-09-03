"""Streamlit UI for SmartSehat — Day 3+ (combined edition).

Adds to the verified Day-3 app (protected files untouched):
- EMERGENCY DISPATCH PACKET: RED cases generate a copyable, WhatsApp-
  sendable dispatch packet (works over 2G; receiver needs no app).
- DOCTOR REVIEW LOOP (async store-and-forward telemedicine): completed
  cases compile a full clinical report FROM THE LEDGER, sendable via
  WhatsApp; the doctor's reply is logged back into the ledger
  (DOCTOR_ADVICE event), closing the loop.
- Launch: python -m streamlit run smartsehat/app.py  (project root)
"""
from __future__ import annotations
import sys
from pathlib import Path

# FIX: make the project root importable no matter where Streamlit
# sets the working directory from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import urllib.parse

import streamlit as st

from smartsehat import audit
from smartsehat.graph import resume_case, run_case
from smartsehat.router import ModelRouter, load_config
from smartsehat import dawa_graph

st.set_page_config(page_title="SmartSehat", layout="wide")

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "assets" / "uploaded"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Nearest-referral demo context (district Rajanpur)
FACILITIES = {
    "BHU-0442": ("BHU Chak 45 — Rajanpur", "DHQ Rajanpur ~18 km"),
    "BHU-0511": ("BHU Karor — Layyah", "DHQ Layyah ~25 km"),
}


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


# ======================================================================
# EMERGENCY DISPATCH PACKET
# ======================================================================

def _emergency_packet(case_id: str) -> str:
    row = audit.get_case(case_id) or {}
    bhu = row.get("bhu_id") or "BHU-0442"
    facility, distance = FACILITIES.get(bhu, (f"{bhu}", "district hospital"))
    evs = audit.get_case_timeline(case_id)
    triage = next((json.loads(e["payload_json"] or "{}") for e in evs
                   if e["event_type"] == "TRIAGE_DECISION"), {})
    return (
        f"🚨 SMARTSEHAT EMERGENCY — {case_id}\n"
        f"Triage: {triage.get('triage_level', 'RED')} — deterministic rules "
        f"(no AI decision)\n"
        f"Triggered: {', '.join(triage.get('rules') or ['red flag'])}\n"
        f"On-site protocol: aspirin 300 mg chewed NOW unless contraindicated; "
        f"position patient, monitor vitals\n"
        f"Location: {facility} ({distance})\n"
        f"Full case timeline in SmartSehat ledger\n"
        f"— Sent by health worker {row.get('paramedic_id') or 'PM-2213'} via SmartSehat"
    )


# ======================================================================
# DOCTOR REVIEW REPORT (compiled FROM the ledger)
# ======================================================================

def _doctor_report(case_id: str) -> str:
    row = audit.get_case(case_id) or {}
    evs = audit.get_case_timeline(case_id)

    def payload(e):
        try:
            return json.loads(e["payload_json"] or "{}")
        except Exception:
            return {}

    transcript = next((payload(e).get("transcript_confirmed", "") for e in evs
                       if e["event_type"] == "VOICE_TRANSCRIPT_CONFIRMED"), "")
    triage = next((payload(e) for e in evs
                   if e["event_type"] == "TRIAGE_DECISION"), {})
    vision = next((payload(e) for e in evs
                   if e["event_type"] == "VISION_FINDING"), {})
    reasoning = next((payload(e) for e in evs
                      if e["event_type"] == "REASONING_RESULT"), {})
    hitl = [payload(e) for e in evs if e["event_type"] == "HITL_DECISION"]

    bhu = row.get("bhu_id") or "BHU-0442"
    facility, _ = FACILITIES.get(bhu, (bhu, ""))

    lines = [
        f"📋 SMARTSEHAT DOCTOR REVIEW REQUEST — {case_id}",
        f"Facility: {facility} | Health worker: {row.get('paramedic_id')}",
        f"Patient: {row.get('patient_id')} — identity withheld per privacy policy",
        "",
        "── HISTORY (Urdu voice intake, worker-confirmed) ──",
        transcript or "(see ledger)",
        "",
        f"── TRIAGE: {triage.get('triage_level')} "
        f"(rules: {triage.get('rules')}) ──",
    ]
    if vision.get("findings"):
        lines.append("")
        lines.append("── X-RAY PATTERNS (AI, conservative read) ──")
        for f in vision["findings"]:
            lines.append(f"• {f.get('pattern')} @ {f.get('location')} "
                         f"({f.get('score')})")
        if vision.get("interpretation"):
            lines.append(f"Interpretation: {vision['interpretation']}")
        if vision.get("recommend"):
            lines.append(f"Recommended: {', '.join(vision['recommend'])}")
    if reasoning.get("ddx"):
        lines.append("")
        lines.append(f"── AI-ASSISTED ASSESSMENT "
                     f"(confidence {reasoning.get('confidence')}) ──")
        for d in reasoning["ddx"]:
            cites = ", ".join(d.get("citations") or [])
            lines.append(f"• {d.get('diagnosis')} ({d.get('probability')}) "
                         f"— cites: {cites or 'none'}")
        if reasoning.get("management"):
            lines.append(f"Suggested management: {reasoning['management']}")
        lines.append("_AI-assisted draft — NOT a diagnosis. "
                     "Doctor confirmation required._")
    if hitl:
        lines.append("")
        lines.append("── WORKER DECISIONS ──")
        for h in hitl:
            lines.append(f"• {h.get('action')} — "
                         f"{h.get('reason') or '(no reason given)'}")
    lines += [
        "",
        "── REQUESTED FROM DOCTOR ──",
        "1. Confirm/adjust working diagnosis",
        "2. Confirm/adjust prescription",
        "3. Any additional advice or red flags to watch",
        "",
        "Reply by message; the health worker will log your reply "
        "in the case ledger.",
    ]
    return "\n".join(lines)


def _whatsapp_link(text: str) -> str:
    return "https://wa.me/?text=" + urllib.parse.quote(text[:1500])


# ======================================================================
# Sidebar / shared helpers (unchanged from Day 3)
# ======================================================================

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

    paramedic_id = st.sidebar.text_input("Health worker ID", value="PM-2213")
    st.sidebar.divider()
    workflow = st.sidebar.radio("Workflow",
                                ["Consultation", "Dawa Check"], index=0)
    st.session_state["_workflow"] = workflow
    return patient_id, paramedic_id


def _render_timeline(case_id: str) -> None:
    for ev in audit.get_case_timeline(case_id):
        with st.container(border=True):
            cols = st.columns([1, 3])
            cols[0].caption(ev["timestamp"])
            cols[0].markdown(f"**{ev['event_type']}**  \n"
                             f"`{ev.get('provider_used') or ''}`")
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

    if gate.get("confidence") is not None:
        try:
            st.caption(f"AI confidence: **{float(gate['confidence']):.2f}**")
        except Exception:
            st.caption(f"AI confidence: {gate.get('confidence')}")

    if gate.get("degraded"):
        st.error(f"⚠️ AI reasoning unavailable — {gate['degraded']}. "
                 f"Referral recommended.")
    if gate.get("ddx"):
        st.markdown("**AI-assisted assessment (cited)**")
        for d in gate["ddx"]:
            cits = ", ".join(d.get("citations") or []) or "no citations"
            st.markdown(f"- **{d.get('diagnosis', '?')}** "
                        f"({d.get('probability', '?')}) — _{cits}_")
    if gate.get("prescription_draft"):
        st.markdown("**Prescription draft (doctor confirmable)**")
        st.code(gate["prescription_draft"], language="text")


def _light_emoji(light: str) -> str:
    return {"RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢"}.get(light, "⚪")


# ======================================================================
# Dawa Check tab (unchanged logic from Day 3)
# ======================================================================

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
    if st.button("Extract medicines", type="primary",
                 disabled=(rx_upload is None)):
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
        st.warning("🎤 Confirm medicine list — health worker review")
        if ss.dawa_gate.get("degraded"):
            st.error(f"⚠️ Vision extraction unavailable — "
                     f"{ss.dawa_gate['degraded']}. Type medicines manually below.")
        meds = list(ss.dawa_gate.get("medicines") or [])
        if not meds:
            meds = [{"name": "", "strength": ""}]
        rows = []
        for i, m in enumerate(meds):
            c1, c2 = st.columns([3, 2])
            n = c1.text_input(f"Medicine {i+1}", value=m.get("name", ""),
                              key=f"dawa_m_{i}")
            s = c2.text_input(f"Strength {i+1}",
                              value=m.get("strength") or "",
                              key=f"dawa_s_{i}")
            if n.strip():
                rows.append({"name": n.strip(),
                             "strength": s.strip() or None})
        if st.button("+ add row"):
            meds.append({"name": "", "strength": ""})
            st.rerun()

        c1, c2 = st.columns(2)
        pregnant = c1.checkbox("Pregnant", value=False)
        age = c2.number_input("Age", min_value=0, max_value=120,
                              value=30, step=1)
        current_meds = st.text_input(
            "Current meds / context (comma separated, optional)", value="")
        indication = st.text_input(
            "Indication (optional — helps stewardship checks)", value="")

        if st.button("Confirm & check", type="primary"):
            patient = {
                "pregnant": bool(pregnant),
                "age": int(age),
                "current_meds": [m.strip() for m in current_meds.split(",")
                                 if m.strip()],
                "indication": indication.strip(),
            }
            r = dawa_graph.resume_dawa_case(
                ss.dawa_thread_id, medicines=rows, patient=patient,
            )
            ss.dawa_gate = _latest_gate_payload(r)
            ss.dawa_stage = ("review"
                             if ss.dawa_gate.get("gate") == "dawa_review"
                             else "done")
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
                        f"- **{f.get('code')}** ({f.get('severity')}) — "
                        f"{f.get('reason')} "
                        + (f"  \n  _cite:_ `{f['citation']}`"
                           if f.get('citation') else "")
                    )
                    with st.expander(f"Dismiss flag {f.get('code')} "
                                     f"on {row['name']}"):
                        reason = st.text_input(
                            "Reason",
                            key=f"dawa_dismiss_{row['name']}_{f.get('code')}")
                        if st.button(
                            "Dismiss",
                            key=f"btn_dismiss_{row['name']}_{f.get('code')}",
                        ):
                            r = dawa_graph.resume_dawa_case(
                                ss.dawa_thread_id, action="dismiss",
                                medicine=row["name"],
                                flag_code=f.get("code"),
                                reason=reason,
                            )
                            ss.dawa_gate = _latest_gate_payload(r)
                            ss.dawa_stage = ("review"
                                             if ss.dawa_gate.get("gate") == "dawa_review"
                                             else "done")
                            st.rerun()
        if st.button("Finalize", type="primary"):
            r = dawa_graph.resume_dawa_case(ss.dawa_thread_id,
                                            action="finalize")
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


# ======================================================================
# Consultation tab (with emergency packet + doctor review loop)
# ======================================================================

def main() -> None:
    patient_id, paramedic_id = _sidebar()
    workflow = st.session_state.get("_workflow", "Consultation")
    if workflow == "Dawa Check":
        _dawa_tab(paramedic_id)
        return
    st.title("SmartSehat — one patient, one journey")

    ss = st.session_state
    ss.setdefault("case_id", None)
    ss.setdefault("thread_id", None)
    ss.setdefault("stage", "idle")   # idle | transcript | hitl | done
    ss.setdefault("gate", {})

    with st.expander("Attach patient inputs (optional in stub mode)",
                     expanded=True):
        c1, c2 = st.columns(2)
        audio = c1.file_uploader("Patient audio (.wav)", type=["wav"])
        image = c2.file_uploader("Chest X-ray (.jpg/.png)",
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
        ss.stage = ("transcript"
                    if ss.gate.get("gate") == "transcript" else "hitl")

    if not ss.case_id:
        st.info("Pick a patient, optionally attach audio/image, and click "
                "**Start new case**.")
        return

    st.subheader(f"Case {ss.case_id}")
    _render_timeline(ss.case_id)

    if ss.stage == "transcript":
        st.warning("🎤 Confirm transcript — health worker review")
        edited = st.text_area("Transcript (Urdu)",
                              value=ss.gate.get("transcript", ""),
                              height=120)
        if st.button("Confirm transcript"):
            r = resume_case(ss.thread_id, action="confirm", transcript=edited)
            ss.gate = _latest_gate_payload(r)
            ss.stage = "hitl" if "__interrupt__" in r else "done"
            st.rerun()

    elif ss.stage == "hitl":
        st.warning("🩺 Health worker gate — your decision required")
        _hitl_card(ss.case_id, ss.gate)
        c1, c2, c3 = st.columns(3)
        if c1.button("✅ Approve assessment"):
            r = resume_case(ss.thread_id, action="approve")
            ss.gate = _latest_gate_payload(r)
            ss.stage = "hitl" if "__interrupt__" in r else "done"
            st.rerun()
        with c2.expander("✏️ Modify prescription"):
            modified = st.text_area("Modified prescription", key="mod_txt")
            if st.button("Submit modification"):
                r = resume_case(ss.thread_id, action="modify",
                                modified_content=modified)
                ss.gate = _latest_gate_payload(r)
                ss.stage = "hitl" if "__interrupt__" in r else "done"
                st.rerun()
        with c3.expander("❌ Reject (reason)"):
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
                       f"(worker decision: {row.get('hitl_outcome')})")

            # ---- EMERGENCY DISPATCH PACKET (RED cases) ----
            if (row.get("triage_level") == "RED"
                    or row.get("outcome") == "emergency_referral"):
                st.error("🚨 Emergency dispatch packet — send now")
                packet = _emergency_packet(ss.case_id)
                st.code(packet)  # Streamlit renders a COPY button
                st.markdown(f"**[📲 Send via WhatsApp]({_whatsapp_link(packet)})**")
                st.caption("Works over SMS/2G. The receiving end needs no app. "
                           "The golden hour, compressed.")

            # ---- DOCTOR REVIEW LOOP (async telemedicine) ----
            elif row.get("outcome") in ("prescription_issued",
                                        "dawa_check_completed"):
                st.subheader("🩺 Send to doctor — async review")
                report = _doctor_report(ss.case_id)
                with st.expander("Doctor Review Report (compiled from ledger)",
                                 expanded=True):
                    st.code(report)
                    st.markdown(f"**[📲 Send via WhatsApp]"
                                f"({_whatsapp_link(report)})** — works on 2G, "
                                f"doctor needs no app")
                st.markdown("**Log the doctor's reply** (closes the loop — "
                            "enters the ledger):")
                doc_reply = st.text_area("Doctor's reply (paste message "
                                         "or voice-note text)",
                                         key=f"doc_reply_{ss.case_id}")
                if st.button("Log doctor advice",
                             key=f"btn_doc_{ss.case_id}"):
                    if doc_reply.strip():
                        audit.log_event(
                            ss.case_id, "DOCTOR_ADVICE",
                            {"advice": doc_reply.strip(),
                             "channel": "async_message"},
                            provider="doctor",
                        )
                        st.success("Logged. The consultation is closed with "
                                   "doctor confirmation — permanently in "
                                   "the ledger.")
                        st.rerun()
                    else:
                        st.warning("Paste the doctor's reply first.")


if __name__ == "__main__":
    main()