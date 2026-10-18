"""Streamlit demo views. Importing this module performs no UI or provider calls."""

from pathlib import Path
from typing import Any

from src.agent_models import AgentRun
from src.application import Application
from src.data_models import Dataset
from src.schemas import DiagnosisRequest
from src.safety import NOTICE
from src.pdf_models import RAGSourceMetadata
from src.retrieval.pdf_metadata import build_pdf_metadata

ROOT = Path(__file__).resolve().parent


def render_answer(st: Any, result: AgentRun, baseline: Dataset,
                  source_metadata: dict[str, RAGSourceMetadata] | None = None) -> None:
    st.caption(f"Route: {result.decision.route} | Sources: {', '.join(result.providers) or 'none'} | {result.elapsed_ms:.0f} ms")
    if result.response.status == "refused":
        st.warning(result.response.message)
        return
    st.subheader("Top supported fixes (up to 3)")
    st.caption(f"Alarm: {result.decision.alarm_code or 'semantic query'} | Equipment: {result.decision.equipment_id}")
    if len(result.response.candidates) < 3:
        st.info(f"{len(result.response.candidates)} fix has sufficient evidence. Unsupported alternatives are not added.")
    for rank, candidate in enumerate(result.response.candidates, 1):
        with st.container(border=True):
            st.markdown(f"**{rank}. {candidate.fix_id}**")
            st.write(candidate.cause)
            st.write(f"Part: {candidate.part_number or 'none supported'}")
            counts = candidate.outcomes
            attempts = counts.worked + counts.did_not_work
            rate = f"{counts.success_rate:.1%}" if counts.success_rate is not None else "Unknown: no observations"
            st.write(f"Synthetic history: {counts.worked}/{attempts} worked | Historical rate: {rate}")
            st.write("Citations: " + ", ".join(candidate.evidence_source_ids))
            st.write("Graph path")
            if candidate.graph_path:
                st.code(" → ".join(candidate.graph_path), language=None)
            else:
                st.caption("No graph path was retrieved for this route.")
            st.caption(candidate.safety_warning)
    with st.expander("Validated explanation"):
        st.text(result.response.message)
        st.caption(f"Explanation: {result.explanation_provider} | Model: {result.explanation_model or 'unspecified'}")
    st.subheader("Cited evidence")
    manuals = {m.section_id: m for m in baseline.manual_sections}
    for source in result.response.evidence:
        with st.expander(f"{source.source_id} · {source.source_kind}"):
            st.caption(f"Provider: {source.provider}")
            if source.source_id in manuals:
                manual = manuals[source.source_id]
                st.caption(f"Fictional document {manual.source_doc} · version {manual.source_version} · page {manual.page}")
            metadata = (source_metadata or {}).get(source.source_id)
            if metadata is not None and metadata.pdf_filename:
                st.caption(f"Verified compilation PDF: {metadata.pdf_filename} · physical page {metadata.pdf_page}")
            st.text(source.text)


def render_feedback(st: Any, application: Application, result: AgentRun | None, actor_id: str) -> None:
    st.subheader("Record a synthetic observation")
    st.caption("This records a fictional outcome. It does not authorize an equipment action.")
    receipt = st.session_state.get("feedback_receipt")
    pending = st.session_state.get("pending_submission")
    if st.session_state.get("feedback_error"):
        st.error(st.session_state["feedback_error"])
    if receipt is not None:
        st.success(f"Saved in Supabase: {receipt.event.work_order.work_order_id}")
        if receipt.sync_state == "pending":
            st.warning("Graph sync pending. Feedback is durable; the supervisor can retry synchronization.")
        else:
            st.caption("Graph synchronization acknowledged.")
        if st.button("Record another observation", key="new_observation"):
            st.session_state.pop("feedback_receipt", None)
            st.session_state.pop("pending_submission", None)
            st.rerun()
        return
    if pending is not None:
        st.info(f"Submission awaiting acknowledgement: {pending.work_order.work_order_id}, {pending.work_order.alarm_code}, {pending.work_order.fix_id}, {pending.work_order.outcome}. Retry uses the same event ID.")
    elif result is None or result.response.status != "supported" or not result.decision.alarm_code:
        st.info("Retrieve a supported synthetic alarm before recording another observation.")
        return
    choices = [pending.work_order.fix_id] if pending is not None else [c.fix_id for c in result.response.candidates]
    with st.form("feedback_form"):
        fix_id = st.selectbox("Supported fix", choices, key="feedback_fix", disabled=pending is not None)
        outcome_label = st.radio("Observed synthetic outcome", ("Worked", "Did not work"), key="feedback_outcome", disabled=pending is not None)
        submit = st.form_submit_button("Retry feedback submission" if pending is not None else "Save synthetic feedback")
    if submit and pending is None:
        outcome = "worked" if outcome_label == "Worked" else "did_not_work"
        try:
            pending = application.new_observation(result.decision.alarm_code, fix_id, actor_id, outcome)
        except Exception:
            st.error("Could not prepare feedback. Check Supabase availability; no submission was attempted.")
            return
        st.session_state["pending_submission"] = pending
    if submit:
        st.session_state.pop("diagnosis_result", None)
        try:
            receipt = application.submit_feedback(pending)
            st.session_state["feedback_receipt"] = receipt
            st.session_state.pop("feedback_error", None)
            # Invalidate the prior answer even if a fresh generation call fails.
            st.session_state.pop("diagnosis_result", None)
            try:
                st.session_state["diagnosis_result"] = application.diagnose(st.session_state["last_request"])
            except Exception:
                st.session_state["refresh_notice"] = "Feedback saved; refresh the diagnosis to load current rates."
            st.rerun()
        except Exception:
            st.session_state["feedback_error"] = "Feedback acknowledgement unavailable. The event ID is retained; retry the same submission to avoid duplicates."
            st.rerun()


