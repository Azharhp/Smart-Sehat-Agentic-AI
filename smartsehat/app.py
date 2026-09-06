"""Streamlit UI for SmartSehat — final edition (follow-up history engine).

The clinical flow now completes the history BEFORE assessment:
  STT → transcript gate (review + ASK follow-up questions + record
  answers) → enriched transcript → triage / vision / reasoning / Dawa
  — so the deterministic rules see the full history (e.g., an answer
  of "bleeding" in pregnancy flips the case to RED).

Also carries: emergency dispatch packet (RED cases), doctor review
loop (async store-and-forward telemedicine), Dawa Check tab, District
View tab.

Launch: python -m streamlit run smartsehat/app.py  (project root)
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
from smartsehat.graph import resume_case, start_case
from smartsehat.router import ModelRouter, load_config
from smartsehat import dawa_graph
from smartsehat.medication_ui import medication_editor, patient_context_editor, render_medication_review
from smartsehat.auth import DemoAuthenticationProvider, get_authentication_provider
from smartsehat.domain import ActorContext
from smartsehat import access, ui_state
from smartsehat.media import MediaValidationError, PrivateMediaStore

st.set_page_config(page_title="SmartSehat", layout="wide")

# Nearest-referral demo context
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


# ======================================================================
# FOLLOW-UP QUESTION ENGINE (deterministic — completes the history
# BEFORE triage/reasoning run; answers appended to the transcript are
# seen by the triage rules, e.g. bleeding-in-pregnancy → RED)
# ======================================================================

def _suggest_followups(transcript: str, patient_row: dict) -> list[tuple[str, str]]:
    """Safety-driven follow-up questions from what the transcript LACKS.

    Deterministic Python — never an LLM. Questions are bilingual
    (Urdu + English gloss) so any health worker can use them.
    """
    t = (transcript or "").lower()
    age = (patient_row or {}).get("age") or 0
    sex = (patient_row or {}).get("sex") or ""
    q: list[tuple[str, str]] = []

    def has(*words: str) -> bool:
        return any(w in t for w in words)

    # Hemoptysis — could flip a cough case toward urgent
    if has("کھانسی", "کھاںسی", "cough") and not has("خون", "khoon", "blood"):
        q.append(("hemoptysis",
                  "کیا بلغم میں خون آتا ہے؟ (Does sputum contain blood?)"))
    # Pregnancy status — critical for triage AND Dawa Check
    if sex == "F" and 12 <= age <= 50 and not has("حاملہ", "حمل", "pregnan"):
        q.append(("pregnancy_status",
                  "کیا آپ حاملہ ہیں؟ (Are you pregnant?)"))
    # Pregnancy danger signs — the question that can flip to RED
    if has("حاملہ", "حمل", "pregnan") and not has("خون", "bleeding"):
        q.append(("pregnancy_bleeding",
                  "کیا خون آ رہا ہے؟ (Any bleeding?) — danger sign"))
    # Duration of fever (rule: fever > 3 days)
    if has("بخار", "fever") and not has("دن", "din", "day", "دنوں"):
        q.append(("fever_duration",
                  "بخار کتنے دنوں سے ہے؟ (How many days of fever?)"))
    # Current medications — feeds Dawa Check + DDI context
    if not has("دوا", "دوائی", "medicin", "clinic"):
        q.append(("current_meds",
                  "کیا آپ کوئی دوا لے رہی ہیں؟ اور کس کلینک سے؟ "
                  "(Taking any medicines? From which clinic?)"))
    # Smoking — TB risk factor
    if has("کھانسی", "کھاںسی", "cough") and not has("smok", "سگریٹ"):
        q.append(("smoking",
                  "کیا آپ سگریٹ پیتے ہیں؟ (Do you smoke?)"))
    return q[:5]  # keep it practical — max 5 questions


# ======================================================================
# EMERGENCY DISPATCH PACKET
# ======================================================================

def _emergency_packet(case_id: str, actor: ActorContext) -> str:
    encounter = access.authorize_export(actor, case_id)
    bhu = encounter.facility_id
    facility, distance = FACILITIES.get(bhu, (f"{bhu}", "district hospital"))
    evs = access.get_timeline(actor, case_id)
    triage = next((json.loads(e["payload_json"] or "{}") for e in evs
                   if e["event_type"] == "TRIAGE_DECISION"), {})
    protocol = next((json.loads(e["payload_json"] or "{}") for e in evs
                     if e["event_type"] == "EMERGENCY_PROTOCOL"), {})
    return (
        f"🚨 SMARTSEHAT EMERGENCY — {case_id}\n"
        f"Triage: {triage.get('triage_level', 'RED')} — deterministic rules "
        f"(no AI decision)\n"
        f"Triggered: {', '.join(triage.get('rules') or ['red flag'])}\n"
        f"Protocol status: {protocol.get('status', 'REQUIRES_CLINICAL_REVIEW')}\n"
        f"Action: {protocol.get('instruction', 'Urgent referral and clinical review required.')}\n"
        f"Location: {facility} ({distance})\n"
        f"Full case timeline in SmartSehat ledger\n"
        f"— Recorded by authenticated actor {encounter.actor_id} via SmartSehat"
    )


# ======================================================================
# DOCTOR REVIEW REPORT (compiled FROM the ledger)
# ======================================================================

def _doctor_report(case_id: str, actor: ActorContext) -> str:
    encounter = access.authorize_export(actor, case_id)
    evs = access.get_timeline(actor, case_id)

    def payload(e):
        try:
            return json.loads(e["payload_json"] or "{}")
        except Exception:
            return {}

    transcript = next((payload(e).get("transcript", "") for e in evs
                       if e["event_type"] == "VOICE_TRANSCRIPT_CONFIRMED"), "")
    triage = next((payload(e) for e in evs
                   if e["event_type"] == "TRIAGE_DECISION"), {})
    vision = next((payload(e) for e in evs
                   if e["event_type"] == "VISION_FINDING"), {})
    reasoning = next((payload(e) for e in evs
                      if e["event_type"] == "REASONING_RESULT"), {})
    hitl = [payload(e) for e in evs if e["event_type"] == "HITL_DECISION"]

    bhu = encounter.facility_id
    facility, _ = FACILITIES.get(bhu, (bhu, ""))

    lines = [
        f"📋 SMARTSEHAT DOCTOR REVIEW REQUEST — {case_id}",
        f"Facility: {facility} | Encounter owner: {encounter.actor_id}",
        f"Patient reference: {encounter.patient_id or encounter.snapshot.patient.display_reference}",
        "",
        "── HISTORY (Urdu voice intake + worker-confirmed follow-ups) ──",
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
    final = next((payload(e) for e in reversed(evs) if e["event_type"] == "RX_FINALIZED"), {})
    review = final.get("medication_review") or {}
    if final:
        lines += ["", "── FINAL WORKER-REVIEWED PLAN ──",
                  final.get("prescription_draft") or "No medicine plan recorded",
                  f"Medication review: {review.get('status', 'not performed')}",
                  f"Rules: {review.get('ruleset_version', 'none')} | Input: {review.get('input_hash', 'none')}",
                  f"Review note: {final.get('review_note') or '(none)'}"]
        for flag in review.get("flags", []):
            evidence = flag.get("evidence") or {}
            lines.append(f"• {flag.get('code')}: {flag.get('reason')} — {evidence.get('url', '')}")
        for item in review.get("not_assessed", []):
            lines.append(f"Not assessed: {item}")
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
    return "https://wa.me/?text=" + urllib.parse.quote(text)


# ======================================================================
# Sidebar / shared helpers
# ======================================================================

def _sidebar() -> tuple[str | None, ActorContext]:
    st.sidebar.title("SmartSehat")
    st.sidebar.caption(f"Agents mode: **{_cfg().get('agents', 'stub')}**")
    actor_data = st.session_state.get("authenticated_actor")
    if not actor_data:
        provider = get_authentication_provider()
        st.sidebar.warning("Development authentication — not a production identity provider.")
        if isinstance(provider, DemoAuthenticationProvider):
            accounts = provider.available_accounts()
            if not accounts:
                st.sidebar.error("No demo accounts. Run `python seed.py` first.")
                st.stop()
            labels = {f"{row['name']} · {row['role']}": row["user_id"] for row in accounts}
            selected = st.sidebar.selectbox("Demo account", list(labels))
            if st.sidebar.button("Start demo session"):
                actor = provider.authenticate(labels[selected])
                st.session_state["authenticated_actor"] = actor.to_dict()
                st.rerun()
        else:
            st.sidebar.error("Production authentication is unavailable.")
        st.stop()
    actor = ActorContext.from_dict(actor_data)
    if st.sidebar.button("Sign out"):
        for key in list(st.session_state):
            if key == "authenticated_actor" or "encounter" in key or key.startswith(("fu_", "transcript_")):
                del st.session_state[key]
        st.rerun()
    if len(actor.facility_ids) > 1:
        facility = st.sidebar.selectbox("Active facility", actor.facility_ids,
                                        index=actor.facility_ids.index(actor.active_facility_id))
        if facility != actor.active_facility_id:
            actor = actor.with_active_facility(facility)
            st.session_state["authenticated_actor"] = actor.to_dict()
    st.sidebar.success(f"{actor.display_name} · {actor.role}\n\n{actor.active_facility_id}")
    if actor.auth_source == "demo-local":
        st.sidebar.warning("Demo identity — not production authentication.")
    st.sidebar.subheader("Provider status")
    for name, ok in _router().provider_health().items():
        st.sidebar.write(f"• {name}: {'✅ ready' if ok else '⚪ disabled/unavailable'}")

    patient_id = None
    if "patient:read" in actor.permissions:
        patients = access.list_patients(actor)
        if not patients:
            st.sidebar.warning("No authorized patients at this facility.")
        else:
            labels = {f"{p['name']} ({p['patient_id']})": p["patient_id"] for p in patients}
            patient_id = labels[st.sidebar.selectbox("Patient", list(labels))]
            st.sidebar.subheader("Recent owned encounters")
            for encounter in access.list_encounters(actor)[:8]:
                if encounter.get("patient_id") == patient_id:
                    st.sidebar.caption(f"{encounter['case_id']} — {encounter.get('status')}")
    st.sidebar.divider()
    workflows = []
    if "encounter:create" in actor.permissions:
        workflows.extend(["Consultation", "Dawa Check"])
    if "district:view" in actor.permissions:
        workflows.append("District View")
    if not workflows:
        st.sidebar.error("This role has no application permissions.")
        st.stop()
    workflow = st.sidebar.radio("Workflow", workflows, index=0)
    st.session_state["_workflow"] = workflow
    return patient_id, actor


def _render_timeline(case_id: str, actor: ActorContext) -> None:
    for ev in access.get_timeline(actor, case_id):
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
            st.caption(f"Model-reported confidence (uncalibrated): **{float(gate['confidence']):.2f}**")
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
        st.markdown("**Proposed medicine plan — human review required**")
        st.code(gate["prescription_draft"], language="text")
    if gate.get("management"):
        st.markdown("**Assessment and management draft**")
        st.write(gate["management"])
        st.caption("Medicine instructions must be reconciled with the structured plan.")
    if gate.get("workup"):
        st.write("Suggested workup: " + "; ".join(str(x) for x in gate["workup"]))
    if gate.get("uncertainty_note"):
        st.caption(gate["uncertainty_note"])
    if gate.get("medication_review"):
        render_medication_review(gate["medication_review"])
        _download_review(case_id, gate["medication_review"], gate.get("medication_context"), "consultation")
    if gate.get("review_validation_error"):
        st.error(gate["review_validation_error"])



def _light_emoji(light: str) -> str:
    return {"RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢"}.get(light, "⚪")


def _review_inputs_changed(gate: dict, medicines: list[dict], context: dict) -> bool:
    med_fields = ("id", "name", "strength", "dose_mg", "frequency_per_day",
                  "duration_days", "route", "indication")
    context_fields = ("age", "sex", "weight_kg", "allergies", "current_meds", "pregnant",
                      "gestational_weeks", "renal_impairment", "liver_disease",
                      "aspirin_prescribed", "context_confirmed")

    def med_view(rows):
        return [{field: row.get(field) for field in med_fields} for row in (rows or [])]

    def context_view(value):
        value = value or {}
        return {field: value.get(field) for field in context_fields}

    return (med_view(medicines) != med_view(gate.get("proposed_medicines"))
            or context_view(context) != context_view(gate.get("medication_context")))


def _download_review(case_id: str, report: dict, context: dict | None, workflow: str) -> None:
    receipt = {"case_id": case_id, "workflow": workflow,
               "patient_context": context, "review": report}
    st.download_button("Download medication evidence receipt (JSON)",
                       json.dumps(receipt, ensure_ascii=False, indent=2),
                       file_name=f"{case_id}-medication-review.json", mime="application/json",
                       key=f"{workflow}:{case_id}:download")


# ======================================================================
# Dawa Check tab
# ======================================================================

def _remember_widget(workflow: str, encounter_id: str, draft_name: str,
                     widget_key: str) -> None:
    ui_state.set_draft(st.session_state, workflow, encounter_id, draft_name,
                       st.session_state.get(widget_key))


def _remember_upload(workflow: str, encounter_id: str, draft_name: str,
                     widget_key: str, confirmation_draft: str | None = None,
                     confirmation_widget_key: str | None = None) -> None:
    uploaded = st.session_state.get(widget_key)
    value = ({"name": uploaded.name, "bytes": uploaded.getvalue()}
             if uploaded is not None else None)
    ui_state.set_draft(st.session_state, workflow, encounter_id, draft_name, value)
    # A modality attestation applies only to the exact file the worker saw.
    # Replacing or clearing the upload therefore requires a fresh attestation.
    if confirmation_draft:
        ui_state.set_draft(st.session_state, workflow, encounter_id,
                           confirmation_draft, False)
        if confirmation_widget_key:
            st.session_state[confirmation_widget_key] = False


def _store_upload(store: PrivateMediaStore, actor: ActorContext, encounter_id: str,
                  uploaded, purpose: str, confirmed: bool = False,
                  retained: dict | None = None) -> str | None:
    if uploaded is not None:
        data, name = uploaded.getvalue(), uploaded.name
    elif retained:
        data, name = retained.get("bytes"), retained.get("name")
    else:
        return None
    metadata = store.store(actor, encounter_id, data,
                           original_filename=name, purpose=purpose,
                           modality_confirmed=confirmed)
    return metadata["artifact_id"]


def _stage_from_result(result: dict, expected_gate: str) -> tuple[str, dict]:
    gate = _latest_gate_payload(result)
    return (expected_gate if gate.get("gate") else "done"), gate


def _encounter_picker(workflow: str, label: str) -> dict | None:
    bucket = st.session_state.get(f"{workflow}_encounters", {})
    if not bucket:
        return None
    ids = list(bucket)
    picker_key = f"{workflow}_encounter_picker"
    pending = st.session_state.pop(f"pending_{workflow}_selection", None)
    if pending in bucket:
        st.session_state[picker_key] = pending
    active_id = st.session_state.get(f"active_{workflow}_id")
    picker_args = {
        "format_func": lambda encounter_id: (
            f"{encounter_id} · {bucket[encounter_id].get('patient_id') or 'walk-in'} · "
            f"{bucket[encounter_id].get('stage')}"
        ),
        "key": picker_key,
    }
    if picker_key not in st.session_state:
        picker_args["index"] = ids.index(active_id) if active_id in ids else 0
    chosen = st.selectbox(label, ids, **picker_args)
    return ui_state.select(st.session_state, workflow, chosen)


def _create_ui_encounter(actor: ActorContext, workflow: str, patient_id: str | None,
                         walk_in_reference: str | None = None) -> dict:
    encounter = access.create_encounter(actor, workflow, patient_id=patient_id,
                                        walk_in_reference=walk_in_reference)
    return ui_state.register(
        st.session_state, workflow, encounter.encounter_id, thread_id=encounter.encounter_id,
        patient_id=encounter.patient_id, facility_id=encounter.facility_id,
        actor_id=encounter.actor_id,
    )


# ======================================================================
# Dawa Check tab
# ======================================================================

def _dawa_tab(actor: ActorContext, selected_patient_id: str | None) -> None:
    st.title("Dawa Check — owned medicine reconciliation")
    store = PrivateMediaStore()
    record = _encounter_picker("dawa", "Open Dawa encounter")

    with st.expander("Create a Dawa encounter", expanded=record is None):
        identity = st.radio("Patient context", ["Registered patient", "Walk-in"],
                            horizontal=True, key="new_dawa_identity")
        walk_ref = st.text_input("Walk-in local reference", key="new_dawa_walkin_reference",
                                 disabled=identity != "Walk-in")
        create_disabled = identity == "Registered patient" and not selected_patient_id
        if st.button("Create Dawa encounter", type="primary", disabled=create_disabled):
            try:
                record = _create_ui_encounter(
                    actor, "dawa", selected_patient_id if identity == "Registered patient" else None,
                    walk_ref if identity == "Walk-in" else None,
                )
                st.rerun()
            except (ValueError, PermissionError, LookupError) as exc:
                st.error(str(exc))

    if not record:
        st.info("Create an encounter before attaching a prescription image.")
        return
    encounter = access.get_encounter(actor, record["encounter_id"])
    if selected_patient_id and encounter.patient_id and selected_patient_id != encounter.patient_id:
        st.warning("The sidebar patient differs from this active encounter. Its patient remains unchanged.")
        c1, c2 = st.columns(2)
        c1.button("Continue current Dawa encounter", disabled=True)
        if c2.button("Start a new Dawa encounter for selected patient"):
            _create_ui_encounter(actor, "dawa", selected_patient_id)
            st.rerun()

    st.info(
        f"Encounter **{encounter.encounter_id}** · "
        f"Patient **{encounter.patient_id or encounter.snapshot.patient.display_reference}** · "
        f"Facility **{encounter.facility_id}** · Actor **{encounter.actor_id}**"
    )

    if record["stage"] == "prepared":
        drafts = record["drafts"]
        upload_key = f"dawa_upload_{encounter.encounter_id}"
        confirmation_key = f"dawa_modality_{encounter.encounter_id}"
        upload = st.file_uploader(
            "Prescription / medicine-bag image (optional)", type=["jpg", "jpeg", "png"],
            key=upload_key, on_change=_remember_upload,
            args=("dawa", encounter.encounter_id, "pending_rx_upload", upload_key,
                  "rx_modality_confirmed", confirmation_key),
        )
        retained_upload = drafts.get("pending_rx_upload")
        if upload is None and retained_upload:
            st.caption(f"Retained for this encounter: {retained_upload['name']}")
        confirmed = st.checkbox("I confirm this image is a prescription or medicine-bag photograph",
                                value=drafts.get("rx_modality_confirmed", False),
                                key=confirmation_key,
                                on_change=_remember_widget,
                                args=("dawa", encounter.encounter_id,
                                      "rx_modality_confirmed", confirmation_key),
                                disabled=upload is None and not retained_upload)
        if st.button("Start Dawa workflow", type="primary"):
            try:
                artifact_id = _store_upload(store, actor, encounter.encounter_id, upload,
                                            "prescription_image", confirmed, retained_upload)
                result = dawa_graph.start_dawa_case(
                    encounter.encounter_id, actor, artifact_id,
                )
                stage, gate = _stage_from_result(result, "confirm")
                ui_state.update(st.session_state, "dawa", encounter.encounter_id,
                                stage=stage, gate=gate)
                ui_state.set_draft(st.session_state, "dawa", encounter.encounter_id,
                                   "rx_artifact_id", artifact_id)
                ui_state.set_draft(st.session_state, "dawa", encounter.encounter_id,
                                   "pending_rx_upload", None)
                st.rerun()
            except (MediaValidationError, PermissionError, ValueError) as exc:
                st.error(str(exc))
        return

    gate = record.get("gate") or {}
    report = gate.get("medication_review") or {}
    key = f"{encounter.encounter_id}:{report.get('input_hash', 'initial')}"

    def accept(result):
        next_gate = _latest_gate_payload(result)
        stage = ("confirm" if next_gate.get("gate") == "dawa_confirm"
                 else "review" if next_gate.get("gate") == "dawa_review" else "done")
        ui_state.update(st.session_state, "dawa", encounter.encounter_id,
                        stage=stage, gate=next_gate)
        ui_state.reconcile_gate_drafts(
            st.session_state, "dawa", encounter.encounter_id, next_gate,
        )
        st.rerun()

    if gate.get("review_validation_error"):
        st.error(gate["review_validation_error"])
    if record["stage"] == "confirm":
        if gate.get("degraded"):
            st.info(gate["degraded"])
        medicines = medication_editor(record["drafts"].get("medicines", gate.get("medicines") or []),
                                      key=key)
        context = patient_context_editor(record["drafts"].get("patient_context",
                                                               gate.get("patient") or {}), key=key)
        ui_state.set_draft(st.session_state, "dawa", encounter.encounter_id,
                           "medicines", medicines)
        ui_state.set_draft(st.session_state, "dawa", encounter.encounter_id,
                           "patient_context", context)
        if st.button("Confirm list and run review", type="primary"):
            accept(dawa_graph.resume_dawa_case(
                encounter.encounter_id, actor, action="confirm",
                medicines=medicines, patient=context,
            ))
    elif record["stage"] == "review":
        render_medication_review(report)
        _download_review(encounter.encounter_id, report, gate.get("patient"), "dawa")
        if st.button("Edit medicines or patient context"):
            accept(dawa_graph.resume_dawa_case(encounter.encounter_id, actor, action="modify"))
        for flag in report.get("flags", []):
            with st.expander(f"Record review: {flag.get('code')}"):
                note_key = f"{key}:{flag['id']}"
                draft_name = f"finding_note:{flag['id']}"
                note = st.text_area(
                    "Finding review note", value=record["drafts"].get(draft_name, ""),
                    key=note_key, on_change=_remember_widget,
                    args=("dawa", encounter.encounter_id, draft_name, note_key),
                )
                if st.button("Record note", key=note_key + ":submit"):
                    accept(dawa_graph.resume_dawa_case(
                        encounter.encounter_id, actor, action="review_flag",
                        flag_id=flag["id"], reason=note,
                    ))
        review_token = report.get("input_hash", "initial")
        ack_draft = f"review_acknowledged:{review_token}"
        ack_key = key + ":ack"
        acknowledged = st.checkbox(
            "I reviewed the findings and checks not assessed.",
            value=record["drafts"].get(ack_draft, False), key=ack_key,
            on_change=_remember_widget,
            args=("dawa", encounter.encounter_id, ack_draft, ack_key),
        )
        note_draft = f"review_plan:{review_token}"
        note_key = key + ":note"
        note = st.text_area(
            "Clinical review / referral plan",
            value=record["drafts"].get(note_draft, ""), key=note_key,
            on_change=_remember_widget,
            args=("dawa", encounter.encounter_id, note_draft, note_key),
        )
        st.caption("Completion records review; it does not authorize dispensing.")
        c1, c2 = st.columns(2)
        if c1.button("Complete reviewed check", disabled=bool(report.get("approval_blocked"))):
            accept(dawa_graph.resume_dawa_case(
                encounter.encounter_id, actor, action="finalize", disposition="reviewed",
                review_acknowledged=acknowledged, reason=note,
            ))
        if c2.button("Record referral for unresolved findings"):
            accept(dawa_graph.resume_dawa_case(
                encounter.encounter_id, actor, action="finalize", disposition="refer",
                review_acknowledged=acknowledged, reason=note,
            ))
    else:
        st.success("Dawa encounter completed.")
    st.divider()
    st.subheader("Authorized evidence and decision timeline")
    _render_timeline(encounter.encounter_id, actor)


# ======================================================================
# Consultation tab
# ======================================================================

def _consultation_tab(actor: ActorContext, selected_patient_id: str | None) -> None:
    st.title("SmartSehat — owned consultation")
    store = PrivateMediaStore()
    record = _encounter_picker("consultation", "Open consultation encounter")
    if st.button("Create consultation encounter for selected patient", type="primary",
                 disabled=selected_patient_id is None):
        try:
            _create_ui_encounter(actor, "consultation", selected_patient_id)
            st.rerun()
        except (PermissionError, ValueError, LookupError) as exc:
            st.error(str(exc))
    if not record:
        st.info("Select a patient and create the encounter before attaching clinical media.")
        return
    encounter = access.get_encounter(actor, record["encounter_id"])
    if selected_patient_id and selected_patient_id != encounter.patient_id:
        st.warning("The sidebar patient differs from this active encounter. The active patient is not rebound.")
        c1, c2 = st.columns(2)
        c1.button("Continue current encounter", disabled=True)
        if c2.button("Start new encounter for selected patient"):
            _create_ui_encounter(actor, "consultation", selected_patient_id)
            st.rerun()

    patient_ref = encounter.patient_id or encounter.snapshot.patient.display_reference
    st.info(
        f"Encounter **{encounter.encounter_id}** · Patient **{patient_ref}** · "
        f"Facility **{encounter.facility_id}** · Actor **{encounter.actor_id}**"
    )

    if record["stage"] == "prepared":
        drafts = record["drafts"]
        with st.expander("Attach encounter-owned inputs", expanded=True):
            c1, c2 = st.columns(2)
            audio_key = f"audio_{encounter.encounter_id}"
            image_key = f"xray_{encounter.encounter_id}"
            confirmation_key = f"xray_modality_{encounter.encounter_id}"
            audio = c1.file_uploader(
                "Patient audio (.wav)", type=["wav"], key=audio_key,
                on_change=_remember_upload,
                args=("consultation", encounter.encounter_id, "pending_audio", audio_key),
            )
            image = c2.file_uploader(
                "Clinical image proposed as chest X-ray (.jpg/.png)",
                type=["jpg", "jpeg", "png"], key=image_key,
                on_change=_remember_upload,
                args=("consultation", encounter.encounter_id, "pending_xray", image_key,
                      "xray_modality_confirmed", confirmation_key),
            )
            retained_audio = drafts.get("pending_audio")
            retained_image = drafts.get("pending_xray")
            if audio is None and retained_audio:
                c1.caption(f"Retained for this encounter: {retained_audio['name']}")
            if image is None and retained_image:
                c2.caption(f"Retained for this encounter: {retained_image['name']}")
            image_confirmed = st.checkbox(
                "I confirm the image is a chest X-ray for this encounter",
                value=drafts.get("xray_modality_confirmed", False),
                key=confirmation_key, disabled=image is None and not retained_image,
                on_change=_remember_widget,
                args=("consultation", encounter.encounter_id,
                      "xray_modality_confirmed", confirmation_key),
            )
        if st.button("Start consultation workflow", type="primary"):
            try:
                audio_id = _store_upload(store, actor, encounter.encounter_id, audio, "audio",
                                         retained=retained_audio)
                image_id = _store_upload(store, actor, encounter.encounter_id, image,
                                         "xray_image", image_confirmed, retained_image)
                result = start_case(encounter.encounter_id, actor,
                                    audio_artifact_id=audio_id, image_artifact_id=image_id)
                stage, gate = _stage_from_result(result, "transcript")
                ui_state.update(st.session_state, "consultation", encounter.encounter_id,
                                stage=stage, gate=gate)
                ui_state.set_draft(st.session_state, "consultation", encounter.encounter_id,
                                   "media", {"audio": audio_id, "xray": image_id})
                ui_state.set_draft(st.session_state, "consultation", encounter.encounter_id,
                                   "pending_audio", None)
                ui_state.set_draft(st.session_state, "consultation", encounter.encounter_id,
                                   "pending_xray", None)
                st.rerun()
            except (MediaValidationError, PermissionError, ValueError) as exc:
                st.error(str(exc))
        return

    gate = record.get("gate") or {}
    if record["stage"] != "done":
        _render_timeline(encounter.encounter_id, actor)

    def accept(result, reconcile_sources: set[str] | None = None):
        next_gate = _latest_gate_payload(result)
        stage = ("transcript" if next_gate.get("gate") == "transcript"
                 else "hitl" if next_gate else "done")
        ui_state.update(st.session_state, "consultation", encounter.encounter_id,
                        stage=stage, gate=next_gate)
        ui_state.reconcile_gate_drafts(
            st.session_state, "consultation", encounter.encounter_id, next_gate,
            reconcile_sources,
        )
        st.rerun()

    if record["stage"] == "transcript":
        st.warning("Complete the history, then confirm")
        transcript_key = f"transcript_{encounter.encounter_id}"
        edited = st.text_area(
            "Transcript — editable",
            value=record["drafts"].get("transcript", gate.get("transcript", "")),
            height=120, key=transcript_key, on_change=_remember_widget,
            args=("consultation", encounter.encounter_id, "transcript", transcript_key),
        )
        patient = encounter.snapshot.patient
        followups = _suggest_followups(edited, {"age": patient.age, "sex": patient.sex})
        answers = []
        for topic, question in followups:
            answer_key = f"fu_{encounter.encounter_id}_{topic}"
            draft_name = f"followup:{topic}"
            answer = st.text_input(
                f"🙋 {question}", value=record["drafts"].get(draft_name, ""),
                key=answer_key, on_change=_remember_widget,
                args=("consultation", encounter.encounter_id, draft_name, answer_key),
            )
            if answer.strip():
                answers.append(answer.strip())
        if st.button("Confirm transcript + answers"):
            full = edited + (("\n" + " ".join(answers)) if answers else "")
            accept(resume_case(encounter.encounter_id, actor, action="confirm", transcript=full))
    elif record["stage"] == "hitl":
        st.warning("Health worker gate — authenticated decision required")
        _hitl_card(encounter.encounter_id, gate)
        report = gate.get("medication_review") or {}
        key = f"{encounter.encounter_id}:{report.get('input_hash', 'initial')}"
        emergency = gate.get("triage_level") == "RED"
        if not emergency:
            with st.expander("Confirm or update patient context"):
                context = patient_context_editor(
                    record["drafts"].get("patient_context",
                                         gate.get("medication_context") or {}), key=key)
                ui_state.set_draft(st.session_state, "consultation", encounter.encounter_id,
                                   "patient_context", context)
                if st.button("Save context and recheck"):
                    accept(resume_case(encounter.encounter_id, actor, action="update_context",
                                       medication_context=context),
                           {"medication_context"})
            with st.expander("Edit the proposed medicine plan"):
                medicines = medication_editor(
                    record["drafts"].get("medicine_edits",
                                         gate.get("proposed_medicines") or []), key=key)
                ui_state.set_draft(st.session_state, "consultation", encounter.encounter_id,
                                   "medicine_edits", medicines)
                if st.button("Submit medicine plan and recheck"):
                    accept(resume_case(encounter.encounter_id, actor, action="modify",
                                       proposed_medicines=medicines),
                           {"proposed_medicines"})
            inputs_changed = _review_inputs_changed(gate, medicines, context)
            if inputs_changed:
                st.warning("Unsaved medicine or context changes must be rechecked before approval.")
            review_token = report.get("input_hash", "initial")
            ack_draft = f"review_acknowledged:{review_token}"
            ack_key = key + ":ack"
            acknowledged = st.checkbox(
                "I reviewed the medicine findings and checks not assessed.",
                value=record["drafts"].get(ack_draft, False), key=ack_key,
                on_change=_remember_widget,
                args=("consultation", encounter.encounter_id, ack_draft, ack_key),
            )
        else:
            acknowledged, inputs_changed = False, False
        review_token = report.get("input_hash", "initial")
        note_draft = f"review_note:{review_token}"
        reason_key = key + ":note"
        reason = st.text_area(
            "Review note / referral plan",
            value=record["drafts"].get(note_draft, ""), key=reason_key,
            on_change=_remember_widget,
            args=("consultation", encounter.encounter_id, note_draft, reason_key),
        )
        c1, c2 = st.columns(2)
        if c1.button("Confirm emergency referral" if emergency else "Approve reviewed plan",
                     disabled=not emergency and (bool(report.get("approval_blocked"))
                                                 or inputs_changed)):
            accept(resume_case(encounter.encounter_id, actor, action="approve", reason=reason,
                               medication_review_acknowledged=acknowledged))
        if c2.button("Reject plan and record referral reason"):
            accept(resume_case(encounter.encounter_id, actor, action="reject", reason=reason))
    else:
        completed = access.get_encounter(actor, encounter.encounter_id)
        st.success(f"Encounter complete · status {completed.status}")
        if gate.get("triage_level") == "RED" or any(
                json.loads(event["payload_json"] or "{}").get("outcome") == "emergency_referral"
                for event in access.get_timeline(actor, encounter.encounter_id)):
            packet = _emergency_packet(encounter.encounter_id, actor)
            st.error("Emergency referral packet")
            st.code(packet)
            st.markdown(f"**[Send via WhatsApp]({_whatsapp_link(packet)})**")
        else:
            report = _doctor_report(encounter.encounter_id, actor)
            with st.expander("Doctor review report", expanded=True):
                st.code(report)
                st.markdown(f"**[Send via WhatsApp]({_whatsapp_link(report)})**")
            reply_key = f"doc_reply_{encounter.encounter_id}"
            reply = st.text_area(
                "Doctor advice received (manual entry)",
                value=record["drafts"].get("doctor_advice", ""), key=reply_key,
                on_change=_remember_widget,
                args=("consultation", encounter.encounter_id, "doctor_advice", reply_key),
            )
            if st.button("Log doctor advice", key=f"btn_doc_{encounter.encounter_id}"):
                if reply.strip():
                    access.log_authorized_event(
                        actor, encounter.encounter_id, "DOCTOR_ADVICE",
                        {"advice": reply.strip(), "channel": "manual_entry",
                         "identity_verified": False}, provider="worker-entered-advice",
                    )
                    st.success("Advice recorded as an authenticated worker entry; doctor identity remains unverified.")
                else:
                    st.warning("Enter the advice first.")


def main() -> None:
    patient_id, actor = _sidebar()
    workflow = st.session_state.get("_workflow")
    if workflow == "District View":
        from smartsehat.dash import render as render_dash
        render_dash(actor)
    elif workflow == "Dawa Check":
        _dawa_tab(actor, patient_id)
    else:
        _consultation_tab(actor, patient_id)


if __name__ == "__main__":
    main()
