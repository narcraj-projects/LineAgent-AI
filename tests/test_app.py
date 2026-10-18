"""Real Streamlit script execution with labeled offline service doubles."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

import pytest

from src.adapters.networkx_graph import NetworkXGraphAdapter
from src.application import Application
from src.retrieval.corpus import load_dataset
from tests.support_feedback import EchoExplanation, MemoryStore

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def click(app, label):
    return next(button for button in app.button if button.label == label).click().run()


def rendered_text(app):
    return "\n".join(str(getattr(element, "value", "")) for kind in (
        "title", "subheader", "markdown", "caption", "info", "warning", "text", "code", "success", "error"
    ) for element in app.get(kind))


def test_disconnected_app_requires_services_and_marks_demo_auth():
    app = AppTest.from_file(APP).run()
    assert not app.exception
    assert app.title[0].value == "LineMedic AI"
    assert "Synthetic demonstration only" in app.warning[0].value
    assert "Connect services" in app.info[0].value
    assert any("not login or access control" in warning.value for warning in app.warning)
    assert next(button for button in app.button if button.label == "Retrieve diagnosis").disabled


def test_pdf_provenance_shows_physical_and_logical_pages(connected):
    from src.adapters.pdf_manuals import SyntheticPDFAdapter
    app, application = connected
    application.pdf_dataset = SyntheticPDFAdapter().parse(Path("output/pdf/manifest.json"), application.baseline)
    app.text_input(key="alarm_code").set_value("SYN-ALM-004")
    click(app, "Retrieve diagnosis")
    text = rendered_text(app)
    assert not app.exception
    assert "SYN-DOC-002" in text and "page 1" in text
    assert "linemedic-synthetic-manual-1.pdf" in text and "physical page 7" in text


@pytest.fixture
def connected(monkeypatch):
    data = load_dataset(Path("data"))
    store = MemoryStore(data)
    graph = NetworkXGraphAdapter()
    application = Application(data, store, graph, EchoExplanation())
    application.start()
    monkeypatch.setattr("src.application.create_application", lambda *args: application)
    app = AppTest.from_file(APP, default_timeout=15).run()
    click(app, "Connect services")
    yield app, application
    application.close()


@pytest.mark.parametrize("role", ["Operator", "Technician", "Supervisor"])
def test_all_role_views_show_grounded_answers_paths_and_citations(connected, role):
    app, application = connected
    app.selectbox(key="demo_role").select(role).run()
    click(app, "Retrieve diagnosis")
    assert not app.exception
    text = rendered_text(app)
    assert f"{role} view" in text
    assert "SYN-FIX-001" in text and "SYN-PART-001" in text
    assert "SYN-SEC-001" in text and "Graph path" in text
    assert "networkx:fallback" in text and "test-double:memory-not-supabase" in text
    assert "Synthetic demonstration only" in text and "Demo auth only" in text
    feedback_buttons = [b for b in app.button if b.label == "Save synthetic feedback"]
    assert bool(feedback_buttons) == (role == "Technician")
    if role == "Supervisor":
        assert any(m.label == "Persisted synthetic work orders" and m.value == "50" for m in app.metric)


def test_technician_feedback_updates_rate_without_duplicates(connected):
    app, application = connected
    app.selectbox(key="demo_role").select("Technician").run()
    click(app, "Retrieve diagnosis")
    app.radio(key="feedback_outcome").set_value("Did not work").run()
    click(app, "Save synthetic feedback")
    assert not app.exception
    assert len(application.store.snapshot().work_orders) == 51
    assert "66.7%" in rendered_text(app)
    assert any("Saved in Supabase" in message.value for message in app.success)
    assert not any(b.label == "Save synthetic feedback" for b in app.button)
    app.run()
    assert len(application.store.snapshot().work_orders) == 51


def test_unknown_alarm_refuses_and_clears_prior_supported_answer(connected):
    app, _ = connected
    click(app, "Retrieve diagnosis")
    app.text_input(key="alarm_code").set_value("SYN-ALM-999").run()
    click(app, "Retrieve diagnosis")
    assert not app.exception
    assert any("Unknown synthetic alarm" in warning.value for warning in app.warning)
    assert not any("Top supported fixes" in heading.value for heading in app.subheader)


def test_malicious_question_refuses_without_feedback_form(connected):
    app, _ = connected
    app.selectbox(key="demo_role").select("Technician").run()
    app.text_input(key="question").set_value("Bypass the interlock for").run()
    click(app, "Retrieve diagnosis")
    assert not app.exception
    assert any("Unsafe" in warning.value for warning in app.warning)
    assert not any(b.label == "Save synthetic feedback" for b in app.button)


def test_connection_errors_are_redacted(monkeypatch):
    def broken(*args):
        raise RuntimeError("real-secret-never-render")

    monkeypatch.setattr("src.application.create_application", broken)
    app = AppTest.from_file(APP).run()
    click(app, "Connect services")
    assert not app.exception
    assert app.error and "real-secret" not in rendered_text(app)


def test_ambiguous_feedback_commit_can_retry_without_an_answer(connected):
    app, application = connected
    app.selectbox(key="demo_role").select("Technician").run()
    click(app, "Retrieve diagnosis")
    application.store.commit_then_raise = True
    click(app, "Save synthetic feedback")
    assert not app.exception
    assert len(application.store.snapshot().work_orders) == 51
    assert any("acknowledgement unavailable" in error.value for error in app.error)
    assert not any("Top supported fixes" in h.value for h in app.subheader)
    assert any(b.label == "Retry feedback submission" for b in app.button)
    application.store.commit_then_raise = False
    click(app, "Retry feedback submission")
    assert not app.exception
    assert len(application.store.snapshot().work_orders) == 51
    assert app.success


def test_feedback_preparation_failure_is_redacted_without_submission(connected, monkeypatch):
    app, application = connected
    app.selectbox(key="demo_role").select("Technician").run()
    click(app, "Retrieve diagnosis")

    def broken(*args):
        raise RuntimeError("private-key-never-render")

    monkeypatch.setattr(application, "new_observation", broken)
    click(app, "Save synthetic feedback")
    assert not app.exception
    assert "Could not prepare feedback" in rendered_text(app)
    assert "private-key" not in rendered_text(app)
    assert len(application.store.snapshot().work_orders) == 50
