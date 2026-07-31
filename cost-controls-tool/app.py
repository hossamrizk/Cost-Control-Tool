"""Streamlit review interface.

Design intent: a project controls reviewer should be able to answer, for any
figure on screen, "where did that come from?" without leaving the page. Every
finding shows the reported value, the calculated value, the rule that fired and
the source row, with the AI's words visually separated from the arithmetic.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import streamlit as st

from costctl import ENGINE_VERSION, RULESET_VERSION
from costctl.ai import build_interpreter
from costctl.engine import run, write_outputs
from costctl.models import FindingType, Severity, Status
from costctl.money import fmt
from costctl.review import InvalidTransition, set_status
from costctl.rules import Thresholds
from costctl.summary import render_markdown

st.set_page_config(page_title="Project Cost Controls Review", layout="wide",
                   initial_sidebar_state="expanded")

CSS = """
<style>
:root {
  --brand:       #8B1A1A;
  --brand-dark:  #6E1416;
  --brand-tint:  #FBECEC;
  --ink:         #1A1A1A;
  --ink-soft:    #555555;
  --paper:       #F4F4F5;
  --card:        #FFFFFF;
  --rule:        #DDDDDD;
  --critical:    #C62828;
  --high:        #EF6C00;
  --medium:      #1976D2;
  --low:         #616161;
  --good:        #2E7D32;
  --mono: ui-monospace, "SF Mono", "IBM Plex Mono", Menlo, monospace;
}

.stApp { background: var(--paper); }
html, body, [class*="css"] { color: var(--ink); }

/* ---------- Page header (Decima red bar) ---------- */
.page-head {
  background: var(--brand);
  color: #FFFFFF;
  padding: 1.1rem 1.4rem;
  margin-bottom: 1.2rem;
  border-radius: 3px;
}
.page-head h1 {
  margin: 0; color: #FFFFFF;
  font-size: 1.5rem; font-weight: 600; letter-spacing: -.01em;
}
.page-head .meta {
  color: rgba(255,255,255,0.85);
  font-size: .82rem; margin-top: .35rem;
}

/* ---------- Section bar (used above tables / groups) ---------- */
.section-bar {
  background: var(--brand);
  color: #FFFFFF;
  padding: .55rem 1rem;
  font-size: .95rem; font-weight: 600; letter-spacing: .01em;
  border-radius: 3px;
  margin: 1.3rem 0 .8rem 0;
}

/* ---------- Small label above a value ---------- */
.label {
  font-size: .78rem;
  color: var(--ink-soft);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .05em;
  margin-bottom: .3rem;
  display: block;
}

/* ---------- KPI tiles ---------- */
.tile {
  background: var(--card);
  border: 1px solid var(--rule);
  border-top: 3px solid var(--brand);
  padding: .95rem 1.1rem;
  border-radius: 3px;
  height: 100%;
}
.tile .value {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
  font-size: 1.45rem; font-weight: 700; color: var(--ink);
  letter-spacing: -.01em;
}
.tile .value.neg { color: var(--critical); }
.tile .note { font-size: .8rem; color: var(--ink-soft); margin-top: .35rem; }

/* ---------- Finding card ---------- */
.finding {
  background: var(--card);
  border: 1px solid var(--rule);
  border-left: 5px solid var(--rule);
  padding: 1rem 1.2rem;
  margin-bottom: .8rem;
  border-radius: 3px;
}
.finding.sev-critical { border-left-color: var(--critical); }
.finding.sev-high     { border-left-color: var(--high); }
.finding.sev-medium   { border-left-color: var(--medium); }
.finding.sev-low      { border-left-color: var(--low); }

.finding-head {
  display: flex; justify-content: space-between;
  align-items: flex-start; gap: 1rem; margin-bottom: .5rem;
}
.finding-title {
  font-size: 1rem; font-weight: 700; color: var(--ink); margin: 0;
}
.finding-sub {
  font-size: .82rem; color: var(--ink-soft); margin-top: .2rem;
}
.finding-badges { display: flex; gap: .4rem; flex-shrink: 0; flex-wrap: wrap; }

