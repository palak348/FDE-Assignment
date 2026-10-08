"""AI Procurement Request Copilot - Streamlit product UI.

Run from the project root:  streamlit run app.py
The copilot is advisory only: it never approves or purchases anything.
"""
from __future__ import annotations

import hashlib
import html
import json
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
    "single": "A - Single agent",
    "staged": "B - Staged (analyst -> reviewer)",
}

ACTION_STYLES = {
    # next_action: (label, text colour, background)
    "route_for_approval": ("Route for approval", "#1d4ed8", "#dbeafe"),
    "request_info": ("Request more information", "#92400e", "#fef3c7"),
    "reuse_existing": ("Reuse existing tool", "#6d28d9", "#ede9fe"),
    "manual_review": ("Manual review", "#c2410c", "#ffedd5"),
    "reject": ("Reject", "#b91c1c", "#fee2e2"),
}

HUMAN_DECISIONS = ["Route to approvers", "Request more information", "Reject", "Override / exception"]

CSS = """
<style>
.pc-badge {display:inline-block; padding:0.35rem 0.85rem; border-radius:999px; font-weight:600;
           font-size:0.95rem; letter-spacing:0.01em; border:1px solid currentColor;}
.pc-chip {display:inline-block; padding:0.15rem 0.6rem; margin:0 0.35rem 0.35rem 0; border-radius:6px;
          font-size:0.85rem; background:#eef2f7; color:#1f2937; border:1px solid #d5dce6;}
.pc-chip-risk {background:#fdecec; color:#9b1c1c; border-color:#f5c2c2;}
.pc-muted {color:#6b7280; font-size:0.85rem;}
.pc-untrusted {border:1px dashed #d97706; border-radius:8px; padding:0.75rem 0.9rem; background:rgba(217,119,6,0.06);}
.pc-untrusted-label {font-size:0.75rem; font-weight:700; text-transform:uppercase; color:#b45309;
                     letter-spacing:0.05em; margin-bottom:0.35rem;}
.pc-advisory {border-left:4px solid #6b7280; padding:0.5rem 0.8rem; background:rgba(107,114,128,0.08);
              border-radius:4px; font-size:0.9rem;}
</style>
"""


# ------------------------------------------------------------------ helpers

def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def chips(items: list[str], risk: bool = False) -> str:
    if not items:
        return '<span class="pc-muted">None</span>'
    cls = "pc-chip pc-chip-risk" if risk else "pc-chip"
    return "".join(f'<span class="{cls}">{_esc(i)}</span>' for i in items)


def action_badge(next_action: str | None) -> str:
    label, fg, bg = ACTION_STYLES.get(next_action or "", (next_action or "Unknown", "#374151", "#f3f4f6"))
    return f'<span class="pc-badge" style="color:{fg}; background:{bg};">{_esc(label)}</span>'


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

def render_request_details(req: dict, trace: dict | None = None) -> None:
    st.subheader("Request details")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Annual cost", fmt_money(req.get("annual_cost_usd")))
    c2.metric("Users", req.get("user_count") if req.get("user_count") is not None else "Unknown")
    c3.metric("Urgency", str(req.get("urgency") or "-").title())
    c4.metric("Data access", str(req.get("data_access_level") or "-"))

    integrations = req.get("requested_integrations") or []
    rows = [
        ("Request ID", req.get("request_id") or "(assigned on submit)"),
        ("Requester", _employee_label(req.get("requester_id"))),
        ("Product", req.get("product_name") or "-"),
        ("Vendor", req.get("vendor_name") or "-"),
        ("Category", req.get("category") or "-"),
        ("Integrations", ", ".join(integrations) if integrations else "None"),
    ]
    st.table({"Field": [r[0] for r in rows], "Value": [str(r[1]) for r in rows]})

    st.markdown(
        '<div class="pc-untrusted"><div class="pc-untrusted-label">Untrusted requester text</div>'
        f'{_esc(req.get("business_justification") or "(no justification provided)")}</div>',
        unsafe_allow_html=True,
    )
    injection = ((trace or {}).get("rules") or {}).get("prompt_injection") or []
    if injection:
        fields = ", ".join(sorted({h.get("field", "?") for h in injection}))
        st.warning(
            f"Instruction-like text was detected in {fields} and ignored. It is treated as data, "
            "never as instructions to the copilot. A human reviewer should check this request."
        )
        with st.expander("Detected text"):
            for h in injection:
                st.markdown(f"- `{h.get('field')}`: \"{h.get('matched_text')}\"")
    elif trace is None:
        st.caption("Requester text is screened for embedded instructions when the analysis runs.")


