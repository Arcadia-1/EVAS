"""EVAS support-tier names and diagnostic helpers."""
from __future__ import annotations

BEHAVIORAL_EVENT = "behavioral-event"
BEHAVIORAL_CONTINUOUS_TIME = "behavioral-continuous-time"
AMS_DIGITAL = "ams-digital"
CONSERVATIVE_CURRENT_KCL = "conservative-current-kcl"
OUTSIDE_CURRENT_SCOPE = "outside-current-scope"


SUPPORT_TIER_ORDER = (
    BEHAVIORAL_EVENT,
    BEHAVIORAL_CONTINUOUS_TIME,
    AMS_DIGITAL,
    CONSERVATIVE_CURRENT_KCL,
)

SUPPORT_TIER_DESCRIPTIONS = {
    BEHAVIORAL_EVENT: (
        "voltage-domain behavioral transient models with event/cross/timer/"
        "transition/state-machine/control logic"
    ),
    BEHAVIORAL_CONTINUOUS_TIME: (
        "legal voltage-domain continuous-time operators such as ddt(), idt(), "
        "laplace_*(), zi_*(), and limexp()"
    ),
    AMS_DIGITAL: (
        "AMS/digital constructs such as wreal, logic, always, continuous "
        "assign, packed vectors, specify/specparam, and connect rules"
    ),
    CONSERVATIVE_CURRENT_KCL: (
        "current contributions, current probes, indirect branch equations, "
        "and KCL/MNA topology solving"
    ),
    OUTSIDE_CURRENT_SCOPE: "constructs outside the current EVAS support taxonomy",
}

_CONTINUOUS_TIME_FUNCTIONS = {
    "absdelay",
    "ddt",
    "idt",
    "idtmod",
    "laplace_nd",
    "laplace_np",
    "laplace_zd",
    "laplace_zp",
    "zi_nd",
    "zi_np",
    "zi_zd",
    "zi_zp",
    "limexp",
}

_AMS_DIGITAL_FUNCTIONS = {
    "$rose",
    "$fell",
    "$changed",
    "$past",
    "$stable",
}


def format_support_tier_hint(tier: str | None) -> str:
    """Return the visible diagnostic suffix for a support tier."""
    return f" [support-tier: {tier}]" if tier else ""


def support_tier_for_function(name: str) -> str:
    """Classify an unsupported function/operator call by EVAS support tier."""
    normalized = name.strip().lower()
    if normalized in _CONTINUOUS_TIME_FUNCTIONS:
        return BEHAVIORAL_CONTINUOUS_TIME
    if normalized in _AMS_DIGITAL_FUNCTIONS:
        return AMS_DIGITAL
    return OUTSIDE_CURRENT_SCOPE


def support_tier_for_parse_error(message: str) -> str | None:
    """Best-effort tier classification for parser-level unsupported syntax."""
    text = message.lower()
    if "digital verilog" in text or "procedural keyword" in text:
        return AMS_DIGITAL
    if "current contribution" in text or "kcl" in text or "branch current" in text:
        return CONSERVATIVE_CURRENT_KCL
    return None


def unsupported_feature_message(feature: str, tier: str, detail: str) -> str:
    """Build a concise unsupported-feature message with stable tier wording."""
    return (
        f"unsupported Verilog-A feature: {feature} "
        f"({tier}) - {detail}"
    )


def support_boundary_message(feature: str, tier: str, detail: str) -> str:
    """Build a diagnostic for supported helpers outside certified core scope."""
    return f"support-tier boundary: {feature} ({tier}) - {detail}"
