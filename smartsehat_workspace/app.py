"""Streamlit UI for SmartSehat — final edition (follow-up history engine).

The clinical flow now completes the history BEFORE assessment:
  STT → transcript gate (review + ASK follow-up questions + record
  answers) → enriched transcript → triage / vision / reasoning / Dawa
  — so the deterministic rules see the full history (e.g., an answer
  of "bleeding" in pregnancy flips the case to RED).

Also carries: emergency dispatch packet (RED cases), doctor review
loop (async store-and-forward telemedicine), Dawa Check tab, District
View tab.

Launch: python -m streamlit run smartsehat_workspace/app.py  (project root)
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

from smartsehat_workspace import audit
from smartsehat_workspace.graph import resume_case, run_case
from smartsehat_workspace.router import ModelRouter, load_config
from smartsehat_workspace import dawa_graph

st.set_page_config(page_title="SmartSehat", layout="wide")

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "assets" / "uploaded"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

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


def _save_upload(uploaded, fixed_name: str) -> str | None:
    if uploaded is None:
        return None
    dest = UPLOAD_DIR / fixed_name
    with open(dest, "wb") as f:
        f.write(uploaded.getbuffer())
    return str(dest)


def _save_dawa_image(uploaded) -> str:
    """Keep the actual image format and avoid overwriting another check's photo."""
    from io import BytesIO
    import hashlib
    from PIL import Image
    data = uploaded.getvalue()
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("Use a medicine photo smaller than 20 MB.")
    try:
        with Image.open(BytesIO(data)) as picture:
            image_format = picture.format
            picture.verify()
    except Exception as exc:
        raise ValueError("This file is not a readable JPG or PNG image.") from exc
    if image_format not in ("JPEG", "PNG"):
        raise ValueError("Use a JPG or PNG medicine photo.")
    suffix = ".png" if image_format == "PNG" else ".jpg"
    dest = UPLOAD_DIR / ("dawa-" + hashlib.sha256(data).hexdigest()[:24] + suffix)
    dest.write_bytes(data)
    return str(dest)


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
# Sidebar / shared helpers
# ======================================================================

def _sidebar() -> tuple[str, str]:
    st.sidebar.title("SmartSehat")
    workflows = ["Consultation", "Doctor console", "Split-screen demo", "Dawa Check", "District View"]
    st.session_state.setdefault("workflow", st.session_state.get("_workflow", "Consultation"))
    workflow = st.sidebar.radio("Workflow", workflows, key="workflow")
    st.session_state["_workflow"] = workflow
    if workflow == "District View":
        st.sidebar.caption("District overview · all saved case sources")
        st.sidebar.caption("Use the dashboard filters to view consultations, legacy encounters and their mapped locations.")
        return "", "PM-2213"
    if workflow in ("Consultation", "Doctor console", "Split-screen demo"):
        st.sidebar.caption("Research prototype · fictional cases only")
        st.sidebar.caption("Clinical thresholds pending review. Doctor identity and delivery are simulated.")
        return "", "PM-2213"
    st.sidebar.caption("Medicine image review · worker confirmation required")

    st.sidebar.subheader("Provider status")
    for name, ok in _router().provider_health().items():
        st.sidebar.write(f"• {name}: {'✅ configured' if ok else '⚠️ not configured'}")
    st.sidebar.caption("Configured keys do not confirm a working service connection.")

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
    return patient_id, paramedic_id


# Triage / traffic-light emoji mapping — kept consistent with design.md §6.1
# (RED 🔴 · YELLOW 🟡 · GREEN 🟢 · unknown ⚪).
_TRIAGE_EMOJI = {"RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢"}


