"""SmartSehat Pulse — District Health Officer read-only dashboard."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from smartsehat.audit import _connect, DB_PATH


BHU_COORDS = {
    "BHU-0442": (28.4, 70.3),
    "BHU-0511": (30.97, 71.22),
    "BHU-0623": (30.47, 71.13),
    "BHU-0788": (28.9, 70.3),
}


@st.cache_data(ttl=120)
def _q(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = _connect()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def _render_header() -> None:
    st.title("🏥 SmartSehat Pulse — District Dashboard")
    st.caption(
        "District Health Officer view — read-only over the ledger. "
        "Data: synthetic demo month + any real cases run."
    )


def _render_kpis(cutoff: str) -> None:
    total_cases = int(_q(
        "SELECT COUNT(*) AS n FROM cases WHERE started_at >= ?", (cutoff,)
    ).iloc[0]["n"])
    active_bhus = int(_q(
        "SELECT COUNT(DISTINCT bhu_id) AS n FROM cases WHERE started_at >= ?",
        (cutoff,),
    ).iloc[0]["n"])
    red_cases = int(_q(
        "SELECT COUNT(*) AS n FROM cases "
        "WHERE (triage_level='RED' OR outcome='emergency_referral') "
        "AND started_at >= ?",
        (cutoff,),
    ).iloc[0]["n"])
    dawa_audits = int(_q(
        "SELECT COUNT(*) AS n FROM cases WHERE outcome='dawa_check_completed'"
    ).iloc[0]["n"])
    hitl = _q(
        "SELECT action, COUNT(*) AS n FROM hitl_decisions GROUP BY action"
    )
    counts = dict(zip(hitl["action"], hitl["n"])) if not hitl.empty else {}
    decisions = sum(int(value) for value in counts.values())
    overrides = int(counts.get("modified", 0)) + int(counts.get("rejected", 0))
    override_rate = overrides / decisions if decisions else 0.0

    columns = st.columns(5)
    columns[0].metric("Total cases (30d)", total_cases)
    columns[1].metric("Active BHUs", active_bhus)
    columns[2].metric("RED emergencies", red_cases)
    columns[3].metric("Dawa audits", dawa_audits)
    columns[4].metric("HITL override rate", f"{override_rate:.0%}")


def _render_cases_per_bhu(cutoff: str) -> pd.DataFrame:
    st.subheader("Cases per BHU — last 30 days")
    cases = _q(
        "SELECT bhu_id, COUNT(*) AS cases FROM cases "
        "WHERE started_at >= ? GROUP BY bhu_id",
        (cutoff,),
    )
    if cases.empty:
        st.info("No cases recorded in the last 30 days.")
    else:
        st.bar_chart(cases.set_index("bhu_id"))
    return cases


def _render_top_diagnoses() -> None:
    st.subheader("Top Diagnoses")
    diagnoses = _q(
        "SELECT final_diagnosis AS Diagnosis, COUNT(*) AS Count "
        "FROM cases WHERE final_diagnosis IS NOT NULL "
        "GROUP BY final_diagnosis ORDER BY Count DESC"
    )
    st.dataframe(diagnoses, width="stretch", hide_index=True)


def _render_hitl_oversight() -> None:
    st.subheader("HITL Oversight")
    actions = _q(
        "SELECT c.bhu_id, h.action, COUNT(*) AS n "
        "FROM hitl_decisions h JOIN cases c ON h.case_id = c.case_id "
        "GROUP BY c.bhu_id, h.action"
    )
    if actions.empty:
        st.metric("Overall override rate", "0%")
        st.info("No HITL decisions recorded.")
        return

    pivot = actions.pivot_table(
        index="bhu_id", columns="action", values="n", aggfunc="sum", fill_value=0
    )
    for action in ("approved", "modified", "rejected"):
        if action not in pivot.columns:
            pivot[action] = 0
    pivot = pivot.astype(int)
    pivot["total"] = pivot.sum(axis=1)
    pivot["overrides"] = pivot["modified"] + pivot["rejected"]
    pivot["override_rate"] = pivot["overrides"] / pivot["total"]

    overall_total = int(pivot["total"].sum())
    overall_overrides = int(pivot["overrides"].sum())
    st.metric(
        "Overall override rate",
        f"{overall_overrides / overall_total:.0%}" if overall_total else "0%",
    )

    breakdown = pivot.reset_index()[
        ["bhu_id", "total", "approved", "overrides", "override_rate"]
    ].rename(columns={
        "bhu_id": "BHU",
        "total": "Decisions",
        "approved": "Approved",
        "overrides": "Modified + rejected",
        "override_rate": "Override rate",
    })
    st.dataframe(
        breakdown.style.format({"Override rate": "{:.0%}"}),
        width="stretch",
        hide_index=True,
    )

    for bhu, row in pivot.iterrows():
        total = int(row["total"])
        approved = int(row["approved"])
        rate = float(row["override_rate"])
        if total >= 10 and approved / total >= 0.95:
            st.warning(
                f"Rubber-stamp risk at {bhu}: {approved}/{total} AI suggestions "
                "approved unmodified — supervision visit recommended"
            )
        if rate > 0.60:
            st.warning(
                f"Review with {bhu}: AI suggestions overridden {rate:.0%} — "
                "case review recommended"
            )


def _render_syndromic_trend() -> None:
    st.subheader("Syndromic Trend + Outbreak Alert")
    cases = _q(
        "SELECT bhu_id, started_at, final_diagnosis AS syndrome "
        "FROM cases WHERE final_diagnosis IS NOT NULL"
    )
    if cases.empty:
        st.info("No diagnosis trend data available.")
        return

    cases["started_at"] = pd.to_datetime(cases["started_at"], errors="coerce", utc=True)
    cases = cases.dropna(subset=["started_at", "syndrome"])
    if cases.empty:
        st.info("No diagnosis trend data available.")
        return

    iso = cases["started_at"].dt.isocalendar()
    cases["week"] = (
        iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    )
    top_syndromes = cases["syndrome"].value_counts().head(3).index
    trend = (
        cases[cases["syndrome"].isin(top_syndromes)]
        .groupby(["week", "syndrome"])
        .size()
        .unstack(fill_value=0)
        .sort_index()
    )
    st.line_chart(trend)

    all_weeks = sorted(cases["week"].unique())
    current_iso = datetime.now(timezone.utc).isocalendar()
    current_week = f"{current_iso.year}-W{current_iso.week:02d}"
    completed_weeks = [week for week in all_weeks if week < current_week]
    latest_week = completed_weeks[-1] if completed_weeks else all_weeks[-1]
    latest_index = all_weeks.index(latest_week)
    baseline_weeks = all_weeks[max(0, latest_index - 4):latest_index]
    if baseline_weeks:
        weekly = cases.groupby(["bhu_id", "syndrome", "week"]).size()
        for (bhu, syndrome), group in cases.groupby(["bhu_id", "syndrome"]):
            last_week_count = int((group["week"] == latest_week).sum())
            baseline_avg = sum(
                int(weekly.get((bhu, syndrome, week), 0))
                for week in baseline_weeks
            ) / len(baseline_weeks)
            if last_week_count > 2 * baseline_avg and last_week_count >= 5:
                st.error(
                    f"🚨 OUTBREAK SIGNAL: {last_week_count} {syndrome} cases at "
                    f"{bhu} this week vs baseline {baseline_avg:.1f} — flagged "
                    "for District Health Officer review"
                )
    st.caption(
        "Pattern detection on the ledger — in production this is the DHO's "
        "early-warning system."
    )


def _render_map(cases_per_bhu: pd.DataFrame) -> None:
    st.subheader("District Case Map")
    counts = (
        dict(zip(cases_per_bhu["bhu_id"], cases_per_bhu["cases"]))
        if not cases_per_bhu.empty else {}
    )
    map_data = pd.DataFrame([
        {"lat": lat, "lon": lon, "size": max(int(counts.get(bhu, 0)) * 10, 10)}
        for bhu, (lat, lon) in BHU_COORDS.items()
    ])
    st.map(map_data, size="size")
    st.caption(
        "Circle size reflects 30-day case volume; BHU-0788 is the seeded TB "
        "outbreak watch point."
    )


def _render_footer() -> None:
    st.caption(
        "Every number on this page is a query over the immutable ledger — "
        "synthetic rows are labeled 'synthetic': true."
    )


def render() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    _render_header()
    _render_kpis(cutoff)
    cases_per_bhu = _render_cases_per_bhu(cutoff)
    _render_top_diagnoses()
    _render_hitl_oversight()
    _render_syndromic_trend()
    _render_map(cases_per_bhu)
    _render_footer()
