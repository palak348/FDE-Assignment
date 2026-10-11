"""AI Procurement Request Copilot - Streamlit product UI.

Run from the project root:  streamlit run app.py   (or: python run_local.py)
The copilot is advisory only: it never approves or purchases anything.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_access import load_employees, load_requests, records  # noqa: E402

AUDIT_LOG = ROOT / "audit_log.jsonl"

DATA_ACCESS_LEVELS = [
    "none", "internal_documents", "internal_marketing", "confidential_documents", "employee_pii",
    "customer_pii", "source_code", "production_telemetry", "credentials", "unknown",
]
URGENCY_LEVELS = ["normal", "high", "urgent"]

ARCH_LABELS = {
    "single": "A · Single agent",
    "staged": "B · Staged (analyst → reviewer)",
}
ARCH_CAPTIONS = {
    "single": "One agent, six tools · ~2 LLM calls · shipped",
    "staged": "Analyst gathers, reviewer decides · ~3 LLM calls",
}

ACTION_STYLES = {
    # next_action: (label, accent colour)
    "route_for_approval": ("Route for approval", "#3b82f6"),
    "request_info": ("Request more information", "#d97706"),
    "reuse_existing": ("Reuse existing tool", "#8b5cf6"),
    "manual_review": ("Manual review", "#ea580c"),
    "reject": ("Reject", "#dc2626"),
}

HUMAN_DECISIONS = ["Route to approvers", "Request more information", "Reject", "Override / exception"]

WORKFLOW = [
    ("Request", "Employee submits what they need and why"),
    ("Understand", "The agent reads the need (request text is untrusted)"),
    ("Evidence", "Tools check budget, catalog, vendor registry and vendor risk"),
    ("Rules", "Code applies thresholds, reviews, expiry and conflicts"),
    ("Recommend", "Next action with evidence, approvals and risk flags"),
    ("Human", "A reviewer makes and records the actual decision"),
]

# Colours use translucent tints so the page reads well in both light and dark themes.
CSS = """
<style>
#MainMenu, footer {visibility: hidden;}
.block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1400px;}
h1, h2, h3 {letter-spacing: -0.01em;}

.pc-hero {display:flex; justify-content:space-between; align-items:flex-end; gap:1rem; flex-wrap:wrap;
          padding-bottom:1.1rem; margin-bottom:1.4rem; border-bottom:1px solid rgba(128,128,128,0.22);}