def _pretty_event(event_type: str, payload: dict) -> str:
    """Render one audit event as clean, human-readable Markdown.

    Replaces the raw JSON dump in the timeline with emoji + bold labels +
    bullet lines per known event type. Every key is read defensively with
    ``.get`` so an unexpected or partial payload shape can never crash the
    render — unknown events fall back to a truncated key/value summary.
    """
    p = payload if isinstance(payload, dict) else {"value": payload}
    et = (event_type or "").upper()

    def g(key: str, default=""):
        v = p.get(key, default)
        return default if v is None else v

    def as_list(v) -> list:
        if v is None:
            return []
        if isinstance(v, list):
            return v
        return [v]

    def quote(text: str) -> list[str]:
        text = str(text or "")
        return [f"> {ln}" for ln in (text.splitlines() or [text])]

    # ── Triage ────────────────────────────────────────────────────────
    if et == "TRIAGE_DECISION":
        level = str(g("triage_level", "—")).upper()
        emoji = _TRIAGE_EMOJI.get(level, "⚪")
        lines = [f"🚦 Triage: {emoji} **{level}**"]
        rules = as_list(g("rules", []))
        if rules:
            lines.append(f"- **Rules:** {', '.join(str(r) for r in rules)}")
        if g("instruction"):
            lines.append(f"- **Instruction:** {g('instruction')}")
        return "\n".join(lines)

    # ── AI reasoning / differential ───────────────────────────────────
    if et == "REASONING_RESULT":
        conf = g("confidence", None)
        head = "🧠 AI assessment"
        if conf not in (None, ""):
            try:
                head += f" (confidence {float(conf):.2f})"
            except Exception:
                head += f" (confidence {conf})"
        lines = [head]
        for d in as_list(g("ddx", [])):
            if isinstance(d, dict):
                cites = ", ".join(str(c) for c in (d.get("citations") or []))
                lines.append(
                    f"- **{d.get('diagnosis', '?')}** "
                    f"({d.get('probability', '?')}) — _cites: {cites or 'none'}_")
            else:
                lines.append(f"- {d}")
        if g("management"):
            lines.append(f"- **Management:** {g('management')}")
        if len(lines) == 1:
            lines.append("- _No assessment details recorded._")
        return "\n".join(lines)

    # ── Health-worker decision ────────────────────────────────────────
    if et == "HITL_DECISION":
        lines = [f"🩺 Worker decision: **{g('action', '—')}**"]
        if g("reason"):
            lines.append(f"- **Reason:** {g('reason')}")
        if g("hitl_status"):
            lines.append(f"- **Status:** {g('hitl_status')}")
        if g("edits_made"):
            lines.append(f"- **Edits made:** {g('edits_made')}")
        return "\n".join(lines)

    # ── Confirmed voice transcript ────────────────────────────────────
    if et == "VOICE_TRANSCRIPT_CONFIRMED":
        text = g("transcript", "") or g("transcript_confirmed", "")
        head = "🎤 Transcript (confirmed)"
        if g("paramedic_corrected", False):
            head += " — _edited by health worker_"
        lines = [head]
        lines += quote(text) if text else ["> _(empty)_"]
        return "\n".join(lines)

    # ── X-ray vision read ─────────────────────────────────────────────
    if et == "VISION_FINDING":
        lines = ["🩻 X-ray patterns"]
        for f in as_list(g("findings", [])):
            if isinstance(f, dict):
                lines.append(
                    f"- **{f.get('pattern', '?')}** @ "
                    f"{f.get('location', '?')} ({f.get('score', '?')})")
            else:
                lines.append(f"- {f}")
        if g("interpretation"):
            lines.append(f"- **Interpretation:** {g('interpretation')}")
        rec = as_list(g("recommend", []))
        if rec:
            lines.append(f"- **Recommend:** {', '.join(str(r) for r in rec)}")
        if len(lines) == 1:
            lines.append("- _No imaging findings recorded._")
        return "\n".join(lines)

    # ── Medicines extracted from Rx ───────────────────────────────────
    if et == "RX_EXTRACTED":
        lines = ["💊 Medicines extracted"]
        for m in as_list(g("medicines", [])):
            if isinstance(m, dict):
                strength = m.get("strength") or m.get("dose") or ""
                lines.append(f"- **{m.get('name', '?')}**"
                             + (f" {strength}" if strength else ""))
            else:
                lines.append(f"- {m}")
        if len(lines) == 1:
            lines.append("- _No medicines listed._")
        return "\n".join(lines)

    # ── Safety flags (two known payload shapes) ───────────────────────
    if et == "MEDICINE_FLAGGED":
        light = str(g("light", "")).upper()
        emoji = _TRIAGE_EMOJI.get(light, "🚦")
        lines = ["🚦 Safety flags" + (f" {emoji}" if light else "")]

        def flag_bullets(flags) -> None:
            for fl in as_list(flags):
                if isinstance(fl, dict):
                    lines.append(
                        f"    - **{fl.get('code', '?')}** "
                        f"({fl.get('severity', '?')}) — {fl.get('reason', '')}")
                else:
                    lines.append(f"    - {fl}")

        if g("name"):  # dawa_graph shape: single medicine + flags
            head = f"- {emoji} **{g('name')}**"
            if g("generic"):
                head += f" (`{g('generic')}`)"
            lines.append(head)
            flag_bullets(g("flags", []))
        for row in as_list(g("grid", [])):  # district-seed shape: grid rows
            if isinstance(row, dict):
                med = row.get("medicine") or row.get("name") or "?"
                warn = row.get("warning") or ""
                lines.append(f"- {emoji} **{med}**"
                             + (f" — {warn}" if warn else ""))
                flag_bullets(row.get("flags"))
            else:
                lines.append(f"- {row}")
        if len(lines) == 1:
            lines.append("- _No flag details recorded._")
        return "\n".join(lines)

    # ── Flag dismissed ────────────────────────────────────────────────
    if et == "FLAG_DISMISSED":
        med = g("medicine") or g("name") or "—"
        code = g("flag_code", "")
        out = f"✅ Flag dismissed: **{med}**"
        if code:
            out += f" · `{code}`"
        if g("reason"):
            out += f" — {g('reason')}"
        return out

    # ── Dawa check finalized ──────────────────────────────────────────
    if et == "DAWA_FINALIZED":
        head = "🏁 Dawa check finalized"
        if g("status"):
            head += f": **{g('status')}**"
        lines = [head]
        kept = as_list(g("kept_green", []))
        if kept:
            names = ", ".join(
                str(k.get("name", k) if isinstance(k, dict) else k) for k in kept)
            lines.append(f"- 🟢 **Kept (safe):** {names}")
        flagged = as_list(g("flagged", []))
        if flagged:
            bits = []
            for it in flagged:
                if isinstance(it, dict):
                    e2 = _TRIAGE_EMOJI.get(str(it.get("light", "")).upper(), "⚪")
                    bits.append(f"{e2} {it.get('name', '?')}")
                else:
                    bits.append(str(it))
            lines.append(f"- **Flagged:** {', '.join(bits)}")
        dismissed = as_list(g("dismissed", []))
        if dismissed:
            lines.append(f"- **Dismissed:** {len(dismissed)} flag(s)")
        return "\n".join(lines)

    # ── Doctor advice (telemedicine reply) ────────────────────────────
    if et == "DOCTOR_ADVICE":
        lines = ["🩺 Doctor advice"]
        lines += quote(g("advice", "")) if g("advice") else ["> _(no text)_"]
        if g("channel"):
            lines.append(f"- _via {g('channel')}_")
        return "\n".join(lines)

    # ── System login ──────────────────────────────────────────────────
    if et == "LOGIN":
        who = (g("user_id") or g("paramedic_id") or g("user")
               or g("role") or g("actor"))
        bhu = g("bhu_id")
        out = "🔑 Login"
        if who:
            out += f": **{who}**"
        if bhu:
            out += f" @ {bhu}"
        return out

    # ── District seed marker ──────────────────────────────────────────
    if et == "SEED_MONTH_COMPLETE":
        parts = []
        if g("cases", "") != "":
            parts.append(f"{g('cases')} cases")
        if g("dawa", "") != "":
            parts.append(f"{g('dawa')} dawa checks")
        return "🗓️ District month seeded" + (f": {', '.join(parts)}" if parts else "")

    # ── Fallback: one-line-per-key summary, long values truncated ─────
    title = et or event_type or "EVENT"
    if not p:
        return f"📄 **{title}** — _no details recorded._"
    lines = [f"📄 **{title}**"]
    for k, v in p.items():
        sv = str(v)
        if len(sv) > 80:
            sv = sv[:77] + "…"
        lines.append(f"- **{k}:** {sv}")
    return "\n".join(lines)


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
            # PRIMARY view: clean, human-readable Markdown (no raw JSON).
            cols[1].markdown(_pretty_event(ev["event_type"], payload))
            # Raw payload stays available for auditors, tucked away.
            with cols[1].expander("Raw JSON (audit)"):
                st.json(payload)


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
# Dawa Check tab
# ======================================================================