def render_supervisor(st: Any, application: Application) -> None:
    st.subheader("Synthetic work-order overview")
    if st.button("Retry pending graph sync", key="retry_sync"):
        try:
            receipts = application.retry_sync()
            cached = st.session_state.get("feedback_receipt")
            if cached is not None:
                for receipt in receipts:
                    if receipt.event.event_id == cached.event.event_id:
                        st.session_state["feedback_receipt"] = receipt
            st.info(f"Retry completed: {sum(r.sync_state == 'synced' for r in receipts)} synced, {sum(r.sync_state == 'pending' for r in receipts)} pending.")
            st.session_state.pop("diagnosis_result", None)
        except Exception:
            st.error("Sync status unavailable. Pending events remain in Supabase for retry.")
    try:
        report = application.supervisor_report()
        left, right = st.columns(2)
        left.metric("Persisted synthetic work orders", report.persisted_work_orders)
        right.metric("Pending graph events", len(report.pending_events))
        rows = [{"Equipment": row.equipment_id, "Fix": row.fix_id, "Worked": row.outcomes.worked,
                 "Attempts": row.outcomes.worked + row.outcomes.did_not_work,
                 "Historical rate": f"{row.outcomes.success_rate:.1%}" if row.outcomes.success_rate is not None else "Unknown"}
                 for row in report.histories]
        st.dataframe(rows, hide_index=True, use_container_width=True)
        st.caption("Rates use distinct persisted work orders in equipment/fix context. Demo roles provide no production authorization.")
    except Exception:
        st.error("Supabase report unavailable; no cached persistence totals are claimed.")


def main() -> None:
    import streamlit as st

    from src.application import create_application
    from src.retrieval.corpus import load_dataset

    st.set_page_config(page_title="LineMedic AI", page_icon="🛠", layout="wide")
    st.title("LineMedic AI")
    st.warning("Synthetic demonstration only. Not validated for real equipment or GxP use.")
    st.caption(NOTICE)
    baseline = load_dataset(ROOT / "data")
    role = st.sidebar.selectbox("Demo role", ("Operator", "Technician", "Supervisor"), key="demo_role")
    actor = next(a for a in baseline.demo_identities if a.role == role.lower())
    st.sidebar.warning("Demo auth only: this selector is not login or access control.")
    st.sidebar.caption(f"Fictional actor: {actor.actor_id}")
    backend = st.sidebar.selectbox("Graph backend", ("auto", "networkx", "neo4j"), key="graph_backend",
        help="Auto prefers Neo4j; unavailable provisioning uses an explicitly labeled NetworkX fallback.")
    application = st.session_state.get("application")
    if application is None:
        if st.sidebar.button("Connect services", key="connect_services"):
            try:
                application = create_application(ROOT / ".env", backend)
                st.session_state["application"] = application
            except Exception:
                st.error("Services not ready. Configure Supabase URL/server key and Fireworks key/chat model; apply the Supabase schema migration.")
    else:
        if st.sidebar.button("Disconnect services", key="disconnect_services"):
            application.close()
            for key in ("application", "diagnosis_result", "feedback_receipt"):
                st.session_state.pop(key, None)
            st.rerun()
    if application is not None:
        st.sidebar.caption(f"Storage: {application.store.provider}")
        st.sidebar.caption(f"Graph: {application.graph.status.provider}:{application.graph.status.mode}")
        st.sidebar.caption("Graph selection changes take effect after reconnecting.")
    else:
        st.info("Connect services to retrieve evidence and store feedback. No local persistence substitute is enabled.")
    st.subheader(f"{role} view")
    st.write({"Operator": "Investigate a fictional alarm and review supported evidence.",
        "Technician": "Review evidence and record a synthetic worked/did-not-work observation.",
        "Supervisor": "Review persisted history and retry pending graph synchronization."}[role])
    with st.form("alarm_form"):
        alarm_code = st.text_input("Synthetic alarm code", value="SYN-ALM-001", key="alarm_code")
        question = st.text_input("Question", value="Show history for", key="question")
        diagnose = st.form_submit_button("Retrieve diagnosis", disabled=application is None)
    if diagnose and application is not None:
        st.session_state.pop("diagnosis_result", None)
        try:
            request = DiagnosisRequest(query=f"{question.strip()} {alarm_code.strip()}", alarm_code=alarm_code.strip())
            st.session_state["last_request"] = request
            with st.spinner("Retrieving and validating synthetic evidence…"):
                st.session_state["diagnosis_result"] = application.diagnose(request)
        except Exception:
            st.error("Diagnosis unavailable. No unsupported answer is displayed; check service configuration and graph sync status.")
    if st.session_state.get("refresh_notice"):
        st.info(st.session_state.pop("refresh_notice"))
    result = st.session_state.get("diagnosis_result")
    if result is not None:
        pdf_metadata = build_pdf_metadata(application.pdf_dataset) if application is not None and application.pdf_dataset else None
        render_answer(st, result, baseline, pdf_metadata)
    if role == "Technician" and application is not None:
        if ((result is not None and result.response.status == "supported" and result.decision.alarm_code)
                or st.session_state.get("pending_submission") is not None or st.session_state.get("feedback_receipt") is not None):
            render_feedback(st, application, result, actor.actor_id)
    if role == "Supervisor" and application is not None:
        render_supervisor(st, application)


if __name__ == "__main__":
    main()
