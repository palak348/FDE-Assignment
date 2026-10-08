"""Vendor-risk client (src/vendor_client.py): never raises, failures are structured data."""
from __future__ import annotations

import pytest
import requests

from src import vendor_client
from src.vendor_client import safe_get_vendor_risk

CLOSED_PORT = "http://127.0.0.1:9"


def test_inprocess_known_vendor_ok():
    out = safe_get_vendor_risk("BrandBoard")
    assert out["ok"] is True
    assert out["transport"] == "inprocess"
    assert out["data"]["vendor_name"] == "BrandBoard"
    assert out["data"]["security_review_status"] == "not_completed"


def test_inprocess_vendor_with_space_in_name():
    out = safe_get_vendor_risk("CreativeSuite Labs")
    assert out["ok"] is True
    assert out["data"]["vendor_name"] == "CreativeSuite Labs"


def test_inprocess_nimbus_outage_is_503_failure():
    out = safe_get_vendor_risk("NimbusAI")
    assert out["ok"] is False
    assert out["status"] == 503
    assert "unavailable" in out["error"].lower()
    assert "data" not in out


def test_inprocess_unknown_vendor_is_404_failure():
    out = safe_get_vendor_risk("TotallyUnknownVendor")
    assert out["ok"] is False
    assert out["status"] == 404


def test_empty_vendor_name_is_bad_request():
    out = safe_get_vendor_risk("")
    assert out == {"ok": False, "status": "bad_request", "error": "No vendor name supplied"}


def test_http_mode_closed_port_returns_connection_error(monkeypatch):
    monkeypatch.setenv("VENDOR_RISK_MODE", "http")
    monkeypatch.setenv("VENDOR_RISK_BASE_URL", CLOSED_PORT)
    out = safe_get_vendor_risk("BrandBoard")  # must not raise
    assert out["ok"] is False
    # Windows retries refused SYNs for ~2s, so a slow refusal may surface as a timeout.
    assert out["status"] in {"connection_error", "timeout"}


def test_auto_mode_closed_port_falls_back_to_inprocess(monkeypatch):
    monkeypatch.setenv("VENDOR_RISK_MODE", "auto")
    monkeypatch.setenv("VENDOR_RISK_BASE_URL", CLOSED_PORT)
    out = safe_get_vendor_risk("BrandBoard")
    assert out["ok"] is True
    assert out["transport"] == "inprocess"


def _raise(exc):
    def fake(*_args, **_kwargs):
        raise exc
    return fake


def test_auto_mode_does_not_retry_away_http_errors(monkeypatch):
    monkeypatch.setenv("VENDOR_RISK_MODE", "auto")
    response = requests.Response()
    response.status_code = 503
    response._content = b'{"detail": "down"}'
    monkeypatch.setattr(vendor_client, "get_vendor_risk", _raise(requests.HTTPError(response=response)))
    assert safe_get_vendor_risk("BrandBoard") == {"ok": False, "status": 503, "error": "down"}


def test_read_timeout_is_failure_not_fallback(monkeypatch):
    monkeypatch.setenv("VENDOR_RISK_MODE", "auto")
    monkeypatch.setattr(vendor_client, "get_vendor_risk", _raise(requests.ReadTimeout()))
    out = safe_get_vendor_risk("BrandBoard")
    assert out["ok"] is False
    assert out["status"] == "timeout"


def test_auto_mode_connect_timeout_falls_back_to_inprocess(monkeypatch):
    monkeypatch.setenv("VENDOR_RISK_MODE", "auto")
    monkeypatch.setattr(vendor_client, "get_vendor_risk", _raise(requests.ConnectTimeout()))
    out = safe_get_vendor_risk("BrandBoard")
    assert out["ok"] is True
    assert out["transport"] == "inprocess"