def _start_dawa_check(image_path: str, paramedic_id: str, *, synthetic: bool = False) -> None:
    with st.spinner("Reading visible medicines. You will confirm every item before checking." if image_path else "Preparing manual medicine entry."):
        cid, tid, first = dawa_graph.run_dawa_case(rx_image_path=image_path, paramedic_id=paramedic_id)
    if synthetic:
        audit.log_event(cid, "FICTIONAL_VISION_DEMO", {"synthetic": True, "source": "bundled fictional sample prescription"})
    ss = st.session_state
    ss.dawa_case_id, ss.dawa_thread_id = cid, tid
    ss.dawa_gate = _latest_gate_payload(first)
    ss.dawa_rows = list(ss.dawa_gate.get("medicines") or [{"name": "", "strength": ""}])
    ss.dawa_stage = "confirm" if ss.dawa_gate.get("gate") == "dawa_confirm" else "done"


def _dawa_tab(paramedic_id: str) -> None:
    st.title("Dawa Check — prescription / medicine-bag audit")
    ss = st.session_state
    ss.setdefault("dawa_case_id", None)
    ss.setdefault("dawa_thread_id", None)
    ss.setdefault("dawa_stage", "idle")   # idle | confirm | review | done
    ss.setdefault("dawa_gate", {})
    ss.setdefault("dawa_rows", [])

    rx_upload = st.file_uploader(
        "Rx / medicine-bag image (.jpg/.png)",
        type=["jpg", "jpeg", "png"], key="dawa_rx",
    )
    if rx_upload:
        st.image(rx_upload, caption="Check names and printed strengths against this image.", width=380)
    if st.button("Extract medicines", type="primary",
                 disabled=(rx_upload is None)):
        try:
            rx_path = _save_dawa_image(rx_upload)
            _start_dawa_check(rx_path, paramedic_id)
        except (ValueError, OSError) as exc:
            st.error(str(exc))

    if st.button("Read bundled fictional prescription", key="dawa_bundled_sample"):
        _start_dawa_check(str(Path(__file__).resolve().parent.parent / "assets/rx/demo_rx.jpg"), paramedic_id, synthetic=True)

    if st.button("Enter medicines manually without a photo", key="dawa_manual_start"):
        _start_dawa_check("", paramedic_id)

    if not ss.dawa_case_id:
        st.info("Upload an Rx or medicine-bag photo, then click "
                "**Extract medicines**.")
        return

    st.subheader(f"Dawa case {ss.dawa_case_id}")

    if ss.dawa_stage == "confirm":
        st.warning("🎤 Confirm medicine list — health worker review")
        if ss.dawa_gate.get("degraded") == "no_rx_image":
            st.info("Manual entry. No medicines have been inferred from a photo.")
        elif ss.dawa_gate.get("degraded"):
            st.error(f"⚠️ Vision extraction unavailable — "
                     f"{ss.dawa_gate['degraded']}. Type medicines manually below.")
        else:
            st.caption("AI vision draft via " + ss.dawa_gate.get("provider", "configured service") + ". Confirm every name and printed strength against the photo.")
        meds = ss.dawa_rows or list(ss.dawa_gate.get("medicines") or [])
        if not meds:
            meds = [{"name": "", "strength": ""}]
        rows, draft_rows = [], []
        for i, m in enumerate(meds):
            c1, c2 = st.columns([3, 2])
            n = c1.text_input(f"Medicine {i+1}", value=m.get("name", ""),
                              key=f"dawa_m_{ss.dawa_case_id}_{i}")
            s = c2.text_input(f"Strength {i+1}",
                              value=m.get("strength") or "",
                              key=f"dawa_s_{ss.dawa_case_id}_{i}")
            draft_rows.append({"name": n, "strength": s})
            if n.strip():
                rows.append({"name": n.strip(),
                             "strength": s.strip() or None})
        if st.button("+ add row"):
            ss.dawa_rows = draft_rows + [{"name": "", "strength": ""}]
            st.rerun()
        ss.dawa_rows = draft_rows

        c1, c2 = st.columns(2)
        pregnant = c1.checkbox("Pregnant", value=False)
        age = c2.number_input("Age", min_value=0, max_value=120,
                              value=30, step=1)
        current_meds = st.text_input(
            "Current meds / context (comma separated, optional)", value="")
        indication = st.text_input(
            "Indication (optional — helps stewardship checks)", value="")

        if st.button("Confirm & check", type="primary", disabled=not rows):
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
# Consultation tab — with the follow-up history engine
# ======================================================================

