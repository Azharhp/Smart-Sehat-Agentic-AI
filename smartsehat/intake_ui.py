"""Incremental Consultation widgets backed by versioned intake definitions.

Pass an encounter-owned ``drafts`` mapping and an encounter-scoped ``key``.
Callbacks retain presentation drafts when Streamlit removes inactive widgets.
Returned actions are requests only: the caller must authorize, validate, persist
and confirm them through the backend. This module never accesses providers,
media files, databases or clinical interpretation thresholds.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

import streamlit as st

from .clinical_intake import question_definitions, relevant_questions, sanitize_answers


_ASSERTIONS = {"Yes": "PRESENT", "No": "ABSENT", "Unknown": "UNKNOWN"}
_LANGUAGES = {"Unknown": "unknown", "Urdu": "ur", "English": "en",
              "Hindi / Devanagari": "hi", "Mixed": "mixed"}


def _remember(drafts: dict, name: str, widget_key: str) -> None:
    drafts[name] = deepcopy(st.session_state.get(widget_key))


def _index(options: list, value: Any) -> int | None:
    return options.index(value) if value in options else None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def complaint_editor(drafts: dict, key: str) -> dict:
    """Return source, typed_text and declared_language; uploads remain app-owned."""
    options = ["Typed complaint", "Audio recording"]
    source_key = f"{key}:complaint_source"
    selected = st.radio(
        "Complaint source", options, index=_index(options, drafts.get("complaint_source")),
        key=source_key, on_change=_remember,
        args=(drafts, "complaint_source", source_key),
    )
    language_key = f"{key}:declared_language"
    language_labels = list(_LANGUAGES)
    language = st.selectbox(
        "Declared language", language_labels,
        index=_index(language_labels, drafts.get("declared_language", "Unknown")),
        key=language_key, on_change=_remember,
        args=(drafts, "declared_language", language_key),
    )
    typed = None
    if selected == "Typed complaint":
        text_key = f"{key}:typed_complaint"
        typed = st.text_area(
            "Patient complaint — type the original statement",
            value=drafts.get("typed_complaint", ""), key=text_key,
            on_change=_remember, args=(drafts, "typed_complaint", text_key),
        )
        st.caption("This original statement will remain available after correction and confirmation.")
    elif selected == "Audio recording":
        st.caption("Attach the audio through this encounter's secure audio uploader.")
    return {"source": {"Typed complaint": "typed", "Audio recording": "audio"}.get(selected),
            "typed_text": typed, "declared_language": _LANGUAGES.get(language, "unknown")}


def history_review(history: dict, drafts: dict, key: str) -> dict | None:
    """Return ``{action: confirm, transcript: corrected}`` on explicit acceptance."""
    history = history or {}
    st.subheader("Step 2 — Review the patient history")
    st.markdown("**RAW — original source text**")
    raw = history.get("raw_text")
    if isinstance(raw, str) and raw.strip():
        st.text(raw)
    else:
        st.warning("Original text is unavailable. Enter the patient's statement manually before acceptance.")
    if history.get("degraded_reason"):
        st.warning(str(history["degraded_reason"]))
    source = history.get("source") or "unknown"
    language = history.get("declared_language") or history.get("detected_language") or "unknown"
    st.caption(f"Source: {source} · Language: {language} · History version: {history.get('version', 'not recorded')}")
    if history.get("status") == "ACCEPTED":
        st.markdown("**CORRECTED — worker-reviewed text**")
        st.text(history.get("corrected_text") or "Not recorded")
        st.markdown("**ACCEPTED — confirmed clinical history**")
        st.text(history.get("accepted_text") or "Not recorded")
        st.caption(f"Confirmed by {history.get('confirming_actor_id') or 'not recorded'} · "
                   f"{history.get('confirmed_at') or 'time not recorded'}")
        return None
    draft_name = f"corrected_history:{history.get('version', 'initial')}"
    corrected_key = f"{key}:{draft_name}"
    initial = history.get("corrected_text")
    if not isinstance(initial, str):
        initial = raw if isinstance(raw, str) else ""
    corrected = st.text_area(
        "CORRECTED — review and edit the statement",
        value=drafts.get(draft_name, initial), key=corrected_key,
        on_change=_remember, args=(drafts, draft_name, corrected_key),
    )
    st.caption("Acceptance records your confirmed text. The original source text is preserved.")
    if st.button("Accept this clinical history", key=f"{key}:accept_history",
                 type="primary", disabled=not corrected.strip()):
        return {"action": "confirm", "transcript": corrected}
    return None


def _answer_changed(drafts: dict, bucket: str, question_id: str, field: str,
                    widget_key: str, definitions: list[dict]) -> None:
    answers = drafts.setdefault(bucket, {})
    answer = deepcopy(answers.get(question_id) or {"assertion": "UNASSESSED"})
    selected = st.session_state.get(widget_key)
    if field == "boolean":
        old = answer.get("assertion")
        answer = {"assertion": _ASSERTIONS.get(selected, "UNASSESSED"),
                  "value": True if selected == "Yes" else False if selected == "No" else None,
                  "unit": None}
        if old != answer["assertion"]:
            pending = [question_id]
            while pending:
                parent = pending.pop()
                for definition in definitions:
                    if (definition.get("depends_on") or {}).get("question_id") == parent:
                        child = definition["question_id"]
                        answers.pop(child, None)
                        pending.append(child)
    elif field == "numeric_assertion":
        answer["assertion"] = {"Enter value": "PRESENT", "Unknown": "UNKNOWN"}.get(selected, "UNASSESSED")
        if answer["assertion"] != "PRESENT":
            answer.update(value=None, unit=None)
    else:
        answer[field] = selected
    answers[question_id] = answer


def _question(definition: dict, drafts: dict, bucket: str, key: str,
              definitions: list[dict]) -> None:
    qid = definition["question_id"]
    answer = drafts.setdefault(bucket, {}).get(qid) or {}
    base = f"{key}:{definition['definition_version']}:{qid}"
    label = definition["label"]
    if definition.get("required"):
        label += " (required information)"
    elif definition.get("important"):
        label += " (important information)"
    if definition["input_type"] == "YES_NO_UNKNOWN":
        choices = list(_ASSERTIONS)
        selection = next((name for name, assertion in _ASSERTIONS.items()
                          if assertion == answer.get("assertion")), None)
        widget_key = base + ":assertion"
        st.radio(label, choices, index=_index(choices, selection), key=widget_key,
                 horizontal=True, on_change=_answer_changed,
                 args=(drafts, bucket, qid, "boolean", widget_key, definitions))
        return
    if definition["input_type"] not in {"DURATION", "NUMBER_UNIT", "NUMBER", "CHOICE", "TEXT"}:
        st.warning(f"{label}: this input type is not supported by the current interface.")
        return
    if definition["input_type"] in {"DURATION", "NUMBER_UNIT", "NUMBER"}:
        choices = ["Enter value", "Unknown"]
        selection = {"PRESENT": "Enter value", "UNKNOWN": "Unknown"}.get(answer.get("assertion"))
        status_key = base + ":assertion"
        status = st.radio(label, choices, index=_index(choices, selection), key=status_key,
                          horizontal=True, on_change=_answer_changed,
                          args=(drafts, bucket, qid, "numeric_assertion", status_key, definitions))
        if status != "Enter value":
            return
        columns = st.columns(2)
        number_key = base + ":value"
        columns[0].number_input(
            f"{definition['label']} — value", min_value=0.0,
            value=_number(answer.get("value")), key=number_key,
            on_change=_answer_changed,
            args=(drafts, bucket, qid, "value", number_key, definitions),
        )
        if definition["input_type"] != "NUMBER":
            unit_key = base + ":unit"
            units = list(definition.get("allowed_units") or [])
            columns[1].selectbox(
                f"{definition['label']} — unit", units,
                index=_index(units, answer.get("unit")), key=unit_key,
                on_change=_answer_changed,
                args=(drafts, bucket, qid, "unit", unit_key, definitions),
            )
        st.caption("The entered value and unit are retained. Blank or unknown measurements are not normal results.")
        return
    # Future declared definition types can be displayed without inventing questions.
    st.info(f"{label}: record this item through the configured clinical review process.")


def intake_editor(recommendation: dict, patient: dict, drafts: dict, key: str) -> dict | None:
    """Render backend-defined intake and return a backend action on submission.

    The caller may collect medication context alongside this helper and attach
    it to the submitted action. No observation is marked confirmed by this UI.
    """
    recommendation = recommendation or {}
    st.write(recommendation.get("reason") or "No pathway recommendation is available.")
    version = recommendation.get("pathway_version") or "unavailable"
    pathway_id = recommendation.get("pathway_id")
    if recommendation.get("status") == "UNSUPPORTED_SCOPE" or not pathway_id:
        st.warning("UNSUPPORTED SCOPE — no configured intake pathway applies. Clinical review is required.")
        note_name = f"unsupported_reason:{version}"
        note_key = f"{key}:{note_name}"
        reason = st.text_area("Clinical review / referral reason", value=drafts.get(note_name, ""),
                              key=note_key, on_change=_remember,
                              args=(drafts, note_name, note_key))
        if st.button("Record unsupported scope for clinical review", key=f"{key}:unsupported",
                     disabled=len(reason.strip()) < 10):
            return {"action": "acknowledge_unsupported", "reason": reason}
        return None
    st.write(f"Suggested pathway: {str(pathway_id).replace('_', ' ')}")
    st.caption(f"Definition version: {version} · Selection method: {recommendation.get('selection_method', 'not recorded')}")
    confirmed_name = f"pathway_confirmed:{version}:{pathway_id}"
    confirmed_key = f"{key}:{confirmed_name}"
    confirmed = st.checkbox(
        "I confirm this supported pathway fits the accepted complaint",
        value=drafts.get(confirmed_name, False), key=confirmed_key,
        on_change=_remember, args=(drafts, confirmed_name, confirmed_key),
    )
    st.caption("Worker confirmation is required. Without confirmation the pathway "
               "remains pending.")
    if recommendation.get("status") == "AMBIGUOUS":
        st.warning("The match is ambiguous. Confirm the pathway only if it fits; otherwise seek clinical review.")
    bucket = f"intake_answers:{version}:{pathway_id}"
    answers = drafts.setdefault(bucket, {})
    definitions = question_definitions()
    # Retire inactive drafts so a reappearing dependent question is not silently reconfirmed.
    active = {item["question_id"] for item in relevant_questions(answers, patient)}
    for qid in list(answers):
        if qid not in active:
            answers.pop(qid, None)
    st.caption("Leave an item untouched if unassessed. Select Unknown only when its answer is unknown. "
               "Saving explicitly confirms the recorded observations; no clinical values are inferred.")
    for definition in relevant_questions(answers, patient):
        with st.container(border=True):
            _question(definition, drafts, bucket, key, definitions)
    if st.button("Confirm structured information and assess", type="primary",
                 key=f"{key}:submit_intake", disabled=not confirmed):
        return {"action": "submit_intake", "selected_pathway_id": pathway_id,
                "pathway_confirmed": confirmed,
                "answers": deepcopy(sanitize_answers(drafts[bucket], patient))}
    return None


def render_assessment(assessment: dict, pathway: dict | None = None) -> None:
    """Present backend safety findings without diagnosing or interpreting values."""
    assessment, pathway = assessment or {}, pathway or {}
    st.subheader("Step 4 — Current assessment")
    if pathway:
        st.write(f"Pathway: {(pathway.get('pathway_id') or 'No supported pathway').replace('_', ' ')}")
        st.caption(f"Version: {pathway.get('pathway_version', 'not recorded')} · {pathway.get('status', 'unconfirmed')}")
        if pathway.get("reason"):
            st.write(pathway["reason"])
    status = assessment.get("status") or assessment.get("level") or "UNASSESSED"
    messages = {
        "RED": (st.error, "RED — urgent clinical assessment and the configured referral workflow are required."),
        "YELLOW": (st.warning, "YELLOW — a configured finding requires timely clinical review."),
        "NO_CONFIGURED_FLAG": (st.info, "NO CONFIGURED FLAG — no rule matched within the assessed scope; this does not establish patient safety."),
        "INSUFFICIENT_DATA": (st.warning, "INSUFFICIENT DATA — important information is missing or unknown."),
        "UNSUPPORTED_SCOPE": (st.warning, "UNSUPPORTED SCOPE — this complaint requires clinical review outside the configured pathway."),
        "UNASSESSED": (st.info, "UNASSESSED — no confirmed assessment is available."),
    }
    show, message = messages.get(status, (st.warning, f"Unrecognized assessment status: {status}. Clinical review is required."))
    show(message)
    for finding in assessment.get("findings") or []:
        with st.container(border=True):
            st.write(finding.get("reason") or finding.get("description") or finding.get("rule_id") or "Configured safety finding")
            evidence = finding.get("evidence") or []
            if isinstance(evidence, dict):
                evidence = [evidence]
            for item in evidence:
                if not isinstance(item, dict):
                    st.text(str(item))
                    continue
                if item.get("text") or item.get("matched_text"):
                    st.text(item.get("text") or item["matched_text"])
                if item.get("concept"):
                    st.write(f"{str(item['concept']).replace('_', ' ')}: {item.get('assertion', '')} "
                             f"{item.get('value') if item.get('value') is not None else ''} {item.get('unit') or ''}")
                if item.get("observation_id"):
                    st.caption(f"Observation: {item['observation_id']}")
            if finding.get("rule_id"):
                st.caption(f"Rule: {finding['rule_id']} · Version: {finding.get('rule_version') or assessment.get('rule_version', 'not recorded')}")
    missing = assessment.get("missing_information") or []
    if missing:
        st.markdown("**Information still needed**")
        for item in missing:
            if isinstance(item, dict):
                st.write(f"• {item.get('label') or item.get('concept') or item.get('question_id')}: {item.get('status', 'MISSING')}")
            else:
                st.write(f"• {item}")
    for item in assessment.get("unresolved_evidence") or []:
        st.warning(item.get("reason") or item.get("text") or "Unresolved safety evidence requires review."
                   if isinstance(item, dict) else str(item))
    st.caption("Only configured rules are assessed. These results do not establish a diagnosis or authorize treatment.")
    for limitation in assessment.get("limitations") or []:
        st.write(f"• {limitation}")
    with st.expander("Assessment provenance"):
        st.caption(f"Rule version: {assessment.get('rule_version', 'not recorded')}")
        st.caption(f"Snapshot version: {assessment.get('snapshot_version', 'not recorded')}")
        st.caption(f"Assessed at: {assessment.get('assessed_at') or 'not recorded'}")
