from __future__ import annotations

import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
# Extra edge-case requests used only by the extended evaluation set.
EXTRA_REQUESTS_PATH = ROOT / "evals" / "extra_requests.json"

# Ad-hoc requests submitted from the UI during this process lifetime.
_RUNTIME_REQUESTS: dict[str, dict] = {}


def load_employees() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "employees.csv")


def load_budgets() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "department_budgets.csv")


def load_software_catalog() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "software_catalog.csv")


def load_vendors() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "vendors.csv")


def load_purchase_history() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "purchase_history.csv")


def load_requests() -> list[dict]:
    return json.loads((DATA_DIR / "requests.json").read_text(encoding="utf-8"))


def load_extra_requests() -> list[dict]:
    if not EXTRA_REQUESTS_PATH.is_file():
        return []
    return json.loads(EXTRA_REQUESTS_PATH.read_text(encoding="utf-8"))


def register_request(request: dict) -> str:
    """Register an ad-hoc request (e.g. from the UI form) so tools can look it up by ID."""
    _RUNTIME_REQUESTS[request["request_id"]] = request
    return request["request_id"]


def get_request(request_id: str) -> dict:
    if request_id in _RUNTIME_REQUESTS:
        return _RUNTIME_REQUESTS[request_id]
    for request in load_requests() + load_extra_requests():
        if request["request_id"] == request_id:
            return request
    raise KeyError(f"Unknown request_id: {request_id}")


def load_policy_text() -> str:
    return (DATA_DIR / "procurement_policy.md").read_text(encoding="utf-8")


def records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list of plain dicts with NaN converted to None."""
    return json.loads(df.to_json(orient="records"))
