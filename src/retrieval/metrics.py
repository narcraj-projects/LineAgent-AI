"""Recall@5 is a measured document metric, not a fix accuracy or confidence."""

from src.retrieval.models import CorpusDocument, SearchHit

EXACT_CASES = ("SYN-ALM-001", "SYN-ALM-020", "SYN-PART-006", "SYN-PART-007")


def relevant_source_ids(documents: tuple[CorpusDocument, ...], token: str) -> frozenset[str]:
    return frozenset(d.source_id for d in documents if token in (*d.alarm_codes, *d.part_numbers))


def recall_at_5(hits: tuple[SearchHit, ...], relevant: frozenset[str]) -> float:
    if not relevant:
        raise ValueError("Recall requires a nonempty independently defined relevance set")
    return len({hit.document.source_id for hit in hits[:5]} & relevant) / len(relevant)