.pc-eyebrow {font-size:0.75rem; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; color:#6366f1;}
.pc-title {font-size:2rem; font-weight:700; line-height:1.15; margin:0.2rem 0 0.35rem;}
.pc-sub {opacity:0.72; font-size:0.95rem; max-width:720px;}
.pc-pill {display:inline-flex; align-items:center; gap:0.45rem; padding:0.4rem 0.8rem; border-radius:999px;
          font-size:0.82rem; font-weight:600; background:rgba(34,197,94,0.12); color:#16a34a;
          border:1px solid rgba(34,197,94,0.35); white-space:nowrap;}
.pc-dot {width:7px; height:7px; border-radius:50%; background:currentColor; display:inline-block;}

.pc-section {font-size:0.78rem; font-weight:700; letter-spacing:0.08em; text-transform:uppercase;
             opacity:0.6; margin:0.2rem 0 0.6rem;}

.pc-stats {display:grid; grid-template-columns:repeat(var(--cols, 4), minmax(0, 1fr)); gap:0.6rem; margin-bottom:0.9rem;}
@media (max-width: 1100px) {.pc-stats {grid-template-columns:repeat(2, minmax(0, 1fr));}}
.pc-stat {border:1px solid rgba(128,128,128,0.22); border-radius:10px; padding:0.65rem 0.8rem;
          background:rgba(128,128,128,0.05);}
.pc-stat-label {font-size:0.72rem; font-weight:600; text-transform:uppercase; letter-spacing:0.06em; opacity:0.6;}
.pc-stat-value {font-size:1.15rem; font-weight:650; margin-top:0.15rem; overflow-wrap:break-word; line-height:1.3;}
.pc-stat-value.small {font-size:0.95rem;}
.pc-stat {min-width:0;}

.pc-kv {display:grid; grid-template-columns:max-content 1fr; column-gap:1.4rem; row-gap:0.5rem;
        border:1px solid rgba(128,128,128,0.22); border-radius:10px; padding:0.85rem 1rem; margin-bottom:0.9rem;
        font-size:0.92rem;}
.pc-kv dt {opacity:0.6; font-weight:500;}
.pc-kv dd {margin:0; font-weight:500; overflow-wrap:anywhere;}

.pc-untrusted {border:1px dashed rgba(217,119,6,0.7); border-radius:10px; padding:0.8rem 1rem;
               background:rgba(217,119,6,0.07); font-size:0.95rem; line-height:1.5;}
.pc-untrusted-label {font-size:0.7rem; font-weight:700; text-transform:uppercase; color:#d97706;
                     letter-spacing:0.08em; margin-bottom:0.35rem;}

.pc-card {border:1px solid rgba(128,128,128,0.25); border-radius:12px; padding:1.1rem 1.2rem;
          background:rgba(128,128,128,0.04); margin-bottom:0.9rem;}
.pc-card-accent {border-left-width:5px;}
.pc-card-head {display:flex; justify-content:space-between; align-items:center; gap:0.6rem; flex-wrap:wrap;
               margin-bottom:0.8rem;}
.pc-badge {display:inline-block; padding:0.32rem 0.8rem; border-radius:999px; font-weight:650; font-size:0.88rem;
           border:1px solid currentColor;}
.pc-arch {font-size:0.8rem; opacity:0.65; font-weight:500;}
.pc-reco {font-size:1.12rem; font-weight:600; line-height:1.45; margin-bottom:0.8rem;}
.pc-next {border-radius:8px; padding:0.6rem 0.8rem; background:rgba(99,102,241,0.09); font-size:0.93rem;
          margin-bottom:0.9rem;}
.pc-next b {color:#6366f1;}
.pc-grid2 {display:grid; grid-template-columns:1fr 1fr; gap:0.9rem 1.2rem;}
@media (max-width: 900px) {.pc-grid2 {grid-template-columns:1fr;}}
.pc-label {font-size:0.72rem; font-weight:700; text-transform:uppercase; letter-spacing:0.06em; opacity:0.6;
           margin-bottom:0.35rem;}
.pc-chip {display:inline-block; padding:0.18rem 0.6rem; margin:0 0.3rem 0.35rem 0; border-radius:6px;
          font-size:0.83rem; font-weight:500; background:rgba(99,102,241,0.12); color:#6366f1;
          border:1px solid rgba(99,102,241,0.3);}
.pc-chip-risk {background:rgba(220,38,38,0.1); color:#e04848; border-color:rgba(220,38,38,0.3);}
.pc-muted {opacity:0.55; font-size:0.88rem;}
.pc-list {margin:0; padding-left:1.1rem; font-size:0.92rem;}
.pc-foot {margin-top:0.9rem; padding-top:0.7rem; border-top:1px solid rgba(128,128,128,0.2); font-size:0.82rem;
          opacity:0.7;}

.pc-steps {display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:0.6rem;}
.pc-step {border:1px solid rgba(128,128,128,0.22); border-radius:10px; padding:0.7rem 0.8rem;}
.pc-step-n {display:inline-flex; width:1.5rem; height:1.5rem; border-radius:50%; align-items:center;
            justify-content:center; font-size:0.78rem; font-weight:700; background:rgba(99,102,241,0.14);
            color:#6366f1; margin-bottom:0.35rem;}
.pc-step-t {font-weight:650; font-size:0.92rem;}
.pc-step-d {font-size:0.8rem; opacity:0.68; line-height:1.4; margin-top:0.15rem;}

.pc-table {width:100%; border-collapse:collapse; font-size:0.88rem; margin-bottom:0.6rem;}
.pc-table th {text-align:left; font-size:0.72rem; font-weight:700; text-transform:uppercase; letter-spacing:0.06em;
              opacity:0.6; padding:0.5rem 0.7rem; border-bottom:1px solid rgba(128,128,128,0.3);}
.pc-table td {padding:0.55rem 0.7rem; border-bottom:1px solid rgba(128,128,128,0.15); vertical-align:top;
              line-height:1.45; overflow-wrap:anywhere;}
.pc-table td.pc-src {white-space:nowrap; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:0.8rem;
                     color:#6366f1;}
.pc-table td.pc-ref {opacity:0.7; font-size:0.82rem; width:24%;}
.pc-brand {font-weight:700; font-size:1.05rem; margin-bottom:0.1rem;}
.pc-brand-sub {font-size:0.8rem; opacity:0.6; margin-bottom:0.6rem;}
</style>
"""


# ------------------------------------------------------------------ helpers

def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def humanize(value: Any) -> str:
    """customer_pii -> Customer PII, prompt_injection_detected -> Prompt injection detected."""
    if value is None or value == "":
        return "-"
    words = str(value).replace("_", " ").split()
    acronyms = {"pii": "PII", "api": "API", "llm": "LLM", "cfo": "CFO", "sso": "SSO", "crm": "CRM"}
    out = [acronyms.get(w.lower(), w) for w in words]
    if out and out[0] == out[0].lower():
        out[0] = out[0].capitalize()
    return " ".join(out)


def chips(items: list[str], risk: bool = False, pretty: bool = False) -> str:
    if not items:
        return '<span class="pc-muted">None</span>'
    cls = "pc-chip pc-chip-risk" if risk else "pc-chip"
    return "".join(f'<span class="{cls}">{_esc(humanize(i) if pretty else i)}</span>' for i in items)


def action_badge(next_action: str | None) -> tuple[str, str]:
    label, colour = ACTION_STYLES.get(next_action or "", (humanize(next_action) if next_action else "Unknown", "#6b7280"))
    badge = (f'<span class="pc-badge" style="color:{colour}; background:{colour}1f; border-color:{colour}66;">'
             f'{_esc(label)}</span>')
    return badge, colour


def stat_tiles(items: list[tuple[str, Any]], small: bool = False, cols: int | None = None) -> str:
    cls = "pc-stat-value small" if small else "pc-stat-value"
    tiles = "".join(f'<div class="pc-stat"><div class="pc-stat-label">{_esc(k)}</div>'
                    f'<div class="{cls}">{_esc(v)}</div></div>' for k, v in items)
    return f'<div class="pc-stats" style="--cols:{cols or len(items)}">{tiles}</div>'


def section(title: str) -> None:
    st.markdown(f'<div class="pc-section">{_esc(title)}</div>', unsafe_allow_html=True)


def _as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return dict(obj)


def fmt_money(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"${value:,.0f}"
    return "Unknown"


def fmt_ms(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value / 1000:.1f} s" if value >= 1000 else f"{value:.0f} ms"
    return "-"


@st.cache_data(show_spinner=False)
def _requests() -> list[dict]:
    return load_requests()


@st.cache_data(show_spinner=False)
def _employees() -> list[dict]:
    return records(load_employees())


def _employee_label(emp_id: str | None) -> str:
    for e in _employees():
        if e["employee_id"] == emp_id:
            return f"{e['name']} ({e['department']}, {emp_id})"
    return emp_id or "Unknown"


def run_analysis(request: str | dict, architecture: str) -> tuple[Any, dict]:
    """Call the backend (slow: real LLM call). Imported lazily so the UI loads even if the LLM is misconfigured."""
    from src.solution import handle_request_payload, run_with_trace

    if isinstance(request, dict):
        return handle_request_payload(request, architecture)
    return run_with_trace(request, architecture)


# ------------------------------------------------------------------ audit log

def append_audit(entry: dict) -> None:
    with AUDIT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_audit(request_id: str, limit: int = 5) -> list[dict]:
    if not AUDIT_LOG.is_file():
        return []
    rows = []
    for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("request_id") == request_id:
            rows.append(row)
    return rows[-limit:][::-1]


# ------------------------------------------------------------------ rendering

def render_header() -> None:
    st.markdown(
        '<div class="pc-hero"><div style="flex:1 1 520px;">'
        '<div class="pc-eyebrow">Internal procurement · AI copilot</div>'
        '<div class="pc-title">Procurement Request Copilot</div>'
        '<div class="pc-sub">Checks software purchase requests against policy, budget, vendor risk and existing '
        'tools, then recommends the next step with the evidence behind it.</div></div>'
        '<span class="pc-pill"><span class="pc-dot"></span>Advisory only · humans approve every purchase</span>'
        '</div>',
        unsafe_allow_html=True,
    )


def render_request_details(req: dict, trace: dict | None = None) -> None:
    section("Request")
    users = req.get("user_count")
    st.markdown(stat_tiles([
        ("Annual cost", fmt_money(req.get("annual_cost_usd"))),
        ("Users", users if users is not None else "Unknown"),
        ("Urgency", humanize(req.get("urgency"))),
        ("Data access", humanize(req.get("data_access_level"))),
    ], cols=2), unsafe_allow_html=True)

    integrations = req.get("requested_integrations") or []
    rows = [
        ("Request ID", req.get("request_id") or "Assigned on submit"),
        ("Requester", _employee_label(req.get("requester_id"))),
        ("Product", req.get("product_name") or "-"),
        ("Vendor", req.get("vendor_name") or "-"),
        ("Category", req.get("category") or "-"),
        ("Integrations", ", ".join(integrations) if integrations else "None"),
    ]
    st.markdown('<dl class="pc-kv">' + "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in rows)
                + "</dl>", unsafe_allow_html=True)

    st.markdown(
        '<div class="pc-untrusted"><div class="pc-untrusted-label">Business justification · untrusted text</div>'
        f'{_esc(req.get("business_justification") or "(no justification provided)")}</div>',
        unsafe_allow_html=True,
    )
    injection = ((trace or {}).get("rules") or {}).get("prompt_injection") or []
    if injection:
        fields = ", ".join(sorted({h.get("field", "?") for h in injection}))
        st.warning(
            f"Instruction-like text was found in **{fields}** and ignored. It is treated as data, never as "
            "instructions to the copilot. A human reviewer should check this request.",
            icon=":material/gpp_maybe:",
        )
        with st.expander("Detected text"):
            for h in injection:
                st.markdown(f"- `{h.get('field')}`: \"{h.get('matched_text')}\"")
    elif trace is None:
        st.caption("Requester text is screened for embedded instructions when the analysis runs.")


def render_empty_state(architecture: str) -> None:
    section("Recommendation")
    st.markdown(
        '<div class="pc-card"><div class="pc-reco">No analysis yet</div>'
        f'<div class="pc-muted" style="opacity:0.75">Click <b>Run analysis</b> in the sidebar to review this request '
        f'with <b>{_esc(ARCH_LABELS[architecture])}</b>. It usually takes 15–35 seconds.</div></div>',
        unsafe_allow_html=True,
    )
    section("How it works")
    steps = "".join(
        f'<div class="pc-step"><div class="pc-step-n">{i}</div><div class="pc-step-t">{_esc(t)}</div>'
        f'<div class="pc-step-d">{_esc(d)}</div></div>'
        for i, (t, d) in enumerate(WORKFLOW, 1))
    st.markdown(f'<div class="pc-steps">{steps}</div>', unsafe_allow_html=True)


def render_recommendation(d: dict, trace: dict, tel: dict) -> None:
    section("Recommendation")
    badge, colour = action_badge(d.get("next_action"))
    arch = ARCH_LABELS.get(trace.get("architecture") or tel.get("architecture"), "")
    missing = d.get("missing_information") or []
    missing_html = ("<ul class='pc-list'>" + "".join(f"<li>{_esc(m)}</li>" for m in missing) + "</ul>"
                    if missing else '<span class="pc-muted">None</span>')
    st.markdown(
        f'<div class="pc-card pc-card-accent" style="border-left-color:{colour};">'
        f'<div class="pc-card-head">{badge}<span class="pc-arch">{_esc(arch)}</span></div>'
        f'<div class="pc-reco">{_esc(d.get("recommendation"))}</div>'
        f'<div class="pc-next"><b>Next step</b> &nbsp;{_esc(d.get("next_step"))}</div>'
        '<div class="pc-grid2">'
        f'<div><div class="pc-label">Required approvals</div>{chips(d.get("required_approvals") or [])}</div>'
        f'<div><div class="pc-label">Risk flags</div>{chips(d.get("risk_flags") or [], risk=True, pretty=True)}</div>'
        f'<div style="grid-column:1/-1"><div class="pc-label">Missing information</div>{missing_html}</div>'
        '</div>'
        '<div class="pc-foot">Human review required · the copilot never approves, purchases or signs anything.</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def render_run_stats(trace: dict, tel: dict) -> None:
    prompt_t, completion_t = tel.get("prompt_tokens"), tel.get("completion_tokens")
    tokens = (prompt_t or 0) + (completion_t or 0) if (prompt_t is not None or completion_t is not None) else None
    tool_calls = tel.get("tool_calls") if tel.get("tool_calls") is not None else len(trace.get("tool_calls") or [])
    st.markdown(stat_tiles([
        ("Latency", fmt_ms(trace.get("latency_ms"))),
        ("LLM calls", tel.get("llm_calls") if tel.get("llm_calls") is not None else "-"),
        ("Tool calls", tool_calls),
        ("Tokens", f"{tokens:,}" if tokens is not None else "-"),
        ("Model", tel.get("model") or "-"),
    ], small=True), unsafe_allow_html=True)


def render_details(d: dict, trace: dict, tel: dict) -> None:
    tool_calls = trace.get("tool_calls") or []
    corrections = tel.get("guardrail_corrections") or []
    staged = (trace.get("architecture") or tel.get("architecture")) == "staged"
    names = [f"Evidence ({len(d.get('evidence') or [])})", f"Tool calls ({len(tool_calls)})", "Rule checks",
             f"Guardrails ({len(corrections)})", "Model output"]
    if staged:
        names.append("Evidence pack")
    tabs = st.tabs(names)

    with tabs[0]:
        evidence = d.get("evidence") or []
        if evidence:
            body = "".join(
                f'<tr><td class="pc-src">{_esc(e.get("source"))}</td><td>{_esc(e.get("finding"))}</td>'
                f'<td class="pc-ref">{_esc(e.get("reference") or "")}</td></tr>' for e in evidence)
            st.markdown('<table class="pc-table"><thead><tr><th>Source tool</th><th>Finding</th><th>Reference</th>'
                        f'</tr></thead><tbody>{body}</tbody></table>', unsafe_allow_html=True)
        else:
            st.info("No evidence items were returned.")
        st.caption("Every item must cite a tool that was actually called; numbers must appear in that tool's output.")
        if tel.get("ungrounded_evidence_dropped"):
            st.caption(f"{tel['ungrounded_evidence_dropped']} model evidence item(s) were dropped because they "
                       "could not be grounded in a tool result.")

    with tabs[1]:
        if not tool_calls:
            st.caption("No tool calls recorded.")
        else:
            st.dataframe(
                [{"#": i, "Tool": c.get("name"),
                  "Arguments": ", ".join(f"{k}={v}" for k, v in (c.get("arguments") or {}).items()) or "-",
                  "Latency": fmt_ms(c.get("latency_ms"))} for i, c in enumerate(tool_calls, 1)],
                width="stretch", hide_index=True,
            )
            for i, call in enumerate(tool_calls, 1):
                with st.expander(f"{i}. {call.get('name')} - result"):
                    st.json(call.get("result"), expanded=False)

    with tabs[2]:
        rules = trace.get("rules") or {}
        if not rules:
            st.caption("No rule result available.")
        else:
            tier = rules.get("approval_tier") or {}
            budget = rules.get("budget") or {}
            st.markdown(stat_tiles([
                ("Approval tier", humanize(tier.get("tier"))),
                ("Budget", humanize(budget.get("status"))),
                ("Baseline action", humanize(rules.get("baseline_next_action"))),
                ("Reference date", rules.get("reference_date") or "-"),
            ], small=True), unsafe_allow_html=True)
            for reason in (tier.get("reason"), budget.get("reason")):
                if reason:
                    st.caption(reason)
            st.caption(f"Department head: {rules.get('department_head') or '-'}")

            triggers = rules.get("review_triggers") or []
            st.markdown("**Review triggers**")
            if triggers:
                st.dataframe(
                    [{"Review": t.get("review"), "Reason": t.get("reason"), "Policy": t.get("policy_section")}
                     for t in triggers],
                    width="stretch", hide_index=True,
                )
            else:
                st.caption("None")
            matches = rules.get("existing_tool_matches") or []
            if matches:
                st.markdown("**Existing tool matches**")
                st.dataframe(
                    [{"Product": m.get("product_name"), "Vendor": m.get("vendor_name"), "Status": m.get("status"),
                      "Scope": m.get("scope"), "Match": ", ".join(m.get("match_reasons") or []),
                      "Available to requester": m.get("available_to_requester")} for m in matches],
                    width="stretch", hide_index=True,
                )
            with st.expander("Vendor assessment"):
                st.json(rules.get("vendor") or {}, expanded=True)
            with st.expander("Full rule result"):
                st.json(rules, expanded=False)

    with tabs[3]:
        st.caption("After the model answers, deterministic code checks its output against the policy rules. "
                   "These are the fields code added or overrode.")
        if corrections:
            st.markdown("\n".join(f"- {_esc(c)}" for c in corrections))
        else:
            st.success("No corrections needed - the model output already followed policy.", icon=":material/check:")

    with tabs[4]:
        raw = trace.get("raw_llm_decision")
        st.caption("The model's decision before guardrails were applied.")
        if raw is None:
            st.caption("No model output available (the LLM call failed or returned nothing usable).")
        else:
            st.json(_as_dict(raw) if not isinstance(raw, (str, list)) else raw, expanded=False)

    if staged:
        with tabs[5]:
            st.caption("Stage 1 (Procurement Analyst) hand-off to stage 2 (Policy/Risk Reviewer).")
            pack = trace.get("evidence_pack")
            if pack is None:
                st.caption("No evidence pack recorded.")
            elif isinstance(pack, str):
                st.markdown(pack)
            else:
                st.json(pack, expanded=True)


def render_result(decision: Any, trace: dict) -> None:
    """Render the recommendation card, run statistics and the detail tabs for one run."""
    d = _as_dict(decision)
    trace = trace or {}
    tel = d.get("telemetry") or {}

    if tel.get("llm_error") or trace.get("error"):
        st.error("AI analysis unavailable - showing deterministic rule checks only.", icon=":material/warning:")
        st.caption(f"Reason: {tel.get('llm_error') or trace.get('error')}")

    render_recommendation(d, trace, tel)
    render_run_stats(trace, tel)


def render_human_review(decision: Any, architecture: str) -> None:
    d = _as_dict(decision)
    request_id = d.get("request_id")
    section("Human review")
    with st.container(border=True):
        st.caption("The procurement reviewer records the actual decision here. The AI recommendation is never "
                   "executed - only this human decision is written to the audit log.")
        with st.form(f"review_{request_id}_{architecture}", clear_on_submit=True, border=False):
            human_decision = st.radio("Decision", HUMAN_DECISIONS, horizontal=True)
            c1, c2 = st.columns([1, 2])
            reviewer = c1.text_input("Reviewer name")
            comment = c2.text_area("Comment (required)", height=80)
            submitted = st.form_submit_button("Record decision", type="primary")
        if submitted:
            if not reviewer.strip() or not comment.strip():
                st.error("Reviewer name and comment are both required.")
            else:
                append_audit({
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "request_id": request_id,
                    "architecture": architecture,
                    "ai_next_action": d.get("next_action"),
                    "human_decision": human_decision,
                    "reviewer": reviewer.strip(),
                    "comment": comment.strip(),
                    "required_approvals": d.get("required_approvals") or [],
                })
                st.success(f"Decision recorded for {request_id}: {human_decision}.", icon=":material/check:")

        history = read_audit(request_id)
        st.markdown("**Audit log for this request**")
        if history:
            st.dataframe(
                [{"Time (UTC)": h.get("timestamp"), "Reviewer": h.get("reviewer"),
                  "Human decision": h.get("human_decision"), "AI next action": humanize(h.get("ai_next_action")),
                  "Architecture": h.get("architecture"), "Comment": h.get("comment")} for h in history],
                width="stretch", hide_index=True,
            )
        else:
            st.caption("No decisions recorded yet.")


# ------------------------------------------------------------------ sidebar

def sidebar_new_request_form() -> tuple[dict, bool]:
    employees = _employees()
    emp_ids = [e["employee_id"] for e in employees]
    with st.sidebar.form("new_request"):
        requester = st.selectbox("Requester", emp_ids, format_func=_employee_label)
        product = st.text_input("Product name")
        vendor = st.text_input("Vendor name")
        category = st.text_input("Category")
        cost = st.number_input("Annual cost (USD)", min_value=0.0, value=None, step=100.0,
                               help="Leave blank if unknown")
        users = st.number_input("User count", min_value=0, value=None, step=1, help="Leave blank if unknown")
        justification = st.text_area("Business justification")
        data_level = st.selectbox("Data access level", DATA_ACCESS_LEVELS, format_func=humanize)
        integrations = st.text_input("Requested integrations", help="Comma-separated, e.g. CRM, Slack")
        urgency = st.selectbox("Urgency", URGENCY_LEVELS, format_func=humanize)
        submitted = st.form_submit_button("Run analysis", type="primary", width="stretch",
                                          icon=":material/play_arrow:")
    payload = {
        "requester_id": requester,
        "product_name": product.strip(),
        "vendor_name": vendor.strip(),
        "category": category.strip(),
        "annual_cost_usd": float(cost) if cost is not None else None,
        "user_count": int(users) if users is not None else None,
        "business_justification": justification.strip(),
        "data_access_level": data_level,
        "requested_integrations": [s.strip() for s in integrations.split(",") if s.strip()],
        "urgency": urgency,
    }
    return payload, submitted


# ------------------------------------------------------------------ page

def main() -> None:
    st.set_page_config(page_title="Procurement Request Copilot", page_icon=":material/inventory_2:",
                       layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.session_state.setdefault("results", {})

    render_header()

    st.sidebar.markdown('<div class="pc-brand">Procurement Copilot</div>'
                        '<div class="pc-brand-sub">Evidence-based purchase reviews</div>', unsafe_allow_html=True)
    modes = ["Sample request", "New request"]
    mode = st.sidebar.segmented_control("Request", modes, default=modes[0], key="mode") or modes[0]
    architecture = st.sidebar.radio("Architecture", list(ARCH_LABELS), format_func=ARCH_LABELS.get,
                                    captions=[ARCH_CAPTIONS[a] for a in ARCH_LABELS], key="architecture")

    results: dict = st.session_state["results"]
    req: dict
    cache_key: tuple
    run_target: str | dict

    if mode == "Sample request":
        by_id = {r["request_id"]: r for r in _requests()}
        rid = st.sidebar.selectbox("Sample request", list(by_id),
                                   format_func=lambda r: f"{r} · {by_id[r]['product_name']}",
                                   key="sample_request")
        req = by_id[rid]
        cache_key = (rid, architecture)
        run_target = rid
        clicked = st.sidebar.button("Run analysis", type="primary", width="stretch",
                                    icon=":material/play_arrow:")
        if cache_key in results:
            if st.sidebar.button("Re-run (calls the model again)", width="stretch",
                                 icon=":material/refresh:"):
                results.pop(cache_key, None)
                clicked = True
    else:
        payload, clicked = sidebar_new_request_form()
        payload_hash = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
        cache_key = (f"new:{payload_hash}", architecture)
        # Reuse the request id assigned on the first run of this exact payload.
        if cache_key in results:
            req = {**payload, "request_id": _as_dict(results[cache_key][0]).get("request_id")}
        elif clicked:
            req = payload
        else:
            last = st.session_state.get("last_new_key")
            if last in results:
                cache_key = last
                req = results[last][2]
            else:
                req = payload
        run_target = req if "request_id" in req else payload

    st.sidebar.divider()
    run_note = st.sidebar.empty()  # filled after any run below, so the count is current

    if clicked and cache_key not in results:
        if mode == "New request" and not (payload.get("product_name") and payload.get("business_justification")):
            st.sidebar.error("Product name and business justification are required.")
        else:
            with st.spinner(f"Analysing with {ARCH_LABELS[architecture]} - this usually takes 15-35 seconds..."):
                try:
                    decision, trace = run_analysis(run_target, architecture)
                except Exception as exc:  # backend is designed to degrade, but never crash the UI
                    st.error(f"Analysis failed: {exc.__class__.__name__}: {exc}")
                else:
                    stored_req = {**req, "request_id": _as_dict(decision).get("request_id")}
                    results[cache_key] = (decision, trace, stored_req)
                    if mode == "New request":
                        st.session_state["last_new_key"] = cache_key
                    st.rerun()  # redraw the sidebar too, so it reflects the cached result (Re-run button, count)

    model = os.getenv("LLM_MODEL") or "gemini-3.1-flash-lite"
    run_note.caption(f"Model: `{model}`  \n{len(results)} analysis run(s) cached this session - switching "
                     "requests or architectures does not call the model again.")

    result = results.get(cache_key)
    trace = result[1] if result else None

    left, right = st.columns([0.85, 1.15], gap="large")
    with left:
        render_request_details(req, trace)
    with right:
        if result:
            render_result(result[0], result[1])
        else:
            render_empty_state(architecture)

    if result:
        st.write("")
        section("Evidence & trace")
        render_details(_as_dict(result[0]), result[1] or {}, _as_dict(result[0]).get("telemetry") or {})
        st.write("")
        render_human_review(result[0], architecture)


if __name__ == "__main__":
    main()
