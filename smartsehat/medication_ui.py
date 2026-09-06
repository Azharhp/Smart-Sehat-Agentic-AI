"""Shared medication review widgets; no database, model, or network operations.

Callers must use encounter-specific keys and explicitly persist/recheck returned
inputs. Pack strength is never interpreted as an administered dose here.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any
from urllib.parse import urlsplit

import pandas as pd
import streamlit as st


_TEXT_FIELDS = ("name", "strength", "route", "indication")
_NUMBER_FIELDS = ("dose_mg", "frequency_per_day", "duration_days")
_EDITOR_FIELDS = ("id", "name", "strength", "dose_mg", "frequency_per_day",
                  "duration_days", "route", "indication")
_TRISTATE_LABELS = ("Unknown", "No", "Yes")


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _number(value: Any, *, allow_zero: bool = False) -> float | None:
    """Return an optional finite number, never NaN, infinity, or a boolean."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or result < 0 or (result == 0 and not allow_zero):
        return None
    return result


def _clean_medicines(records: list[dict]) -> list[dict]:
    """Normalize editor rows to JSON-safe values without inferring a dose."""
    result = []
    for record in records:
        row = {field: _text(record.get(field)) or None for field in _TEXT_FIELDS}
        row.update({field: _number(record.get(field)) for field in _NUMBER_FIELDS})
        # A newly inserted row may have a generated ID but no clinical input.
        if not any(value is not None for value in row.values()):
            continue
        identifier = _text(record.get("id"))
        if identifier:
            row["id"] = identifier
        result.append(row)
    return result


def medication_editor(medicines: list[dict], key: str) -> list[dict]:
    """Edit proposed medicines with dynamic rows and explicit dose units."""
    records = []
    for medicine in medicines or []:
        row = {field: _text(medicine.get(field)) or None
               for field in ("id", *_TEXT_FIELDS)}
        row.update({field: _number(medicine.get(field)) for field in _NUMBER_FIELDS})
        records.append(row)
    frame = pd.DataFrame(records, columns=_EDITOR_FIELDS)
    for field in _NUMBER_FIELDS:
        frame[field] = pd.array(frame[field], dtype="Float64")
    st.caption(
        "Add or remove rows below. Dose is the amount administered each time in mg; "
        "pack/label strength is separate and is never used to infer a dose. "
        "Leave unknown values blank."
    )
    edited = st.data_editor(
        frame,
        key=f"{key}:medicines",
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "id": None,
            "name": st.column_config.TextColumn("Medicine name"),
            "strength": st.column_config.TextColumn("Pack / label strength"),
            "dose_mg": st.column_config.NumberColumn(
                "Administered dose (mg / dose)", min_value=0.001,
                help="Actual amount per administration, not tablet/pack strength.",
            ),
            "frequency_per_day": st.column_config.NumberColumn(
                "Administrations / day", min_value=0.001,
                help="Number of administrations per day; leave blank if unknown.",
            ),
            "duration_days": st.column_config.NumberColumn(
                "Duration (days)", min_value=0.001,
            ),
            "route": st.column_config.TextColumn("Route"),
            "indication": st.column_config.TextColumn("Reason for medicine"),
        },
    )
    return _clean_medicines(edited.to_dict(orient="records"))


def _split_entries(value: str, *, comma: bool = False) -> list[str]:
    separator = "," if comma else "\n"
    return [part.strip() for part in value.split(separator) if part.strip()]


def _current_names(medicines: Any) -> str:
    if medicines is None:
        return ""
    if isinstance(medicines, str):
        return medicines.strip()
    return "\n".join(
        name for medicine in medicines
        if (name := _text(medicine.get("name") if isinstance(medicine, dict) else medicine))
    )


def _allergy_names(allergies: Any) -> str:
    if allergies is None:
        return ""
    if isinstance(allergies, str):
        return allergies.strip()
    return ", ".join(
        name for allergy in allergies
        if (name := _text(allergy.get("substance") if isinstance(allergy, dict) else allergy))
    )


def _invalidate_confirmation(confirmation_key: str) -> None:
    st.session_state[confirmation_key] = False


def _tristate(label: str, value: Any, key: str, confirmation_key: str) -> bool | None:
    index = 2 if value is True else 1 if value is False else 0
    selected = st.selectbox(
        label, _TRISTATE_LABELS, index=index, key=key,
        on_change=_invalidate_confirmation, args=(confirmation_key,),
    )
    return {"Unknown": None, "No": False, "Yes": True}[selected]


