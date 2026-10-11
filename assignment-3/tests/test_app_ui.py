"""The Streamlit UI renders without errors (no LLM call: nothing is clicked)."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_sample_request_page_renders():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    html = " ".join(m.value for m in at.markdown)
    assert "Procurement Request Copilot" in html
    assert "No analysis yet" in html
    assert at.selectbox(key="sample_request").value == "REQ-1001"


def test_new_request_form_renders():
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["mode"] = "New request"
    at.run()
    assert not at.exception
    labels = {w.label for w in at.text_input}
    assert {"Product name", "Vendor name", "Category"} <= labels
