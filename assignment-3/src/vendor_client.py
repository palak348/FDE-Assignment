from __future__ import annotations

import os
from urllib.parse import quote, urlparse
import requests


def get_vendor_risk(vendor_name: str, timeout_seconds: float = 3.0) -> dict:
    """Low-level API client. Decide yourself whether/how this becomes an agent tool."""
    base_url = os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
    url = f"{base_url}/vendor-risk/{quote(str(vendor_name), safe='')}"
    response = requests.get(url, timeout=timeout_seconds)
    response.raise_for_status()
    return response.json()


# In "auto" mode, remember that no local server is listening so later calls skip the
# (slow on Windows) failed connection attempt and go straight to the in-process mock.
_HTTP_UNREACHABLE = False


def _in_process_get(vendor_name: str) -> requests.Response | object:
    """Call the same mock service in-process (used when no local server is running)."""
    from fastapi.testclient import TestClient
    from mock_api.app import app

    return TestClient(app).get(f"/vendor-risk/{quote(str(vendor_name), safe='')}")


def safe_get_vendor_risk(vendor_name: str, timeout_seconds: float = 3.0) -> dict:
    """Never raises. Returns {"ok": True, "data": {...}, "transport": ...} or a structured failure.

    VENDOR_RISK_MODE:
      - "http": only call VENDOR_RISK_BASE_URL.
      - "inprocess": call the mock FastAPI app in-process.
      - "auto" (default): HTTP first; if no *local* server is listening, fall back to the in-process
        mock. HTTP error responses (404/503) are always reported as failures, never retried away.
        A remote VENDOR_RISK_BASE_URL is never replaced by the mock: its outage is reported.
    """
    mode = os.getenv("VENDOR_RISK_MODE", "auto").lower()
    if mode == "auto" and not _is_local(os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001")):
        mode = "http"
    vendor_name = str(vendor_name).strip() if vendor_name is not None else ""
    if not vendor_name:
        return {"ok": False, "status": "bad_request", "error": "No vendor name supplied"}

    global _HTTP_UNREACHABLE
    if mode == "http" or (mode == "auto" and not _HTTP_UNREACHABLE):
        try:
            return {"ok": True, "data": get_vendor_risk(vendor_name, timeout_seconds), "transport": "http"}
        except requests.HTTPError as exc:
            resp = exc.response
            detail = _detail(resp)
            return {"ok": False, "status": resp.status_code if resp is not None else "http_error", "error": detail}
        except requests.ReadTimeout:
            return {"ok": False, "status": "timeout", "error": f"Vendor-risk service timed out after {timeout_seconds}s"}
        except requests.ConnectionError as exc:  # includes ConnectTimeout: nothing listening
            _HTTP_UNREACHABLE = mode == "auto"
            if mode == "http":
                if isinstance(exc, requests.ConnectTimeout):
                    return {"ok": False, "status": "timeout",
                            "error": f"Vendor-risk service connect timed out after {timeout_seconds}s"}
                return {"ok": False, "status": "connection_error", "error": f"Vendor-risk service unreachable: {exc.__class__.__name__}"}
        except ValueError:
            return {"ok": False, "status": "invalid_response", "error": "Vendor-risk service returned invalid JSON"}
        except requests.RequestException as exc:  # redirects, broken connections mid-body, ...
            return {"ok": False, "status": "connection_error",
                    "error": f"Vendor-risk service request failed: {exc.__class__.__name__}"}
        except Exception as exc:  # never raise: an unverifiable status is reported, not assumed
            return {"ok": False, "status": "error", "error": f"Vendor-risk lookup failed: {exc.__class__.__name__}"}

    try:
        resp = _in_process_get(vendor_name)
        if resp.status_code != 200:
            return {"ok": False, "status": resp.status_code, "error": _detail(resp)}
        return {"ok": True, "data": resp.json(), "transport": "inprocess"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "status": "connection_error", "error": f"Vendor-risk service unavailable: {exc}"}


def _is_local(base_url: str) -> bool:
    return (urlparse(base_url).hostname or "").lower() in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _detail(resp) -> str:
    if resp is None:
        return "No response"
    try:
        return str(resp.json().get("detail", resp.text))
    except Exception:
        return getattr(resp, "text", "") or f"HTTP {resp.status_code}"
