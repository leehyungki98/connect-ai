"""목표 4 검증: 200종목 입력에서 상위 후보 랭킹이 재현되는지."""
import random

import pytest

from autotrader.screener.ranking import SymbolData, rank


def make_universe(n=200, seed=42):
    """시드 고정 랜덤워크로 200종목 합성 (90 거래일)."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        price = rng.randint(5_000, 100_000)
        drift = rng.uniform(-0.004, 0.004)
        closes = []
        for _ in range(90):
            price = max(100, round(price * (1 + drift + rng.uniform(-0.015, 0.015))))
            closes.append(price)
        out.append(SymbolData(f"{i:06d}", tuple(closes)))
    return out


def uptrend(symbol="000001", daily=0.005, days=90, start=10_000):
    closes = tuple(round(start * (1 + daily) ** i) for i in range(days))
    return SymbolData(symbol, closes)


# --- 재현성 (핵심 검증) ---

def test_same_input_same_ranking():
    u = make_universe()
    r1, r2 = rank(u), rank(u)
    assert r1 == r2


def test_ranking_independent_of_input_order():
    u = make_universe()
    shuffled = list(u)
    random.Random(7).shuffle(shuffled)
    assert rank(u) == rank(shuffled)


def test_rebuilt_universe_reproduces_ranking():
    # 같은 시드로 유니버스를 다시 만들어도 (프로세스 재시작 시뮬레이션) 동일 랭킹
    assert rank(make_universe()) == rank(make_universe())


def test_returns_exactly_top_n():
    r = rank(make_universe(), top_n=10)
    assert len(r) == 10
    scores = [x.score for x in r]
    assert scores == sorted(scores, reverse=True)


# --- 랭킹 논리 ---

def test_stronger_momentum_ranks_higher():
    u = make_universe(n=50)
    u.append(uptrend("999998", daily=0.008))  # 강한 상승
    u.append(uptrend("999997", daily=0.003))  # 약한 상승
    r = rank(u, top_n=5)
    syms = [x.symbol for x in r]
    assert syms[0] == "999998"
    assert syms.index("999998") < syms.index("999997")


def test_tie_broken_by_symbol_asc():
    a = uptrend("000002")
    b = SymbolData("000001", a.closes)  # 완전히 같은 시세
    r = rank([a, b] + make_universe(n=30), top_n=5)
    ia, ib = [x.symbol for x in r].index("000001"), [x.symbol for x in r].index("000002")
    assert ia + 1 == ib  # 동점 → 코드 오름차순 인접


# --- 필터: 부적격 종목 제외 ---

def test_short_history_excluded():
    s = SymbolData("111111", tuple(10_000 + i * 50 for i in range(60)))  # 60 < 61
    assert all(x.symbol != "111111" for x in rank([s] + make_universe(n=30)))


def test_below_ma20_excluded():
    # 급락으로 현재가 < 20일 이평
    closes = tuple([10_000] * 80 + [9_000] * 10)
    s = SymbolData("222222", closes)
    assert all(x.symbol != "222222" for x in rank([s] + make_universe(n=30)))


def test_high_volatility_excluded():
    rng = random.Random(1)
    price, closes = 10_000, []
    for _ in range(90):
        price = max(100, round(price * (1 + rng.uniform(-0.10, 0.11))))  # 일 ±10%
        closes.append(price)
    closes[-1] = max(closes[-1], round(sum(closes[-20:]) / 20) + 1)  # 이평 필터는 통과시킴
    s = SymbolData("333333", tuple(closes))
    assert all(x.symbol != "333333" for x in rank([s] + make_universe(n=30)))


def test_nonpositive_price_excluded():
    s = SymbolData("444444", tuple([10_000] * 89 + [0]))
    assert all(x.symbol != "444444" for x in rank([s] + make_universe(n=30)))


# --- 파라미터 경계 ---

def test_top_n_out_of_range_raises():
    u = make_universe(n=30)
    with pytest.raises(ValueError):
        rank(u, top_n=4)
    with pytest.raises(ValueError):
        rank(u, top_n=16)
