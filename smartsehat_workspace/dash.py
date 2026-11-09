"""SmartSehat Pulse: all saved encounters, current consultation status and map."""
from __future__ import annotations

from datetime import datetime, timezone
import html

import pandas as pd
import pydeck as pdk
import streamlit as st

from smartsehat_workspace.district_data import load_cases, filter_cases, map_counts, PERIODS
from smartsehat_workspace.consultation.timeutils import care_timezone


def _render_map(cases: pd.DataFrame) -> None:
    st.subheader("District Case Map")
    points = map_counts(cases)
    mapped = int(points["Cases"].sum())
    st.caption(f"{mapped:,} of {len(cases):,} displayed cases mapped · {len(cases) - mapped:,} without a configured location. Demo coordinates are illustrative clinic/village locations, not patient GPS.")
    if not points.empty:
        if st.button("Reset map view", key="district_reset_map"):
            st.session_state["district_map_revision"] = st.session_state.get("district_map_revision", 0) + 1
        data = points.copy()
        data["label"] = data["Location"].map(html.escape)
        data["radius"] = data["Cases"].pow(0.5) * 3500
        data["colour"] = data["RED cases"].map(lambda n: [220, 61, 69, 190] if n else [20, 130, 113, 190])
        deck = pdk.Deck(map_provider="carto", map_style="light",
            initial_view_state=pdk.ViewState(latitude=float(data["lat"].mean()), longitude=float(data["lon"].mean()), zoom=6.0),
            layers=[pdk.Layer("ScatterplotLayer", id="district-case-locations", data=data, get_position="[lon, lat]", get_radius="radius", radius_min_pixels=10,
                radius_max_pixels=35, get_fill_color="colour", pickable=True, stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=2)],
            tooltip={"html": "<b>{label}</b><br/>Cases: {Cases}<br/>Consultations: {Consultations}<br/>Legacy encounters: {Legacy cases}<br/>RED cases: {RED cases}<br/>Open cases: {Open cases}",
                "style": {"backgroundColor": "#123f3a", "color": "white"}})
        st.pydeck_chart(deck, height=440, key=f"district_case_map_{st.session_state.get('district_map_revision', 0)}")
        st.caption("Larger circles mean more cases. Red markers contain at least one case with a latest RED status, including closed referral cases. Demo coordinates are illustrative; map tiles need a connection.")
        st.dataframe(points[["Location", "Cases", "Consultations", "Legacy cases", "RED cases", "Open cases"]], hide_index=True, width="stretch")
    else:
        st.info("No configured map locations match this view. These cases are still included in the total and the case list.")
    unlocated = cases[cases["lat"].isna() | cases["lon"].isna()]
    if not unlocated.empty:
        with st.expander(f"Locations not mapped · {len(unlocated)} case(s)"):
            st.dataframe(unlocated.groupby("site").size().rename("Cases").reset_index().rename(columns={"site": "Recorded clinic / village"}), hide_index=True)


def _select_consultation(cid: str) -> None:
    st.query_params["case"] = cid
    st.session_state["consultation_case"] = cid
    st.session_state["workflow"] = "Consultation"
    st.session_state["_workflow"] = "Consultation"


def _render_registry(cases: pd.DataFrame) -> None:
    st.subheader("All cases in this view")
    if cases.empty:
        st.info("No saved cases match these filters.")
        return
    registry = cases.copy()
    registry["created"] = registry["created"].dt.tz_convert(care_timezone()).dt.strftime("%d %b %Y, %H:%M").fillna("Not recorded")
    registry["synthetic"] = registry["synthetic"].map({True: "Fictional / synthetic", False: "Not marked synthetic"})
    registry["doctor_replied"] = registry.apply(lambda row: "Yes" if row["doctor_replied"] else "No" if row["source"] == "Consultation" else "See legacy record", axis=1)
    view = registry[["case_id", "source", "name", "site", "created", "urgency", "stage", "doctor_replied", "outcome", "synthetic"]].rename(columns={
        "case_id": "Case ID", "source": "Source", "name": "Patient / fictional name", "site": "Clinic / village", "created": "Created (Asia/Karachi)",
        "urgency": "Latest urgency", "stage": "Saved stage", "doctor_replied": "Doctor response", "outcome": "Outcome", "synthetic": "Data label"})
    st.dataframe(view, hide_index=True, width="stretch", height=360)
    st.download_button("Download displayed cases (CSV)", view.to_csv(index=False).encode("utf-8-sig"), "smartsehat-district-cases.csv", "text/csv", key="district_cases_csv")
    consultations = cases[cases["source"] == "Consultation"]
    if not consultations.empty:
        with st.expander("Open a consultation from the case list"):
            names = dict(zip(consultations["case_id"], consultations["name"]))
            cid = st.selectbox("Consultation case", list(names), format_func=lambda value: f"{names[value]} · {value}", key="district_open_case")
            if st.button("Open selected consultation", key="district_open_case_button", on_click=_select_consultation, args=(cid,)):
                st.rerun()


