"""Readable clinical data and portable, Unicode-friendly report exports."""
from html import escape
import json


def label(value):
    return str(value).replace('_', ' ').strip().capitalize()


def readable_html(value):
    """Preserve every supplied field while escaping all patient/model content."""
    if isinstance(value, dict):
        return '<dl>' + ''.join(
            f'<dt>{escape(label(key))}</dt><dd>{readable_html(item)}</dd>'
            for key, item in value.items()) + '</dl>'
    if isinstance(value, (list, tuple)):
        return ('<ul>' + ''.join(f'<li>{readable_html(item)}</li>' for item in value)
                + '</ul>') if value else '<span class="muted">None recorded</span>'
    if value is None:
        value = 'Not recorded'
    elif isinstance(value, bool):
        value = 'Yes' if value else 'No'
    return f'<div dir="auto" class="report-value">{escape(str(value))}</div>'


def render_readable(value):
    import streamlit as st
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            pass
    st.markdown('<div class="readable-data">' + readable_html(value) + '</div>',
                unsafe_allow_html=True)


def report_html(title, content):
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>
body{{font:16px/1.6 Arial,sans-serif;color:#0b1c30;background:#f8f9ff;margin:0;padding:32px}}
main{{max-width:900px;margin:auto;background:white;padding:40px;border-radius:18px}}
h1{{color:#005057;font-size:28px}} dt{{font-weight:bold;color:#315762;margin-top:14px}}
dd{{margin:4px 0 12px 18px}} li{{margin:8px 0}} .report-value{{white-space:pre-wrap;overflow-wrap:anywhere}}
.muted,footer{{color:#526770}}button{{background:#005057;color:white;border:0;padding:12px 20px;border-radius:8px;cursor:pointer}}
@media print{{body{{background:white;padding:0}}main{{padding:0;max-width:none}}button,.print-help{{display:none}}}}
</style></head><body><main><header><p>SMARTSEHAT Â· CARE REPORT</p>
<h1>{escape(title)}</h1><button onclick="window.print()">Print / Save as PDF</button>
<p class="print-help">Choose Save as PDF in your browser's print dialog.</p></header>
{readable_html(content)}<footer><hr>Recorded clinical information. AI drafts and review findings are not clinical approval.</footer>
</main></body></html>'''


def download_report(title, content, filename, key):
    import streamlit as st
    st.download_button('Download readable report (HTML / print to PDF)',
                       report_html(title, content), file_name=filename + '.html',
                       mime='text/html', key=key + ':html')
    st.caption('Open the downloaded report, then select Print / Save as PDF. Urdu text is preserved.')


def welcome():
    """Public introduction shown before authentication; contains no patient data."""
    import streamlit as st
    st.markdown('''<section class="welcome">
<div class="welcome-eyebrow">CONNECTED PRIMARY CARE</div>
<h1>Welcome to SmartSehat</h1>
<h2>Clarity at every step of care.</h2>
<p class="welcome-intro">From the first patient statement to a reviewed care plan,
SmartSehat brings clinical information into one clear workflow—helping frontline
teams document, review, and coordinate the next step.</p>
<div class="welcome-login"><span class="welcome-arrow" aria-hidden="true">←</span><div>
<strong>Your workspace starts here</strong>
<p>Select your account in the sidebar, then choose <b>Start demo session</b>.
Health workers and district health officers will see the tools available to their role.
If the sidebar is closed, open it using the arrow at the top left.</p></div></div>
<div class="welcome-grid">
<article><span class="welcome-number">01 / CONSULTATION</span><h3>Listen. Capture. Confirm.</h3>
<p>Capture a typed or voice complaint, confirm the patient history, and work through structured clinical information.</p></article>
<article><span class="welcome-number">02 / DAWA CHECK</span><h3>A clearer medicine review.</h3>
<p>Reconcile medicine details, review supported safety checks, and document findings that need attention.</p></article>
<article><span class="welcome-number">03 / DISTRICT PULSE</span><h3>See the wider picture.</h3>
<p>Give district health officers a view of facility activity, review patterns, and signals requiring follow-up.</p></article>
</div>
<footer>Built around human review <span aria-hidden="true">·</span> Clear evidence <span aria-hidden="true">·</span> Coordinated handoff</footer>
</section>''', unsafe_allow_html=True)


def brand():
    import base64
    from pathlib import Path
    import streamlit as st
    logo = Path(__file__).resolve().parent.parent / 'assets' / 'smartsehat-logo.png'
    data = base64.b64encode(logo.read_bytes()).decode('ascii')
    st.markdown('<div class="brand-logo"><svg viewBox="114 284 879 333" role="img" '
                'aria-label="SmartSehat" xmlns="http://www.w3.org/2000/svg">'
                f'<image href="data:image/png;base64,{data}" width="1024" height="1024" />'
                '</svg></div>'
                '<p class="brand-tagline">Clear information. Better care.</p>', unsafe_allow_html=True)


def encounter_header(encounter, stage, workflow='consultation'):
    import streamlit as st
    patient = encounter.snapshot.patient
    fields = [('Patient', encounter.patient_id or patient.display_reference),
              ('Age', f'{patient.age:g} {patient.age_unit or "years"}' if patient.age is not None else 'Not recorded'),
              ('Facility', encounter.facility_id), ('Encounter', encounter.encounter_id)]
    content = ''.join(f'<div><small>{escape(name)}</small><strong>{escape(str(value))}</strong></div>'
                      for name, value in fields)
    st.markdown(f'<div class="encounter-strip">{content}<div><small>Status</small>'
                f'<strong>{escape(label(encounter.status))}</strong></div></div>', unsafe_allow_html=True)
    if workflow == 'consultation':
        steps = ['Complaint', 'History review', 'Structured information', 'Assessment & review', 'Handoff']
        active = {'prepared': 0, 'transcript': 1, 'intake': 2, 'hitl': 3, 'done': 4}.get(stage, 0)
    else:
        steps = ['Prescription input', 'Confirm medicines', 'Review findings', 'Completion']
        active = {'prepared': 0, 'confirm': 1, 'review': 2, 'done': 3}.get(stage, 2)
    items = ''.join(f'<div class="journey-step {"current" if i == active else ""}" '
                    + ('aria-current="step"' if i == active else '')
                    + f'><span>{i + 1}</span><div>{escape(title)}</div></div>'
                    for i, title in enumerate(steps))
    st.markdown(f'<nav class="journey" aria-label="Encounter progress">{items}</nav>', unsafe_allow_html=True)


def apply_theme():
    import streamlit as st
    st.markdown('''<style>
    .stApp {background:#f8f9ff;color:#0b1c30}
    /* The toolbar owns its own band; only the workspace below it scrolls. */
    [data-testid="stAppViewContainer"] {--workspace-toolbar-height:3.75rem;overflow:hidden}
    [data-testid="stHeader"] {height:var(--workspace-toolbar-height,3.75rem);background:#ffffff;border-bottom:1px solid #dbe3ef;box-shadow:0 2px 8px rgba(11,28,48,.04)}
    [data-testid="stMain"] {margin-top:var(--workspace-toolbar-height,3.75rem);height:calc(100dvh - var(--workspace-toolbar-height,3.75rem));max-height:calc(100dvh - var(--workspace-toolbar-height,3.75rem));min-height:0;overflow-y:auto;overflow-x:hidden;scroll-padding-top:24px;overscroll-behavior-y:contain}
    .stMainBlockContainer {padding-top:2rem;max-width:1500px}
    .stMainBlockContainer:has(.welcome) {max-width:none;padding:24px 32px}
    .welcome {max-width:none;margin:0;padding:0}
    .welcome-eyebrow {color:#006a73;font-size:12px;font-weight:700;letter-spacing:.16em;margin-bottom:20px}
    .welcome h1 {font-size:clamp(32px,3.5vw,52px)!important;line-height:1.15;margin-bottom:8px}
    .welcome h2 {font-size:clamp(23px,2.5vw,34px)!important;color:#005057;font-weight:500;margin-bottom:22px}
    .welcome-intro {font-size:18px;line-height:1.8;color:#526770;max-width:770px}
    .welcome-login {display:flex;gap:18px;background:#e5f2f3;border:1px solid #bddbdf;border-radius:14px;padding:24px;margin:32px 0}
    .welcome-login strong {color:#005057;font-size:18px}.welcome-login p {margin:8px 0 0;color:#315762;line-height:1.7}
    .welcome-arrow {font-size:28px;color:#005057}
    .welcome-grid {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}
    .welcome-grid article {background:white;border:1px solid #e2e8f0;border-radius:14px;padding:24px}
    .welcome-number {font-size:11px;color:#006a73;font-weight:700;letter-spacing:.08em}
    .welcome-grid h3 {font-size:19px;margin:18px 0 12px}.welcome-grid p {color:#526770;line-height:1.75;margin:0}
    .welcome footer {color:#526770;font-size:13px;text-align:center;margin-top:32px}.welcome footer span {margin:0 10px}
    @media(max-width:900px) {.welcome-grid {grid-template-columns:1fr}.stMainBlockContainer:has(.welcome) {padding:16px}.welcome {margin:0;padding:0}}
    h1,h2,h3 {color:#0b1c30;letter-spacing:-.025em}
    h1 {font-size:2rem!important} h2 {font-size:1.5rem!important}
    [data-testid="stSidebar"] {background:#21242f;color:#ebf1ff}
    [data-testid="stSidebarCollapseButton"] {visibility:visible!important}
    [data-testid="stSidebarCollapseButton"] button {background:#343b49!important;color:#ffffff!important;border:1px solid #64748b;border-radius:8px;min-width:40px;min-height:40px}
    [data-testid="stSidebarCollapseButton"] button:hover {background:#465166!important}
    [data-testid="stSidebarCollapseButton"] button * {color:#ffffff!important}
    [data-testid="stSidebarCollapseButton"] button:focus-visible {outline:3px solid #98e7f1;outline-offset:2px}
    [data-testid="stExpandSidebarButton"] {color:#005057!important}
    [data-testid="stExpandSidebarButton"] * {color:#005057!important}
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
    [data-testid="stSidebar"] label,[data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3 {color:#ebf1ff}
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {color:#ebf1ff}
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {color:#b9c8db}
    [data-testid="stSidebar"] [role="radiogroup"] label {padding:10px;border-radius:8px}
    [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background:#006a73}
    [data-testid="stSidebar"] [data-baseweb="select"] {color:#0b1c30}
    .brand-logo {background:transparent;padding:8px}
    .brand-logo svg {display:block;width:100%;height:auto}
    /* Preserve the transparent original; use a reverse brand mark on dark chrome.
       Light surfaces retain teal. A light sidebar can override this variable to none. */
    [data-testid="stSidebar"] {--brand-logo-filter:brightness(0) invert(1)}
    [data-testid="stSidebar"] .brand-logo svg {filter:var(--brand-logo-filter)}
    .brand-tagline {font-size:12px;color:#c6d1e1;margin:8px 0 20px;text-align:center;width:100%}
    [data-testid="stVerticalBlockBorderWrapper"], [data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(>div>details) {border-radius:14px}
    [data-testid="stVerticalBlockBorderWrapper"] {background:white;border-color:#e2e8f0}
    [data-testid="stExpander"] {background:white;border-radius:12px}
    .stButton button,.stDownloadButton button {border-radius:8px;min-height:44px;font-weight:600}
    .stButton button[kind="primary"] {background:#005057;border-color:#005057;color:white}
    [data-testid="stSidebar"] .stButton button {background:#343b49;color:#ffffff;border:1px solid #64748b}
    [data-testid="stSidebar"] .stButton button:hover {background:#465166;color:#ffffff;border-color:#98e7f1}
    [data-testid="stSidebar"] .stButton button[kind="primary"] {background:#005057;color:#ffffff;border-color:#84d3dd}
    [data-testid="stSidebar"] .stButton button[kind="primary"]:hover {background:#006a73}
    [data-testid="stSidebar"] .stButton button [data-testid="stMarkdownContainer"],
    [data-testid="stSidebar"] .stButton button p {color:inherit}
    [data-testid="stSidebar"] .stButton button:focus-visible {outline:3px solid #98e7f1;outline-offset:3px}
    .stButton button:disabled {opacity:.5}
    textarea {background:#f3f6ff!important;line-height:1.8!important;unicode-bidi:plaintext}
    [data-testid="stFileUploader"] {background:#eff4ff;padding:16px;border-radius:12px}
    [data-testid="stMetric"] {background:white;padding:20px;border:1px solid #e2e8f0;border-radius:12px}
    [data-testid="stAlert"] {border-radius:12px}
    .readable-data {background:#eff4ff;border:1px solid #e2e8f0;border-radius:12px;padding:16px 20px;margin:8px 0}
    .readable-data dt {font-weight:600;color:#315762;margin-top:10px}
    .readable-data dd {margin:4px 0 12px 16px}.readable-data li {margin-bottom:8px}
    .report-value {white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8}.muted {color:#526770}
    .encounter-strip {display:flex;flex-wrap:wrap;gap:20px;background:#eff4ff;border:1px solid #dbe8eb;border-radius:12px;padding:18px 22px}
    .encounter-strip>div {flex:1;min-width:100px}.encounter-strip small {display:block;color:#526770;font-size:12px;margin-bottom:5px}
    .encounter-strip strong {font-size:14px;overflow-wrap:anywhere}
    .journey {display:flex;gap:8px;background:white;border:1px solid #e2e8f0;border-radius:12px;padding:14px;margin:12px 0 24px}
    .journey-step {flex:1;display:flex;align-items:center;gap:9px;padding:10px;color:#526770;font-size:13px}
    .journey-step span {border-radius:50%;background:#eff4ff;padding:5px 10px}
    .journey-step.current {background:#eff4ff;color:#005057;border-radius:8px;font-weight:700}
    .journey-step.current span {background:#005057;color:white}
    @media(max-width:760px) {.journey {flex-wrap:wrap}.journey-step {min-width:130px}.stMainBlockContainer {padding:1rem}h1 {font-size:1.6rem!important}}
    </style>''', unsafe_allow_html=True)
