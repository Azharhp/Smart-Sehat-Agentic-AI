"""Streamlit worker journey and simulated doctor console."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
import base64
from pathlib import Path
from typing import get_args

import streamlit as st
import streamlit.components.v1 as components

from .clinical import Age, ROOT, config, danger_questions, respiratory_threshold
from .timeutils import local_today, care_timezone, display_time
from .measurements import completed_breath_count
from .store import Store, STAGES
from .questions import next_question, QUESTIONS
from . import ai
from .medicines import resources, catalogue, check_prescription, check_plan
from .dosing import Formulation, Route
from .exports import offline_capture, patient_card
from .guidance import guide, PHOTO_GUIDES
from .privacy import save_media, read_media
from .illustrations import illustration
from .languages import VOICE_LANGUAGES

breath_counter = components.declare_component("consultation_breath_counter", path=str(Path(__file__).parent / "components/breath"))
speech = components.declare_component("consultation_speech", path=str(Path(__file__).parent / "components/speech"))
quick_danger = components.declare_component("consultation_quick_danger", path=str(Path(__file__).parent / "components/danger"))


@st.cache_resource
def _store_resource(database_path: str, implementation_id: int) -> Store:
    # A reloaded Store class must not reuse an instance of the previous class.
    service = Store(database_path)
    from .scheduler import start
    start(service.path)
    return service


def store() -> Store:
    path = str(Path(os.environ.get("CONSULTATION_DB", str(ROOT / "consultation.db"))).resolve())
    return _store_resource(path, id(Store))


def act(callback, *args, **kwargs):
    try:
        result = callback(*args, **kwargs)
        return result if result is not None else True
    except (ValueError, TypeError) as exc:
        st.error(str(exc))
        return False


def _upload(file, cid: str, purpose: str) -> str:
    suffix = Path(file.name).suffix.lower() if getattr(file, "name", None) else ".wav"
    if suffix not in (".png", ".jpg", ".jpeg", ".pdf", ".wav", ".mp3", ".webm", ".m4a", ".ogg", ".flac"):
        suffix = ".bin"
    content = file.getvalue()
    path = ROOT / "assets/uploaded/consultation" / cid / (purpose + "-" + hashlib.sha256(content).hexdigest()[:12] + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(save_media(path, content))


def _open(cid: str) -> None:
    st.query_params["case"] = cid
    st.session_state["consultation_case"] = cid
    st.rerun()


def _return_to_worker(cid: str) -> None:
    """Widget callback: retain the encounter while returning to worker review."""
    st.query_params["case"] = cid
    st.session_state["consultation_case"] = cid
    st.session_state["workflow"] = "Consultation"
    st.session_state["_workflow"] = "Consultation"


def _values(cid: str) -> dict:
    return {f["key"]: f["value"] for f in store().facts(cid)}


def _persist_draft(cid: str, section: str, fields: dict[str, str]) -> None:
    data = store().draft(cid, section)
    data.update({name: st.session_state.get(widget) for name, widget in fields.items()})
    if section == "story":
        data["source_fact_id"] = next((f["id"] for f in store().facts(cid) if f["key"] == "transcript"), None)
    store().save_draft(cid, section, data)


def _table(rows: list[dict]) -> None:
    # Mixed ledger values must be rendered explicitly; Arrow cannot mix dicts,
    # booleans, numbers and strings in a single column without conversion.
    normalized = [{k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v) if v is not None else ""
                   for k, v in row.items()} for row in rows]
    st.dataframe(normalized, width="stretch", hide_index=True)


def _show_media(path: Path | str, key: str, width: int = 280) -> None:
    path = Path(path)
    if not path.is_file():
        st.caption("File unavailable; its audit reference is retained.")
        return
    suffix = Path(path.name.removesuffix(".enc")).suffix.lower()
    try:
        content = read_media(path)
        if suffix in (".wav", ".mp3", ".webm", ".m4a", ".ogg", ".flac"):
            mime = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".webm": "audio/webm", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".flac": "audio/flac"}[suffix]
            st.audio(content, format=mime)
        elif suffix == ".pdf":
            st.download_button("Download laboratory PDF", content, file_name=path.name.removesuffix(".enc"), mime="application/pdf", key=key)
        else:
            st.image(content, width=width)
    except Exception:
        st.warning("This attachment cannot be opened. Its audit reference remains available.")


def _orders_panel(cid: str, doctor_view: bool = False) -> None:
    orders = store().orders(cid)
    if not orders:
        return
    st.write("🧪 Doctor-requested tests and results")
    _table([{"Test": o["label"], "Where": o["location"], "Status": o["status"],
             "Doctor reviewed result": o["doctor_reviewed"], "Result / reason": o.get("value") or o.get("reason", "Awaiting worker response")} for o in orders])
    if doctor_view:
        for order in orders:
            if order["status"] != "pending":
                continue
            with st.expander("Cancel pending order: " + order["label"]):
                reason = st.text_input("Doctor's cancellation reason", key=f"cancel_reason_{order['order_id']}")
                if st.button("Cancel this test order", key=f"cancel_order_{order['order_id']}", disabled=not reason.strip()):
                    if act(store().cancel_order, cid, order["order_id"], reason):
                        st.rerun()


def _care_panel(cid: str, doctor_view: bool = False) -> None:
    state = store().care_status(cid)
    if state["referrals"]:
        st.write("🏥 Referral follow-through · reported status")
        _table([{"Facility": r["facility"]["name"], "Status": r["status"], "Notes": r.get("notes", ""), "Updated": r["updated"]} for r in state["referrals"]])
        for referral in state["referrals"]:
            rid = referral["referral_id"]
            if doctor_view:
                with st.expander("Doctor referral actions: " + referral["facility"]["name"]):
                    reason = st.text_input("Reason for referral change", key=f"doctor_referral_reason_{rid}")
                    if referral["status"] in ("requested", "transport arranged", "departed", "unable"):
                        if st.button("Cancel this referral", key=f"cancel_referral_{rid}", disabled=not reason.strip()):
                            if act(store().cancel_referral, cid, rid, reason):
                                st.rerun()
                    if referral["status"] in ("cancelled", "unable"):
                        if st.button("Request referral again", key=f"reopen_referral_{rid}", disabled=not reason.strip()):
                            if act(store().reopen_referral, cid, rid, reason):
                                st.rerun()
            elif referral["status"] != "cancelled":
                with st.expander("Update referral progress: " + referral["facility"]["name"]):
                    choice = st.selectbox("Reported referral status", ["Choose", "transport arranged", "departed", "arrived", "admitted", "unable"], key=f"referral_status_{rid}")
                    notes = st.text_input("Transport, arrival or barrier details", key=f"referral_notes_{rid}")
                    correction = st.checkbox("This corrects a previously recorded referral status", key=f"referral_correction_{rid}")
                    if st.button("Save referral update", key=f"save_referral_{rid}", disabled=choice == "Choose"):
                        if act(store().referral_status, cid, rid, choice, notes, correction=correction):
                            st.rerun()
    if state["calls"]:
        st.write("📞 Requested contact · reported status")
        st.caption("Contact uses the agreed care-team channel. Call delivery and clinician identity are simulated.")
        for call in state["calls"]:
            call_id = call["call_id"]
            st.caption("Call " + call_id + " · " + call["status"] + (" · " + call["notes"] if call.get("notes") else ""))
            if call["status"] in ("completed", "cancelled"):
                continue
            if doctor_view:
                reason = st.text_input("Reason to cancel call request", key=f"doctor_call_reason_{call_id}")
                if st.button("Cancel call request", key=f"cancel_call_{call_id}", disabled=not reason.strip()):
                    if act(store().cancel_call, cid, call_id, reason):
                        st.rerun()
            else:
                choice = st.selectbox("Reported call status", ["Choose", "planned", "completed", "unable"], key=f"call_status_{call_id}")
                notes = st.text_input("Contact details or reason unavailable", key=f"call_notes_{call_id}")
                if st.button("Save call update", key=f"save_call_{call_id}", disabled=choice == "Choose"):
                    if act(store().call_status, cid, call_id, choice, notes):
                        st.rerun()
    if state["requests"]:
        pending = [r for r in state["requests"] if r["status"] == "open"]
        with st.expander("Questions and barriers for the doctor", expanded=bool(pending)):
            _table([{"Request": r["text"], "Type": r["kind"], "Status": r["status"]} for r in state["requests"]])
    if state["guidance"]:
        with st.expander("Recorded counselling confirmations"):
            _table([{"Doctor response": g["reply_id"], "Explained through": g["channel"], "Patient reported understanding": "Yes" if g["understood"] is True else "No" if g["understood"] is False else "Unknown", "Notes": g["notes"]} for g in state["guidance"]])


def _counselling(cid: str) -> None:
    state = store().care_status(cid)
    reply = state["latest_reply"]
    if not reply:
        return
    rid = reply["reply_id"]
    prior = next((g for g in state["guidance"] if g["reply_id"] == rid), None)
    with st.expander("Explain guidance and check understanding", expanded=prior is None):
        st.caption("Record the patient's/caregiver's response after explaining this exact doctor reply.")
        st.caption("Use ‘Report worsening / new danger now’ above for a new urgent change.")
        explained = st.checkbox("Worker explained this doctor response", key=f"guidance_explained_{cid}_{rid}")
        understood = st.radio("Patient/caregiver understanding", ["Choose", "Reports understanding", "Needs clarification", "Unknown"], key=f"guidance_understood_{cid}_{rid}", horizontal=True)
        channel = st.selectbox("How guidance was explained", ["spoken read-back", "recorded audio", "printed card"], key=f"guidance_channel_{cid}_{rid}")
        notes_key = f"guidance_notes_{cid}_{rid}"
        notes = st.text_area("Question or reason for clarification", value=store().draft(cid, "counselling_" + rid).get("notes", ""), key=notes_key,
            on_change=_persist_draft, args=(cid, "counselling_" + rid, {"notes": notes_key}))
        if st.button("Save counselling confirmation", key=f"save_counselling_{cid}_{rid}", disabled=not explained or understood == "Choose"):
            answer = True if understood == "Reports understanding" else False if understood == "Needs clarification" else None
            if act(store().guidance_receipt, cid, rid, answer, channel, notes, worker_confirmed=explained):
                st.rerun()


def _persist_prescription_product(cid: str) -> None:
    draft = store().draft(cid, "doctor_reply")
    product = {field: st.session_state.get(f"rx_product_{field}_{cid}", default)
               for field, default in (("formulation", "unknown"), ("strength", ""), ("route", "unknown"))}
    store().save_draft(cid, "doctor_reply", {**draft, "prescription_product": product})


def _persist_prescription_regimen(cid: str) -> None:
    draft = store().draft(cid, "doctor_reply")
    regimen = {field: st.session_state.get(f"rx_regimen_{field}_{cid}") or None for field in ("doses_per_day", "duration_days")}
    regimen.update({"indication": st.session_state.get(f"rx_regimen_indication_{cid}", ""), "as_needed": st.session_state.get(f"rx_regimen_as_needed_{cid}", False)})
    store().save_draft(cid, "doctor_reply", {**draft, "prescription_regimen": regimen})


def _persist_fever_context(cid: str, source_id: str | None, keys: dict) -> None:
    data = {field: st.session_state.get(key, "") for field, key in keys.items()}
    if hasattr(data["observed_on"], "isoformat"):
        data["observed_on"] = data["observed_on"].isoformat()
    store().save_draft(cid, "fever_context", {**data, "source_fact_id": source_id})


def _fever_context(cid: str) -> None:
    from datetime import date
    values = _values(cid)
    if values.get("fever") is not True and "fever_context" not in values:
        return
    current = next((f for f in store().facts(cid) if f["key"] == "fever_context"), None)
    source_id = current["id"] if current else None
    draft = store().draft(cid, "fever_context")
    saved = (draft if draft.get("source_fact_id") == source_id else {}) or (current["value"] if current else {}) or {}
    keys = {field: f"fever_context_{field}_{cid}_{source_id or 'new'}" for field in ("country", "province", "district", "observed_on", "unknown_reason", "notes")}
    defaults = {"country": "Pakistan", "province": "", "district": "", "observed_on": local_today().isoformat(), "unknown_reason": "", "notes": ""}
    for field, key in keys.items():
        if key not in st.session_state:
            value = saved.get(field, defaults[field])
            st.session_state[key] = date.fromisoformat(value) if field == "observed_on" and value else value
    callback = {"on_change": _persist_fever_context, "args": (cid, source_id, keys)}
    with st.expander("District and season context / ضلع اور موسم"):
        st.caption("Confirm where and when this illness occurred. Do not infer a district from the village. This is context for the next packet; use the normal review/read-back/send steps to share an updated packet.")
        st.text_input("Illness country", key=keys["country"], **callback)
        st.text_input("Illness province / region", key=keys["province"], **callback)
        st.text_input("Illness district", key=keys["district"], **callback)
        st.date_input("Illness location reference date", max_value=local_today(), key=keys["observed_on"], **callback)
        st.text_input("Reason location is unknown, if applicable", key=keys["unknown_reason"], **callback)
        st.text_area("Reported travel, exposure or local context (optional)", key=keys["notes"], **callback)
        if st.button("Confirm context for next packet", key=f"confirm_fever_context_{cid}"):
            data = {field: st.session_state[key] for field, key in keys.items()}
            if act(store().confirm_fever_context, cid, data, source_id):
                st.rerun()
        st.caption("Local disease context is unavailable until a reviewed district/month entry matches. It does not set a diagnosis or lower urgency.")


def _banner(cid: str) -> None:
    urgency = store().output(cid, "urgency")
    if urgency:
        text = f"{urgency['level']} · {config()['routing']['targets'][urgency['level']]}"
        {"RED": st.error, "AMBER": st.warning, "GREEN": st.success}[urgency["level"]](text)
        if urgency["missing_critical"]:
            st.caption("Missing critical information: " + ", ".join(urgency["missing_critical"]))
        diff = urgency.get("diff", {})
        if diff.get("before") and diff["before"] != diff.get("after"):
            st.caption(f"Urgency recalculated: {diff['before']} → {diff['after']}")


def _intake() -> None:
    st.subheader("Identify and consent / شناخت اور رضامندی")
    st.caption("Research prototype · Use fictional patient details only.")
    left, right = st.columns(2)
    name = left.text_input("Fictional name or ID", value="")
    village = right.text_input("Village", value="Synthetic village")
    age_value = left.number_input("Age value", min_value=0.0, value=14.0, step=1.0)
    age_unit = right.selectbox("Age unit", ["months", "years", "weeks", "days"])
    age = Age.from_input(age_value, age_unit)
    st.info(age.display + " · " + age.pathway.replace("_", " "))
    accepted = True
    dob_enabled = st.checkbox("Use date of birth instead")
    if dob_enabled:
        from datetime import date
        reference = local_today()
        dob = st.date_input("Date of birth", min_value=date(1900, 1, 1), max_value=reference)
        dob_age = Age.from_dob(dob, reference)
        if abs(age.days - dob_age.days) > config()["age"]["dob_mismatch_days"]:
            st.warning("Typed age and date of birth differ. Date of birth will be used; confirm it.")
            accepted = st.checkbox("I confirmed the date of birth and calculated age", key=f"dob_confirm_{dob}_{reference}_{age_value}_{age_unit}")
        age = dob_age
        st.write(age.display)
        st.caption(f"Birth date {age.date_of_birth} · Age calculated on {age.as_of} · {care_timezone()}")
    if age.warning:
        st.warning(age.warning)
        age_checked = st.checkbox("I checked the age and unit")
        accepted = accepted and age_checked
    sex = left.selectbox("Sex", ["Male", "Female", "Other / not recorded"])
    if sex == "Male":
        st.session_state["intake_pregnant"] = False
        pregnant = False
    else:
        pregnant = right.checkbox("Pregnant", key="intake_pregnant")
    language = left.selectbox("Language", ["Urdu", "English"])
    consent = st.checkbox("Consent recorded to share this fictional case with the simulated doctor")
    if st.button("Create consultation", type="primary", disabled=not accepted):
        cid = store().create(name, age, sex, pregnant, village, language, consent)
        if age.warning:
            age_fact = next(f for f in store().facts(cid) if f["key"] == "age")
            store().record(cid, "AGE_CONFIRMED", {"fact_id": age_fact["id"]})
        _open(cid)


def _danger_choice(cid: str, key: str, selection: str) -> None:
    allowed = {q["key"] for q in danger_questions(store().age(cid), store().case(cid)["data"]["pregnant"])}
    if key not in allowed:
        st.error("The danger screen changed. Reload its current questions.")
        return
    value = True if selection == "Yes" else False if selection == "No" else None
    old = next((f for f in store().facts(cid) if f["key"] == key), None)
    if not old or old["value"] != value:
        if not act(store().add_fact, cid, key, value, reason_missing="Worker unable to assess" if value is None else None):
            return
    st.session_state[f"ds_{cid}_{key}"] = selection


def _prompt_audio(text: str, language: str, key: str) -> None:
    from .speech_audio import synthesize, recorded_prompt, save_prompt_recording
    path = recorded_prompt(text, language)
    mode = "Saved prompt recording. Listen against the wording; independent native-speaker/clinical validation pending."
    if path is None:
        path, mode = synthesize(text, language)
    if path is None and st.button("Prepare recorded prompt", key="prepare_" + key):
        path, mode = synthesize(text, language, live=True)
    if path:
        _show_media(path, key="prompt_media_" + key)
    st.caption(mode)
    recording = st.file_uploader("Or supply a recording of this exact prompt", type=["wav", "mp3", "webm", "m4a", "ogg"], key="prompt_upload_" + key)
    if recording:
        st.audio(recording)
        digest = hashlib.sha256(recording.getvalue()).hexdigest()[:16]
        confirmed = st.checkbox("I listened and checked this exact prompt in " + language, key="prompt_checked_" + key + digest)
        if st.button("Keep this prompt recording", disabled=not confirmed, key="prompt_keep_" + key):
            if act(save_prompt_recording, text, language, recording.getvalue(), Path(recording.name).suffix.lower(), confirmed):
                st.rerun()
    speech(language="ur-PK" if language == "Urdu" else "en-PK", readback=text, read_only=True, case_id=key, key=key, default=None)


def _readback_audio(cid: str, packet: dict, language: str) -> None:
    from .speech_audio import offline_readback, synthesize, text_identity
    from .privacy import read_media
    with st.expander("Recorded complete read-back / محفوظ خلاصہ"):
        section = f"readback_audio:{packet['version']}:{language}"
        summary = packet["readbacks"][language]["text"]
        draft = store().draft(cid, section)
        candidate = draft.get("path")
        recorded = st.audio_input("Record the complete read-back in the selected language", key=f"readback_record_{cid}_{packet['version']}_{language}")
        uploaded = st.file_uploader("Or upload the complete read-back recording", type=["wav", "mp3", "webm", "m4a", "ogg"], key=f"readback_upload_{cid}_{packet['version']}_{language}")
        if recorded or uploaded:
            candidate = _upload(recorded or uploaded, cid, "readback")
            store().save_draft(cid, section, {"path": candidate, "text_hash": text_identity(summary, language), "origin": "human-recording"})
        if st.button("Generate audio from this exact read-back", key=f"readback_generate_{cid}_{packet['version']}_{language}"):
            generated, mode = synthesize(summary, language, live=True)
            st.info(mode)
            if generated:
                target = ROOT / "assets/uploaded/consultation" / cid / ("readback-" + generated.name.removesuffix(".enc"))
                target.parent.mkdir(parents=True, exist_ok=True)
                candidate = str(save_media(target, read_media(generated)))
                store().save_draft(cid, section, {"path": candidate, "text_hash": text_identity(summary, language), "origin": "generated-unreviewed"})
        if candidate:
            _show_media(candidate, key=f"readback_preview_{cid}_{packet['version']}_{language}")
            identity = hashlib.sha256((candidate + summary).encode()).hexdigest()[:16]
            confirmed = st.checkbox("I listened and confirm this recording matches the entire current read-back", key=f"readback_audio_ok_{cid}_{identity}")
            if st.button("Keep confirmed read-back recording", disabled=not confirmed, key=f"keep_readback_audio_{cid}_{packet['version']}_{language}"):
                if act(store().attach_readback_audio, cid, packet["version"], language, candidate, confirmed):
                    st.success("Recording saved for this exact packet and language.")
        bound = store().readback_audio(cid, packet, language)
        audio, mime = None, "audio/wav"
        if bound and Path(bound["path"]).is_file():
            try:
                audio = read_media(bound["path"])
                suffix = Path(bound["path"].removesuffix(".enc")).suffix
                mime = {".mp3": "audio/mpeg", ".webm": "audio/webm", ".m4a": "audio/mp4", ".ogg": "audio/ogg"}.get(suffix, mime)
            except (OSError, ValueError):
                st.warning("Saved recording is unavailable; read the complete text aloud.")
        st.download_button("Download complete offline read-back", offline_readback(store().case(cid)["data"]["name"], packet, language, audio, mime),
            file_name=f"{cid}-readback-v{packet['version']}-{language}.html", mime="text/html", key=f"offline_readback_{cid}_{packet['version']}_{language}")


def _danger_observation_panel(cid: str, suffix: str) -> None:
    events = store().events(cid)
    captures = [event for event in events if event["type"] == "QUICK_DANGER_CAPTURE"]
    if not captures:
        return
    with st.expander("Observer timing report / مشاہداتی وقت کی رپورٹ"):
        st.caption("Record an actual separate person's stopwatch observation on the target phone. Automated clicks, prefilled answers and browser timers do not establish independent usability. Saving records a reported observation; identity and observation remain unverified.")
        by_id = {event["seq"]: event for event in captures}
        selected = st.selectbox("Observed screen attempt", list(by_id), format_func=lambda seq: by_id[seq]["ts"] + " · " + by_id[seq]["data"]["outcome"], key=f"observer_attempt_{cid}_{suffix}")
        attempt = by_id[selected]["data"]
        st.caption(f"Browser-reported {attempt['elapsed_seconds']:.1f}s · {attempt['previously_documented_count']} previously documented answers · {attempt['unknown_count']} unknown answers")
        observer = st.text_input("Observer name / identifier", key=f"observer_name_{cid}_{suffix}")
        participant = st.text_input("Participant code (no patient name)", key=f"observer_participant_{cid}_{suffix}")
        device = st.text_input("Actual phone / computer used", key=f"observer_device_{cid}_{suffix}")
        browser = st.text_input("Browser and version", key=f"observer_browser_{cid}_{suffix}")
        procedure = st.selectbox("Attempt type", ["first use", "trained repeat"], key=f"observer_procedure_{cid}_{suffix}")
        seconds = st.text_input("Observer stopwatch seconds (enter measured value)", key=f"observer_seconds_{cid}_{suffix}")
        expected = st.selectbox("Expected result in the observed fictional task", ["screen documented", "positive danger reported"], key=f"observer_expected_{cid}_{suffix}")
        understood = st.checkbox("Observer reports that the participant understood every presented question", key=f"observer_understood_{cid}_{suffix}")
        notes = st.text_area("Observed difficulties, audio issues or other findings", key=f"observer_notes_{cid}_{suffix}")
        confirmed = st.checkbox("A separate person actually observed and timed this attempt; this is not an automated or invented result", key=f"observer_confirmed_{cid}_{suffix}")
        if st.button("Record this reported observation", disabled=not confirmed or not seconds.strip(), key=f"observer_save_{cid}_{suffix}"):
            try:
                report = {"capture_event": selected, "observer": observer, "participant_code": participant, "device": device, "browser": browser,
                    "procedure": procedure, "observed_seconds": float(seconds), "expected_outcome": expected, "wording_understood": understood, "notes": notes}
                if act(store().report_danger_observation, cid, report, confirmed):
                    st.success("Reported observation recorded for the exact content version. It does not create clinical approval or verified field validation.")
            except ValueError as exc:
                st.error(str(exc))
        reports = [{"event_id": event["seq"], "ts": event["ts"], **event["data"]} for event in events if event["type"] == "DANGER_OBSERVATION_REPORTED"]
        if reports:
            _table(reports)
            st.download_button("Download reported danger-screen observations", json.dumps(reports, ensure_ascii=False, indent=2), file_name=cid + "-danger-observations.json", mime="application/json", key=f"observer_export_{cid}_{suffix}")


def _danger(cid: str) -> None:
    st.subheader("Danger signs first / پہلے خطرے کی علامات")
    st.caption("Answer each item. Any YES interrupts immediately; do not wait to finish the screen.")
    _proposals(cid)
    values = _values(cid)
    prompt_language = store().case(cid)["data"]["language"]
    questions = danger_questions(store().age(cid), store().case(cid)["data"]["pregnant"])
    screen = store().quick_screen_snapshot(cid)
    scope = screen["scope"]
    from .danger_content import inline_prompt
    with st.expander("Quick danger screen / فوری خطرے کی جانچ", expanded=True):
        st.caption("Answer one question at a time, or show all. Any Yes is reported immediately. Prepared recordings are included in this screen; generated pronunciation and clinical review remain pending.")
        payload = [{**q, "image": "data:image/svg+xml;base64," + base64.b64encode(illustration(q["key"], q["en"]).encode()).decode(),
                    "spoken": q["ur"] if screen["language"] == "Urdu" else q["en"],
                    "audio": inline_prompt(q["ur"] if screen["language"] == "Urdu" else q["en"], screen["language"])} for q in screen["questions"]]
        response = quick_danger(case_id=cid, scope=scope, questions=payload,
            answers=screen["answers"], language="ur-PK" if screen["language"] == "Urdu" else "en-PK", key=f"quick_danger_{cid}", default=None)
        if response and response.get("scope") == scope:
            changed = act(store().record_quick_screen, cid, response)
            if changed:
                for key, value in response["answers"].items():
                    st.session_state[f"ds_{cid}_{key}"] = "Yes" if value is True else "No" if value is False else "Unknown"
                st.rerun()
        captures = [event for event in store().events(cid) if event["type"] == "QUICK_DANGER_CAPTURE"]
        if captures:
            last = captures[-1]["data"]
            st.caption(f"Last browser-reported interaction: {last['elapsed_seconds']:.1f}s · {last['outcome']}. This is not independently observed field validation.")
    _danger_observation_panel(cid, "screen")
    completed = sum(q["key"] in values for q in questions)
    st.progress(completed / max(1, len(questions)), text=f"Danger screen: {completed}/{len(questions)} recorded; unknown answers remain visible.")
    for q in questions:
        with st.container(border=True):
            icon, instruction, instruction_ur = guide(q["key"])
            st.markdown("### " + icon + " " + q["en"])
            st.image(illustration(q["key"], q["en"]), width=150)
            st.write(q["ur"])
            st.caption(instruction + " / " + instruction_ur)
            with st.expander("🔊 Hear this danger-sign question", expanded=False):
                _prompt_audio(q["ur"] if prompt_language == "Urdu" else q["en"], prompt_language, f"danger_audio_{cid}_{q['key']}")
            yes_col, no_col, unknown_col = st.columns(3)
            for column, label, selection in ((yes_col, "Yes / ہاں", "Yes"), (no_col, "No / نہیں", "No"), (unknown_col, "Unknown / نامعلوم", "Unknown")):
                column.button(label, key=f"danger_fast_{cid}_{q['key']}_{selection}", on_click=_danger_choice, args=(cid, q["key"], selection))
            existing = values.get(q["key"])
            radio_key = f"ds_{cid}_{q['key']}"
            radio_options = {"key": radio_key}
            if radio_key not in st.session_state:
                radio_options["index"] = 3 if existing is None and q["key"] in values else 0 if existing is None else (2 if existing else 1)
            answer = st.radio("Answer", ["Not answered", "No", "Yes", "Unknown"], horizontal=True, label_visibility="collapsed", **radio_options)
            desired = True if answer == "Yes" else False if answer == "No" else None
            if answer != "Not answered" and (q["key"] not in values or desired != existing):
                store().add_fact(cid, q["key"], desired, reason_missing="Worker unable to assess" if desired is None else None)
                st.rerun()
    if st.button("Continue to story", disabled=not store().danger_complete(cid), type="primary"):
        store().stage(cid, "STORY")
        st.rerun()


def _emergency(cid: str) -> None:
    from .danger_content import EMERGENCY_WORDING
    st.error("EMERGENCY LANE / ہنگامی راستہ — do not delay referral to complete the history")
    st.write(EMERGENCY_WORDING["English"])
    st.write(EMERGENCY_WORDING["Urdu"])
    st.caption("Prototype first-response wording: pending clinical review.")
    if not store().case(cid)["data"]["consent"]:
        consent = st.checkbox("Consent recorded to share the urgent fictional case", key=f"emergency_consent_{cid}")
        if st.button("Record urgent sharing consent", disabled=not consent, key=f"emergency_consent_save_{cid}"):
            store().consent(cid, True)
            st.rerun()
    facilities = sorted([f for f in resources()["facilities"] if "oxygen" in f["capabilities"]], key=lambda f: f["distance_km"])
    selected = st.selectbox("Oxygen-capable facility (synthetic distances)", facilities, format_func=lambda f: f"{f['name']} · {f['distance_km']} km", key=f"er_fac_{cid}")
    st.write("Transport: " + selected["transport"])
    if st.button("Create referral and simulated pre-alert now", type="primary", key=f"er_send_{cid}"):
        if act(store().emergency_referral, cid, selected["id"]):
            st.success("Referral recorded. Doctor/facility delivery is simulated; no message was sent.")
    facts = store().facts(cid)
    values = _values(cid)
    data = store().case(cid)["data"]
    danger = next((r["text"] for r in store().output(cid, "urgency")["fired"] if r["level"] == "RED"), "RED review required")
    compact = f"SYNTHETIC RED | {store().age(cid).display} | {data['sex']} | RR {values.get('resp_rate', 'unknown')} | SpO2 {values.get('spo2', 'unknown')} | {danger} | {data['village']}"
    st.code(compact, language=None)
    st.download_button("Download emergency pre-alert", compact, file_name=f"{cid}-emergency.txt", key=f"er_download_{cid}")
    _care_panel(cid)
    _delivery_panel(cid, "emergency")
    _danger_observation_panel(cid, "emergency")
    if st.button("Continue collection while waiting", key=f"er_continue_{cid}"):
        if store().danger_complete(cid):
            store().stage(cid, "STORY")
            st.rerun()
        else:
            store().stage(cid, "DANGER_SCREEN") if store().danger_complete(cid) else None
            st.session_state[f"complete_danger_{cid}"] = True
    if st.session_state.get(f"complete_danger_{cid}"):
        _danger(cid)
    if store().case(cid)["sent"] and any(not t["done"] for t in store().tasks(cid)):
        st.write("Requested observations while arranging urgent care")
        _tasks(cid)


def _story(cid: str) -> None:
    from .languages import EXPERIMENTAL_STT
    st.subheader("Patient story / مریض کی بات")
    live = st.toggle("Use live AI extraction (offline fallback remains available)", key=f"live_extract_{cid}")
    default_language = store().case(cid)["data"]["language"]
    text_key, language_key = f"transcript_{cid}", f"voice_language_{cid}"
    saved_voice = store().draft(cid, "story").get("voice_language", default_language)
    if saved_voice not in VOICE_LANGUAGES:
        saved_voice = default_language
    active_key = f"story_active_language_{cid}"

    def language_changed():
        data = store().draft(cid, "story")
        previous = st.session_state.get(active_key, saved_voice)
        texts = dict(data.get("transcripts_by_language") or {})
        texts[previous] = st.session_state.get(text_key, data.get("transcript", ""))
        chosen = st.session_state[language_key]
        current = texts.get(chosen, "")
        store().save_draft(cid, "story", {**data, "voice_language": chosen, "transcript": current,
            "transcript_language": chosen, "transcripts_by_language": texts})
        st.session_state[text_key] = current
        st.session_state[active_key] = chosen
        st.session_state.pop(f"last_spoken_{cid}", None)

    voice_language = st.selectbox("Language spoken in the recording", list(VOICE_LANGUAGES), index=list(VOICE_LANGUAGES).index(saved_voice), key=language_key, on_change=language_changed)
    st.session_state[active_key] = voice_language
    voice_spec = VOICE_LANGUAGES[voice_language]
    st.caption(voice_spec["status"])
    regional = not bool(voice_spec["stt"])
    interpreter = ""
    experimental = False
    if regional:
        interpreter_key = f"story_interpreter_{cid}_{voice_language}"
        data = store().draft(cid, "story")

        def interpreter_changed():
            data = store().draft(cid, "story")
            store().save_draft(cid, "story", {**data, "interpreters": {**(data.get("interpreters") or {}),
                voice_language: st.session_state.get(interpreter_key, "")}})

        interpreter = st.text_input("Interpreter or bilingual worker name / identifier", value=(data.get("interpreters") or {}).get(voice_language, ""), key=interpreter_key,
            on_change=interpreter_changed)
        if voice_language in EXPERIMENTAL_STT:
            experimental = st.toggle("Try experimental " + voice_language + " transcription", key=f"experimental_stt_{cid}_{voice_language}")
            if experimental:
                st.warning("Experimental draft only. A named bilingual reviewer must check the recording and editable transcript. Language, dialect and script accuracy are not validated.")
        st.caption("Retain the original recording and enter an interpreter-assisted transcript. Automated speech accuracy is not claimed for this language.")
    spoken = speech(language=voice_spec["browser"], case_id=cid, key=f"speech_{cid}_{voice_language}", default=None) if voice_spec["stt"] else None
    draft = store().draft(cid, "story")
    confirmed_transcript = next((f for f in store().facts(cid) if f["key"] == "transcript"), None)
    confirmed_language = _values(cid).get("transcript_language", default_language)
    if confirmed_transcript and confirmed_language == voice_language and st.session_state.get(f"last_transcript_fact_{cid}") != confirmed_transcript["id"]:
        st.session_state[text_key] = draft["transcript"] if "transcript" in draft and draft.get("source_fact_id") == confirmed_transcript["id"] else confirmed_transcript["value"]
        st.session_state[f"last_transcript_fact_{cid}"] = confirmed_transcript["id"]
    proposal = (draft.get("transcription_proposals") or {}).get(voice_language)
    if proposal and st.session_state.get(f"adopted_transcript_{cid}_{voice_language}") != proposal["id"]:
        st.session_state[text_key] = proposal["text"]
        st.session_state[f"adopted_transcript_{cid}_{voice_language}"] = proposal["id"]
    if spoken and spoken != st.session_state.get(f"last_spoken_{cid}"):
        st.session_state[text_key] = spoken
        st.session_state[f"last_spoken_{cid}"] = spoken

    def transcript_changed():
        data = store().draft(cid, "story")
        text = st.session_state.get(text_key, "")
        texts = {**(data.get("transcripts_by_language") or {}), voice_language: text}
        store().save_draft(cid, "story", {**data, "transcript": text, "transcript_language": voice_language,
            "source_fact_id": confirmed_transcript["id"] if confirmed_transcript else None, "transcripts_by_language": texts})

    initial = draft.get("transcript", "") if draft.get("transcript_language", saved_voice) == voice_language else (draft.get("transcripts_by_language") or {}).get(voice_language, "")
    transcript_options = {"height": 130, "key": text_key, "on_change": transcript_changed}
    if text_key not in st.session_state:
        transcript_options["value"] = spoken or initial or (_values(cid).get("transcript", "") if confirmed_language == voice_language else "")
    transcript = st.text_area("Editable transcript / مریض کی بات", **transcript_options)
    if proposal:
        st.caption("AI transcript draft · " + proposal["provider"] + " · retained recording available for review")
    if (draft.get("transcription_status") or {}).get(voice_language):
        st.caption((draft.get("transcription_status") or {})[voice_language])
    retained = (draft.get("recordings") or {}).get(voice_language) or proposal
    if retained and retained.get("path"):
        with st.expander("Listen to the retained original recording"):
            _show_media(retained["path"], key=f"story_original_{cid}_{voice_language}")
            if retained.get("quality"):
                st.json(retained["quality"])
    recorded = st.audio_input("Record patient audio (optional)", key=f"audio_{cid}_{voice_language}")
    with st.expander("Use an existing recording"):
        uploaded = st.file_uploader("Patient story recording", type=["wav", "mp3", "m4a", "ogg", "webm", "flac"], key=f"story_upload_{cid}_{voice_language}")
    recorded = uploaded if uploaded is not None else recorded
    if recorded:
        st.audio(recorded)
        from .voice import inspect_audio
        quality = inspect_audio(recorded.getvalue())
        st.caption("Recording: " + str(quality["bytes"]) + " bytes" + (f" · {quality['duration_seconds']:.1f}s" if "duration_seconds" in quality else "") + " · signal checks do not establish speech accuracy")
        for warning in quality["warnings"]:
            st.warning(warning)
        code = voice_spec["stt"] or (EXPERIMENTAL_STT.get(voice_language) if experimental else None)
        oversized = quality["bytes"] > 20 * 1024 * 1024
        if oversized:
            st.error("Use a recording smaller than 20 MB.")
        if st.button("Keep audio and transcribe" if code else "Keep original audio", key=f"transcribe_{cid}", disabled=oversized or experimental and len(interpreter.strip()) < 2):
            path = _upload(recorded, cid, "story")
            recording = act(store().attach_story_recording, cid, path, voice_language, experimental=experimental, reviewer=interpreter, automatic=bool(code))
            if not recording:
                return
            try:
                text, provider = ai.transcribe(path, language=code, experimental=experimental) if code else (None, "Recording retained. Enter an interpreter-assisted transcript; automated transcription is not enabled for this language.")
            except Exception:
                text, provider = None, "Speech service unavailable. Audio kept; check or enter the typed transcript against the new recording."
            if act(store().save_transcription_draft, cid, voice_language, recording, text, provider, experimental=experimental, reviewer=interpreter) is False:
                return
            st.rerun()
    needs_review = True
    st.caption("Check negatives, symptom duration, numbers and units, and medicine names against the recording or the patient's words. Correct [unclear] spans with the patient/interpreter; leave unestablished facts unknown.")
    review_key = hashlib.sha256(json.dumps([transcript, voice_language, interpreter, (proposal or {}).get("id"), (retained or {}).get("recording_sha256"), (retained or {}).get("generation", 0)], ensure_ascii=False).encode()).hexdigest()[:16]
    reviewed = st.checkbox("I checked the entire transcript against the recording or the patient's words", key=f"story_checked_{cid}_{review_key}") if needs_review and transcript.strip() else not needs_review
    if st.button("Extract proposed facts", type="primary", key=f"extract_{cid}", disabled=not transcript.strip() or regional and len(interpreter.strip()) < 2 or not reviewed):
        source = act(store().confirm_story, cid, transcript, voice_language, reviewed=reviewed, reviewer=interpreter,
            proposal_id=(proposal or {}).get("id"), expected_fact_id=confirmed_transcript["id"] if confirmed_transcript else None, expected_recording=retained)
        if not source:
            return
        proposed, mode = ai.extract(transcript, live, store().age(cid).pathway)
        if act(store().propose_story_facts, cid, source["id"], proposed, mode, review_id=source["review_id"]) is False:
            return
        st.rerun()
    _proposals(cid)
    if st.button("Continue to questions", key=f"story_next_{cid}", disabled=bool(store().pending(cid))):
        store().stage(cid, "QUESTIONS")
        st.rerun()


def _proposals(cid: str) -> None:
    for f in store().pending(cid):
        with st.container(border=True):
            render = st.warning if (f.get("confidence") or 0) < .8 else st.info
            render(f"AI-proposed · {f['key']} = {f['value']} · confidence {f.get('confidence') or 0:.0%}")
            st.caption(f.get("quote") or "Requires worker confirmation")
            if f.get("evidence"):
                st.caption("Source: " + f["evidence"]["transcript_fact_id"] + " · " + f["evidence"]["language"] + " · check the quoted evidence before confirming")
            issue = store().story_proposal_issue(cid, f)
            if issue:
                st.warning(issue)
            yes, no = st.columns(2)
            if yes.button("Confirm", key=f"confirm_{f['id']}", disabled=bool(issue)):
                if act(store().confirm, cid, f["id"]):
                    st.rerun()
            if no.button("Dismiss", key=f"dismiss_{f['id']}"):
                store().dismiss(cid, f["id"])
                st.rerun()


def _speech_evaluation_panel() -> None:
    from .speech_evaluation import evaluation_template, evaluate_dataset
    with st.expander("Speech accuracy and device evaluation"):
        st.caption("Real reference recordings are required to measure language accuracy. Imported reviews are reported evidence; reviewer identity and reference quality need external verification.")
        _table(store().speech_readiness())
        st.download_button("Download speech evaluation schema and empty dataset", json.dumps(evaluation_template(), ensure_ascii=False, indent=2), file_name="smartsehat-speech-evaluation-template.json", mime="application/json")
        uploaded = st.file_uploader("Import reviewed speech evaluation dataset JSON", type=["json"], key="speech_evaluation_import")
        if uploaded:
            try:
                if uploaded.size > 8 * 1024 * 1024:
                    raise ValueError("Use an evaluation JSON smaller than 8 MB (up to 100 samples).")
                data = json.loads(uploaded.getvalue())
                result = evaluate_dataset(data)
                _table(result["groups"])
                st.caption(result["warning"])
                st.download_button("Download computed speech results", json.dumps(result, ensure_ascii=False, indent=2), file_name="smartsehat-speech-results.json", mime="application/json")
                if st.button("Record this reported speech evaluation", key="speech_evaluation_record"):
                    if act(store().report_speech_evaluation, data):
                        st.success("Reported evaluation saved with encrypted references and exact dataset hash. Independent language validation remains pending.")
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
        reports = store().speech_evaluations()
        if reports:
            _table([{"dataset_sha256": r["results"]["dataset_sha256"], "samples": len(r["dataset"]["samples"]), "reported_only": True} for r in reports])


def _questions(cid: str) -> None:
    question, completeness = next_question(store().facts(cid), store().age(cid))
    st.subheader("Guided questions / رہنمائی کے سوالات")
    st.progress(completeness, text=f"Question completeness {completeness:.0%}")
    from .questions import collection_status, explanation
    status = collection_status(store().facts(cid), store().age(cid))
    st.caption(f"Known: {status['known']} · Unknown: {status['unknown']} · Not asked: {status['not asked']}. Documentation progress is not clinical completeness.")
    with st.expander("Question coverage and unknown reasons"):
        _table(status["questions"])
    if question:
        urdu_layout = st.session_state.get("consultation_urdu_layout", False)
        st.markdown("### " + question["text_ur" if urdu_layout else "text_en"])
        st.write(question["text_en" if urdu_layout else "text_ur"])
        st.caption(explanation(question, "Urdu") + " / " + explanation(question, "English") + (" · Pertinent negative" if question.get("pertinent_negative", False) else ""))
        if st.button("Explain this question in simpler Urdu", key=f"simplify_{cid}_{question['id']}"):
            simpler, mode = ai.reword_question(question, st.session_state.get(f"live_extract_{cid}", False))
            st.info(simpler)
            st.caption(mode + " · Original question remains the reference.")
            store().record(cid, "QUESTION_REWORDED", {"question_id": question["id"], "asks_key": question["asks_key"], "mode": mode}, "ai")
        with st.expander("🔊 Hear the question"):
            prompt_language = store().case(cid)["data"]["language"]
            _prompt_audio(question["text_ur"] if prompt_language == "Urdu" else question["text_en"], prompt_language, f"question_audio_{cid}_{question['id']}")
        kind = question["kind"]
        section = "question_" + question["id"]
        draft = store().draft(cid, section)
        widget = f"q_{cid}_{question['id']}"
        if widget not in st.session_state and "answer" in draft:
            st.session_state[widget] = draft["answer"]
        autosave = {"on_change": _persist_draft, "args": (cid, section, {"answer": widget})}
        if kind == "bool":
            answer = st.radio("Answer", ["Choose", "Yes", "No", "Unknown"], key=widget, horizontal=True, **autosave)
            value = True if answer == "Yes" else False if answer == "No" else None
            ready = answer != "Choose"
        elif kind == "number":
            value = st.number_input("Days", min_value=0.0, value=0.0, key=widget, **autosave)
            ready = True
        elif kind == "choice":
            value = st.selectbox("Answer", question["choices"], index=None, key=widget, **autosave)
            ready = value is not None
            if value == "unknown":
                value = None
        else:
            value = st.text_input("Answer", key=widget, **autosave)
            ready = bool(value.strip())
        reason_key = f"question_reason_{cid}_{question['id']}"
        reason = st.text_input("Reason if this answer is unknown or cannot be obtained", value=draft.get("reason", ""), key=reason_key,
            on_change=_persist_draft, args=(cid, section, {"reason": reason_key}))
        if st.button("Save answer", type="primary", disabled=not ready or value is None and not reason.strip()):
            store().add_fact(cid, question["asks_key"], value, reason_missing=reason if value is None else None)
            st.rerun()
        if st.button("Unable to obtain this answer", key=f"unknown_q_{cid}_{question['id']}", disabled=not reason.strip()):
            store().add_fact(cid, question["asks_key"], None, reason_missing=reason)
            st.rerun()
    else:
        st.success("All applicable questions documented. Review the unknown answers with the doctor.")
    if st.button("Continue to measurements"):
        store().stage(cid, "MEASURE")
        st.rerun()


def _measurements(cid: str) -> None:
    st.subheader("Measurements and observation / پیمائش")
    age = store().age(cid)
    with st.expander("60-second breath counter", expanded=True):
        counted = breath_counter(case_id=cid, threshold=respiratory_threshold(age), key=f"breath_{cid}", default=None)
        if completed_breath_count(counted, cid):
            st.info(f"Completed count: {counted['count']} breaths/min")
            if st.button("Confirm counted respiratory rate"):
                if act(store().measurement, cid, "resp_rate", counted["count"], source="device", quote="Worker confirmed a completed 60-second tap count."):
                    st.rerun()
    existing = _values(cid)
    for key, spec in config()["measurements"].items():
        with st.expander(key.replace("_", " ").title() + f" · {existing.get(key, 'not recorded')}"):
            st.image(illustration(key, key.replace("_", " ")), width=145)
            st.caption("Copy the device value and unit. Use an explicit missing reason when it cannot be measured; the illustration does not classify the patient.")
            section = f"measurement:{key}"
            draft = store().draft(cid, section)
            value_key, unit_key, reason_key = f"measure_value_{cid}_{key}", f"measure_unit_{cid}_{key}", f"measure_reason_{cid}_{key}"
            pairs = {"value": value_key, "reason": reason_key}
            value = st.text_input("Value", value=draft.get("value", "" if existing.get(key) is None else str(existing[key])), key=value_key,
                on_change=_persist_draft, args=(cid, section, pairs))
            if key == "temperature":
                unit = st.selectbox("Unit", ["C", "F"], index=1 if draft.get("unit") == "F" else 0, key=unit_key,
                    on_change=_persist_draft, args=(cid, section, {"unit": unit_key}))
            else:
                unit = spec["unit"]
                st.caption(f"Unit: {unit}")
            reason = st.text_input("If not done, give the reason", value=draft.get("reason", ""), key=reason_key,
                on_change=_persist_draft, args=(cid, section, pairs))
            if st.button("Save measurement", key=f"save_measurement_{cid}_{key}"):
                if act(store().measurement, cid, key, value, unit, reason, source="device"):
                    st.rerun()
    with st.expander("Observation / point-of-care tests / photos"):
        obs = st.selectbox("Observation", ["chest_indrawing", "stridor", "dehydration", "pallor", "jaundice", "oedema", "rash"])
        icon, instruction, instruction_ur = guide(obs)
        st.image(illustration(obs, obs.replace("_", " ")), width=170)
        st.info(icon + " " + instruction)
        st.write(instruction_ur)
        obs_answer = st.radio("Finding", ["Yes", "No", "Unknown"], horizontal=True)
        if st.button("Save observation"):
            store().add_fact(cid, obs, True if obs_answer == "Yes" else False if obs_answer == "No" else None, reason_missing="Not assessable" if obs_answer == "Unknown" else None)
            st.rerun()
        from .poc import tests as poc_tests
        test = st.selectbox("Local test", list(poc_tests()), format_func=lambda k: poc_tests()[k]["label"])
        spec = poc_tests()[test]
        unavailable = st.checkbox("Test not done", key=f"poc_missing_{cid}_{test}")
        unit = st.selectbox("Test unit", list(spec["units"]), key=f"poc_unit_{cid}_{test}") if spec["kind"] == "number" else ""
        result = st.text_input("Numeric result", key=f"poc_value_{cid}_{test}") if spec["kind"] == "number" else st.selectbox("Test result", spec["choices"], index=None, key=f"poc_choice_{cid}_{test}")
        reason = st.text_input("Reason test not done", key=f"poc_reason_{cid}_{test}") if unavailable else ""
        if st.button("Record test"):
            if act(store().test_result, cid, test, None if unavailable else result, unit, reason):
                st.rerun()
        site = st.selectbox("Photo framing guide", list(PHOTO_GUIDES))
        st.info(PHOTO_GUIDES[site])
        photo = st.file_uploader("Clinical photo", type=["jpg", "jpeg", "png"], key=f"clinical_photo_{cid}")
        if photo and st.button("Save clinical photo"):
            store().record(cid, "PHOTO_ATTACHED", {"path": _upload(photo, cid, "clinical"), "site": site, "experimental": True})
            st.success("Photo saved for doctor review.")
        cough = st.audio_input("Cough recording for doctor listening only; experimental, no AI score", key=f"cough_recording_{cid}")
        if cough and st.button("Keep cough recording"):
            store().record(cid, "COUGH_AUDIO_ATTACHED", {"path": _upload(cough, cid, "cough"), "experimental": True, "ai_scored": False})
            st.success("Cough audio saved for doctor listening.")
    with st.expander("Read a measurement from a device photo"):
        device_key = st.selectbox("Device measurement", ["spo2", "resp_rate", "temperature", "pulse", "systolic", "diastolic"])
        device_photo = st.file_uploader("Device display photo", type=["jpg", "jpeg", "png"], key=f"device_photo_{cid}")
        live_device = st.toggle("Use live device-photo reading", key=f"device_live_{cid}")
        if device_photo and st.button("Read device display"):
            path = Path(_upload(device_photo, cid, "device"))
            reading, mode = ai.read_device(path, device_key, live_device)
            store().record(cid, "PHOTO_ATTACHED", {"path": str(path), "site": "device", "measurement": device_key})
            if reading:
                store().add_fact(cid, device_key, reading["value"], confirmed=False, source="ai_proposed", unit=reading["unit"], confidence=reading["confidence"], quote="Confirm the number and unit against the device display")
                st.rerun()
            st.info(mode)
        _proposals(cid)
    if st.button("Continue to Dawa Check", type="primary"):
        store().stage(cid, "MEDS")
        st.rerun()


def _reported_amounts(cid: str) -> None:
    from .reported_doses import record_hash
    values = _values(cid)
    records = values.get("medicine_records") or []
    if not records:
        return
    with st.expander("Reported medicine amounts · one check per confirmed product"):
        index = st.selectbox("Confirmed medicine row", list(range(len(records))), format_func=lambda i: f"{i+1}. {records[i]['name']} · {records[i]['strength']} · {records[i]['formulation']}", key=f"reported_med_index_{cid}")
        record = records[index]
        identity = record_hash(record)
        entry = next((row for row in values.get("reported_doses") or [] if row["record_hash"] == identity), {})
        section = "reported_dose:" + identity
        draft = store().draft(cid, section)
        defaults = {"dose_mg": entry.get("dose_mg") or 0., "doses_per_day": int((entry.get("regimen") or {}).get("doses_per_day") or 0),
            "duration_days": (entry.get("regimen") or {}).get("duration_days") or 0., "indication": (entry.get("regimen") or {}).get("indication", ""),
            "route": (entry.get("product") or {}).get("route", "unknown"), "reason": entry.get("unknown_reason", "")}
        keys = {field: f"reported_{field}_{cid}_{identity}" for field in defaults}
        for field, key in keys.items():
            if key not in st.session_state:
                st.session_state[key] = draft.get(field, defaults[field])
        callback = {"on_change": _persist_draft, "args": (cid, section, keys)}
        st.caption("Record only an established mg amount actually taken. Do not guess a conversion from tablets or mL; explain unknown amounts/timing instead. These entries do not prescribe a dose.")
        amount = st.number_input("Reported mg per dose (0 = unknown)", min_value=0., key=keys["dose_mg"], **callback)
        daily = st.number_input("Reported maximum doses in any 24 hours (0 = unknown)", min_value=0, step=1, key=keys["doses_per_day"], **callback)
        days = st.number_input("Reported days taken (0 = unknown)", min_value=0., key=keys["duration_days"], **callback)
        indication = st.text_input("Reported reason for taking it", key=keys["indication"], **callback)
        route = st.selectbox("Reported administration route", list(get_args(Route)), key=keys["route"], **callback)
        reason = st.text_input("Reason amount or timing is unknown", key=keys["reason"], **callback)
        confirmation_id = hashlib.sha256(json.dumps([identity, amount, daily, days, indication, route, reason]).encode()).hexdigest()[:16]
        confirmed = st.checkbox("I confirmed these reported amounts or unknowns with the patient", key=f"reported_confirm_{cid}_{confirmation_id}")
        if st.button("Confirm reported medicine amount", disabled=not confirmed, key=f"save_reported_{cid}_{identity}"):
            if act(store().reported_medicine_amount, cid, index, identity, amount or None, route,
                   {"doses_per_day": daily or None, "duration_days": days or None, "indication": indication}, reason):
                st.rerun()
        for checked in store().medcheck(cid).get("reported_dose_checks", []):
            st.write(checked["medicine"] + " · amount: " + checked["status"] + " · regimen: " + checked["regimen_status"])
            if checked.get("unknown_reason"):
                st.caption(checked["unknown_reason"])


def _meds(cid: str) -> None:
    st.subheader("Dawa Check / دوا کی جانچ")
    st.caption("Record each medicine separately. Confirm the printed strength and what the patient actually took; leave unknowns explicit.")
    st.info(PHOTO_GUIDES["medicine"])
    photo = st.file_uploader("Medicine strip, bag or prescription", type=["jpg", "jpeg", "png"], key=f"medicine_photo_{cid}")
    live = st.toggle("Use live medicine vision", key=f"medicine_live_{cid}")
    if not live:
        st.caption("Live vision is off: only a saved result for this exact photo can be used. Turn it on to read a new image, or enter the medicine rows manually.")
    if photo:
        st.image(photo, width=280)
        if st.button("Read medicine details"):
            path = Path(_upload(photo, cid, "medicines"))
            proposed, mode = ai.medicine_records_photo(path, live)
            st.session_state[f"medicine_proposed_records_{cid}"] = proposed
            st.session_state.pop(f"medicine_editor_base_{cid}", None)
            st.session_state[f"medicine_editor_revision_{cid}"] = st.session_state.get(f"medicine_editor_revision_{cid}", 0) + 1
            store().save_draft(cid, "medicines", {**store().draft(cid, "medicines"), "records": proposed})
            store().record(cid, "MEDICINE_IMAGE_READ", {"path": str(path), "proposed_records": proposed, "mode": mode}, "ai")
            st.info(mode)
    values = _values(cid)
    draft = store().draft(cid, "medicines")
    proposed = st.session_state.get(f"medicine_proposed_records_{cid}")
    initial = draft["records"] if "records" in draft else proposed or values.get("medicine_records") or [ai.MedicineRecord(name=name).model_dump() for name in values.get("medicines", [])]
    if proposed:
        st.warning("Image-derived medicine details are proposals. Check every name and strength against the image.")
    if not initial:
        initial = [{"name": "", "strength": "unknown", "formulation": "unknown", "amount": "unknown", "frequency": "unknown", "duration": "unknown", "adviser": "unknown"}]
    # Editors rerun when a cell is committed; persist their returned rows before
    # navigation, while keeping the base fixed for Streamlit's edit deltas.
    editor_base = f"medicine_editor_base_{cid}"
    allergy_base = f"allergy_editor_base_{cid}"
    editor_key = f"medicine_editor_{cid}_{st.session_state.get(f'medicine_editor_revision_{cid}', 0)}"
    if editor_base not in st.session_state or editor_key not in st.session_state:
        st.session_state[editor_base] = initial
    if allergy_base not in st.session_state or f"allergy_editor_{cid}" not in st.session_state:
        st.session_state[allergy_base] = (draft["allergy_records"] if "allergy_records" in draft else values.get("allergy_records")) or [{"substance": "", "reaction": "unknown", "severity": "unknown"}]
    with st.container():
        records = st.data_editor(st.session_state[editor_base], num_rows="dynamic", width="stretch", key=editor_key,
            column_config={"name": st.column_config.TextColumn("Medicine name", required=True),
                "strength": "Printed strength", "amount": "Amount actually taken", "frequency": "How often", "duration": "For how long",
                "formulation": st.column_config.SelectboxColumn("Form", options=["unknown", "tablet", "capsule", "syrup", "injection", "inhaler", "cream", "other"]),
                "adviser": st.column_config.SelectboxColumn("Who advised it?", options=["unknown", "self", "pharmacy", "doctor", "hakeem", "other provider"])})
        history_key, allergy_key, other_key = (f"med_history_{cid}", f"med_allergy_{cid}", f"med_other_{cid}")
        history = st.text_area("Additional medicine history", value=draft.get("history", values.get("medicine_history", "")), key=history_key,
            on_change=_persist_draft, args=(cid, "medicines", {"history": history_key}))
        choices = ["Unknown", "No", "Yes"]
        allergy = st.radio("Known penicillin allergy", choices, index=choices.index(draft.get("allergy", "Yes" if values.get("allergy_penicillin") is True else "No" if values.get("allergy_penicillin") is False else "Unknown")), horizontal=True, key=allergy_key,
            on_change=_persist_draft, args=(cid, "medicines", {"allergy": allergy_key}))
        other = st.text_input("Other allergies (none / unknown allowed)", value=draft.get("other", values.get("allergies", "unknown")), key=other_key,
            on_change=_persist_draft, args=(cid, "medicines", {"other": other_key}))
        allergy_rows = st.data_editor(st.session_state[allergy_base], num_rows="dynamic", width="stretch", key=f"allergy_editor_{cid}",
            column_config={"substance": "Known allergy substance / brand", "reaction": "What reaction occurred?", "severity": st.column_config.SelectboxColumn("Reported severity", options=["unknown", "mild", "moderate", "severe"])})
        confirmed = st.checkbox("I checked all medicine details with the patient/image, or confirm none reported")
        updated = {"records": records, "allergy_records": allergy_rows, "history": history, "allergy": allergy, "other": other}
        if updated != store().draft(cid, "medicines"):
            store().save_draft(cid, "medicines", updated)
        if st.button("Confirm reconciliation"):
            if not confirmed:
                st.error("Confirm the reconciliation before saving.")
            elif act(store().medicines, cid, records, history, True if allergy == "Yes" else False if allergy == "No" else None, other, allergy_rows):
                st.session_state.pop(f"medicine_proposed_records_{cid}", None)
                st.rerun()
    checked = store().medcheck(cid)
    for med in checked["medicines"]:
        st.write(med["brand"] + " → " + (", ".join(med["generic"]) or "unresolved"))
    for flag in checked["flags"]:
        st.warning(f"{flag['rule_id']} · {flag['text']}")
    _reported_amounts(cid)
    if st.button("Continue to review", type="primary"):
        store().stage(cid, "REVIEW")
        st.rerun()


def _corrections(cid: str) -> None:
    with st.expander("Correct age or a confirmed fact · saved history is preserved"):
        data = store().case(cid)["data"]
        with st.expander("Correct demographic details or pregnancy status"):
            sex = st.selectbox("Corrected sex", ["Male", "Female", "Other / not recorded"], index=["Male", "Female", "Other / not recorded"].index(data["sex"]) if data["sex"] in ("Male", "Female", "Other / not recorded") else 2, key=f"demog_sex_{cid}")
            pregnancy_key = f"demog_pregnant_{cid}"
            st.session_state.setdefault(pregnancy_key, data["pregnant"])
            if sex == "Male":
                st.session_state[pregnancy_key] = False
                pregnant = False
            else:
                pregnant = st.checkbox("Confirmed pregnant", key=pregnancy_key)
            village = st.text_input("Corrected village", value=data["village"], key=f"demog_village_{cid}")
            language = st.selectbox("Patient language", ["Urdu", "English"], index=0 if data["language"] == "Urdu" else 1, key=f"demog_language_{cid}")
            if st.button("Save demographic correction"):
                if act(store().demographics, cid, sex, pregnant, village, language):
                    st.rerun()
        age = store().age(cid)
        c1, c2 = st.columns(2)
        value = c1.number_input("Corrected age", min_value=0.0, value=float(age.value), key=f"correct_age_{cid}")
        unit = c2.selectbox("Corrected age unit", ["days", "weeks", "months", "years"], index=["days", "weeks", "months", "years"].index(age.unit), key=f"correct_unit_{cid}")
        corrected = Age.from_input(value, unit)
        st.caption(corrected.display)
        checked = st.checkbox("Age and unit checked", value=not bool(corrected.warning), key=f"checked_age_{cid}")
        if st.button("Save age correction", disabled=not checked):
            fact = store().add_fact(cid, "age", corrected.json())
            store().record(cid, "AGE_CONFIRMED", {"fact_id": fact["id"]})
            st.rerun()
        facts = store().facts(cid)
        st.caption("Correct structured medicine and allergy entries in the Dawa Check step.")
        choice = st.selectbox("Fact to correct", [f for f in facts if f["key"] not in ("age", "medicines", "medicine_records", "allergy_records")], format_func=lambda f: f"{f['id']} · {f['key']} = {f['value']}", key=f"edit_fact_{cid}")
        if choice:
            text = st.text_input("New value (true, false, a number, or text)", key=f"edit_value_{cid}_{choice['id']}")
            if st.button("Save correction"):
                if choice["key"] in config()["measurements"]:
                    ok = act(store().measurement, cid, choice["key"], text, supersedes=choice["id"])
                else:
                    try:
                        parsed = json.loads(text)
                    except ValueError:
                        parsed = text
                    kind = next((q["kind"] for q in QUESTIONS if q["asks_key"] == choice["key"]), "text")
                    if type(choice["value"]) is bool and type(parsed) is not bool:
                        st.error("Enter true or false for a yes/no fact.")
                        ok = False
                    elif kind == "number" and (type(parsed) not in (int, float) or parsed < 0):
                        st.error("Enter a non-negative number.")
                        ok = False
                    else:
                        ok = act(store().add_fact, cid, choice["key"], parsed, supersedes=choice["id"])
                if ok:
                    st.rerun()
        changes = [e for e in store().events(cid) if e["type"] == "FACT_SUPERSEDED"][-5:]
        for event in reversed(changes):
            old, new = event["data"]["before"], event["data"]["fact"]
            st.write(f"{new['key']}: {old['value']} → {new['value']}")
        if changes:
            st.caption("Previous derived outputs and packet versions are marked stale. Generate and read back a new packet.")


def _transfer_panel(cid: str, packet: dict, suffix: str) -> None:
    from .transport import package, chunk_archive
    service = store()
    with st.expander("Portable case package / interrupted-transfer recovery"):
        st.caption("Includes the complete packet and optional original media. Missing media is listed explicitly. Transfers use checksums; the receiving clinician must still review the source and patient facts.")
        include_media = st.checkbox("Include available original case media", key=f"transfer_media_{cid}_{suffix}")
        ready = not packet.get("stale") and packet["verification"]["status"] == "VERIFIED" and service.case(cid)["data"]["consent"]
        marker = hashlib.sha256(json.dumps([service.review_snapshot(cid), packet["version"], include_media], sort_keys=True).encode()).hexdigest()
        cache_key = f"transfer_{cid}_{suffix}_{marker}"
        if st.button("Prepare lossless portable package", disabled=not ready, key=f"transfer_prepare_{cid}_{suffix}"):
            try:
                snapshot = service.review_snapshot(cid)
                def reader(path):
                    checked = service._media_path(cid, path, (".wav", ".mp3", ".webm", ".m4a", ".ogg", ".jpg", ".jpeg", ".png", ".pdf"))
                    return read_media(checked), Path(checked.name.removesuffix(".enc")).suffix.lower()
                blob, info = package(packet, media_reader=reader if include_media else None)
                current = service.output(cid, "packet")
                if snapshot != service.review_snapshot(cid) or not service.case(cid)["data"]["consent"] or not current or current["version"] != packet["version"]:
                    raise ValueError("The case changed during preparation. Regenerate its current packet.")
                st.session_state[cache_key] = {"blob": blob, "info": info}
                service.record(cid, "TRANSFER_PREPARED", {"packet_version": packet["version"], "include_media": include_media, **info})
            except (ValueError, OSError) as exc:
                st.error(str(exc))
        saved = st.session_state.get(cache_key) if ready else None
        if saved:
            info = saved["info"]
            st.write(f"{info['bytes']:,} compressed bytes · {info['media_available']}/{info['media_total']} original media files included")
            st.code(info["sha256"], language=None)
            st.download_button("Download portable case ZIP", saved["blob"], file_name=f"{cid}-v{packet['version']}-case.zip", mime="application/zip", key=f"transfer_zip_{cid}_{suffix}")
            if st.button("Prepare resumable 64 KiB transfer parts", key=f"parts_prepare_{cid}_{suffix}"):
                saved["parts"] = chunk_archive(saved["blob"])
            if saved.get("parts"):
                st.download_button("Download transfer parts ZIP", saved["parts"], file_name=f"{cid}-v{packet['version']}-parts.zip", mime="application/zip", key=f"parts_zip_{cid}_{suffix}")
            st.caption("Extract the parts ZIP, transfer its JSON parts in any order, and use the receiver to identify missing parts. Payload-only estimates: " +
                "; ".join(f"{kbps} kbit/s ≈ {info['bytes'] * 8 / (kbps * 1000):.1f}s" for kbps in (16, 64, 256)) + ". These estimates exclude network delay and are not measured field performance.")
        if not ready:
            st.info("A current verified packet and sharing consent are required to prepare this transfer.")


def _transfer_receiver() -> None:
    from .transport import assemble, inspect_package
    with st.expander("Receive portable case or interrupted transfer parts"):
        st.caption("Byte checks detect missing/corrupt parts. They do not authenticate a sender or prove clinical correctness. Receiving creates no confirmed facts or doctor decisions.")
        archive = st.file_uploader("Verify a portable case ZIP", type=["zip"], key="receive_case_zip")
        if archive and st.button("Verify this case archive", key="verify_case_zip"):
            try:
                result = inspect_package(archive.getvalue())
                st.success(f"Archive bytes verified · packet v{result['manifest']['packet_version']}")
                st.json(result["manifest"])
                st.text(result["packet"].get("full_text", result["packet"]["text"]))
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
        parts = st.file_uploader("Add received JSON transfer parts", type=["json"], accept_multiple_files=True, key="receive_transfer_parts")
        if parts and st.button("Keep parts and check what is missing", key="assemble_transfer_parts"):
            try:
                incoming = []
                for part in parts:
                    if part.size > 100_000:
                        raise ValueError("Each part must be smaller than 100 KB.")
                    incoming.append(json.loads(part.getvalue()))
                # Keep one transfer in this session, independently of uploader state.
                received = st.session_state.get("received_transfer_parts", [])
                combined = received + incoming
                blob, info = assemble(combined)
                st.session_state["received_transfer_parts"] = list({row["index"]: row for row in combined}.values())
                st.session_state["received_transfer_info"] = info
                if blob:
                    st.session_state["received_transfer_blob"] = blob
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
        info = st.session_state.get("received_transfer_info")
        if info:
            st.write(f"{info['received']}/{info['count']} parts received")
            if info["missing"]:
                st.write("Missing part indices: " + ", ".join(map(str, info["missing"])))
            else:
                st.success("Complete transfer; full checksum and package file checks passed.")
                st.download_button("Save reconstructed case ZIP", st.session_state["received_transfer_blob"], file_name="smartsehat-received-case.zip", mime="application/zip", key="save_received_transfer")
        if st.button("Clear receiver for a different transfer", key="clear_transfer_receiver"):
            for key in ("received_transfer_parts", "received_transfer_info", "received_transfer_blob"):
                st.session_state.pop(key, None)
            st.rerun()


def _delivery_panel(cid: str, suffix: str) -> None:
    from .delivery import gateway_config
    with st.expander("Delivery gateway / request outbox"):
        try:
            gateway_config()
            configured = True
        except ValueError:
            configured = False
        st.caption("Prepare requests for the configured care-team, SMS, WhatsApp or call gateway. Nothing is sent automatically. A gateway acceptance does not prove recipient receipt, reading or care.")
        if not configured:
            st.info("Gateway inactive: server URL and credentials have not been configured. Local routing remains simulated.")
        kind = st.selectbox("Content to deliver", ["case_packet", "emergency_prealert", "patient_guidance"], key=f"delivery_kind_{cid}_{suffix}")
        recipient = st.text_input("Recipient identifier agreed with your gateway", key=f"delivery_recipient_{cid}_{suffix}")
        channel = st.selectbox("Gateway channel", ["care_team", "sms", "whatsapp", "call"], key=f"delivery_channel_{cid}_{suffix}")
        if st.button("Prepare current delivery request", disabled=not recipient.strip(), key=f"queue_delivery_{cid}_{suffix}"):
            if act(store().queue_delivery, cid, kind, recipient, channel):
                st.success("Request prepared locally. Review its exact payload before sending.")
        rows = store().deliveries(cid)
        if rows:
            _table([{key: row.get(key) for key in ("kind", "recipient", "channel", "status", "updated", "receipt_id")} for row in rows])
            identities = [row["delivery_id"] for row in rows]
            identity = st.selectbox("Prepared request", identities, format_func=lambda value: next(row["kind"] + " · " + row["status"] + " · " + value[:12] for row in rows if row["delivery_id"] == value), key=f"select_delivery_{cid}_{suffix}")
            row = next(row for row in rows if row["delivery_id"] == identity)
            st.json(row["payload"])
            in_flight = row["status"] == "sending" and (datetime.now(timezone.utc) - datetime.fromisoformat(row["updated"])).total_seconds() < 120
            if row["status"] == "sending" and not in_flight:
                st.warning("No result was recorded for the earlier attempt. Its external outcome is unknown. Check the gateway before retrying the same idempotency key.")
            confirmation = st.checkbox("I checked this recipient and exact payload and authorize external sending", key=f"confirm_delivery_{cid}_{suffix}_{row['revision']}")
            if st.button("Send this request to the configured gateway", disabled=not configured or not confirmation or in_flight or row["status"] in ("gateway accepted", "cancelled"), key=f"dispatch_delivery_{cid}_{suffix}"):
                result = act(store().dispatch_delivery, cid, identity, confirmation)
                if result:
                    st.info(result["status"] + ". " + result.get("note", ""))
                    st.rerun()
            if row["status"] not in ("gateway accepted", "cancelled", "sending") and st.button("Cancel this unsent request", key=f"cancel_delivery_{cid}_{suffix}"):
                if act(store().cancel_delivery, cid, identity):
                    st.rerun()
            if row["status"] == "outcome unknown":
                st.warning("The gateway may already have received this request. Check its status before retrying. Retries retain the same idempotency key.")


def _packet(cid: str, doctor: bool = False) -> None:
    packet = store().output(cid, "packet", True)
    if not packet:
        st.info("No packet generated yet.")
        return
    st.caption(f"Packet version {packet['version']} · {packet['mode']}" + (" · SUPERSEDED / STALE" if packet["stale"] else ""))
    if packet["stale"]:
        st.warning("Facts changed. This packet is superseded and cannot be sent.")
    verification = packet["verification"]
    if verification["status"] == "BLOCKED":
        st.error("PACKET BLOCKED — a claim conflicts with confirmed evidence or has no valid citation.")
        for failure in verification["failures"]:
            st.write(failure)
    else:
        st.success("Packet fact statements match the current confirmed ledger. Clinical interpretation remains for the doctor.")
    with st.expander("SBAR case packet", expanded=not doctor):
        if packet.get("sections"):
            for section, rows in packet["sections"].items():
                st.markdown("#### " + section)
                for row in rows:
                    st.write(row["text"])
                    st.caption(row["source"])
        else:
            st.text(packet["text"])
        st.download_button("Download full SBAR", packet.get("full_text", packet["text"]), file_name=f"{cid}-SBAR-v{packet['version']}.txt", key=f"download_packet_{cid}_{doctor}")
        st.download_button("Download compact transport summary", packet["text"], file_name=f"{cid}-compact-v{packet['version']}.txt", key=f"download_compact_{cid}_{doctor}")
        if packet.get("transport_budget_exceeded"):
            st.warning("This case exceeds the compact payload budget. Use the complete structured packet; no clinical text has been silently truncated.")
    _transfer_panel(cid, packet, str(doctor))
    with st.expander("Trust panel · provenance and Fact Ledger"):
        if verification.get("interpretation"):
            st.write("Consideration/source checks: " + verification["interpretation"]["status"])
            st.caption(verification["interpretation"]["clinical_interpretation"])
        _table(packet["source_lines"])
        _table(store().facts(cid))
    with st.expander("AI-proposed considerations · doctor interprets"):
        st.caption(packet.get("ranking_basis", "Doctor-facing considerations, not diagnoses. No patient dose is generated."))
        for item in packet["considerations"]:
            st.markdown(f"**{item.get('rank', '')}. {item['title']}**")
            st.write("Supporting evidence")
            for evidence in item.get("evidence_for", []):
                st.write("• " + evidence)
            st.write("Evidence against / pertinent negatives")
            for evidence in item.get("evidence_against", []):
                st.write("• " + evidence)
            st.caption("Missing: " + (", ".join(item.get("missing", [])) or "No catalogue gaps recorded"))
            st.write("Doctor-selected separating assessment: " + item.get("separating_test", "Doctor examination"))
        st.write("Gaps: " + ", ".join(packet["urgency"]["missing_critical"]))
        st.write("Evidence against: review the confirmed negative facts in the ledger. Separating tests are chosen by the doctor.")
        for ref in packet["references"]:
            st.caption(ref["id"] + " · " + str(ref["source"]))
    from .epidemiology import context_for
    local_context = context_for(store().facts(cid)) if doctor else packet.get("fever_context", {})
    if local_context and local_context.get("status") != "NOT_APPLICABLE":
        with st.expander("District and seasonal fever context"):
            if doctor:
                st.caption("Current confirmed location and reference catalogue. Any superseded SBAR above must be regenerated before sending it again.")
            st.write("Reference status: " + local_context["status"])
            if local_context.get("location"):
                location = local_context["location"]
                st.write(f"{location['country'] or 'Unknown country'} · {location['province'] or 'Unknown region'} · {location['district'] or 'Unknown district'} · {location['observed_on']}")
                if location.get("notes"):
                    st.write("Worker-confirmed reported context: " + location["notes"])
                if location.get("unknown_reason"):
                    st.write("Unknown location reason: " + location["unknown_reason"])
            for entry in local_context.get("entries", []):
                st.write(entry["condition"] + ": " + entry["context"])
                st.caption(f"{entry['id']} · {entry['reviewed_by']} · {entry['reviewed_on']} · {entry['source']}")
            st.caption(local_context["scope"])
    if packet.get("appendix"):
        with st.expander("Appendix · media, source facts and timeline"):
            for media in packet["appendix"]["media"]:
                path = Path(media.get("path", ""))
                if path.is_file():
                    st.caption(media["type"] + " · " + str(media.get("site", "")))
                    _show_media(path, key=f"packet_media_{cid}_{media['event_id']}_{doctor}")
                else:
                    st.caption(media["type"] + " · File unavailable; the audit reference is retained.")
            _table(packet["appendix"]["facts"])
            _table([{k: event[k] for k in ("ts", "actor", "type", "data")} for event in packet["appendix"]["timeline"][-20:]])
            st.download_button("Download complete packet and appendix", json.dumps(packet, ensure_ascii=False, indent=2), file_name=f"{cid}-packet-v{packet['version']}.json", key=f"packet_json_{cid}_{doctor}")
    with st.expander("Before versus after"):
        st.write("Original voice note / transcript")
        st.write(_values(cid).get("transcript", "Fictional demo: bachay ko khansi aur bukhar hai"))
        st.write("Structured packet")
        st.text(packet["text"])


def _translated_readback(cid: str, packet: dict, language: str) -> dict | None:
    view = store().readback_view(cid, packet, language)
    if language in ("English", "Urdu"):
        return view
    section = f"translation:{packet['version']}:{language}"
    draft = store().draft(cid, section)
    with st.expander("Interpreter-confirmed complete read-back", expanded=view is None):
        st.text_area("Complete English source for the interpreter", packet["readbacks"]["English"]["text"], disabled=True, key=f"translation_source_{cid}_{packet['version']}_{language}")
        st.caption("Translate the complete source, preserving every written number and medicine name. The interpreter must check meaning, negation and units; the text guard does not establish translation accuracy.")
        text_key, name_key = f"translation_text_{cid}_{packet['version']}_{language}", f"translation_name_{cid}_{packet['version']}_{language}"
        text = st.text_area("Complete interpreter translation", value=draft.get("text", ""), key=text_key, on_change=_persist_draft, args=(cid, section, {"text": text_key}))
        name = st.text_input("Interpreter name / identifier", value=draft.get("interpreter", ""), key=name_key, on_change=_persist_draft, args=(cid, section, {"interpreter": name_key}))
        identity = hashlib.sha256((text + name).encode()).hexdigest()[:16]
        checked = st.checkbox("Interpreter checked the complete meaning, negations, numbers, units and medicine names", key=f"translation_ok_{cid}_{identity}")
        if st.button("Confirm complete translated read-back", disabled=not checked, key=f"translation_save_{cid}_{packet['version']}_{language}"):
            if act(store().confirm_readback_translation, cid, packet["version"], language, text, name, checked):
                st.rerun()
    return view


def _review(cid: str) -> None:
    st.subheader("Review and read-back / خلاصے کی تصدیق")
    next_item, completion = next_question(store().facts(cid), store().age(cid))
    st.progress(completion, text=f"Question collection documented: {completion:.0%}. Unknown answers remain visible to the doctor.")
    if next_item:
        st.warning("Some history questions have not been recorded. Continue collecting, or explicitly document why the remaining information is unavailable.")
        if st.button("Continue the guided history", key=f"return_questions_{cid}"):
            store().stage(cid, "QUESTIONS")
            st.rerun()
        reason = st.text_input("Reason remaining history cannot be obtained", key=f"remaining_reason_{cid}")
        if st.button("Document unanswered questions as unavailable", disabled=not reason.strip()):
            from .questions import applicable
            keys = {f["key"] for f in store().facts(cid)}
            unanswered = [q for q in applicable(store().facts(cid), store().age(cid)) if q["asks_key"] not in keys]
            store().add_facts(cid, [{"key": q["asks_key"], "value": None, "reason_missing": reason} for q in unanswered])
            st.rerun()
    if not store().case(cid)["data"]["consent"]:
        consent = st.checkbox("Record consent to share this fictional case", key=f"share_consent_{cid}")
        if st.button("Save sharing consent", disabled=not consent):
            store().consent(cid, True)
            st.rerun()
    _proposals(cid)
    live = st.toggle("Use live considerations call", key=f"live_reason_{cid}")
    left, right = st.columns(2)
    if left.button("Generate verified case packet", type="primary"):
        if act(store().review, cid, live):
            st.rerun()
    if right.button("Demo: inject a contradiction"):
        if act(store().review, cid, False, True):
            st.rerun()
    _packet(cid)
    packet = store().output(cid, "packet")
    if packet and packet["verification"]["status"] == "VERIFIED":
        default_language = store().case(cid)["data"]["language"]
        language = st.selectbox("Patient read-back language", list(VOICE_LANGUAGES), index=list(VOICE_LANGUAGES).index(default_language), key=f"readback_language_{cid}_{packet['version']}")
        view = _translated_readback(cid, packet, language)
        if not view:
            st.info("Confirm the complete interpreter translation before patient read-back or audio confirmation.")
            return
        v = _values(cid)
        urdu = store().case(cid)["data"]["language"] == "Urdu"
        summary = ((f"کھانسی: {'ہاں' if v.get('cough') is True else 'نہیں' if v.get('cough') is False else 'معلوم نہیں'}۔ "
                    f"کھانسی کے دن: {v.get('cough_days', 'معلوم نہیں')}۔ "
                    f"بخار: {'ہاں' if v.get('fever') is True else 'نہیں' if v.get('fever') is False else 'معلوم نہیں'}۔ "
                    f"الٹی: {'ہاں' if v.get('vomiting') is True else 'نہیں' if v.get('vomiting') is False else 'معلوم نہیں'}۔") if urdu else
                   f"Cough: {v.get('cough', 'unknown')}; duration: {v.get('cough_days', 'unknown')} days; fever: {v.get('fever', 'unknown')}; vomiting: {v.get('vomiting', 'unknown')}.")
        if urdu:
            summary += f" بخار کے دن: {v.get('fever_days', 'معلوم نہیں')}۔ سانس فی منٹ: {v.get('resp_rate', 'معلوم نہیں')}۔ آکسیجن: {v.get('spo2', 'معلوم نہیں')}۔ وزن: {v.get('weight', 'معلوم نہیں')} کلو۔ دوا: {', '.join(v.get('medicines', [])) or 'کوئی درج نہیں'}۔ الرجی: {v.get('allergies', 'معلوم نہیں')}۔"
        else:
            summary += f" Fever duration: {v.get('fever_days', 'unknown')} days. RR: {v.get('resp_rate', 'unknown')}/min; SpO2: {v.get('spo2', 'unknown')}%; weight: {v.get('weight', 'unknown')} kg. Medicines: {', '.join(v.get('medicines', [])) or 'none recorded'}. Allergies: {v.get('allergies', 'unknown')}."
        summary = view["readbacks"][language]["text"]
        from .speech_audio import text_identity
        readback_id = text_identity(summary, language)[:16]
        st.text_area("Complete patient read-back", value=summary, height=220, disabled=True, key=f"full_readback_{cid}_{packet['version']}_{readback_id}")
        speech(language=VOICE_LANGUAGES[language]["browser"], case_id=f"{cid}:{packet['version']}", readback=summary, read_only=True, key=f"readback_{cid}", default=None)
        _readback_audio(cid, view, language)
        patient = st.checkbox("Patient/caregiver confirms the read-back", key=f"patient_yes_{cid}_{packet['version']}_{readback_id}")
        worker = st.checkbox("Worker confirms the full packet and consent to send", key=f"worker_yes_{cid}_{packet['version']}_{readback_id}")
        checked = store().medcheck(cid)
        medicine_ack = st.checkbox("I reviewed the medicine safety flags and will include them in the doctor handoff", key=f"med_ack_{cid}_{checked['version']}") if checked["flags"] else True
        correction = st.text_input("If the patient disagrees, record what needs correcting", key=f"readback_correction_{cid}_{packet['version']}")
        if st.button("Record patient's correction request", disabled=not correction.strip()):
            store().record(cid, "READBACK_REJECTED", {"packet_version": packet["version"], "reason": correction})
            st.warning("Use the correction panel above, then generate and read back a new packet. This version cannot be confirmed.")
        if st.button("Confirm read-back and send to doctor", disabled=not (patient and worker and medicine_ack), type="primary"):
            if act(store().acknowledge_medcheck, cid, checked["version"]) and act(store().readback, cid, patient, worker, language) and act(store().send, cid):
                st.rerun()


def _use_task_breath(cid: str, tid: str, count: int) -> None:
    draft = store().draft(cid, "task_" + tid)
    st.session_state[f"task_value_{tid}"] = str(count)
    store().save_draft(cid, "task_" + tid, {**draft, "value": str(count), "timer_count": count, "unit": "/min"})


def _tasks(cid: str) -> None:
    tasks = store().tasks(cid)
    if not tasks:
        st.info("No doctor tasks yet. Tasks appear here automatically.")
    from .poc import tests as poc_tests
    for task in tasks:
        with st.container(border=True):
            st.write(("Cancelled · " if task.get("cancelled") else "✓ " if task["done"] else "☐ ") + task["text"])
            if task["done"]:
                response = task.get("response")
                if response and response.get("value") is None:
                    st.caption("Unavailable · " + response.get("reason", "Reason not recorded"))
                continue
            key, tid = task["key"], task["task_id"]
            section = "task_" + tid
            draft = store().draft(cid, section)
            previous = draft.get("answer")
            fields = {}

            def field(name, widget):
                fields[name] = widget
                return {"key": widget, "on_change": _persist_draft, "args": (cid, section, {name: widget})}

            question = next((q for q in QUESTIONS if q["asks_key"] == key), None)
            if key == "lab_result":
                previous = previous or {}
                result = st.text_area("Laboratory report result (copy exactly)", value=draft.get("result", previous.get("result", "")), **field("result", f"lab_result_{tid}"))
                laboratory = st.text_input("Laboratory name (if known)", value=draft.get("laboratory", previous.get("laboratory", "")), **field("laboratory", f"lab_name_{tid}"))
                report_date = st.text_input("Report date YYYY-MM-DD (optional)", value=draft.get("report_date", previous.get("report_date") or ""), **field("report_date", f"lab_date_{tid}"))
                report = st.file_uploader("Laboratory report PDF or photo (optional)", type=["pdf", "jpg", "jpeg", "png"], key=f"lab_file_{tid}")
                attachment = _upload(report, cid, "lab-report") if report else draft.get("attachment_path", previous.get("attachment_path"))
                val = {"result": result, "laboratory": laboratory or "Not recorded", "report_date": report_date or None}
                if attachment:
                    st.caption("Report attachment saved with this draft.")
                    if st.checkbox("Include saved attachment with answer", value=draft.get("include_attachment", True), **field("include_attachment", f"task_attachment_{tid}")):
                        val["attachment_path"] = attachment
                draft["attachment_path"] = attachment
            elif key in config()["measurements"]:
                spec = config()["measurements"][key]
                previous = previous if isinstance(previous, dict) else {"value": previous}
                if key == "resp_rate":
                    scope = f"{cid}:{tid}"
                    counted = breath_counter(case_id=scope, threshold=respiratory_threshold(store().age(cid)), key=f"task_breath_{scope}", default=None)
                    if completed_breath_count(counted, scope):
                        st.info(f"Completed task count: {counted['count']} breaths/min. Use it as a draft, then return the answer.")
                        st.button("Use completed breath count for this task", key=f"task_count_{tid}", on_click=_use_task_breath, args=(cid, tid, counted["count"]))
                saved = draft.get("value", previous.get("value"))
                entry_options = field("value", f"task_value_{tid}")
                if entry_options["key"] not in st.session_state:
                    entry_options["value"] = str(saved) if saved is not None else ""
                raw = st.text_input("Measured value", **entry_options)
                if key == "temperature":
                    unit = st.selectbox("Measurement unit", ["C", "F"], index=1 if draft.get("unit", previous.get("unit")) == "F" else 0, **field("unit", f"task_unit_{tid}"))
                else:
                    unit = spec["unit"]
                    st.caption("Unit: " + unit)
                val = {"value": raw, "unit": unit}
                try:
                    use_count = key == "resp_rate" and draft.get("timer_count") is not None and float(raw) == draft["timer_count"]
                except ValueError:
                    use_count = False
                if use_count:
                    val.update({"method": "full-minute-tap-counter", "duration_seconds": 60})
                    st.caption("Completed 60-second count retained in this draft.")
            elif key in ("photo", "audio"):
                file = st.file_uploader("Requested photo", type=["jpg", "png", "jpeg"], key=f"task_photo_{tid}") if key == "photo" else st.audio_input("Requested recording", key=f"task_audio_{tid}")
                attachment = _upload(file, cid, "task") if file else draft.get("attachment_path", previous)
                draft["attachment_path"] = attachment
                val = None
                if attachment:
                    st.caption("Attachment saved with this draft; it has not been returned to the doctor.")
                    if st.checkbox("Include saved attachment with answer", value=draft.get("include_attachment", True), **field("include_attachment", f"task_attachment_{tid}")):
                        val = attachment
            elif key in poc_tests():
                spec = poc_tests()[key]
                previous = previous or {}
                if spec["kind"] == "number":
                    value = st.text_input("Test value", value=str(draft.get("value", previous.get("value", ""))), **field("value", f"task_poc_value_{tid}"))
                    units = list(spec["units"])
                    unit = st.selectbox("Test unit", units, index=units.index(draft.get("unit", previous.get("unit", units[0]))), **field("unit", f"task_poc_unit_{tid}"))
                    val = {"value": value, "unit": unit}
                else:
                    selected = draft.get("result", previous.get("result"))
                    val = {"result": st.selectbox("Test result", spec["choices"], index=spec["choices"].index(selected) if selected in spec["choices"] else None, **field("result", f"task_poc_choice_{tid}"))}
            elif question and question["kind"] == "bool":
                options = ["Choose", "Yes", "No"]
                selected = draft.get("answer_choice", "Yes" if previous is True else "No" if previous is False else "Choose")
                answer = st.radio("Answer", options, index=options.index(selected), horizontal=True, **field("answer_choice", f"task_answer_{tid}"))
                val = True if answer == "Yes" else False if answer == "No" else None
            elif question and question["kind"] == "choice":
                selected = draft.get("answer_choice", previous)
                val = st.selectbox("Answer", question["choices"], index=question["choices"].index(selected) if selected in question["choices"] else None, **field("answer_choice", f"task_answer_{tid}"))
            elif question and question["kind"] == "number":
                saved = draft.get("value", previous)
                val = st.number_input("Answer", min_value=0.0, value=float(saved) if saved is not None else None, **field("value", f"task_number_{tid}"))
            else:
                val = st.text_area("Worker answer", value=draft.get("value", previous or ""), **field("value", f"task_text_{tid}"))
            reason = st.text_input("If unavailable, reason", value=draft.get("reason", ""), **field("reason", f"task_reason_{tid}"))
            updated = {**draft, **{name: st.session_state[widget] for name, widget in fields.items()}, "answer": val, "reason": reason}
            if updated != store().draft(cid, section):
                store().save_draft(cid, section, updated)
            st.caption("Draft saved on field change. Return the answer to confirm and share it.")
            if st.button("Return task answer", key=f"task_done_{tid}"):
                if act(store().complete_task, cid, tid, val, reason):
                    st.rerun()


def _patient(cid: str) -> None:
    reply = store().latest_reply(cid)
    if not reply:
        st.info("Patient guidance will appear after the doctor's response.")
        return
    st.subheader("Patient card / مریض کی رہنمائی")
    snapshot = store().review_snapshot(cid)
    if reply.get("fact_ids") != snapshot["fact_ids"] or reply.get("media_ids", []) != snapshot["media_ids"]:
        st.warning("Recorded doctor guidance predates a fact correction or new case photo/audio. Ask the doctor to review the updated case before treating this as current guidance.")
    st.caption("SYNTHETIC · Simulated doctor response · guidance below is authored by the doctor.")
    if reply.get("status") == "awaiting_information":
        st.warning("Interim doctor guidance: return the requested information/tests for review before a final plan is recorded.")
    st.info(reply["advice"])
    if reply.get("local_language"):
        st.write(reply["local_language"])
    from .patient import complete_guidance
    with st.expander("Hear the complete written patient instructions"):
        spoken_language = st.selectbox("Reading language / voice labels", ["Urdu", "English"], key=f"patient_reading_language_{cid}_{reply['reply_id']}")
        spoken_text = complete_guidance(reply, spoken_language)
        st.caption("Reads all written instructions, including the prescription, schedule, warnings and tests. This does not translate them. The scheduled follow-up date is shown separately below.")
        speech(language="ur-PK" if spoken_language == "Urdu" else "en-PK", readback=spoken_text, read_only=True,
            case_id=f"{cid}:{reply['reply_id']}", key=f"patient_audio_{cid}", default=None)
        st.text(spoken_text)
        st.download_button("Download complete written instruction script", spoken_text, file_name=f"{cid}-instructions-{reply['reply_id']}.txt", key=f"patient_script_{cid}_{reply['reply_id']}")
    st.write("☀️ / 🌙 Medicine schedule: " + (reply.get("prescription") or "No prescription recorded"))
    if reply.get("medication_schedule"):
        _table([{ "Medicine": row["medicine"], "Doctor's dose": row["dose"],
            "☀️ Morning": "✓" if row["morning"] else "", "🌤 Noon": "✓" if row["noon"] else "",
            "🌇 Evening": "✓" if row["evening"] else "", "🌙 Night": "✓" if row["night"] else "", "Notes": row["notes"]} for row in reply["medication_schedule"]])
    if reply.get("feeding"):
        st.write("🥣 Feeding / fluids: " + reply["feeding"])
    if reply.get("ors"):
        st.write("💧 Doctor's ORS instructions: " + reply["ors"])
    if reply.get("counselling_script"):
        with st.expander("Worker counselling script from the doctor", expanded=True):
            st.text(reply["counselling_script"])
    st.write("🏥 Referral: " + (reply.get("referral") or "None recorded"))
    st.write("⚠️ Return / seek help if: " + (reply.get("return_if") or "Ask the doctor to supply return precautions."))
    audio_content, audio_mime = None, "audio/wav"
    if reply.get("guidance_audio_path") and reply.get("guidance_audio_confirmed") is True:
        audio_path = Path(reply["guidance_audio_path"])
        if audio_path.is_file():
            try:
                audio_content = read_media(audio_path)
                suffix = Path(audio_path.name.removesuffix(".enc")).suffix.lower()
                audio_mime = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".webm": "audio/webm", ".m4a": "audio/mp4", ".ogg": "audio/ogg"}.get(suffix, "audio/wav")
                st.write("🔊 Doctor-confirmed recorded patient guidance")
                st.audio(audio_content, format=audio_mime)
                st.download_button("Download recorded patient guidance", audio_content, file_name=cid + "-guidance" + suffix, mime=audio_mime, key=f"guidance_audio_download_{cid}")
            except Exception:
                st.warning("The guidance recording is unavailable. Use the doctor's written guidance and contact the care team.")
        else:
            st.warning("The guidance recording file is unavailable; the written plan and audit reference are retained.")
    if reply.get("request_call"):
        st.warning("The doctor requested a phone/video call. Contact the care team through the agreed channel; call delivery is simulated here.")
    st.caption("Follow-up due: " + display_time(reply["followup_due"]) if reply.get("followup_due") else "Awaiting the doctor's next review after requested information/results.")
    text = json.dumps({"synthetic": True, "patient": store().case(cid)["data"]["name"], **reply}, ensure_ascii=False, indent=2)
    st.download_button("Download patient card", text, file_name=f"{cid}-patient-card.json", key=f"patient_download_{cid}")
    st.download_button("Print-ready pictogram card", patient_card(store().case(cid)["data"]["name"], reply, audio_content, audio_mime, care=store().care_status(cid)), file_name=f"{cid}-patient-card.html", mime="text/html", key=f"patient_print_{cid}")
    if audio_content:
        st.caption("The downloaded HTML card includes this recording for playback without a connection.")
    _care_panel(cid)
    _counselling(cid)
    _orders_panel(cid)
    if any(not task["done"] for task in store().tasks(cid)):
        _tasks(cid)
    if reply.get("status") == "awaiting_information":
        return
    completed_seq = max((e["seq"] for e in store().events(cid) if e["type"] == "FOLLOWUP"), default=0)
    due_events = [e for e in store().notifications(cid) if e["type"] == "FOLLOWUP_DUE" and e["seq"] > completed_seq]
    if due_events:
        st.warning("Scheduled follow-up is due. Record the patient's current answers below.")
    with st.expander("Follow-up and outcome", expanded=bool(due_events)):
        if st.button("Fast-forward 48 hours", key=f"followup_clock_{cid}"):
            store().advance_clock(cid, 48 * 60)
            st.rerun()
        section = f"followup_{reply['reply_id']}_{completed_seq}"
        draft = store().draft(cid, section)

        def answer(label, name, choices, select=False):
            widget = f"{cid}_{section}_{name}"
            default = draft.get(name, choices[0])
            kwargs = {"index": choices.index(default), "key": widget, "on_change": _persist_draft, "args": (cid, section, {name: widget})}
            return st.selectbox(label, choices, **kwargs) if select else st.radio(label, choices, horizontal=True, **kwargs)

        condition = answer("Compared with before", "condition", ["unknown", "better", "same", "worse"])
        drinking = answer("Drinking/eating", "drinking", ["unknown", "normal", "less", "unable"])
        attended = answer("Went to the referred facility", "attended", ["Unknown", "Yes", "No"])
        danger = answer("New danger sign reported", "new_danger", ["Unknown", "Yes", "No"])
        outcome = answer("Outcome", "outcome", ["ongoing", "recovered", "referred", "admitted", "lost to follow-up"], select=True)
        st.caption("Answers are saved as a draft on field change. Save the follow-up to confirm them.")
        worsening = condition == "worse" or drinking == "unable" or danger == "Yes"
        if worsening:
            st.warning("Report this urgent change immediately, including when the selected outcome still needs correction.")
            if st.button("Report urgent follow-up change", key=f"urgent_followup_{cid}"):
                if act(store().safety_update, cid, worse=condition == "worse", new_danger=danger == "Yes", unable_drink=drinking == "unable", notes="Reported during follow-up; outcome has not been confirmed.", worker_confirmed=True):
                    st.rerun()
        if st.button("Save follow-up", key=f"followup_submit_{cid}"):
            if act(store().followup, cid, {"condition": condition, "drinking": drinking, "attended": True if attended == "Yes" else False if attended == "No" else None, "new_danger": True if danger == "Yes" else False if danger == "No" else None, "outcome": outcome}):
                st.rerun()


@st.fragment(run_every="2s")
def _worker_updates(cid: str) -> None:
    _banner(cid)
    _care_panel(cid)
    _orders_panel(cid)
    _tasks(cid)
    reply = store().latest_reply(cid)
    if reply:
        if reply.get("status") == "awaiting_information":
            st.info("The doctor is awaiting information/results. Interim guidance is available in Patient card.")
        else:
            st.success("Doctor response available in Patient card.")
    routed = next((e for e in reversed(store().events(cid)) if e["type"] == "ROUTED"), None)
    if routed:
        reviewer = routed["data"].get("reviewer")
        st.caption("Assigned: " + (reviewer["name"] if reviewer else "No matching simulated reviewer; escalate"))
    seen = next((e for e in reversed(store().events(cid)) if e["type"] == "DOCTOR_SEEN"), None)
    if seen and seen["ts"] >= (store().case(cid)["sent"] or seen["ts"]):
        st.caption("Seen by simulated doctor: " + seen["ts"])
    elif seen:
        st.caption("Awaiting acknowledgement of the latest worker update.")
    due = [e for e in store().notifications(cid) if e["type"] == "FOLLOWUP_DUE"]
    if due and not any(e["type"] == "FOLLOWUP" and e["ts"] >= due[-1]["ts"] for e in store().events(cid)):
        st.warning("The scheduled follow-up is due. Open Patient card / Follow-up and complete the five questions.")


def _safety_update(cid: str) -> None:
    latest = max((e["seq"] for e in store().events(cid) if e["type"] == "SAFETY_UPDATE"), default=0)
    section = f"safety_update_{latest}"
    draft = store().draft(cid, section)
    with st.expander("Report worsening / new danger now"):
        st.caption("Available while awaiting the doctor or tests. Select only changes the worker has checked with the patient/caregiver. Recording this does not contact emergency services.")
        flags = {}
        for name, label in (("worse", "Condition is worse"), ("new_danger", "New danger sign observed or reported"), ("unable_drink", "Patient is unable to drink/eat")):
            widget = f"{cid}_{section}_{name}"
            flags[name] = st.checkbox(label, value=draft.get(name, False), key=widget, on_change=_persist_draft, args=(cid, section, {name: widget}))
        widget = f"{cid}_{section}_notes"
        notes = st.text_area("What changed / reported danger", value=draft.get("notes", ""), key=widget, on_change=_persist_draft, args=(cid, section, {"notes": widget}))
        confirmed = st.checkbox("Worker confirms this reported change", key=f"{cid}_{section}_confirmed")
        if st.button("Report urgent change now", disabled=not (any(flags.values()) and confirmed), key=f"{cid}_{section}_save"):
            if act(store().safety_update, cid, **flags, notes=notes, worker_confirmed=confirmed):
                st.rerun()


@st.fragment(run_every="15s")
def _worker_worklist() -> None:
    items = store().worker_worklist()
    if not items:
        return
    with st.expander(f"Worker action list · {len(items)} active case(s)", expanded=not (st.query_params.get("case") or st.session_state.get("consultation_case"))):
        st.caption("Urgent cases first, then due follow-ups and pending doctor tasks. Refreshes every 15 seconds while this view is open.")
        _table([{"Patient / case": f"{item['name']} · {item['case_id']}", "Urgency": item["urgency"],
                 "Actions": "; ".join(item["actions"]), "Next follow-up": display_time(item["followup_at"]) if item["followup_at"] else "Not scheduled",
                 "Sharing": "Active simulated handoff" if item["sharing_active"] else "Local only"} for item in items])
        by_id = {item["case_id"]: item for item in items}
        selected = st.selectbox("Case needing worker action", list(by_id), format_func=lambda cid: f"{by_id[cid]['urgency']} · {by_id[cid]['name']} · {cid}", key="worker_action_case")
        if st.button("Open worker case", key="worker_action_open"):
            _open(selected)


def worker(cid: str) -> None:
    case = store().case(cid)
    age = store().age(cid)
    st.subheader(case["data"]["name"] + " · " + age.display)
    if age.date_of_birth:
        st.caption(f"Birth date {age.date_of_birth} · Encounter age as of {age.as_of}")
    st.caption(f"Case {cid} · Saved step: {case['stage']} · Autosaved on each answer or explicit save. Reopen this case link to resume.")
    _banner(cid)
    _safety_update(cid)
    _corrections(cid)
    _fever_context(cid)
    options = ["DANGER_SCREEN", "STORY", "QUESTIONS", "MEASURE", "MEDS", "REVIEW", "SENT", "PATIENT_CARD", "FOLLOWUP"]
    current = case["stage"]
    if current == "EMERGENCY":
        _emergency(cid)
        return
    displayed = current if current in options else "PATIENT_CARD" if current in ("DOCTOR_REPLIED", "CLOSED") else "DANGER_SCREEN"
    selected = st.selectbox("Saved consultation step", options, index=options.index(displayed),
                            format_func=lambda s: {"SENT": "Doctor tasks / awaiting response", "PATIENT_CARD": "Patient card", "FOLLOWUP": "Follow-up"}.get(s, s.replace("_", " ").title()), key=f"navigation_{cid}_{current}")
    if selected != displayed and selected != "SENT":
        if act(store().stage, cid, selected):
            st.rerun()
    st.progress((options.index(selected) + 1) / len(options))
    if selected == "DANGER_SCREEN":
        _danger(cid)
    elif selected == "STORY":
        _story(cid)
    elif selected == "QUESTIONS":
        _questions(cid)
    elif selected == "MEASURE":
        _measurements(cid)
    elif selected == "MEDS":
        _meds(cid)
    elif selected == "REVIEW":
        _review(cid)
    elif selected == "SENT":
        st.caption("Delivery is simulated. Open Doctor console in a second browser tab to review and respond.")
        _worker_updates(cid)
    else:
        _patient(cid)
    with st.expander("Case timeline / audit trail"):
        _table([{k: e[k] for k in ("ts", "actor", "type", "data")} for e in store().events(cid)[-20:]])
    _delivery_panel(cid, "worker")
    with st.expander("Export encounter / review evidence"):
        exported = {"synthetic": True, "case": store().case(cid), "facts": store().facts(cid), "events": store().events(cid), "packet": store().output(cid, "packet", True), "care": store().care_status(cid)}
        st.download_button("Download encounter JSON and audit", json.dumps(exported, ensure_ascii=False, indent=2), file_name=f"{cid}-encounter.json", key=f"encounter_export_{cid}")
        from .fhir import bundle
        document = bundle(store().case(cid), store().facts(cid), store().output(cid, "packet", True), store().tasks(cid), store().latest_reply(cid), care=store().care_status(cid))
        st.download_button("Download FHIR R4 document export", json.dumps(document, ensure_ascii=False, indent=2), file_name=f"{cid}-fhir.json", mime="application/fhir+json", key=f"fhir_export_{cid}")
        st.caption("Text terminology mappings; provincial/DHIS2 interoperability has not been validated.")


def _doctor_case(cid: str) -> None:
    case, values = store().case(cid), _values(cid)
    opened_handoff = store().handoff(cid)
    if not case["sent"] or not case["data"]["consent"]:
        st.warning("Patient sharing consent is not active for this case.")
        return
    st.subheader(case["data"]["name"])
    st.caption(f"{store().age(cid).display} · {case['data']['sex']} · Pregnancy: {'Yes' if case['data']['pregnant'] else 'No'} · {case['data']['village']} · {case['data']['language']}")
    if store().age(cid).date_of_birth:
        st.caption(f"Birth date {store().age(cid).date_of_birth} · Encounter age as of {store().age(cid).as_of}")
    _banner(cid)
    columns = st.columns(4)
    facts_by_id = {f["id"]: f for f in store().facts(cid)}
    rules = store().output(cid, "urgency")["fired"]
    for col, key in zip(columns, ("resp_rate", "spo2", "temperature", "weight")):
        related = [r for r in rules if any(facts_by_id.get(fid, {}).get("key") == key for fid in r["fact_ids"])]
        text = key.replace("_", " ").title() + ": " + str(values.get(key, "Unknown"))
        if any(r["level"] == "RED" for r in related):
            col.error(text)
        elif any(r["level"] == "AMBER" for r in related):
            col.warning(text)
        else:
            col.metric(key.replace("_", " ").title(), str(values.get(key, "Unknown")))
    with st.expander("Rule trace", expanded=True):
        _table(store().output(cid, "urgency")["fired"])
    for flag in store().medcheck(cid)["flags"]:
        st.warning(flag["text"])
    _packet(cid, doctor=True)
    _orders_panel(cid, doctor_view=True)
    _care_panel(cid, doctor_view=True)
    if st.button("Acknowledge / mark seen", key=f"seen_{cid}"):
        if act(store().record, cid, "DOCTOR_SEEN", {"seen_at": datetime.now(timezone.utc).isoformat(), "handoff_id": opened_handoff["handoff_id"], "simulated": True}, "doctor (simulated)"):
            st.success("Seen acknowledgement recorded.")
    with st.expander("Send a task back to worker", expanded=True):
        tasks = {"resp_rate": "Recount respiratory rate for a full minute", "spo2": "Recheck SpO2", "chest_indrawing": "Check chest indrawing", "tb_contact": "Ask about TB contact", "photo": "Send a clinical photo"}
        tasks.update({q["asks_key"]: q["text_en"] for q in QUESTIONS if q["asks_key"] not in tasks})
        tasks.update({"audio": "Send an audio recording for my review", "free_text": "Please clarify the following information", "glucose": "Measure glucose and return the value with its unit", "malaria_rdt": "Return the malaria rapid-test result", "haemoglobin": "Return haemoglobin with its unit", "pregnancy_test": "Return the pregnancy-test result"})
        key = st.selectbox("Task", list(tasks), format_func=tasks.get, key=f"doc_task_key_{cid}")
        instructions = st.text_area("Task instructions", value=tasks[key], key=f"doc_task_text_{cid}_{key}")
        if st.button("Send simulated task", key=f"doc_task_send_{cid}"):
            if act(store().task, cid, key, instructions):
                st.success("Task saved; worker will receive it on refresh.")
        _table(store().tasks(cid))
        for task in store().tasks(cid):
            if task["done"] or task.get("order_id"):
                continue
            with st.expander("Cancel pending task: " + task["text"]):
                reason = st.text_input("Doctor's task cancellation reason", key=f"cancel_task_reason_{task['task_id']}")
                if st.button("Cancel this task", key=f"cancel_task_{task['task_id']}", disabled=not reason.strip()):
                    if act(store().cancel_task, cid, task["task_id"], reason):
                        st.rerun()
    with st.expander("Doctor reply / referral / tests", expanded=True):
        from .patient import TEMPLATES, simplify_doctor_advice, guidance_confirmation_id
        st.caption("The doctor authors and confirms the clinical plan. Clinician identity and external delivery are simulated.")
        draft = store().draft(cid, "doctor_reply")
        current_snapshot = store().review_snapshot(cid)
        current_fact_ids = current_snapshot["fact_ids"]
        if "reviewed_fact_ids" not in draft or "reviewed_media_ids" not in draft or "reviewed_care_revision" not in draft or "reviewed_handoff_id" not in draft or "reviewed_fever_catalogue_hash" not in draft:
            fresh = "reviewed_fact_ids" not in draft
            draft.setdefault("reviewed_fact_ids", current_fact_ids)
            draft.setdefault("reviewed_media_ids", current_snapshot["media_ids"])
            draft.setdefault("reviewed_care_revision", current_snapshot["care_revision"])
            draft.setdefault("reviewed_handoff_id", current_snapshot["handoff_id"] if fresh else None)
            draft.setdefault("reviewed_fever_catalogue_hash", current_snapshot["fever_catalogue_hash"] if fresh else None)
            store().save_draft(cid, "doctor_reply", draft)
        changed_while_replying = draft["reviewed_fact_ids"] != current_fact_ids or draft["reviewed_media_ids"] != current_snapshot["media_ids"] or draft["reviewed_care_revision"] != current_snapshot["care_revision"] or draft["reviewed_handoff_id"] != current_snapshot["handoff_id"] or draft["reviewed_fever_catalogue_hash"] != current_snapshot["fever_catalogue_hash"]
        if changed_while_replying:
            st.warning("Worker information, attachments, questions, handoff statuses or local reference content changed while this reply was being prepared. Review the latest case before saving.")
            if st.button("I reviewed the updated worker information", key=f"review_changes_{cid}"):
                store().save_draft(cid, "doctor_reply", {**draft, "reviewed_fact_ids": current_fact_ids, "reviewed_media_ids": current_snapshot["media_ids"], "reviewed_care_revision": current_snapshot["care_revision"], "reviewed_handoff_id": current_snapshot["handoff_id"], "reviewed_fever_catalogue_hash": current_snapshot["fever_catalogue_hash"]})
                st.session_state[f"rx_ack_{cid}"] = False
                st.rerun()
        def draft_text(label, field, height=90):
            widget = f"doctor_{field}_{cid}"
            return st.text_area(label, value=draft.get(field, ""), height=height, key=widget,
                on_change=_persist_draft, args=(cid, "doctor_reply", {field: widget}))
        voice_reply = st.audio_input("Optional doctor voice reply", key=f"doctor_voice_{cid}")
        if voice_reply and st.button("Transcribe doctor voice draft", key=f"doctor_transcribe_{cid}"):
            audio_path = _upload(voice_reply, cid, "doctor-reply")
            store().record(cid, "DOCTOR_AUDIO_ATTACHED", {"path": audio_path, "simulated": True}, "doctor (simulated)")
            try:
                text, provider = ai.transcribe(audio_path, language="ur" if case["data"]["language"] == "Urdu" else "en")
                if text:
                    st.session_state[f"doctor_advice_{cid}"] = str(text)
                    store().save_draft(cid, "doctor_reply", {**draft, "advice": str(text)})
                else:
                    st.warning(provider)
            except Exception:
                st.warning("Transcription unavailable. The recording is retained; enter advice manually.")
        recorded_guidance = st.file_uploader("Or upload recorded patient guidance", type=["wav", "mp3", "webm", "m4a", "ogg"], key=f"doctor_guidance_upload_{cid}")
        if (recorded_guidance or voice_reply) and st.button("Keep recording for the patient card", key=f"keep_guidance_audio_{cid}"):
            path = _upload(recorded_guidance or voice_reply, cid, "patient-guidance")
            store().record(cid, "DOCTOR_AUDIO_ATTACHED", {"path": path, "purpose": "patient guidance draft", "simulated": True}, "doctor (simulated)")
            audio_draft = {**store().draft(cid, "doctor_reply"), "guidance_audio_path": path}
            audio_draft.pop("guidance_audio_proposal", None)
            store().save_draft(cid, "doctor_reply", audio_draft)
            st.rerun()
        template = st.selectbox("Reply template", list(TEMPLATES), key=f"reply_template_{cid}")
        if st.button("Use selected reply template", key=f"use_template_{cid}"):
            st.session_state[f"doctor_advice_{cid}"] = TEMPLATES[template]
            store().save_draft(cid, "doctor_reply", {**store().draft(cid, "doctor_reply"), "advice": TEMPLATES[template]})
        advice = draft_text("Doctor advice", "advice", 130)
        open_requests = {r["request_id"]: r for r in store().care_status(cid)["requests"] if r["status"] == "open"}
        resolved_requests = st.multiselect("Worker questions or barriers answered in this response", list(open_requests), format_func=lambda rid: open_requests[rid]["text"], key=f"resolve_requests_{cid}_{current_snapshot['care_revision']}") if open_requests else []
        diagnosis = draft_text("Doctor assessment / diagnosis (optional)", "diagnosis")
        live_wording = st.toggle("Use AI to draft simpler Urdu wording", key=f"doctor_wording_live_{cid}")
        if st.button("Draft patient-friendly wording from my advice", disabled=not advice.strip(), key=f"doctor_reword_{cid}"):
            suggestion = simplify_doctor_advice(advice, live_wording)
            st.session_state[f"doctor_local_language_{cid}"] = suggestion["text"]
            store().save_draft(cid, "doctor_reply", {**store().draft(cid, "doctor_reply"), "local_language": suggestion["text"],
                "guidance_proposal": {"source_hash": suggestion["source_hash"], "mode": suggestion["mode"]}})
            st.info(suggestion["mode"])
        local = draft_text("Patient guidance in Urdu (doctor authored)", "local_language", 120)
        proposal = store().draft(cid, "doctor_reply").get("guidance_proposal")
        ai_used = bool(proposal and local.strip())
        if proposal:
            st.caption("Assisted wording draft · " + proposal.get("mode", "Doctor verification required"))
            if proposal.get("source_hash") != hashlib.sha256(advice.encode()).hexdigest():
                st.warning("The source advice changed after this wording was drafted. Update the wording or verify it carefully against the current advice.")
        approval_id = guidance_confirmation_id(cid, advice, local, current_snapshot)
        guidance_confirmed = st.checkbox("I verified that the patient guidance preserves my clinical instructions", key=f"guidance_confirm_{cid}_{approval_id}") if local.strip() else True
        capability_options = ["No referral", "oxygen", "paediatric", "sputum", "caesarean", "xray"]
        capability = st.selectbox("Referral capability", capability_options, index=capability_options.index(draft.get("capability", "No referral")), key=f"capability_{cid}",
            on_change=_persist_draft, args=(cid, "doctor_reply", {"capability": f"capability_{cid}"}))
        eligible = [f for f in resources()["facilities"] if capability in f["capabilities"]]
        medicine = st.text_input("Doctor-selected medicine (optional, exact brand or generic)", value=draft.get("medicine", ""), key=f"rx_medicine_{cid}",
            on_change=_persist_draft, args=(cid, "doctor_reply", {"medicine": f"rx_medicine_{cid}"}))
        product_draft = draft.get("prescription_product") or {}
        forms, routes = list(get_args(Formulation)), list(get_args(Route))
        product = {
            "formulation": st.selectbox("Selected medicine formulation", forms, index=forms.index(product_draft.get("formulation", "unknown")),
                key=f"rx_product_formulation_{cid}", on_change=_persist_prescription_product, args=(cid,)),
            "strength": st.text_input("Printed strength of selected product", value=product_draft.get("strength", ""),
                help="Copy the package exactly, including units and denominator. No strength is inferred from the brand name.",
                key=f"rx_product_strength_{cid}", on_change=_persist_prescription_product, args=(cid,)),
            "route": st.selectbox("Doctor-selected administration route", routes, index=routes.index(product_draft.get("route", "unknown")),
                key=f"rx_product_route_{cid}", on_change=_persist_prescription_product, args=(cid,)),
        }
        dose = st.number_input("Doctor-entered amount per dose in mg (0 = not entered)", min_value=0.0, value=float(draft.get("dose_mg", 0) or 0), key=f"rx_amount_{cid}",
            on_change=_persist_draft, args=(cid, "doctor_reply", {"dose_mg": f"rx_amount_{cid}"}))
        regimen_draft = draft.get("prescription_regimen") or {}
        frequency_col, duration_col = st.columns(2)
        regimen = {
            "doses_per_day": frequency_col.number_input("Doctor-entered maximum doses in any 24 hours (0 = not entered)", min_value=0,
                value=int(regimen_draft.get("doses_per_day") or 0), step=1, key=f"rx_regimen_doses_per_day_{cid}", on_change=_persist_prescription_regimen, args=(cid,)) or None,
            "duration_days": duration_col.number_input("Doctor-entered course length in days (0 = not entered)", min_value=0.0,
                value=float(regimen_draft.get("duration_days") or 0), key=f"rx_regimen_duration_days_{cid}", on_change=_persist_prescription_regimen, args=(cid,)) or None,
            "indication": st.text_input("Doctor-selected indication / reason for this medicine", value=regimen_draft.get("indication", ""),
                key=f"rx_regimen_indication_{cid}", on_change=_persist_prescription_regimen, args=(cid,)),
            "as_needed": st.checkbox("Doctor has prescribed this medicine only when needed", value=regimen_draft.get("as_needed", False),
                key=f"rx_regimen_as_needed_{cid}", on_change=_persist_prescription_regimen, args=(cid,)),
        }
        st.caption("These structured fields check the doctor's selected prescription. Record the actual patient instructions in the written prescription/schedule below; they are not inferred from these fields.")
        rx_check = check_prescription(medicine, dose or None, store().facts(cid), store().age(cid), case["data"]["pregnant"], product, regimen)
        if medicine.strip():
            st.caption("Product-specific per-dose check: " + rx_check["dose_check"]["status"] + " · Regimen check: " + rx_check["dose_check"]["regimen_status"])
            if rx_check["dose_check"]["checks"]:
                _table([{"check": name, "status": status} for name, status in rx_check["dose_check"]["checks"].items()])
            entry = rx_check["dose_check"].get("entry")
            if entry:
                st.caption(f"Matched entry {entry['id']} · {entry['reviewed_by']} · {entry['reviewed_on']} · Source: {entry['source']}")
        if rx_check.get("same_ingredient_stock_options"):
            st.info("Same-ingredient stock options for doctor review: " + ", ".join(rx_check["same_ingredient_stock_options"]))
        prescription = draft_text("Doctor prescription / schedule (optional)", "prescription")
        st.caption("Dose ranges await clinical review. The system never supplies a patient dose. Synthetic inventory: " + ", ".join(resources()["stock"]))
        st.write("Patient medicine schedule: enter only the doctor's prescribed amounts")
        schedule_base = f"doctor_schedule_base_{cid}"
        if schedule_base not in st.session_state or f"doctor_schedule_{cid}" not in st.session_state:
            st.session_state[schedule_base] = draft.get("medication_schedule") or [{"medicine": "", "dose": "", "morning": False, "noon": False, "evening": False, "night": False, "notes": ""}]
        schedule = st.data_editor(st.session_state[schedule_base],
            num_rows="dynamic", width="stretch", key=f"doctor_schedule_{cid}",
            column_config={"morning": st.column_config.CheckboxColumn("☀️ Morning"), "noon": st.column_config.CheckboxColumn("🌤 Noon"), "evening": st.column_config.CheckboxColumn("🌇 Evening"), "night": st.column_config.CheckboxColumn("🌙 Night")})
        if schedule != draft.get("medication_schedule"):
            store().save_draft(cid, "doctor_reply", {**store().draft(cid, "doctor_reply"), "medication_schedule": schedule})
        full_check = check_plan(medicine, dose or None, schedule, prescription, store().facts(cid), store().age(cid), case["data"]["pregnant"], product, regimen)
        for flag in full_check["flags"]:
            st.warning(flag["rule_id"] + " · " + flag["text"])
        rx_revision = hashlib.sha256(json.dumps([medicine, dose, product, regimen, schedule, prescription, current_snapshot], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:14]
        safety_acknowledged = st.checkbox("Doctor has independently reviewed the prescription flags and unvalidated dose check", key=f"rx_ack_{cid}_{rx_revision}")
        from .orders import definitions
        local_tests = st.multiselect("Worker can do now", [spec["label"] for spec in definitions().values() if spec["location"] == "local"], default=draft.get("local_tests", []), key=f"doctor_local_tests_{cid}",
            on_change=_persist_draft, args=(cid, "doctor_reply", {"local_tests": f"doctor_local_tests_{cid}"}))
        lab_tests = st.multiselect("Needs referral lab", [spec["label"] for spec in definitions().values() if spec["location"] == "lab"], default=draft.get("lab_tests", []), key=f"doctor_lab_tests_{cid}",
            on_change=_persist_draft, args=(cid, "doctor_reply", {"lab_tests": f"doctor_lab_tests_{cid}"}))
        repeat_tests = st.checkbox("Repeat previously completed, unavailable or cancelled selected tests", key=f"repeat_tests_{cid}", help="Pending orders are reused. Completed tests are repeated only with this explicit request.")
        returned_orders = [o for o in store().orders(cid) if o["status"] in ("result returned", "unavailable") and not o["doctor_reviewed"]]
        results_reviewed = st.checkbox("I reviewed the returned test reports and unavailable-test reasons", key=f"review_orders_{cid}_" + "_".join(o["order_id"] for o in returned_orders)) if returned_orders else True
        facility_options = ["None"] + [f["name"] for f in eligible]
        referral = st.selectbox("Facility", facility_options, index=facility_options.index(draft.get("referral")) if draft.get("referral") in facility_options else 0, key=f"doctor_referral_{cid}",
            on_change=_persist_draft, args=(cid, "doctor_reply", {"referral": f"doctor_referral_{cid}"}))
        return_if = draft_text("Return precautions / counselling script", "return_if")
        feeding = draft_text("Doctor's feeding / fluid advice", "feeding")
        ors = draft_text("Doctor's ORS preparation instructions, if relevant", "ors")
        counselling = draft_text("Worker counselling script (doctor authored)", "counselling_script")
        call = st.checkbox("Request a phone/video call (simulated)", value=draft.get("request_call", False), key=f"doctor_call_{cid}",
            on_change=_persist_draft, args=(cid, "doctor_reply", {"request_call": f"doctor_call_{cid}"}))
        response_labels = {"Final plan": "final", "Awaiting information / results": "awaiting_information"}
        response_label = st.radio("Response status", list(response_labels), index=1 if draft.get("response_label") == "Awaiting information / results" else 0, key=f"doctor_response_status_{cid}",
            on_change=_persist_draft, args=(cid, "doctor_reply", {"response_label": f"doctor_response_status_{cid}"}), help="Use awaiting information/results for a provisional response. Recovery follow-up starts after the final plan.")
        from .patient import complete_guidance
        from .speech_audio import synthesize
        written_guidance = {"advice": advice, "local_language": local, "prescription": prescription, "medication_schedule": schedule,
            "feeding": feeding, "ors": ors, "counselling_script": counselling, "return_if": return_if,
            "referral": referral if referral != "None" else "", "local_tests": local_tests, "lab_tests": lab_tests,
            "request_call": call, "status": response_labels[response_label]}
        with st.expander("Prepare audio from the complete written guidance"):
            audio_language = st.selectbox("Generated recording language / labels", ["Urdu", "English"], key=f"doctor_audio_language_{cid}")
            audio_text = complete_guidance(written_guidance, audio_language)
            st.text(audio_text)
            st.caption("The script includes every written patient instruction and does not translate or infer doses. Generated audio is a draft: listen against this entire script before approval. The scheduled follow-up date is shown separately on the saved card.")
            if st.button("Generate complete patient-guidance audio draft", disabled=not advice.strip() or not guidance_confirmed or changed_while_replying, key=f"generate_guidance_audio_{cid}"):
                generated, mode = synthesize(audio_text, audio_language, live=True)
                st.info(mode)
                if generated:
                    target = ROOT / "assets/uploaded/consultation" / cid / ("patient-guidance-generated-" + generated.name.removesuffix(".enc"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    path = str(save_media(target, read_media(generated)))
                    audio_proposal = {"path": path, "text_hash": hashlib.sha256(audio_text.encode()).hexdigest(), "language": audio_language}
                    store().record(cid, "DOCTOR_AUDIO_ATTACHED", {**audio_proposal, "purpose": "generated complete patient guidance draft", "simulated": True}, "doctor (simulated)")
                    store().save_draft(cid, "doctor_reply", {**store().draft(cid, "doctor_reply"), "guidance_audio_path": path, "guidance_audio_proposal": audio_proposal})
                    st.rerun()
        audio_path = store().draft(cid, "doctor_reply").get("guidance_audio_path")
        audio_proposal = store().draft(cid, "doctor_reply").get("guidance_audio_proposal")
        generated_audio_stale = bool(audio_path and isinstance(audio_proposal, dict) and audio_proposal.get("path") == audio_path and
            hashlib.sha256(complete_guidance(written_guidance, audio_proposal.get("language", "Urdu")).encode()).hexdigest() != audio_proposal.get("text_hash"))
        if generated_audio_stale:
            st.warning("The written guidance changed after this audio draft was prepared. Generate a new recording or replace it with a complete human recording before saving.")
        audio_confirmed = False
        if audio_path:
            st.write("Recording proposed for this patient card")
            _show_media(audio_path, key=f"doctor_guidance_preview_{cid}")
            plan_hash = hashlib.sha256(json.dumps([audio_path, advice, local, prescription, medicine, dose, product, regimen, schedule, return_if, feeding, ors, counselling, call, response_label, referral, local_tests, lab_tests, draft["reviewed_fact_ids"], draft["reviewed_media_ids"], draft["reviewed_care_revision"], draft["reviewed_handoff_id"], draft["reviewed_fever_catalogue_hash"]], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:14]
            audio_confirmed = st.checkbox("I listened to this recording and confirm it matches this patient's current written guidance", key=f"approve_guidance_audio_{cid}_{plan_hash}")
            if st.button("Remove guidance recording from draft", key=f"remove_guidance_audio_{cid}"):
                updated = store().draft(cid, "doctor_reply")
                updated.pop("guidance_audio_path", None)
                updated.pop("guidance_audio_proposal", None)
                store().save_draft(cid, "doctor_reply", updated)
                st.rerun()
        if st.button("Save simulated doctor response", type="primary", disabled=changed_while_replying or not results_reviewed or not guidance_confirmed or generated_audio_stale or bool(audio_path) and not audio_confirmed, key=f"save_doctor_reply_{cid}"):
            if act(store().reply, cid, {"advice": advice, "diagnosis": diagnosis, "local_language": local,
                "ai_guidance_used": ai_used, "guidance_confirmed": guidance_confirmed, "guidance_confirmation_id": approval_id if guidance_confirmed else None, "prescription": prescription,
                "medication_schedule": schedule, "medicine": medicine, "dose_mg": dose or None, "prescription_product": product, "prescription_regimen": regimen, "safety_acknowledged": safety_acknowledged,
                "local_tests": local_tests, "lab_tests": lab_tests, "referral": referral if referral != "None" else "",
                "return_if": return_if, "feeding": feeding, "ors": ors, "counselling_script": counselling, "request_call": call,
                "status": response_labels[response_label], "guidance_audio_path": audio_path, "guidance_audio_confirmed": audio_confirmed,
                "repeat_tests": repeat_tests, "reviewed_order_ids": [o["order_id"] for o in returned_orders] if results_reviewed else [],
                "reviewed_fact_ids": draft["reviewed_fact_ids"], "reviewed_media_ids": draft["reviewed_media_ids"],
                "reviewed_care_revision": draft["reviewed_care_revision"], "reviewed_handoff_id": draft["reviewed_handoff_id"], "reviewed_fever_catalogue_hash": draft["reviewed_fever_catalogue_hash"], "resolved_request_ids": resolved_requests}):
                st.rerun()
        with st.expander("Review effort"):
            st.json(store().effort(cid))
    with st.expander("Photos, audio and audit"):
        for event in store().events(cid):
            if event["type"] in ("PHOTO_ATTACHED", "AUDIO_ATTACHED", "DOCTOR_AUDIO_ATTACHED", "COUGH_AUDIO_ATTACHED"):
                path = Path(event["data"]["path"])
                if path.exists():
                    _show_media(path, key=f"doctor_media_{cid}_{event['seq']}", width=300)
        _table(store().events(cid)[-20:])


@st.fragment(run_every="2s")
def doctor() -> None:
    active_cid = st.query_params.get("case") or st.session_state.get("consultation_case")
    cases = store().list_cases()
    active_case = next((case for case in cases if case["id"] == active_cid), None)
    if active_case and not active_case["sent"]:
        st.info(f"Current case {active_cid} has not been sent to the doctor yet. Generate its case packet in Consultation, confirm the read-back and medicine review, then send it. Other sent cases may appear in the queue below.")
        st.button("Open worker review for this case", on_click=_return_to_worker, args=(active_cid,))
    reviewers = store().reviewers()
    with st.expander("Simulated reviewer availability"):
        st.caption("Prototype roster controls. These statuses do not represent verified clinicians or real service availability.")
        for reviewer in reviewers:
            options = ["available", "busy", "offline"]
            key = reviewer["id"] + "_" + reviewer["updated"]
            status = st.selectbox("Availability · " + reviewer["name"], options, index=options.index(reviewer["availability"]), key="reviewer_status_" + key)
            notes = st.text_input("Roster note · " + reviewer["name"], value=reviewer["notes"], key="reviewer_note_" + key)
            if st.button("Save simulated availability", key="save_reviewer_" + key):
                if act(store().reviewer_availability, reviewer["id"], status, notes):
                    st.rerun()
    queue = [c for c in cases if c["sent"] and c["data"]["consent"] and c["stage"] != "CLOSED"]
    names = {d["id"]: d["name"] for d in reviewers}
    selected_reviewer = st.selectbox("Assigned simulated reviewer", ["All reviewers", *names], format_func=lambda rid: names.get(rid, rid), key="doctor_reviewer_filter")
    if selected_reviewer != "All reviewers":
        queue = [c for c in queue if (store().routing_status(c["id"]).get("reviewer") or {}).get("id") == selected_reviewer]
    ordering = {"RED": 0, "AMBER": 1, "GREEN": 2}
    queue.sort(key=lambda c: (ordering[store().output(c["id"], "urgency")["level"]], c["sent"]))
    if not queue:
        st.info("No sent cases. Send a worker packet or create an emergency referral first.")
        return
    queue_ids = [c["id"] for c in queue]
    selected = st.selectbox("Doctor queue · urgency then arrival time", queue_ids,
                            index=queue_ids.index(active_cid) if active_cid in queue_ids else 0,
                            key=f"doctor_queue_choice_{active_cid or 'all'}_{active_case['sent'] if active_case else 'none'}",
                            format_func=lambda cid: f"{store().output(cid, 'urgency')['level']} · {store().case(cid)['data']['name']} · {cid}")
    case = store().case(selected)
    handoff = store().handoff(selected)
    waiting = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(case["sent"])).total_seconds() // 60)) + max(0, case["clock_minutes"] - handoff["clock_minutes_at_start"])
    st.caption(f"Time since current handoff: {waiting} minutes · includes simulated clock advances")
    assigned = store().routing_status(selected).get("reviewer")
    if assigned:
        st.caption("Assigned: " + assigned["name"] + " · simulated delivery and clinician identity")
    else:
        st.warning("No available simulated reviewer matches this case's age/pregnancy group and language.")
    _doctor_case(selected)


def render(mode: str = "Consultation") -> None:
    st.title({"Doctor console": "SmartSehat · Doctor console", "Split-screen demo": "SmartSehat · Split-screen demo"}.get(mode, "SmartSehat · Doctor-ready consultation"))
    st.caption("SYNTHETIC DATA ONLY · RESEARCH PROTOTYPE · Clinical content pending review · Delivery and clinician identity simulated")
    if mode == "Split-screen demo":
        st.caption("Worker on the left, doctor on the right. On narrow screens, scroll below the worker to reach the doctor. The doctor can respond after the case is sent or an emergency referral is created.")
    urdu_layout = st.toggle("اردو ترتیب / Urdu layout", key="consultation_urdu_layout")
    direction = "rtl" if urdu_layout else "ltr"
    st.markdown(f"<style>[data-testid='stMainBlockContainer']{{font-size:18px;direction:{direction}}}[data-testid='stMainBlockContainer'] button{{min-height:48px}}textarea{{direction:{direction};font-size:18px!important}}</style>", unsafe_allow_html=True)
    with st.expander("Evidence and clinical review status"):
        from .content_review import review_pack, review_archive
        reviews = store().content_reviews()
        bundle = review_pack(include_audio=any(row["scope"] in ("fixed_audio", "danger_audio") for row in reviews))
        st.download_button("Download complete clinician/pharmacist/language review package", json.dumps(bundle, ensure_ascii=False, indent=2), file_name="smartsehat-content-review.json", mime="application/json")
        if st.button("Prepare review archive with diagrams and available fixed audio"):
            st.session_state["content_review_archive"] = review_archive()
        if st.session_state.get("content_review_archive"):
            st.download_button("Download clinical and language review archive", st.session_state["content_review_archive"], file_name="smartsehat-content-review.zip", mime="application/zip")
        if st.button("Prepare focused danger-screen / Emergency Lane review package"):
            from .danger_content import review_archive as danger_review_archive
            st.session_state["danger_review_archive"] = danger_review_archive()
        if st.session_state.get("danger_review_archive"):
            st.download_button("Download danger-screen clinical and audio review package", st.session_state["danger_review_archive"], file_name="smartsehat-danger-screen-review.zip", mime="application/zip")
        review_file = st.file_uploader("Import an external review report JSON", type=["json"], key="content_review_report")
        if review_file and st.button("Record this reported external review"):
            try:
                if act(store().report_content_review, json.loads(review_file.getvalue())):
                    st.success("Review report recorded for its exact content version. Reviewer identity is not verified by this prototype; clinical tables were not activated.")
            except (ValueError, TypeError) as exc:
                st.error(str(exc))
        reviews = store().content_reviews()
        if reviews:
            hashes = {item["scope"]: item["content_hash"] for item in bundle["targets"]}
            _table([{**row, "matches_current_content": hashes.get(row["scope"]) == row["content_hash"]} for row in reviews])
        report = ROOT / "docs/vignette_results.md"
        if report.exists():
            st.markdown("\n".join(report.read_text(encoding="utf-8").splitlines()[:15]))
        register = ROOT / "docs/clinical_content_register.md"
        if register.exists():
            st.download_button("Clinical Content Register", register.read_text(encoding="utf-8"), file_name="clinical_content_register.md")
        from .dosing import ReviewedDoseEntry
        from .epidemiology import ContextEntry
        schemas = {"format": "smartsehat-partner-data-contracts-1", "status": "pending external clinical review; no active entries supplied",
            "dose_entry_schema": ReviewedDoseEntry.model_json_schema(), "regional_context_entry_schema": ContextEntry.model_json_schema(),
            "dose_table_template": {"status": "pending", "entries": []},
            "regional_context_template": {"version": "partner-context-1", "review_status": "pending", "entries": []}}
        st.download_button("Download partner dose/context schemas and empty templates", json.dumps(schemas, ensure_ascii=False, indent=2), file_name="smartsehat-partner-data-contracts.json", mime="application/json")
    _speech_evaluation_panel()
    with st.expander("Offline safety capture and import"):
        st.download_button("Save standalone offline safety form", offline_capture(), file_name="smartsehat-offline.html", mime="text/html")
        st.caption("The saved HTML form runs independently of Streamlit and stores fictional answers on this browser. Export JSON, then import it here for confirmation. It is a safety fallback, not automatic offline sync.")
        imported = st.file_uploader("Import an offline capture for worker confirmation", type=["json"], key="offline_import")
        if imported and st.button("Create case from offline capture"):
            try:
                data = json.loads(imported.getvalue())
                if data.get("format") != "smartsehat-offline-1" or data.get("synthetic") is not True:
                    raise ValueError("Only SmartSehat fictional offline exports can be imported.")
                age = Age.from_input(float(data["age"]["value"]), data["age"]["unit"])
                cid = store().create(data.get("name", "Offline fictional case"), age, "Not recorded", bool(data.get("pregnant")), "Synthetic village", "Urdu", False)
                # Device/browser facts are proposals on import; never trust supplied urgency or citations.
                from .ai import allowed_keys
                allowed = allowed_keys() | set(config()["measurements"])
                items = [{"key": f["key"], "value": f["value"], "source": "ai_proposed", "confirmed": False,
                          "unit": f.get("unit"), "quote": "Imported offline capture — worker confirmation required"}
                         for f in data.get("facts", []) if f.get("key") in allowed]
                store().add_facts(cid, items)
                store().record(cid, "OFFLINE_IMPORTED", {"count": len(items), "supplied_urgency_ignored": True})
                _open(cid)
            except (ValueError, KeyError, TypeError) as exc:
                st.error("Import rejected: " + str(exc))
    _transfer_receiver()
    if mode == "Doctor console":
        doctor()
        return
    cases = store().list_cases()
    _worker_worklist()
    with st.expander("Open a saved case / demo controls", expanded=not cases):
        if cases:
            chosen = st.selectbox("Saved cases", [c["id"] for c in cases], format_func=lambda cid: f"{store().case(cid)['data']['name']} · {cid} · {store().case(cid)['stage']}")
            if st.button("Resume saved case"):
                _open(chosen)
        a, b, c = st.columns(3)
        if a.button("Load fictional Ayaan"):
            _open(store().load_ayaan())
        if b.button("Load Ayaan with wrong age"):
            _open(store().load_ayaan(wrong_age=True))
        if c.button("New consultation"):
            st.query_params.clear()
            st.session_state.pop("consultation_case", None)
            st.session_state["new_consultation"] = True
            st.rerun()
    cid = st.query_params.get("case") or st.session_state.get("consultation_case")
    if not cid:
        if mode == "Split-screen demo":
            st.info("Load fictional Ayaan or resume a saved case above to open both sides of the consultation.")
        _intake()
        return
    if not any(c["id"] == cid for c in cases):
        st.warning("Saved case not found. Create or select a case.")
        _intake()
        return
    if st.button("Demo: advance clock 10 minutes"):
        store().advance_clock(cid)
        st.rerun()
    if mode == "Split-screen demo":
        left, right = st.columns([1, 1])
        with left:
            st.markdown("### Worker phone")
            worker(cid)
        with right:
            st.markdown("### Doctor console · simulated")
            if store().case(cid)["sent"]:
                _doctor_case(cid)
            else:
                st.info("This case has not been sent yet. In the worker panel, generate the case packet, confirm the patient read-back, worker consent and medicine review, then select Confirm read-back and send to doctor. The doctor panel opens automatically after handoff.")
    else:
        worker(cid)