def render_result(decision: Any, trace: dict) -> None:
    """Render the recommendation card, evidence panel and telemetry for one run."""
    d = _as_dict(decision)
    trace = trace or {}
    tel = d.get("telemetry") or {}

    if tel.get("llm_error") or trace.get("error"):
        st.error("AI analysis unavailable - showing deterministic rule checks only.")
        st.caption(f"Reason: {tel.get('llm_error') or trace.get('error')}")

    # ---- recommendation card
    st.subheader("Recommendation")
    with st.container(border=True):
        top_l, top_r = st.columns([3, 1])
        with top_l:
            st.markdown(action_badge(d.get("next_action")), unsafe_allow_html=True)
        with top_r:
            st.caption(f"{ARCH_LABELS.get(trace.get('architecture') or tel.get('architecture'), '')}")
        st.markdown(f"#### {_esc(d.get('recommendation'))}")
        st.markdown(f"**Next step:** {_esc(d.get('next_step'))}")

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Required approvals**")
            st.markdown(chips(d.get("required_approvals") or []), unsafe_allow_html=True)
        with col_b:
            st.markdown("**Risk flags**")
            st.markdown(chips(d.get("risk_flags") or [], risk=True), unsafe_allow_html=True)

        missing = d.get("missing_information") or []
        st.markdown("**Missing information**")
        if missing:
            st.markdown("\n".join(f"- {_esc(m)}" for m in missing))
        else:
            st.markdown('<span class="pc-muted">None</span>', unsafe_allow_html=True)

        st.markdown(
            '<div class="pc-advisory">Advisory only - the copilot never approves or purchases. '
            "Human review required.</div>",
            unsafe_allow_html=True,
        )

    # ---- telemetry
    prompt_t, completion_t = tel.get("prompt_tokens"), tel.get("completion_tokens")
    tokens = (prompt_t or 0) + (completion_t or 0) if (prompt_t is not None or completion_t is not None) else None
    t1, t2, t3, t4, t5 = st.columns(5)
    t1.metric("Total latency", fmt_ms(trace.get("latency_ms")))
    t2.metric("LLM calls", tel.get("llm_calls") if tel.get("llm_calls") is not None else "-")
    t3.metric("Tool calls", tel.get("tool_calls") if tel.get("tool_calls") is not None else len(trace.get("tool_calls") or []))
    t4.metric("Tokens", f"{tokens:,}" if tokens is not None else "-",
              help=f"prompt {prompt_t if prompt_t is not None else '-'} / completion {completion_t if completion_t is not None else '-'}")
    t5.metric("Model", tel.get("model") or "-")

    # ---- evidence panel
    st.subheader("Evidence")
    evidence = d.get("evidence") or []
    if evidence:
        st.dataframe(
            [{"Source tool": e.get("source"), "Finding": e.get("finding"), "Reference": e.get("reference") or ""}
             for e in evidence],
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No evidence items were returned.")
    if tel.get("ungrounded_evidence_dropped"):
        st.caption(f"{tel['ungrounded_evidence_dropped']} evidence item(s) from the model were dropped because "
                   "they could not be grounded in a tool result.")

    tool_calls = trace.get("tool_calls") or []
    with st.expander(f"Tool calls ({len(tool_calls)})"):
        if not tool_calls:
            st.caption("No tool calls recorded.")
        for i, call in enumerate(tool_calls, 1):
            st.markdown(f"**{i}. `{call.get('name')}`** - {fmt_ms(call.get('latency_ms'))}")
            st.caption("Arguments")
            st.json(call.get("arguments") or {}, expanded=False)
            st.caption("Result")
            st.json(call.get("result"), expanded=False)

    rules = trace.get("rules") or {}
    with st.expander("Deterministic rule checks"):
        if not rules:
            st.caption("No rule result available.")
        else:
            tier = rules.get("approval_tier") or {}
            budget = rules.get("budget") or {}
            vendor = rules.get("vendor") or {}
            r1, r2, r3 = st.columns(3)
            r1.markdown(f"**Approval tier**  \n{_esc(tier.get('tier') or '-')}")
            r1.markdown(chips(tier.get("approvals") or []), unsafe_allow_html=True)
            if tier.get("reason"):
                r1.caption(tier["reason"])
            r2.markdown(f"**Budget**  \n{_esc(budget.get('status', '-'))}")
            if budget.get("reason"):
                r2.caption(budget["reason"])
            r3.markdown(f"**Baseline next action**  \n{_esc(rules.get('baseline_next_action', '-'))}")
            r3.caption(f"Department head: {rules.get('department_head') or '-'}  \n"
                       f"Reference date: {rules.get('reference_date') or '-'}")

            triggers = rules.get("review_triggers") or []
            st.markdown("**Review triggers**")
            if triggers:
                st.dataframe(
                    [{"Review": t.get("review"), "Reason": t.get("reason"), "Policy": t.get("policy_section")}
                     for t in triggers],
                    use_container_width=True, hide_index=True,
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
                    use_container_width=True, hide_index=True,
                )
            st.markdown("**Vendor assessment**")
            st.json(vendor, expanded=False)
            st.markdown("**Full rule result**")
            st.json(rules, expanded=False)

    corrections = tel.get("guardrail_corrections") or []
    with st.expander(f"Guardrail corrections ({len(corrections)})"):
        st.caption("Deterministic code checks the model output against the policy rules after the LLM responds. "
                   "These are the fields that code added or overrode - the model's draft was not trusted as-is.")
        if corrections:
            st.markdown("\n".join(f"- {_esc(c)}" for c in corrections))
        else:
            st.caption("No corrections were needed.")

    with st.expander("Raw model output (pre-guardrail)"):
        raw = trace.get("raw_llm_decision")
        if raw is None:
            st.caption("No model output available (the LLM call failed or returned nothing usable).")
        else:
            st.json(_as_dict(raw) if not isinstance(raw, (str, list)) else raw, expanded=False)

    if (trace.get("architecture") or tel.get("architecture")) == "staged":
        with st.expander("Analyst evidence pack"):
            pack = trace.get("evidence_pack")
            if pack is None:
                st.caption("No evidence pack recorded.")
            elif isinstance(pack, str):
                st.markdown(pack)
            else:
                st.json(pack, expanded=False)


def render_human_review(decision: Any, architecture: str) -> None:
    d = _as_dict(decision)
    request_id = d.get("request_id")
    st.subheader("Human review")
    st.caption("The procurement reviewer records the actual decision here. The AI recommendation is never executed - "
               "only this human decision is written to the audit log.")
    with st.form(f"review_{request_id}_{architecture}", clear_on_submit=True):
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
            st.success(f"Decision recorded for {request_id}: {human_decision}.")

    history = read_audit(request_id)
    st.markdown("**Recent audit entries for this request**")
    if history:
        st.dataframe(
            [{"Time (UTC)": h.get("timestamp"), "Reviewer": h.get("reviewer"), "Human decision": h.get("human_decision"),
              "AI next action": h.get("ai_next_action"), "Architecture": h.get("architecture"),
              "Comment": h.get("comment")} for h in history],
            use_container_width=True, hide_index=True,
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
        cost = st.number_input("Annual cost (USD) - leave blank if unknown", min_value=0.0, value=None, step=100.0)
        users = st.number_input("User count - leave blank if unknown", min_value=0, value=None, step=1)
        justification = st.text_area("Business justification")
        data_level = st.selectbox("Data access level", DATA_ACCESS_LEVELS)
        integrations = st.text_input("Requested integrations (comma-separated)")
        urgency = st.selectbox("Urgency", URGENCY_LEVELS)
        submitted = st.form_submit_button("Run analysis", type="primary", use_container_width=True)
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
    st.set_page_config(page_title="Procurement Request Copilot", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.session_state.setdefault("results", {})

    st.title("Procurement Request Copilot")
    st.caption("Checks software purchase requests against policy, budget, vendor risk and existing tools, "
               "then recommends the next step. Advisory only - a human makes every decision.")

    st.sidebar.header("Request")
    mode = st.sidebar.radio("Mode", ["Sample request", "New request"], horizontal=True)
    architecture = st.sidebar.radio("Architecture", list(ARCH_LABELS), format_func=ARCH_LABELS.get)

    results: dict = st.session_state["results"]
    req: dict
    cache_key: tuple
    run_target: str | dict

    if mode == "Sample request":
        by_id = {r["request_id"]: r for r in _requests()}
        rid = st.sidebar.selectbox("Sample request", list(by_id),
                                   format_func=lambda r: f"{r} - {by_id[r]['product_name']}")
        req = by_id[rid]
        cache_key = (rid, architecture)
        run_target = rid
        clicked = st.sidebar.button("Run analysis", type="primary", use_container_width=True)
        if cache_key in results:
            if st.sidebar.button("Re-run (calls the model again)", use_container_width=True):
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
    st.sidebar.caption(f"Results are cached for this session ({len(results)} run(s)); switching requests or "
                       "architectures does not re-call the model.")

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
                    req = stored_req

    result = results.get(cache_key)
    trace = result[1] if result else None

    left, right = st.columns([0.9, 1.1], gap="large")
    with left:
        render_request_details(req, trace)
    with right:
        if result:
            render_result(result[0], result[1])
        else:
            st.subheader("Recommendation")
            st.info(f"Choose an architecture and click **Run analysis** to get a recommendation "
                    f"({ARCH_LABELS[architecture]}).")
            st.markdown(
                '<div class="pc-advisory">Advisory only - the copilot never approves or purchases. '
                "Human review required.</div>",
                unsafe_allow_html=True,
            )

    if result:
        st.divider()
        render_human_review(result[0], architecture)


if __name__ == "__main__":
    main()
