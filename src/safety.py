"""Conservative input policy; manuals remain untrusted even after this screen."""

import re

from src.schemas import DiagnosisResponse

NOTICE = "Synthetic advisory only. Follow site-approved procedures and qualified review; no actuation or restart authorization."
UNSAFE = re.compile(
    r"\b(bypass|override|disable|defeat|jumper|force\s+start|restart\s+(?:the\s+)?(?:line|machine)|"
    r"authorize\s+(?:a\s+)?restart|ignore\s+(?:the\s+)?safety|release\s+(?:the\s+)?batch|"
    r"validated\s+gxp|gxp\s+compliant|turn\s+off\s+(?:the\s+)?(?:interlock|guard)|"
    r"bridge\s+(?:the\s+)?(?:safety|interlock)|start\s+(?:the\s+)?(?:line|machine))\b", re.I,
)
INJECTION = re.compile(
    r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|system|above)\s+instructions|"
    r"(?:system|developer)\s*(?:message|prompt|:)|<\|(?:im_start|system)|"
    r"reveal\s+(?:the\s+)?(?:secret|key|password)|api[_ -]?key|"
    r"(?:send|upload|exfiltrate).{0,60}(?:credential|secret)|"
    r"follow\s+(?:these|my)\s+instructions|you\s+are\s+now", re.I,
)


def unsafe_text(text: str) -> bool:
    return bool(UNSAFE.search(text) or INJECTION.search(text))


def refusal(reason: str) -> DiagnosisResponse:
    return DiagnosisResponse(status="refused", message=f"{reason} {NOTICE}")


def scaffold_refusal() -> DiagnosisResponse:
    """Refuse all diagnostic requests until a verified evidence pipeline exists."""
    return DiagnosisResponse(
        status="refused",
        message=(
            "Diagnosis is unavailable: this scaffold has no verified evidence pipeline. "
            "Do not use it to repair equipment or bypass safeguards. "
            "Follow approved site procedures and consult qualified personnel."
        ),
    )