def patient_context_editor(context: dict, key: str) -> dict:
    """Collect confirmed context, preserving unknown vs explicitly absent data.

    Existing structured current-medicine doses survive unchanged names. Editing
    names returns a plain name list so old dose details are not misattributed.
    Editing any clinical field clears the encounter confirmation checkbox.
    """
    original = deepcopy(context or {})
    updated = deepcopy(original)
    confirm_key = f"{key}:context_confirmed"
    changed = {"on_change": _invalidate_confirmation, "args": (confirm_key,)}
    st.caption(
        "Drug rules cover a limited set of medicines and checks. Clinician "
        "confirmation is needed. Unknown history is different from a confirmed 'none'."
    )
    left, right = st.columns(2)
    updated["age"] = left.number_input(
        "Age (years; blank if unknown)", min_value=0.0,
        value=_number(original.get("age"), allow_zero=True), key=f"{key}:age",
        **changed,
    )
    updated["weight_kg"] = right.number_input(
        "Weight (kg; blank if unknown)", min_value=0.001,
        value=_number(original.get("weight_kg")), key=f"{key}:weight_kg", **changed,
    )

    allergies = original.get("allergies")
    allergy_text = _allergy_names(allergies)
    allergy_known = st.checkbox(
        "Allergy history confirmed (empty list means no known allergies)",
        value=allergies is not None, key=f"{key}:allergies_known", **changed,
    )
    entered_allergies = st.text_input(
        "Allergies (comma separated)", value=allergy_text,
        key=f"{key}:allergies", disabled=not allergy_known, **changed,
        help="Existing allergy reaction and type details are retained while names are unchanged. "
             "Changed names require the reaction to be clarified during review.",
    )
    if not allergy_known:
        updated["allergies"] = None
    elif entered_allergies == allergy_text and allergies is not None:
        updated["allergies"] = deepcopy(allergies)
    else:
        updated["allergies"] = _split_entries(entered_allergies, comma=True)
    for allergy in allergies or []:
        if isinstance(allergy, dict):
            details = [f"{field}: {_text(allergy.get(field))}" for field in ("type", "reaction")
                       if _text(allergy.get(field))]
            if details:
                st.caption(f"Recorded {_text(allergy.get('substance'))}: {'; '.join(details)}")

    medicines = original.get("current_meds")
    original_names = _current_names(medicines)
    medicines_known = st.checkbox(
        "Current medicines confirmed (empty list means taking none)",
        value=medicines is not None, key=f"{key}:current_meds_known", **changed,
    )
    entered_names = st.text_area(
        "Current medicine names (one per line)", value=original_names,
        key=f"{key}:current_meds", disabled=not medicines_known, **changed,
        help="Existing dose details are retained while names are unchanged. "
             "After editing names, confirm their dosing details in the medicine review.",
    )
    if not medicines_known:
        updated["current_meds"] = None
    elif entered_names == original_names and medicines is not None:
        updated["current_meds"] = deepcopy(medicines)
    else:
        updated["current_meds"] = _split_entries(entered_names)

    updated["pregnant"] = _tristate(
        "Pregnant", original.get("pregnant"), f"{key}:pregnant", confirm_key,
    )
    if updated["pregnant"] is True:
        updated["gestational_weeks"] = st.number_input(
            "Gestational age (weeks; blank if unknown)", min_value=0.0,
            value=_number(original.get("gestational_weeks"), allow_zero=True),
            key=f"{key}:gestational_weeks", **changed,
        )
        updated["aspirin_prescribed"] = _tristate(
            "Clinician prescribed low-dose aspirin 81 mg in this pregnancy",
            original.get("aspirin_prescribed"), f"{key}:aspirin_prescribed", confirm_key,
        )
        st.caption("This records reported prescribing context; it does not verify a clinician's identity.")
    else:
        updated["gestational_weeks"] = None
        updated["aspirin_prescribed"] = None
    updated["renal_impairment"] = _tristate(
        "Renal impairment", original.get("renal_impairment"),
        f"{key}:renal_impairment", confirm_key,
    )
    updated["liver_disease"] = _tristate(
        "Liver disease", original.get("liver_disease"), f"{key}:liver_disease", confirm_key,
    )
    updated["context_confirmed"] = st.checkbox(
        "I reviewed this patient context for this encounter",
        value=original.get("context_confirmed") is True, key=confirm_key,
    )
    return updated


def _format_number(value: Any) -> str:
    number = _number(value, allow_zero=True)
    return f"{number:g}" if number is not None else "not entered"


