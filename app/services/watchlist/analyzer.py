"""
관심종목 중장기 분석 서비스 (스펙: docs/watchlist_spec.md).

핵심 원칙:
- AI 역할 = 데이터 집계 + 생각 구조화. 주가 방향 예측 금지.
- 분석 시점의 입력 스냅샷(input_snapshot)을 결과와 함께 저장 → 사후 재구성 가능.
- KIS에 없는 데이터는 가짜로 채우지 않고 data_flags에 결측으로 명시.
- recency bias 억제: 펀더멘털 우선, 뉴스는 노이즈/구조적 변화 구분 강제.
"""
import json
import logging
import uuid
from datetime import date, datetime, timezone

from app.models.watchlist import StockAnalysis
from app.services.kis.client import KISClient

logger = logging.getLogger(__name__)

WATCHLIST_MODEL = "gemini-2.5-flash"  # 검색 그라운딩 필요 (뉴스/공시/컨센서스 보강)

_ANALYSIS_PROMPT = """당신은 데이터를 구조화하는 애널리스트입니다. 역할은 투자 판단 재료의 정리이며, 주가 방향 예측이 아닙니다.

[종목] {stock_name}({stock_code}) — 섹터: {sector}
[분석 기준일] {analysis_date}

[입력 데이터 — KIS 실측 (이 수치만 신뢰하고, 여기 없는 수치는 검색으로 확인된 것만 사용)]
{snapshot_json}

[검색으로 보강할 것 — 확인된 사실만, 출처 필수]
- 최근 DART 공시: 실적발표, 유상증자, 자사주, 대형계약, 합병/분할
- 섹터·테마 동향, 정책 모멘텀 ({sector} 관련)
- 증권사 목표주가/컨센서스: KIS 데이터에 목표주가는 없음. 검색으로 확인된 것만 인용하고, 확인 안 되면 "확인 불가"로 표기. 수치를 만들어내지 말 것.
- 리스크: 관리종목/거래정지 가능성, 최대주주 지분 변동, 소송/규제
- 매크로는 이 섹터에 실질 영향 있는 것만 (환율/금리/원자재 — 수출주/금융주/소재주 등 해당 시)

[규칙]
1. 펀더멘털 우선, 뉴스는 보조. 언급하는 모든 뉴스/이벤트는 "일시적 노이즈"인지 "구조적 변화"인지 반드시 구분해 표기할 것.
2. 근거 없는 방향성 단언 금지 — "단기 조정 후 상승 예상" 같은 표현 금지. 데이터가 말해주는 것과 말해주지 않는 것을 구분할 것.
3. 입력 데이터의 data_flags에 표시된 결측 항목은 결측으로 다루고, 그 공백이 판단에 중요하면 명시할 것.
4. 무효화_조건은 반드시 관측 가능한 신호로 쓸 것 (falsifiable) — 나쁜 예: "실적이 나빠지면" / 좋은 예: "다음 분기 단일분기 영업이익률이 X% 아래로 내려가면", "외국인 누적 순매도가 N거래일 이상 지속되면".
5. 출력은 아래 JSON 형식만. 백틱(```)이나 설명 문장 없이 JSON 객체 하나만 출력할 것.

[출력 JSON 형식]
{{
  "논거": "현재 상태 요약 — 입력 데이터 기반. 펀더멘털(분기 추세/수익성/재무구조) → 수급 → 밸류 순으로.",
  "단기_촉매": [{{"이벤트": "...", "예상_시점": "...", "성격": "노이즈|구조적"}}],
  "장기_논거": "중장기 투자 논거 — 구조적 변화 중심. 없으면 '뚜렷한 장기 논거 확인 불가'라고 쓸 것.",
  "무효화_조건": ["관측 가능한 신호 1", "신호 2", "..."],
  "밸류_코멘트": "현재 밸류에이션 평가 — 입력의 자기 과거 PER 이력 대비 위치 중심. 업종 대비는 데이터 없으면 언급하지 말 것.",
  "뉴스_출처": [{{"제목": "...", "매체": "...", "날짜": "YYYY-MM-DD", "url": "..."}}]
}}"""

_INVALIDATION_RETRY_SUFFIX = """

[재요청] 직전 응답에 무효화_조건이 비어 있었습니다. 무효화_조건은 이 분석에서 가장 중요한 필드입니다.
입력 데이터에서 현재 상태를 정의하는 핵심 수치(분기 영업이익률, 외국인 순매수 추세, PER 위치 등)를 골라,
그것이 꺾이는 관측 가능한 임계 신호를 최소 2개 이상 반드시 작성하세요."""


# ------------------------------------------------------------------ #
# 입력 데이터 수집 → 스냅샷
# ------------------------------------------------------------------ #

def _pct(cur: float | None, base: float | None) -> float | None:
    if cur is None or not base:
        return None
    return round((cur / base - 1) * 100, 2)