def main() -> None:
    patient_id, paramedic_id = _sidebar()
    workflow = st.session_state.get("_workflow", "Consultation")
    if workflow in ("Consultation", "Doctor console", "Split-screen demo"):
        from smartsehat_workspace.consultation.ui import render
        render(workflow)
        return
    if workflow == "Dawa Check":
        _dawa_tab(paramedic_id)
        return
    if workflow == "District View":
        from smartsehat_workspace.dash import render as render_dash
        render_dash()
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
        st.warning("🎤 Complete the history — then confirm")
        st.caption("Step 1: review the transcript · Step 2: ask the "
                   "suggested follow-up questions aloud · Step 3: type the "
                   "patient's answers below · Step 4: confirm — the "
                   "enriched transcript then flows into triage, reasoning, "
                   "and Dawa Check.")

        edited = st.text_area("Transcript (Urdu) — editable",
                              value=ss.gate.get("transcript", ""),
                              height=120)

        # Deterministic follow-up questions — asked BEFORE triage runs,
        # so answers (e.g. bleeding in pregnancy) reach the triage rules.
        all_patients = audit.list_patients()
        prow = next((p for p in all_patients
                     if p["patient_id"] == patient_id), {})
        followups = _suggest_followups(edited, prow)
        answers: list[str] = []
        if followups:
            st.markdown("**Ask the patient (safety-driven):**")
            for topic, question in followups:
                a = st.text_input(f"🙋 {question}", key=f"fu_{topic}")
                if a.strip():
                    answers.append(a.strip())

        if st.button("Confirm transcript + answers"):
            # Append answers to the transcript — the enriched history
            # now feeds triage, structure, reasoning, and Dawa Check.
            full = edited
            if answers:
                full = edited + "\n" + " ".join(answers)
            r = resume_case(ss.thread_id, action="confirm", transcript=full)
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
                st.markdown(
                    f"**[📲 Send via WhatsApp]({_whatsapp_link(packet)})**")
                st.caption("Works over SMS/2G. The receiving end needs no "
                           "app. The golden hour, compressed.")

            # ---- DOCTOR REVIEW LOOP (async telemedicine) ----
            elif row.get("outcome") in ("prescription_issued",
                                        "dawa_check_completed"):
                st.subheader("🩺 Send to doctor — async review")
                report = _doctor_report(ss.case_id)
                with st.expander("Doctor Review Report (compiled from ledger)",
                                 expanded=True):
                    st.code(report)
                    st.markdown(
                        f"**[📲 Send via WhatsApp]({_whatsapp_link(report)})** "
                        f"— works on 2G, doctor needs no app")
                st.markdown("**Log the doctor's reply** (closes the loop — "
                            "enters the ledger):")
                doc_reply = st.text_area(
                    "Doctor's reply (paste message or voice-note text)",
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
