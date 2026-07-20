"""밴드 판정 — 순수 함수.

이탈 = |현재 비중 − 목표| 이 band 를 **초과(>)** 할 때. 정확히 ±5.0%p 는 유지
(경계값 규칙 — config.py 주석·테스트로 고정).
"""

_EPS = 1e-12   # 부동소수점 잡음 방지 — 경계 '초과' 판정의 안정화


def deviations(weights: dict, targets: dict) -> dict:
    return {s: weights.get(s, 0.0) - t for s, t in targets.items()}


def check_bands(weights: dict, targets: dict, band: float) -> list:
    dev = deviations(weights, targets)
    return [s for s, d in dev.items() if abs(d) > band + _EPS]
