"""공용 타입."""
from dataclasses import dataclass


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str
