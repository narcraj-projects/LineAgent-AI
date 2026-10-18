"""Evidence validation and deterministic ranking, independent of the model."""

from src.adapters.diagnosis_contracts import SQLEvidenceAdapter
from src.agent_models import Explanation, GroundedFact
from src.data_models import Dataset, graph_relationships
from src.retrieval.corpus import build_corpus
from src.router import PART
from src.safety import NOTICE, unsafe_text
from src.schemas import DiagnosisRequest, DiagnosisResponse, Evidence, FixCandidate


class GroundingError(ValueError):
    """Controlled evidence rejection; never display untrusted content."""


def validate_sources(data: Dataset, request: DiagnosisRequest, retrieved: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    corpus = {doc.source_id: doc for doc in build_corpus(data)}
    raw = {m.section_id: m.text for m in data.manual_sections}
    raw.update({w.work_order_id: w.note for w in data.work_orders})
    accepted: dict[str, Evidence] = {}
    edges = {(e.source_id, e.target_id) for e in graph_relationships(data)}
    for item in retrieved:
        item = Evidence.model_validate_json(item.model_dump_json())
        doc = corpus.get(item.source_id)
        if (doc is None or item.source_kind != doc.source_kind or item.text not in (doc.text, raw[item.source_id])
                or unsafe_text(item.text)):
            raise GroundingError("Unverified or unsafe evidence was rejected.")
        if request.equipment_id not in doc.equipment_ids:
            raise GroundingError("Retrieved evidence has conflicting equipment context.")
        if item.path and (len(item.path) < 2 or any(pair not in edges for pair in zip(item.path, item.path[1:]))):
            raise GroundingError("Retrieved graph path is unverified.")
        if item.path and item.source_kind == "manual":
            manual = next(m for m in data.manual_sections if m.section_id == item.source_id)
            alarm = next((a for a in data.alarms if a.alarm_id == item.path[0]), None)
            if (alarm is None or alarm.equipment_id != request.equipment_id or len(item.path) not in (3, 4)
                    or item.path[1] != manual.failure_mode_id or item.path[2] not in manual.fix_ids):
                raise GroundingError("Graph path does not substantiate its cited manual.")
        if item.path and item.source_kind == "work_order":
            order = next(w for w in data.work_orders if w.work_order_id == item.source_id)
            if item.path != (order.work_order_id, order.fix_id):
                raise GroundingError("Graph path does not substantiate its cited work order.")
        if request.alarm_code and request.alarm_code not in doc.alarm_codes:
            continue
        # Different adapter representations were each checked against canonical
        # raw/indexed text above. Deduplicate deterministically by source ID.
        accepted.setdefault(item.source_id, item)
    return tuple(accepted[key] for key in sorted(accepted))


def prepare_facts(data: Dataset, request: DiagnosisRequest, retrieved: tuple[Evidence, ...],
                  sql: SQLEvidenceAdapter) -> tuple[tuple[GroundedFact, ...], tuple[Evidence, ...], tuple[FixCandidate, ...]]:
    evidence = validate_sources(data, request, retrieved)
    ids = {e.source_id for e in evidence}
    # Contrast sections mention both a fix and a decoy: they cannot establish
    # positive support. Never consult evaluation questions or answer keys.
    manuals = [m for m in data.manual_sections if m.section_id in ids and len(m.fix_ids) == 1]
    requested_parts = set(PART.findall(request.query.upper()))
    fixes = {f.fix_id: f for f in data.fixes}
    if requested_parts:
        manuals = [m for m in manuals if requested_parts <= set(fixes[m.fix_ids[0]].part_numbers)]
    if not manuals or request.equipment_id is None:
        raise GroundingError("Insufficient positive manual evidence or equipment context.")
    if len({m.failure_mode_id for m in manuals}) != 1:
        raise GroundingError("Conflicting causes; specify an exact synthetic alarm.")
    modes = {m.failure_mode_id: m for m in data.failure_modes}
    candidates: list[FixCandidate] = []
    facts: list[GroundedFact] = []
    all_evidence = {e.source_id: e for e in evidence}
    for manual in sorted(manuals, key=lambda m: m.section_id):
        fix = fixes[manual.fix_ids[0]]
        if any(c.fix_id == fix.fix_id for c in candidates):
            continue
        if any(part not in manual.text for part in fix.part_numbers) or fix.fix_id not in manual.text:
            raise GroundingError("Manual does not substantiate the fix and its parts.")
        history = sql.fix_history(request.equipment_id, fix.fix_id)
        canonical = tuple(sorted((w for w in data.work_orders if w.equipment_id == request.equipment_id
                                  and w.fix_id == fix.fix_id), key=lambda w: w.work_order_id))
        if history.equipment_id != request.equipment_id or history.fix_id != fix.fix_id or history.work_orders != canonical:
            raise GroundingError("SQL history conflicts with the canonical synthetic snapshot.")
        sources = tuple(sorted((manual.section_id, *(w.work_order_id for w in history.work_orders))))
        for order in history.work_orders:
            all_evidence.setdefault(order.work_order_id, Evidence(source_id=order.work_order_id,
                source_kind="work_order", text=order.note, provider=sql.provider))
        outcomes = history.outcomes
        facts.append(GroundedFact(fix_id=fix.fix_id, part_numbers=fix.part_numbers, source_ids=sources,
            worked=outcomes.worked, did_not_work=outcomes.did_not_work, rate=outcomes.success_rate,
            quote_source_id=manual.section_id, quote=manual.text))
        candidates.append(FixCandidate(fix_id=fix.fix_id, cause=modes[manual.failure_mode_id].description,
            part_number=fix.part_numbers[0], evidence_source_ids=sources, outcomes=outcomes,
            safety_warning=NOTICE, graph_path=next((e.path for e in evidence if e.source_id == manual.section_id), ())))
    order = sorted(range(len(facts)), key=lambda i: (-(facts[i].rate if facts[i].rate is not None else -1),
                   -(facts[i].worked + facts[i].did_not_work), facts[i].fix_id))[:3]
    checked = validate_sources(data, request.model_copy(update={"alarm_code": None}), tuple(all_evidence.values()))
    return tuple(facts[i] for i in order), checked, tuple(candidates[i] for i in order)


def validated_answer(proposal: Explanation, expected: tuple[GroundedFact, ...],
                     evidence: tuple[Evidence, ...], candidates: tuple[FixCandidate, ...]) -> DiagnosisResponse:
    # Closed equality rejects uncited prose, altered rates, omitted outcomes,
    # swapped parts, duplicate candidates and model-selected decoys.
    if proposal.facts != expected:
        raise GroundingError("Model explanation does not match retrieved and audited evidence.")
    messages = []
    for fact in proposal.facts:
        attempts = fact.worked + fact.did_not_work
        rate = f"{fact.rate:.1%}" if fact.rate is not None else "unknown (no observations)"
        messages.append(f"{fact.fix_id}: {fact.quote} [{fact.quote_source_id}] "
                        f"Recorded outcomes: {fact.worked}/{attempts} worked; historical rate {rate}. "
                        f"Sources: {', '.join(fact.source_ids)}.")
    return DiagnosisResponse(status="supported", message="\n".join(messages) + "\n" + NOTICE,
                             candidates=candidates, evidence=evidence)