def _render_assessments(cases: pd.DataFrame) -> None:
    diagnosed = cases[cases["diagnosis"].str.strip() != ""]
    with st.expander("Recorded assessments and trends", expanded=False):
        st.caption("Recorded diagnoses/doctor assessments for the same filtered cases. Text from fictional doctor examples is not a validated diagnosis or an outbreak finding.")
        if diagnosed.empty:
            st.info("No doctor assessment or diagnosis is recorded in this view.")
            return
        st.dataframe(diagnosed["diagnosis"].value_counts().rename_axis("Recorded assessment / diagnosis").reset_index(name="Cases"), hide_index=True, width="stretch")
        dated = diagnosed.dropna(subset=["created"]).copy()
        if not dated.empty:
            dated["week"] = dated["created"].dt.strftime("%G-W%V")
            top = dated["diagnosis"].value_counts().head(3).index
            trend = dated[dated["diagnosis"].isin(top)].groupby(["week", "diagnosis"]).size().unstack(fill_value=0).sort_index()
            st.line_chart(trend)
            legacy = dated[dated["source"] == "Legacy ledger"]
            if not legacy.empty:
                current = datetime.now(timezone.utc).strftime("%G-W%V")
                weeks = sorted(legacy["week"].unique())
                completed = [week for week in weeks if week < current]
                latest = completed[-1] if completed else weeks[-1]
                index = weeks.index(latest)
                baseline = weeks[max(0, index - 4):index]
                if baseline:
                    weekly = legacy.groupby(["site", "diagnosis", "week"]).size()
                    for (site, syndrome), group in legacy.groupby(["site", "diagnosis"]):
                        count = int((group["week"] == latest).sum())
                        average = sum(int(weekly.get((site, syndrome, week), 0)) for week in baseline) / len(baseline)
                        if count > 2 * average and count >= 5:
                            st.warning(f"Recorded syndromic signal · {latest}: {count} {syndrome} cases at {site} versus prior recorded-week average {average:.1f}. A review flag, not a confirmed outbreak.")


def _render_oversight(cases: pd.DataFrame, decisions: pd.DataFrame) -> None:
    with st.expander("Legacy Dawa audits and human review", expanded=False):
        legacy_ids = cases.loc[cases["source"] == "Legacy ledger", "case_id"]
        decisions = decisions[decisions["case_id"].isin(legacy_ids)]
        st.caption("Legacy-ledger review decisions for the displayed encounters. Consultation fact confirmations are not counted as HITL approvals.")
        dawa = int((cases["outcome"] == "dawa_check_completed").sum())
        overrides = int(decisions["action"].isin(["modified", "rejected"]).sum())
        a, b = st.columns(2)
        a.metric("Legacy Dawa audits", dawa)
        b.metric("HITL override rate", f"{overrides / len(decisions):.0%}" if len(decisions) else "—")
        if decisions.empty:
            st.info("No legacy review decisions match this view.")
        else:
            sites = cases[cases["source"] == "Legacy ledger"][["case_id", "site"]]
            grouped = decisions.merge(sites, on="case_id").groupby(["site", "action"]).size().unstack(fill_value=0)
            for action in ("approved", "modified", "rejected"):
                if action not in grouped:
                    grouped[action] = 0
            grouped["Decisions"] = grouped[["approved", "modified", "rejected"]].sum(axis=1)
            grouped["Override rate"] = (grouped["modified"] + grouped["rejected"]) / grouped["Decisions"]
            st.dataframe(grouped.reset_index().rename(columns={"site": "Clinic"}), hide_index=True, width="stretch")
            for site, row in grouped.iterrows():
                if row["Decisions"] >= 10 and row["approved"] / row["Decisions"] >= .95:
                    st.warning(f"Review reminder at {site}: {int(row['approved'])}/{int(row['Decisions'])} suggestions approved unchanged in this view; consider supervisory review.")
                if row["Override rate"] > .6:
                    st.warning(f"Review reminder at {site}: {row['Override rate']:.0%} of suggestions modified or rejected in this view.")


@st.fragment(run_every="10s")
def _live_dashboard(period: str, source: str, urgency: str, query: str) -> None:
    all_cases, decisions = load_cases()
    cases = filter_cases(all_cases, period, source, urgency, query)
    st.caption(f"{len(all_cases):,} saved cases across both stores · Showing {len(cases):,} · {period} · Refreshed {datetime.now(timezone.utc).astimezone(care_timezone()):%H:%M:%S} (Asia/Karachi). Updates every 10 seconds.")
    a, b, c, d, e, f = st.columns(6)
    a.metric("Total cases", len(cases))
    b.metric("Consultation cases", int((cases["source"] == "Consultation").sum()))
    c.metric("Legacy cases", int((cases["source"] == "Legacy ledger").sum()))
    d.metric("RED cases", int((cases["urgency"] == "RED").sum()))
    e.metric("Open cases", int((~cases["closed"].astype(bool)).sum()))
    f.metric("Closed cases", int(cases["closed"].astype(bool).sum()))
    st.caption("Totals count encounters once, including saved drafts and completed cases. RED is the latest recorded urgency; a closed referral can retain its historical RED warning.")
    _render_map(cases)
    _render_registry(cases)
    _render_assessments(cases)
    _render_oversight(cases, decisions)


def render() -> None:
    st.title("🏥 SmartSehat Pulse — District Dashboard")
    st.caption("All saved consultation cases and legacy encounters, including the fictional presentation examples.")
    a, b, c, d = st.columns([1.1, 1.4, 1.3, .7])
    period = a.selectbox("Time range", list(PERIODS), key="district_period")
    source = b.selectbox("Case source", ["All sources", "Consultation", "Legacy ledger"], key="district_source")
    urgency = c.selectbox("Urgency", ["All urgency levels", "RED", "AMBER", "GREEN", "UNKNOWN"], key="district_urgency")
    d.button("Refresh", key="district_refresh")
    query = st.text_input("Search cases", placeholder="Case ID, name, clinic, village or outcome", key="district_search")
    _live_dashboard(period, source, urgency, query)