def _summarize_price(client: KISClient, stock_code: str, holding: dict | None) -> dict:
    """6개월 일봉 → 수익률/밴드/이평선 요약. 원시 봉은 프롬프트에 넣지 않는다."""
    bars = client.get_ohlcv_long(stock_code, days=130)  # ≈ 6개월 거래일
    if not bars:
        return {"available": False}
    closes = [float(b.close) for b in bars]  # 최신순
    cur = closes[0]

    def _ma(n: int) -> float | None:
        return round(sum(closes[:n]) / n, 1) if len(closes) >= n else None

    high_6m, low_6m = max(float(b.high) for b in bars), min(float(b.low) for b in bars)
    return {
        "available": True,
        "current_price": cur,
        "change_pct_today": holding.get("change_pct") if holding else None,
        "return_1m_pct": _pct(cur, closes[20]) if len(closes) > 20 else None,
        "return_3m_pct": _pct(cur, closes[60]) if len(closes) > 60 else None,
        "return_6m_pct": _pct(cur, closes[-1]),
        "high_6m": high_6m,
        "low_6m": low_6m,
        "pos_in_6m_band_pct": round((cur - low_6m) / (high_6m - low_6m) * 100, 1)
                              if high_6m > low_6m else None,
        "ma20": _ma(20), "ma60": _ma(60), "ma120": _ma(120),
        "rsi_14": float(v) if (v := KISClient._compute_rsi(bars)) is not None else None,
    }


def _derive_quarters(income_rows: list[dict]) -> list[dict]:
    """손익계산서 YTD 누적 행 → 단일 분기 값 차분 + YoY + 영업이익률.
    income_rows: 최신순 [{period: 'YYYYMM', revenue, operating_profit, net_income}]"""
    by_period = {r["period"]: r for r in income_rows if r.get("period")}

    def _single(period: str) -> dict | None:
        row = by_period.get(period)
        if not row:
            return None
        year, month = period[:4], period[4:]
        if month == "03":  # 1분기는 YTD == 단일 분기
            vals = {k: row.get(k) for k in ("revenue", "operating_profit", "net_income")}
        else:
            prev_period = f"{year}{int(month) - 3:02d}"
            prev = by_period.get(prev_period)
            if not prev:
                return None
            vals = {}
            for k in ("revenue", "operating_profit", "net_income"):
                a, b = row.get(k), prev.get(k)
                vals[k] = round(a - b, 1) if a is not None and b is not None else None
        return vals

    out = []
    for r in income_rows:
        period = r.get("period")
        if not period:
            continue
        cur = _single(period)
        if not cur:
            continue
        yoy_period = f"{int(period[:4]) - 1}{period[4:]}"
        prev_y = _single(yoy_period)
        entry = {
            "period": period,
            "revenue_q": cur["revenue"],
            "operating_profit_q": cur["operating_profit"],
            "net_income_q": cur["net_income"],
            "op_margin_q_pct": round(cur["operating_profit"] / cur["revenue"] * 100, 2)
                               if cur.get("operating_profit") is not None and cur.get("revenue") else None,
        }
        for k, label in (("revenue", "revenue_yoy_pct"),
                         ("operating_profit", "op_yoy_pct"),
                         ("net_income", "ni_yoy_pct")):
            entry[label] = _pct(cur.get(k), prev_y.get(k)) if prev_y else None
        out.append(entry)
    return out


def _summarize_flow(rows: list[dict], holding: dict | None) -> dict:
    """일별 투자자 순매수(최근 30거래일) → 누적/최근 흐름 요약. 금액 단위: 백만원."""
    if not rows:
        return {"available": False}

    def _cum(key: str, n: int) -> float | None:
        vals = [r[key] for r in rows[:n] if r.get(key) is not None]
        return round(sum(vals), 0) if vals else None

    return {
        "available": True,
        "unit": "백만원",
        "frgn_net_5d": _cum("frgn_ntby_amt", 5),
        "frgn_net_20d": _cum("frgn_ntby_amt", 20),
        "frgn_net_30d": _cum("frgn_ntby_amt", 30),
        "orgn_net_5d": _cum("orgn_ntby_amt", 5),
        "orgn_net_20d": _cum("orgn_ntby_amt", 20),
        "orgn_net_30d": _cum("orgn_ntby_amt", 30),
        "prsn_net_30d": _cum("prsn_ntby_amt", 30),
        "recent_5d_daily": [
            {"date": r["date"], "frgn": r["frgn_ntby_amt"], "orgn": r["orgn_ntby_amt"]}
            for r in rows[:5]
        ],
        "frgn_exhaust_rate_pct": holding.get("frgn_exhaust_rate") if holding else None,
    }


