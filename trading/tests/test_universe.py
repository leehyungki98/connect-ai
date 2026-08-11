from autotrader.screener.universe import StockRow, select_universe


def row(symbol, name="보통주식", value=1_000_000_000):
    return StockRow(symbol, name, value)


def test_preferred_stock_excluded():
    rows = [row("005930"), row("005935")]  # 005935 = 삼성전자우
    assert select_universe(rows) == ["005930"]


def test_spac_excluded():
    rows = [row("005930"), row("123450", name="하나스팩29호")]
    assert select_universe(rows) == ["005930"]


def test_zero_value_excluded():
    rows = [row("005930"), row("000660", value=0)]  # 거래정지 등
    assert select_universe(rows) == ["005930"]


def test_top_n_by_value_desc():
    rows = [row(f"{i:05d}0", value=(i + 1) * 1_000) for i in range(300)]
    out = select_universe(rows, size=200)
    assert len(out) == 200
    assert out[0] == "002990"  # i=299, 최대 거래대금
    assert "000990" not in out  # i=99, 하위 100개 탈락


def test_deterministic_tie_break():
    rows = [row("000020"), row("000010"), row("000030")]  # 동일 거래대금
    assert select_universe(rows) == ["000010", "000020", "000030"]


def test_bad_symbol_format_excluded():
    rows = [row("005930"), row("05930"), row("00593A")]
    assert select_universe(rows) == ["005930"]