/* ---------- Badges (solid, high-contrast) ---------- */
.badge {
  display: inline-block;
  padding: .2rem .6rem;
  font-size: .72rem; font-weight: 700;
  border-radius: 3px;
  color: #FFFFFF;
  text-transform: uppercase; letter-spacing: .04em;
  white-space: nowrap;
}
.badge.critical { background: var(--critical); }
.badge.high     { background: var(--high); }
.badge.medium   { background: var(--medium); }
.badge.low      { background: var(--low); }
.badge.draft    { background: #FFFFFF; color: var(--ink); border: 1px solid var(--rule); }
.badge.reviewed { background: var(--good); }
.badge.accepted { background: var(--good); }
.badge.rejected { background: var(--low); }
.badge.closed   { background: var(--low); }
.badge.rule     { background: #FFFFFF; color: var(--brand);
                  border: 1px solid var(--brand); }

/* ---------- Finding description ---------- */
.finding .desc {
  font-size: .92rem; color: var(--ink);
  margin: .5rem 0 .7rem 0; line-height: 1.55;
}

/* ---------- Numbers grid ---------- */
.numbers {
  display: grid;
  grid-template-columns: repeat(4, minmax(0,1fr));
  gap: .5rem;
  background: var(--paper);
  padding: .7rem;
  border-radius: 3px;
  margin: .5rem 0;
}
.numbers .cell .lbl {
  font-size: .7rem;
  color: var(--ink-soft);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .05em;
  margin-bottom: .2rem;
}
.numbers .cell .val {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
  font-size: .95rem; font-weight: 700; color: var(--ink);
}
.numbers .cell .val.neg { color: var(--critical); }

/* ---------- AI interpretation panel ---------- */
.ai {
  background: var(--brand-tint);
  border-left: 3px solid var(--brand);
  padding: .7rem .9rem;
  margin: .6rem 0;
  border-radius: 2px;
}
.ai .lbl {
  font-size: .72rem;
  color: var(--brand);
  font-weight: 700;
  text-transform: uppercase; letter-spacing: .05em;
  margin-bottom: .35rem;
}
.ai p { margin: .35rem 0; font-size: .88rem; line-height: 1.5; }
.ai.blocked { background: #FFF3E0; border-left-color: var(--high); }
.ai.blocked .lbl { color: var(--high); }

/* ---------- Recommendations block ---------- */
.recs { margin-top: .6rem; }
.recs .rec {
  font-size: .87rem; line-height: 1.5; margin: .3rem 0;
  color: var(--ink);
}
.recs .rec strong { color: var(--brand-dark); font-weight: 700; }

/* ---------- Trace (source) ---------- */
.trace {
  font-family: var(--mono);
  font-size: .76rem;
  color: var(--ink-soft);
  margin-top: .7rem;
  padding-top: .5rem;
  border-top: 1px solid var(--rule);
}

/* ---------- Variance bridge ---------- */
.bridge {
  background: var(--card);
  border: 1px solid var(--rule);
  padding: 1rem 1.2rem;
  border-radius: 3px;
}
.bridge-row {
  display: grid;
  grid-template-columns: 1fr 8rem 9rem;
  align-items: center;
  gap: .8rem;
  padding: .5rem 0;
  border-bottom: 1px solid var(--rule);
}
.bridge-row:last-child { border-bottom: none; }
.bridge-row .lbl { font-size: .9rem; color: var(--ink); }
.bridge-row .mv, .bridge-row .pos {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
  text-align: right;
  font-size: .92rem; font-weight: 600;
}
.bridge-row .mv.up { color: var(--good); }
.bridge-row .mv.down { color: var(--critical); }
.bridge-row.total {
  border-top: 2px solid var(--brand);
  border-bottom: none;
  margin-top: .35rem;
  padding-top: .65rem;
}
.bridge-row.total .lbl { font-weight: 700; }
.bridge-row.total .pos { font-weight: 800; font-size: 1.05rem; color: var(--brand-dark); }
.bar { height: 6px; background: var(--rule); position: relative; border-radius: 3px; margin-top: .3rem; }
.bar > span { position: absolute; top: 0; bottom: 0; border-radius: 3px; }

/* ---------- Sidebar labels ---------- */
[data-testid="stSidebar"] .label { color: var(--ink); }

/* ---------- Streamlit tabs — match brand ---------- */
.stTabs [data-baseweb="tab-list"] { gap: .25rem; border-bottom: 2px solid var(--brand); }
.stTabs [data-baseweb="tab"] {
  font-weight: 600; font-size: .92rem;
  color: var(--ink-soft);
  padding: .5rem 1rem;
}
.stTabs [aria-selected="true"] { color: var(--brand); }

/* ---------- Buttons ---------- */
.stButton>button[kind="primary"] {
  background: var(--brand); border-color: var(--brand); font-weight: 600;
}
.stButton>button[kind="primary"]:hover {
  background: var(--brand-dark); border-color: var(--brand-dark);
}

@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

SEV_CLASS = {
    Severity.CRITICAL: "critical",
    Severity.HIGH: "high",
    Severity.MEDIUM: "medium",
    Severity.LOW: "low",
}
STATUS_CLASS = {
    Status.DRAFT: "draft",
    Status.REVIEWED: "reviewed",
    Status.ACCEPTED: "accepted",
    Status.REJECTED: "rejected",
    Status.CLOSED: "closed",
}


def section(title: str) -> None:
    """Render a Decima-red section bar."""
    st.markdown(f'<div class="section-bar">{title}</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Sidebar: inputs and the analysis run
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.markdown('<span class="label">Source data</span>', unsafe_allow_html=True)
    data_dir = st.text_input("Data directory", value="data", label_visibility="collapsed")

    st.markdown('<span class="label">Interpretation layer</span>', unsafe_allow_html=True)
    ai_choice = st.selectbox(
        "AI provider", ["none", "gemini", "deterministic"], index=0,
        label_visibility="collapsed",
        help="Figures are identical whichever you pick. 'none' runs the deterministic "
             "engine only. 'gemini' needs GEMINI_API_KEY. 'deterministic' generates the "
             "same fields from templates so the tool is demonstrable offline.")

    st.markdown('<span class="label">BR-04 thresholds</span>', unsafe_allow_html=True)
    movement_abs = st.number_input("Absolute ($)", value=2_000_000, step=250_000,
                                   format="%d")
    movement_pct = st.number_input("Percent of budget", value=3.0, step=0.5) / 100

    if st.button("Run analysis", type="primary", use_container_width=True):
        thresholds = Thresholds(movement_abs=Decimal(str(int(movement_abs))),
                                movement_pct=Decimal(str(movement_pct)))
        interpreter = None if ai_choice == "none" else build_interpreter(ai_choice)
        with st.spinner("Calculating"):
            try:
                st.session_state.result = run(data_dir, thresholds=thresholds,
                                              interpreter=interpreter)
            except Exception as exc:
                st.session_state.result = None
                st.error(f"Analysis could not complete: {exc}")

    reviewer = st.text_input("Reviewer", value="", placeholder="your name",
                             help="Recorded against every status change in the audit log.")
    st.session_state.reviewer = reviewer

# Run once on first load so the tool opens on a populated review sheet rather
# than an empty state. The deterministic engine takes milliseconds.
if "result" not in st.session_state:
    try:
        st.session_state.result = run(data_dir, log=True)
    except Exception as exc:
        st.session_state.result = None
        st.error(f"Could not load {data_dir}: {exc}")

result = st.session_state.get("result")
if result is None:
    st.markdown(
        '<div class="page-head"><h1>Project Cost Controls Review</h1>'
        '<div class="meta">No analysis loaded</div></div>', unsafe_allow_html=True)
    st.info("Point the sidebar at a directory of cost-control CSV files and select "
            "**Run analysis**. All findings arrive as Draft and stay there until "
            "a reviewer moves them.")
    st.stop()
summary = result.summary


# --------------------------------------------------------------------------- #
# Header
# --------------------------------------------------------------------------- #
st.markdown(
    f'<div class="page-head">'
    f'<h1>{summary.project} — Cost Review, {summary.period}</h1>'
    f'<div class="meta">Run {result.run_id} &nbsp;·&nbsp; engine {ENGINE_VERSION} '
    f'&nbsp;·&nbsp; ruleset {RULESET_VERSION} &nbsp;·&nbsp; '
    f'AI {result.ai_provider}</div>'
    f'</div>', unsafe_allow_html=True)

for warning in result.warnings:
    st.warning(warning)


def tile(column, label, value, note="", negative=False):
    column.markdown(
        f'<div class="tile">'
        f'<span class="label">{label}</span>'
        f'<div class="value{" neg" if negative else ""}">{value}</div>'
        f'<div class="note">{note}</div>'
        f'</div>', unsafe_allow_html=True)


cols = st.columns(5)
tile(cols[0], "Current budget", fmt(summary.total_budget))
tile(cols[1], "Calculated EAC", fmt(summary.calculated_eac),
     f"reported {fmt(summary.reported_eac)}")
tile(cols[2], "Calculated VAC", fmt(summary.calculated_vac),
     f"reported {fmt(summary.reported_vac)}", summary.calculated_vac < 0)
tile(cols[3], "Adjusted VAC", fmt(summary.adjusted_vac),
     f"{summary.adjusted_vac_pct}% of budget after exposure",
     summary.adjusted_vac < 0)
tile(cols[4], "Net headroom", fmt(summary.net_headroom),
     "contingency less exposure and pending draws", summary.net_headroom < 0)

st.write("")
tabs = st.tabs(["Findings register", "Variance bridge", "Packages",
                "Executive summary", "Provenance"])


# --------------------------------------------------------------------------- #
# Findings register
# --------------------------------------------------------------------------- #
with tabs[0]:
    section("Findings register")

    filters = st.columns([1, 1, 1, 1.4])
    sev_filter = filters[0].multiselect("Severity", [s.value for s in Severity],
                                        default=["Critical", "High"])
    type_filter = filters[1].multiselect("Classification",
                                         [t.value for t in FindingType],
                                         default=[t.value for t in FindingType])
    status_filter = filters[2].multiselect("Status", [s.value for s in Status],
                                          default=[s.value for s in Status])
    rules = sorted({f.rule_id for f in result.findings})
    rule_filter = filters[3].multiselect("Rule", rules, default=rules)

    visible = [f for f in result.findings
               if f.severity.value in sev_filter
               and f.finding_type.value in type_filter
               and f.status.value in status_filter
               and f.rule_id in rule_filter]

    st.markdown(
        f'<p style="color:var(--ink-soft); font-size:.88rem; margin:.4rem 0 .8rem 0;">'
        f'Showing <strong>{len(visible)}</strong> of {len(result.findings)} findings '
        f'&nbsp;·&nbsp; {summary.counts["confirmed_errors"]} confirmed errors, '
        f'{summary.counts["requires_explanation"]} requiring explanation'
        f'</p>', unsafe_allow_html=True)

    for finding in visible:
        sev_class = SEV_CLASS[finding.severity]
        status_class = STATUS_CLASS[finding.status]

        def cell(label, value):
            shown = fmt(value) if isinstance(value, Decimal) else (value or "—")
            neg = isinstance(value, Decimal) and value < 0
            return (f'<div class="cell">'
                    f'<div class="lbl">{label}</div>'
                    f'<div class="val{" neg" if neg else ""}">{shown}</div>'
                    f'</div>')

        cost_code_suffix = "" if finding.cost_code == "PROJECT" else f" · {finding.cost_code}"
        body = (
            f'<div class="finding sev-{sev_class}">'
            f'<div class="finding-head">'
            f'  <div>'
            f'    <div class="finding-title">{finding.finding_id} — {finding.package}{cost_code_suffix}</div>'
            f'    <div class="finding-sub">'
            f'      {finding.finding_category.value} &nbsp;·&nbsp; {finding.finding_type.value} '
            f'&nbsp;·&nbsp; confidence {finding.confidence}%'
            f'    </div>'
            f'  </div>'
            f'  <div class="finding-badges">'
            f'    <span class="badge rule">{finding.rule_id}</span>'
            f'    <span class="badge {sev_class}">{finding.severity.value}</span>'
            f'    <span class="badge {status_class}">{finding.status.value}</span>'
            f'  </div>'
            f'</div>'
            f'<div class="desc">{finding.finding_description}</div>'
            f'<div class="numbers">'
            f'{cell("Reported", finding.reported_value)}'
            f'{cell("Calculated", finding.calculated_value)}'
            f'{cell("Difference", finding.difference)}'
            f'{cell("Potential exposure", finding.potential_exposure)}'
            f'</div>'
        )

        if finding.ai.explanation or finding.ai.guardrail == "blocked_numeric":
            blocked = finding.ai.guardrail != "passed"
            body += (
                f'<div class="ai{" blocked" if blocked else ""}">'
                f'<div class="lbl">AI interpretation · {finding.ai.provider} '
                f'{finding.ai.model} · guardrail {finding.ai.guardrail}</div>'
                f'<p>{finding.ai.explanation}</p>'
                + (f'<p><strong>Proposed severity:</strong> {finding.ai.proposed_severity} '
                   f'— {finding.ai.severity_rationale}</p>'
                   if finding.ai.proposed_severity else "")
                + '</div>')

        body += (
            f'<div class="recs">'
            f'<div class="rec"><strong>Recommended review:</strong> '
            f'{finding.ai.recommended_review or finding.recommended_review}</div>'
            f'<div class="rec"><strong>Recommended action:</strong> '
            f'{finding.ai.recommended_action or finding.recommended_action}</div>'
            f'</div>'
            f'<div class="trace">Source: {finding.source_file} &nbsp;›&nbsp; '
            f'{finding.source_reference}</div>'
            f'</div>')

        st.markdown(body, unsafe_allow_html=True)

        with st.expander(f"Review {finding.finding_id}", expanded=False):
            from costctl.models import VALID_TRANSITIONS
            options = [s.value for s in VALID_TRANSITIONS[finding.status]]
            if not options:
                st.caption("This finding is Closed. No further transitions are permitted.")
            else:
                left, right = st.columns([1, 2])
                target = left.selectbox("Move to", options,
                                        key=f"target_{finding.finding_id}")
                note = right.text_input("Reviewer note",
                                        key=f"note_{finding.finding_id}")
                if st.button("Record decision", key=f"btn_{finding.finding_id}"):
                    try:
                        set_status(finding, Status(target),
                                   reviewer=st.session_state.get("reviewer", ""),
                                   note=note, run_id=result.run_id)
                        st.success(f"{finding.finding_id} moved to {target}")
                        st.rerun()
                    except InvalidTransition as exc:
                        st.error(str(exc))
            if finding.evidence:
                st.markdown('<span class="label">Supporting evidence</span>',
                            unsafe_allow_html=True)
                st.json({k: v for k, v in finding.evidence.items() if v is not None})


# --------------------------------------------------------------------------- #
# Variance bridge — the signature view
# --------------------------------------------------------------------------- #
with tabs[1]:
    section("Variance bridge — reported position walked to adjusted position")

    scale = max(abs(step.running) for step in summary.bridge) or Decimal("1")
    rows = ""
    for index, step in enumerate(summary.bridge):
        last = index == len(summary.bridge) - 1
        width = (abs(step.running) / scale * 100).quantize(Decimal("0.1"))
        colour = "var(--critical)" if step.running < 0 else "var(--brand)"
        if step.amount is None:
            movement = "&nbsp;"
            klass = ""
        else:
            movement = fmt(step.amount, signed=True)
            klass = "up" if step.amount > 0 else "down"
        rows += (
            f'<div class="bridge-row{" total" if last else ""}">'
            f'<div class="lbl">{step.label}'
            f'<div class="bar"><span style="width:{width}%;background:{colour}"></span></div>'
            f'</div>'
            f'<div class="mv {klass}">{movement}</div>'
            f'<div class="pos">{fmt(step.running)}</div></div>')
    st.markdown(f'<div class="bridge">{rows}</div>', unsafe_allow_html=True)

    st.write("")
    st.markdown(
        f"Adjusted variance at completion is **{fmt(summary.adjusted_vac)}**, "
        f"{summary.adjusted_vac_pct}% of a {fmt(summary.total_budget)} budget.")
    with st.expander("Why contingency draws are not deducted here"):
        st.markdown(
            "Approved contingency draws fund costs that already sit inside package "
            "forecasts, so deducting them from VAC would count the same money twice. "
            "Contingency is therefore reported separately as available cover.")

    st.write("")
    left, right = st.columns(2)
    with left:
        section("Contingency")
        st.dataframe({
            "Measure": ["Opening balance", "Approved usage", "Remaining (approved basis)",
                        "Pending draws", "Balance per register", "Net headroom"],
            "Amount": [fmt(summary.contingency_opening),
                       fmt(summary.contingency_approved_usage),
                       fmt(summary.contingency_remaining_approved_basis),
                       fmt(summary.contingency_pending_usage),
                       fmt(summary.contingency_reported_remaining),
                       fmt(summary.net_headroom)],
        }, hide_index=True, use_container_width=True)
    with right:
        section("Other exposures")
        st.dataframe({
            "Measure": ["Excluded change exposure (BR-06)",
                        "Unapproved change inside forecast",
                        "Unfunded commitments (BR-05)", "Uncommitted budget"],
            "Amount": [fmt(summary.excluded_change_exposure),
                       fmt(summary.unapproved_change_in_forecast),
                       fmt(summary.unfunded_commitments),
                       fmt(summary.uncommitted_budget)],
        }, hide_index=True, use_container_width=True)


# --------------------------------------------------------------------------- #
# Packages
# --------------------------------------------------------------------------- #
with tabs[2]:
    section("Package position — calculated basis")

    model = result.model
    table = {"Cost code": [], "Package": [], "Budget": [], "Commitments": [],
             "Actual": [], "FTC": [], "Calculated EAC": [], "Calculated VAC": [],
             "Movement": [], "Findings": []}
    counts = {}
    for finding in result.findings:
        counts[finding.cost_code] = counts.get(finding.cost_code, 0) + 1
    for code, line in sorted(model.current.items(), key=lambda kv: int(kv[0])):
        table["Cost code"].append(code)
        table["Package"].append(line.package)
        table["Budget"].append(fmt(line.current_budget))
        table["Commitments"].append(fmt(line.commitments))
        table["Actual"].append(fmt(line.actual_cost))
        table["FTC"].append(fmt(line.forecast_to_complete))
        table["Calculated EAC"].append(fmt(line.calculated_eac))
        table["Calculated VAC"].append(fmt(line.calculated_vac))
        table["Movement"].append(fmt(model.movement(code), signed=True))
        table["Findings"].append(counts.get(code, 0))
    st.dataframe(table, hide_index=True, use_container_width=True)

    section("Packages requiring management attention")
    st.dataframe({
        "Cost code": [e["cost_code"] for e in summary.watchlist],
        "Package": [e["package"] for e in summary.watchlist],
        "Worst severity": [e["worst_severity"].value for e in summary.watchlist],
        "Findings": [e["findings"] for e in summary.watchlist],
        "Exposure": [fmt(e["exposure"]) for e in summary.watchlist],
        "Rules": [", ".join(e["rules"]) for e in summary.watchlist],
    }, hide_index=True, use_container_width=True)


# --------------------------------------------------------------------------- #
# Executive summary and downloads
# --------------------------------------------------------------------------- #
with tabs[3]:
    section("Executive summary")

    markdown = render_markdown(summary, result.findings)
    left, right = st.columns(2)
    left.download_button("Download executive_summary.md", markdown,
                         file_name="executive_summary.md", use_container_width=True)
    right.download_button("Download findings.json",
                          json.dumps(result.to_document(), indent=2),
                          file_name="findings.json", mime="application/json",
                          use_container_width=True)
    st.write("")
    st.markdown(markdown)


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
with tabs[4]:
    section("Provenance — inputs, versions and thresholds")
    st.json(result.provenance())

    section("Audit log")
    from costctl.audit import read_events
    events = read_events()
    if events:
        st.dataframe({
            "Timestamp": [e["timestamp"] for e in events[-50:]],
            "Event": [e["event_type"] for e in events[-50:]],
            "Actor": [e["actor"] for e in events[-50:]],
            "Detail": [json.dumps(e["payload"])[:160] for e in events[-50:]],
        }, hide_index=True, use_container_width=True)
    else:
        st.caption("No audit events recorded yet.")
