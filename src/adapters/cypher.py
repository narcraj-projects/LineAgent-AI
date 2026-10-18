"""Allowlisted Cypher only. Schema tokens come from constants; values are parameters."""

from types import MappingProxyType

from src.adapters.graph_common import NODE_SPECS

CONSTRAINTS = tuple(
    f"CREATE CONSTRAINT linemedic_{label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
    for _, label, key in NODE_SPECS
) + (
    "CREATE CONSTRAINT linemedic_alarm_code IF NOT EXISTS FOR (n:Alarm) REQUIRE n.code IS UNIQUE",
    "CREATE CONSTRAINT linemedic_canonical_id IF NOT EXISTS FOR (n:LineMedicSynthetic) REQUIRE n.canonical_id IS UNIQUE",
)

_templates = {
    f"seed_{label}": (
        f"UNWIND $rows AS row MERGE (n:LineMedicSynthetic:{label} {{{key}: row.{key}, namespace: $namespace}}) "
        "SET n += row RETURN count(n) AS merged"
    ) for _, label, key in NODE_SPECS
}
for _relation in ("HAS", "RAISED_BY", "INDICATES", "RESOLVED_BY", "REQUIRES", "ON", "APPLIED", "DESCRIBES"):
    _templates[f"seed_{_relation}"] = (
        "UNWIND $rows AS row "
        "MATCH (s:LineMedicSynthetic {canonical_id: row.source_id, namespace: $namespace}) "
        "MATCH (t:LineMedicSynthetic {canonical_id: row.target_id, namespace: $namespace}) "
        f"MERGE (s)-[r:{_relation}]->(t) RETURN count(r) AS merged"
    )
_templates["counts"] = """
MATCH (n:LineMedicSynthetic {namespace: $namespace})
WITH count(n) AS nodes
OPTIONAL MATCH (s:LineMedicSynthetic {namespace: $namespace})-[r]->(t:LineMedicSynthetic {namespace: $namespace})
RETURN nodes, count(r) AS relationships
"""
_templates["fix_history"] = """
MATCH (w:WorkOrder:LineMedicSynthetic {namespace: $namespace})-[:ON]->
      (e:Equipment:LineMedicSynthetic {namespace: $namespace, equipment_id: $equipment_id})
MATCH (w)-[:APPLIED]->(f:Fix:LineMedicSynthetic {namespace: $namespace, fix_id: $fix_id})
RETURN DISTINCT w.payload_json AS payload_json ORDER BY payload_json
"""
_templates["alarm_paths"] = """
MATCH (a:Alarm:LineMedicSynthetic {namespace: $namespace, code: $alarm_code})-[:RAISED_BY]->
      (e:Equipment:LineMedicSynthetic {namespace: $namespace})
WHERE $equipment_id IS NULL OR e.equipment_id = $equipment_id
MATCH path=(a)-[:INDICATES]->(fm:FailureMode:LineMedicSynthetic {namespace: $namespace})
                  -[:RESOLVED_BY]->(f:Fix:LineMedicSynthetic {namespace: $namespace})
OPTIONAL MATCH (f)-[:REQUIRES]->(sp:SparePart:LineMedicSynthetic {namespace: $namespace})
WITH a,e,fm,f,path,collect(DISTINCT sp.part_number) AS parts
OPTIONAL MATCH (w:WorkOrder:LineMedicSynthetic {namespace: $namespace})-[:ON]->(e)
WHERE (w)-[:APPLIED]->(f)
WITH a,e,fm,f,path,parts,collect(DISTINCT w.payload_json) AS history,
     count(DISTINCT w) AS attempts,
     count(DISTINCT CASE WHEN w.outcome = 'worked' THEN w END) AS worked
OPTIONAL MATCH (m:ManualSection:LineMedicSynthetic {namespace: $namespace})-[:DESCRIBES]->(e)
WHERE (m)-[:DESCRIBES]->(f) AND m.failure_mode_id = fm.failure_mode_id
RETURN a.code AS alarm_code, a.alarm_id AS alarm_id, e.equipment_id AS equipment_id,
       fm.failure_mode_id AS failure_mode_id, f.fix_id AS fix_id, parts,
       [n IN nodes(path) | n.canonical_id] AS base_path,
       history, attempts, worked, collect(DISTINCT m.payload_json) AS manuals
ORDER BY equipment_id, fix_id
"""
_templates["upsert_work_order"] = """
MATCH (a:Alarm:LineMedicSynthetic {namespace:$namespace, code:$alarm_code})-[:RAISED_BY]->
      (e:Equipment:LineMedicSynthetic {namespace:$namespace, equipment_id:$equipment_id})
MATCH (a)-[:INDICATES]->(:FailureMode:LineMedicSynthetic {namespace:$namespace})-[:RESOLVED_BY]->
      (f:Fix:LineMedicSynthetic {namespace:$namespace, fix_id:$fix_id})
MERGE (w:WorkOrder:LineMedicSynthetic {namespace:$namespace, work_order_id:$work_order_id})
WITH w,e,f WHERE w.payload_json IS NULL OR w.payload_json = $payload_json
SET w += $properties
MERGE (w)-[:ON]->(e)
MERGE (w)-[:APPLIED]->(f)
RETURN count(DISTINCT w) AS merged
"""
TEMPLATES = MappingProxyType(_templates)
