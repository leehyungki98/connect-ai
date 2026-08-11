"""KIS 모의투자 REST 클라이언트. 절대규칙 5: 모의 전용, 실전 전환 불가능.

방어선 3중:
1. base URL은 PAPER_BASE_URL 상수 — 생성자 파라미터로 바꿀 수 없다.
2. TR ID는 PAPER_TR_IDS(VT 접두사)에서만 선택된다.
3. place_order()가 내부에서 check_compliance()를 재검사 — 실패 시 HTTP 전송 자체가 없다.

transport 주입으로 테스트 가능. 기본 transport는 urllib(표준 라이브러리).
잔고 응답 파싱은 KIS 문서 기준으로 작성 — 실계정 스모크에서 스키마 확인 필요.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Callable

from autotrader.gates.compliance import (
    PAPER_BASE_URL,
    PAPER_TR_IDS,
    OrderEndpoint,
    check_compliance,
)
from autotrader.gates.types import OrderIntent, Portfolio, Position

ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
PRICE_TR_ID = "FHKST01010100"    # 시세 TR은 모의/실전 공용
BALANCE_TR_ID = "VTTC8434R"      # 잔고 조회 (모의)

# (method, url, headers, body_or_None) -> (status_code, json_dict)
Transport = Callable[[str, str, dict, dict | None], tuple[int, dict]]


def _urllib_transport(method: str, url: str, headers: dict, body: dict | None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


@dataclass(frozen=True)
class OrderResult:
    success: bool
    order_no: str
    message: str


class KISPaperClient:
    def __init__(
        self,
        appkey: str,
        appsecret: str,
        account_no: str,          # "12345678-01" 형식
        transport: Transport = _urllib_transport,
    ):
        cano, _, prdt = account_no.partition("-")
        if len(cano) != 8 or len(prdt) != 2:
            raise ValueError(f"account_no must be '8자리-2자리': {account_no!r}")
        self._appkey = appkey
        self._appsecret = appsecret
        self._cano = cano
        self._prdt = prdt
        self._transport = transport
        self._token: str | None = None

    # --- 인증 ---

    def ensure_token(self) -> str:
        if self._token is None:
            status, data = self._transport(
                "POST",
                f"{PAPER_BASE_URL}/oauth2/tokenP",
                {"content-type": "application/json"},
                {
                    "grant_type": "client_credentials",
                    "appkey": self._appkey,
                    "appsecret": self._appsecret,
                },
            )
            if status != 200 or "access_token" not in data:
                raise RuntimeError(f"token request failed: status={status} {data}")
            self._token = data["access_token"]
        return self._token

    def _headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.ensure_token()}",
            "appkey": self._appkey,
            "appsecret": self._appsecret,
            "tr_id": tr_id,
        }

    # --- 주문 ---

    def place_order(self, intent: OrderIntent, pf: Portfolio) -> OrderResult:
        tr_id = PAPER_TR_IDS.get(intent.side, "")
        decision = check_compliance(intent, OrderEndpoint(PAPER_BASE_URL, tr_id), pf)
        if not decision.allowed:
            return OrderResult(False, "", f"compliance rejected: {decision.reason}")
        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._prdt,
            "PDNO": intent.symbol,
            "ORD_DVSN": "00",                 # 지정가
            "ORD_QTY": str(intent.qty),
            "ORD_UNPR": str(intent.limit_price),
        }
        status, data = self._transport(
            "POST", f"{PAPER_BASE_URL}{ORDER_PATH}", self._headers(tr_id), body
        )
        if status == 200 and data.get("rt_cd") == "0":
            odno = str(data.get("output", {}).get("ODNO", ""))
            return OrderResult(True, odno, str(data.get("msg1", "")).strip())
        return OrderResult(False, "", f"status={status} msg={data.get('msg1', '')}")

    # --- 조회 ---

    def get_price(self, symbol: str) -> int:
        url = (
            f"{PAPER_BASE_URL}{PRICE_PATH}"
            f"?FID_COND_MRKT_DIV_CODE=J&FID_INPUT_ISCD={symbol}"
        )
        status, data = self._transport("GET", url, self._headers(PRICE_TR_ID), None)
        if status != 200 or data.get("rt_cd") != "0":
            raise RuntimeError(f"price query failed: status={status} {data.get('msg1')}")
        return int(data["output"]["stck_prpr"])

    def get_portfolio(self) -> Portfolio:
        url = (
            f"{PAPER_BASE_URL}{BALANCE_PATH}"
            f"?CANO={self._cano}&ACNT_PRDT_CD={self._prdt}"
            "&AFHR_FLPR_YN=N&OFL_YN=&INQR_DVSN=02&UNPR_DVSN=01"
            "&FUND_STTL_ICLD_YN=N&FNCG_AMT_AUTO_RDPT_YN=N"
            "&PRCS_DVSN=00&CTX_AREA_FK100=&CTX_AREA_NK100="
        )
        status, data = self._transport("GET", url, self._headers(BALANCE_TR_ID), None)
        if status != 200 or data.get("rt_cd") != "0":
            raise RuntimeError(f"balance query failed: status={status} {data.get('msg1')}")
        positions = {}
        for row in data.get("output1", []):
            qty = int(row.get("hldg_qty", "0"))
            if qty > 0:
                positions[row["pdno"]] = Position(qty, int(row["evlu_amt"]))
        summary = data["output2"][0]
        return Portfolio(
            equity_krw=int(summary["tot_evlu_amt"]),
            cash_krw=int(summary["dnca_tot_amt"]),
            positions=positions,
        )
