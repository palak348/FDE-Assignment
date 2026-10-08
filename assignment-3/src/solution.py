from __future__ import annotations

import time
import uuid
from typing import Any

from src import data_access as da
from src.agents import single, staged
from src.contracts import Architecture, ProcurementDecision
from src.guardrails import finalize
from src.llm import ChatModel, LLMError, ModelSwitchedError, get_default_llm
from src.tools import RunContext

ARCHITECTURES = {"single": single.run, "staged": staged.run}
MAX_MODEL_RESTARTS = 8


def run_with_trace(request: str | dict, architecture: Architecture = "single",
                   llm: ChatModel | None = None) -> tuple[ProcurementDecision, dict[str, Any]]:
    """Run one architecture and return (decision, trace). The trace is for the UI and evals:
    raw model decision (pre-guardrail), tool calls with results, evidence pack, latency."""
    if architecture not in ARCHITECTURES:
        raise ValueError(f"Unknown architecture '{architecture}'. Use 'single' or 'staged'.")
    req = da.get_request(request) if isinstance(request, str) else request
    start = time.perf_counter()
    trace: dict[str, Any] = {"architecture": architecture, "model_switches": []}
    for _attempt in range(MAX_MODEL_RESTARTS + 1):
        ctx = RunContext(req)
        llm_decision, error, model_name = None, None, None
        try:
            llm = llm or get_default_llm()
            model_name = getattr(llm, "model", None)
            llm_decision, extra = ARCHITECTURES[architecture](ctx, llm)
            trace.update(extra)
        except ModelSwitchedError as exc:
            # Restart from scratch on the fallback model: one run never mixes models.
            trace["model_switches"].append(str(exc))
            error = str(exc)
            continue
        except LLMError as exc:
            error = str(exc)
        except Exception as exc:  # the product must degrade, not crash
            error = f"{exc.__class__.__name__}: {exc}"
        break
    decision = finalize(ctx, llm_decision, architecture=architecture, model=model_name, llm_error=error)
    trace.update({
        "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        "error": error,
        "tool_calls": [{"name": c.name, "arguments": c.arguments, "result": c.result, "latency_ms": c.latency_ms}
                       for c in ctx.calls],
        "rules": ctx.rules(),
    })
    return decision, trace


def handle_request(request_id: str, architecture: Architecture = "single") -> ProcurementDecision:
    """Public entry point used by the UI and the evaluation harness.

    Runs the chosen architecture ("single" or "staged") on a stored request and
    returns the guardrailed ProcurementDecision.
    """
    decision, _ = run_with_trace(request_id, architecture)
    return decision


def handle_request_payload(request: dict, architecture: Architecture = "single"
                           ) -> tuple[ProcurementDecision, dict[str, Any]]:
    """Run an ad-hoc request (e.g. submitted from the UI form)."""
    request = dict(request)
    request.setdefault("request_id", f"REQ-NEW-{uuid.uuid4().hex[:6].upper()}")
    da.register_request(request)
    return run_with_trace(request, architecture)
