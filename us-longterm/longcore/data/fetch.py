"""yfinance 시세·환율 수집 + 로컬 캐시 (state/cache/).

- 수정종가 auto_adjust=True = 배당 재투자 가정 (벤치마크와 동일 기준)
- 같은 날 재호출 시 캐시 사용
- 실패 시 예외를 위로 전파 — 조용한 기본값 금지 (fail-closed)
- yfinance import 는 함수 안에서만: 오프라인 테스트가 이 모듈 경유 없이 돌도록
"""
from datetime import date
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parents[2] / "state" / "cache"


def _cache_path(kind: str, tickers, start, end) -> Path:
    key = "-".join(sorted(tickers))
    stamp = f"{start}_{end or date.today().isoformat()}"
    return CACHE_DIR / f"{kind}_{key}_{stamp}.csv"


def _download(tickers, start, end) -> pd.DataFrame:
    import yfinance as yf
    raw = yf.download(list(tickers), start=start, end=end,
                      auto_adjust=True, progress=False)
    if raw is None or raw.empty:
        raise RuntimeError(f"yfinance 응답 없음: {tickers} {start}~{end}")
    close = raw["Close"]
    if isinstance(close, pd.Series):          # 단일 종목
        close = close.to_frame(name=list(tickers)[0])
    close = close.dropna(how="all")
    missing = [t for t in tickers if t not in close.columns or close[t].dropna().empty]
    if missing:
        raise RuntimeError(f"시세 없음: {missing}")
    return close


def fetch_history(tickers, start: str, end: str = None,
                  cache_dir: Path = None) -> pd.DataFrame:
    """일봉 수정종가 DataFrame (index=날짜, columns=티커)."""
    cache = _cache_path("hist", tickers, start, end)
    if cache_dir is not None:
        cache = Path(cache_dir) / cache.name
    if cache.exists():
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
        return df
    df = _download(tickers, start, end)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache)
    return df


def fetch_latest(tickers) -> tuple:
    """(기준일 date, {ticker: 최신 수정종가}). 최근 10일 창에서 마지막 유효값."""
    df = fetch_history(tickers, start=(pd.Timestamp.today()
                                       - pd.Timedelta(days=10)).date().isoformat())
    df = df.ffill()
    last = df.iloc[-1]
    if last.isna().any():
        raise RuntimeError(f"최신가 결측: {last[last.isna()].index.tolist()}")
    return df.index[-1].date(), {t: float(last[t]) for t in tickers}