def collect_input_snapshot(client: KISClient, stock_code: str,
                           stock_name: str, sector: str | None) -> dict:
    """분석 입력 데이터 일괄 수집. 이 dict가 그대로 프롬프트에 들어가고 DB에 저장된다."""
    holding = client.get_foreign_holding(stock_code)
    ratios = client.get_financial_ratios(stock_code, quarterly=True)
    income = client.get_income_statements(stock_code, quarterly=True)
    estimate = client.get_estimate_perform(stock_code)
    investor = client.get_investor_daily(stock_code)

    snapshot = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "stock": {"code": stock_code, "name": stock_name, "sector": sector},
        "price": _summarize_price(client, stock_code, holding),
        "valuation_current": {
            # KIS per/eps는 trailing(직전 공시 실적) 기준 — forward 아님
            "per_trailing": holding.get("per") if holding else None,
            "pbr": holding.get("pbr") if holding else None,
            "eps_trailing": holding.get("eps") if holding else None,
            "bps": holding.get("bps") if holding else None,
            "per_band_annual": {
                # estimate-perform의 연도별 PER (실적 3년 + 추정 2년) — 자기 과거 밴드 근사
                "periods": estimate.get("periods"),
                "per": estimate.get("per"),
            } if estimate else None,
        },
        "fundamentals_quarterly": {
            "ratios": ratios[:8],              # ROE/부채비율/EPS/BPS/성장률 (최근 8분기)
            "income_single_q": _derive_quarters(income[:10]),  # 단일분기 차분 + YoY + 영업이익률
            "income_note": "손익 수치 단위: 억원. income_single_q는 KIS YTD 누적을 차분한 단일 분기 값",
        },
        "consensus_estimate": estimate,        # 연도별 추정 매출/영업익/EPS/PER/ROE, 투자의견
        "investor_flow": _summarize_flow(investor, holding),
        "data_flags": {
            "consensus_target_price": "KIS 미제공 — 검색으로만 확인 가능",
            "investor_flow_history": "KIS API 한계로 최근 30거래일까지만 (3~6개월 누적 불가)",
            "frgn_holding_trend": "현재 시점 소진율만 제공 — 변화 추세는 과거 분석 스냅샷 축적 필요",
            "peer_valuation_band": "동종업종 대비 PER/PBR 밴드 KIS 미제공",
            "pbr_band": "PBR 과거 밴드 미계산 (연도별 PER 밴드만 제공)",
        },
    }
    if not estimate:
        snapshot["data_flags"]["consensus_estimate"] = "이 종목은 KIS 추정실적 커버리지 없음"
    return snapshot


# ------------------------------------------------------------------ #
# 분석 실행
# ------------------------------------------------------------------ #

def run_analysis(db, user_id: uuid.UUID, stock_code: str, stock_name: str,
                 sector: str | None, analysis_date: date,
                 trigger_type: str = "manual") -> StockAnalysis:
    """
    데이터 수집 → Gemini 구조화 → 스냅샷+결과 저장.
    주의: KIS 데이터는 항상 '현재 시점' 기준 — analysis_date를 과거로 지정해도
    입력은 수집 시점 데이터다 (snapshot.collected_at으로 구분 가능).
    """
    from app.services.kis.client import get_kis_client
    from app.services.gemini.analyzer import GeminiAnalyzer

    client = get_kis_client(db)
    snapshot = collect_input_snapshot(client, stock_code, stock_name, sector)

    prompt = _ANALYSIS_PROMPT.format(
        stock_name=stock_name,
        stock_code=stock_code,
        sector=sector or "미분류",
        analysis_date=str(analysis_date),
        snapshot_json=json.dumps(snapshot, ensure_ascii=False, indent=1),
    )

    analyzer = GeminiAnalyzer()
    result, raw_text, model = analyzer.grounded_json(prompt, WATCHLIST_MODEL)

    # 무효화_조건은 이 일지의 핵심 — 비어 있으면 1회 강제 재요청
    if not result.get("무효화_조건"):
        logger.warning("무효화_조건 누락 (%s) — 재요청", stock_code)
        result, raw_text, model = analyzer.grounded_json(
            prompt + _INVALIDATION_RETRY_SUFFIX, WATCHLIST_MODEL
        )
        if not result.get("무효화_조건"):
            raise ValueError("AI가 무효화_조건을 생성하지 못했습니다. 다시 시도해주세요.")

    # 스펙: 사용된 뉴스 출처는 스냅샷에도 포함 (grounding URL은 유통기한이 짧아 제목/매체/날짜 필수)
    snapshot["news_sources"] = result.get("뉴스_출처", [])

    analysis = StockAnalysis(
        user_id=user_id,
        stock_code=stock_code,
        stock_name=stock_name,
        analysis_date=analysis_date,
        trigger_type=trigger_type,
        gemini_model=model,
        result=result,
        input_snapshot=snapshot,
        raw_response=raw_text,
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    logger.info("Watchlist analysis saved: %s(%s) %s [%s]",
                stock_name, stock_code, analysis_date, model)
    return analysis
