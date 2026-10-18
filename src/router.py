"""Ordered, deterministic rules. No model receives tool-selection authority."""

import re

from src.agent_models import RouteDecision
from src.safety import unsafe_text
from src.schemas import DiagnosisRequest

ALARM = re.compile(r"\bSYN-ALM-\d{3}\b", re.I)
EQUIPMENT = re.compile(r"\bSYN-EQ-\d{3}\b", re.I)
PART = re.compile(r"\bSYN-PART-\d{3}\b", re.I)
HISTORY = re.compile(r"\b(history|historical|rate|worked|attempts|path|relationship)\b", re.I)
DETAIL = re.compile(r"\b(manual|explain|why|cause|fix|part|parts|troubleshoot)\b", re.I)
DOMAIN = re.compile(r"\b(alarm|equipment|filling|packaging|photoeye|sensor|conveyor|carton|reject|capper|filler|labeler|manual|work order)\b", re.I)
OUTSIDE = re.compile(r"\b(weather|poem|politics|stock price|investment|patient|dosage|prescribe|recipe|football)\b", re.I)


def select_route(request: DiagnosisRequest) -> RouteDecision:
    text = request.query
    alarms = set(ALARM.findall(text.upper()))
    equipment = set(EQUIPMENT.findall(text.upper()))
    if request.alarm_code:
        alarms.add(request.alarm_code.upper())
    if request.equipment_id:
        equipment.add(request.equipment_id.upper())
    if unsafe_text(text):
        return RouteDecision(route="refuse", reason="Unsafe or instruction-manipulation request.")
    if OUTSIDE.search(text):
        return RouteDecision(route="refuse", reason="Outside synthetic equipment troubleshooting scope.")
    if len(alarms) > 1 or len(equipment) > 1:
        return RouteDecision(route="refuse", reason="Specify one alarm and one equipment context.")
    alarm = next(iter(alarms), None)
    eq = next(iter(equipment), None)
    context = {"alarm_code": alarm, "equipment_id": eq}
    if (alarm and not ALARM.fullmatch(alarm)) or (eq and not EQUIPMENT.fullmatch(eq)):
        return RouteDecision(route="refuse", reason="Only canonical synthetic IDs are supported.")
    if HISTORY.search(text):
        if not (alarm or eq):
            return RouteDecision(route="refuse", reason="History requires an alarm or equipment ID.")
        return RouteDecision(route="graph", reason="Relationship or history request.", **context)
    if alarm:
        if DETAIL.search(text) or PART.search(text):
            return RouteDecision(route="combined", reason="Alarm plus explanatory or part evidence.", **context)
        return RouteDecision(route="sql", reason="Exact canonical alarm lookup.", **context)
    if PART.search(text):
        return RouteDecision(route="hybrid", reason="Exact part token benefits from lexical and dense retrieval.", **context)
    if DOMAIN.search(text):
        return RouteDecision(route="dense", reason="Semantic synthetic equipment/manual question.", **context)
    return RouteDecision(route="refuse", reason="Outside synthetic equipment troubleshooting scope.")