def _items(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def render_medication_review(report: dict) -> None:
    """Show the bounded rule review and its evidence without claiming safety."""
    report = report or {}
    status = report.get("status")
    status_messages = {
        "HOLD": (st.error, "HOLD — resolve the blocking medication findings before approval."),
        "REVIEW_REQUIRED": (st.warning, "REVIEW REQUIRED — medication findings or missing context need review."),
        "NO_FLAGS_IN_SUPPORTED_CHECKS": (st.info, "NO FLAGS IN SUPPORTED CHECKS — clinician confirmation is still needed."),
        "NO_MEDICINES_PROPOSED": (st.info, "NO MEDICINES PROPOSED — no proposed medicine regimen was assessed."),
    }
    renderer, message = status_messages.get(
        status, (st.warning, "Medication review is unavailable or incomplete. Request a new review."),
    )
    renderer(message)
    st.caption("Drug rules cover a limited set of medicines and checks; this report is not a clinical approval.")
    current = report.get("current_medicines") or []
    if current:
        st.markdown("**Reported current medicines**")
        for medicine in current:
            if isinstance(medicine, dict):
                st.write(
                    f"{_text(medicine.get('name')) or 'Unnamed medicine'} · "
                    f"Administered dose (mg / dose): {_format_number(medicine.get('dose_mg'))} · "
                    f"Administrations / day: {_format_number(medicine.get('frequency_per_day'))}"
                )
            else:
                st.write(_text(medicine))
        st.caption("Current medicines are reported history. A review finding is not an instruction to stop a medicine.")
    sources: list[dict] = []
    displayed_flag_ids: set[str] = set()
    for index, medicine in enumerate(report.get("medicines") or [], start=1):
        with st.container(border=True):
            name = _text(medicine.get("name")) or "Unnamed medicine"
            st.markdown(f"**{index}. {name}**")
            if medicine.get("generic"):
                st.write(f"Generic: {medicine['generic']}")
            st.write(
                f"Pack / label strength: {_text(medicine.get('strength')) or 'not entered'} · "
                f"Administered dose (mg / dose): {_format_number(medicine.get('dose_mg'))} · "
                f"Administrations / day: {_format_number(medicine.get('frequency_per_day'))}"
            )
            details = []
            if medicine.get("duration_days") is not None:
                details.append(f"Duration: {_format_number(medicine['duration_days'])} days")
            if medicine.get("route"):
                details.append(f"Route: {medicine['route']}")
            if medicine.get("indication"):
                details.append(f"Reason: {medicine['indication']}")
            if details:
                st.caption(" · ".join(details))
            flags = medicine.get("flags") or []
            if not flags:
                st.caption("No flags in the checks applied to this medicine.")
            for flag in flags:
                if flag.get("id"):
                    displayed_flag_ids.add(str(flag["id"]))
                severity = _text(flag.get("severity")) or "REVIEW"
                blocking = " — blocking" if flag.get("blocking") else ""
                st.write(f"{severity}{blocking}: {flag.get('reason') or flag.get('message') or flag.get('code') or 'Review required'}")
                for source in _items(flag.get("evidence")):
                    if isinstance(source, dict):
                        sources.append(source)
    # Context-level findings may not belong to a single medicine.
    for flag in report.get("flags") or []:
        if flag.get("id") and str(flag["id"]) in displayed_flag_ids:
            continue
        st.write(f"Review: {flag.get('reason') or flag.get('message') or flag.get('code')}")
        sources.extend(source for source in _items(flag.get("evidence")) if isinstance(source, dict))

    not_assessed = report.get("not_assessed") or []
    if not_assessed:
        st.markdown("**Not assessed**")
        for item in not_assessed:
            st.write(f"• {item}")
    with st.expander("Review scope and evidence"):
        st.caption(f"Rule version: {report.get('ruleset_version') or 'not recorded'}")
        coverage = report.get("coverage") or {}
        for name, value in coverage.items():
            label = str(name).replace("_", " ").capitalize()
            st.write(f"{label}: {value}")
        for limitation in report.get("limitations") or []:
            st.write(f"• {limitation}")
        seen = set()
        for source in sources:
            identifier = _text(source.get("id"))
            title = _text(source.get("title")) or identifier or "Source"
            url = _text(source.get("url"))
            section = _text(source.get("section"))
            signature = (identifier, url, section)
            if signature in seen:
                continue
            seen.add(signature)
            st.write(title)
            if identifier or section:
                st.caption(" · ".join(value for value in (identifier, section) if value))
            try:
                parsed = urlsplit(url)
                external = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
            except ValueError:
                external = False
            if external:
                st.link_button("Open source", url)
            elif identifier == "SMARTSEHAT_RULE_SCOPE" or url:
                st.caption("Application review policy — see the medication evidence document.")
            else:
                st.caption("Source link was not supplied.")
