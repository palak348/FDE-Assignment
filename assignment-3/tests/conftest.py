"""Shared fixtures: keep every test offline and deterministic."""
from __future__ import annotations

import os

import pytest

# Set at import time too, so nothing reaches for a real vendor-risk server.
os.environ["VENDOR_RISK_MODE"] = "inprocess"


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch):
    monkeypatch.setenv("VENDOR_RISK_MODE", "inprocess")
    # Make sure no test can accidentally build a real LLM client.
    for key in ("LLM_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "LLM_CACHE_DIR"):
        monkeypatch.delenv(key, raising=False)
    # vendor_client may cache "no HTTP server" process-wide; isolate tests from each other.
    from src import vendor_client
    monkeypatch.setattr(vendor_client, "_HTTP_UNREACHABLE", False, raising=False)
