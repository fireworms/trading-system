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
from datetime import date, datetime, timedelta, timezone

from app.models.watchlist import StockAnalysis
from app.services.kis.client import KISClient

logger = logging.getLogger(__name__)

WATCHLIST_MODEL = "gemini-2.5-flash"  # 검색 그라운딩 필요 (뉴스/공시/컨센서스 보강)

_ANALYSIS_PROMPT = """당신은 데이터를 구조화하는 애널리스트입니다. 역할은 투자 판단 재료의 정리이며, 주가 방향 예측이 아닙니다.

[종목] {stock_name}({stock_code}) — 섹터: {sector}
[분석 기준일] {analysis_date}

[입력 데이터 — KIS 실측 (이 수치만 신뢰하고, 여기 없는 수치는 검색으로 확인된 것만 사용)]
{snapshot_json}

[확정 외부 데이터 — 입력 스냅샷에 포함됨, 재검색 불필요]
- dart_disclosures: 공식 DART API로 수집한 최근 14일 공시 목록 (확정 데이터). 이 목록에 없는 공시를 만들어내지 말 것. 검색은 목록 내 주요 공시의 내용·시장 반응 해석에 사용할 것.
- news_recent: 네이버 뉴스 API 최신순 기사 목록 (제목/날짜/링크 — 날짜 신뢰 가능). 이 목록을 최우선 참조하고, 검색은 기사 내용 확인·보강 용도로 사용할 것. 뉴스_출처에 인용 시 목록의 날짜·링크를 그대로 쓸 것.

[검색으로 보강할 것 — 확인된 사실만, 출처 필수]
- 뉴스·공시는 분석 기준일 기준 최근 14일({recent_window_start} ~ {analysis_date}) 자료를 최우선으로 검색할 것. 그 이전 자료는 배경 맥락으로만 쓰고 날짜를 명시할 것.
- 최근 주가 변동의 동인 (필수 확인): {price_move_note} — 이 변동의 동인으로 지목되는 공시/기사를 dart_disclosures·news_recent에서 먼저 찾고, 없으면 검색으로 확인할 것. 그래도 확인되지 않으면 논거에 "동인 확인 불가"로 명시하고, 관련 없는 기사를 억지로 연결하지 말 것.
- dart_disclosures 목록 내 주요 공시(실적발표, 유상증자, 자사주, 대형계약, 합병/분할 등)의 내용과 시장 반응
- 섹터·테마 동향, 정책 모멘텀 ({sector} 관련)
- 증권사 목표주가/컨센서스: KIS 데이터에 목표주가는 없음. 검색으로 확인된 것만 인용하고, 확인 안 되면 "확인 불가"로 표기. 수치를 만들어내지 말 것.
- 리스크: 관리종목/거래정지 가능성, 최대주주 지분 변동, 소송/규제
- 매크로는 이 섹터에 실질 영향 있는 것만 (환율/금리/원자재 — 수출주/금융주/소재주 등 해당 시)

[규칙]
1. 펀더멘털 우선, 뉴스는 보조. 언급하는 모든 뉴스/이벤트는 "일시적 노이즈"인지 "구조적 변화"인지 반드시 구분해 표기할 것.
2. 근거 없는 방향성 단언 금지 — "단기 조정 후 상승 예상" 같은 표현 금지. 데이터가 말해주는 것과 말해주지 않는 것을 구분할 것.
3. 입력 데이터의 data_flags에 표시된 결측 항목은 결측으로 다루고, 그 공백이 판단에 중요하면 명시할 것.
4. 무효화_조건은 반드시 관측 가능한 신호로 쓸 것 (falsifiable) — 나쁜 예: "실적이 나빠지면" / 좋은 예: "다음 분기 단일분기 영업이익률이 X% 아래로 내려가면". 각 조건은 아래 구조의 객체로 쓸 것 — 앱이 check_type별로 매일 자동 감시한다. params의 수치 임계값은 입력 데이터의 현재 수치에서 도출하고(임의 생성 금지), 자동 감시 가능한 유형으로 표현되는 조건은 반드시 해당 유형을 쓸 것:
   - flow (외국인/기관 수급): {{"investor": "frgn"|"orgn", "direction": "sell"|"buy", "metric": "consecutive_days"|"cum_amount", "days": 거래일수, "amount_eok": 억원 (cum_amount일 때만)}}
   - fx (원/달러 레벨): {{"op": "above"|"below", "level": 원 단위 숫자}}
   - valuation (PBR 5년 밴드 위치): {{"metric": "pbr_percentile_5y", "op": "above"|"below", "value": 0~100}}
   - earnings (특정 분기 실적 — 공시 후 자동 판정): {{"period": "YYYYMM (대상 분기말, 예: 202606)", "metric": "op_margin_q_pct"|"op_yoy_pct"|"revenue_yoy_pct"|"ni_yoy_pct", "op": "below"|"above", "value": 숫자}}
   - consensus (연간 컨센서스 변화): {{"year": "YYYY", "metric": "operating_profit"|"eps"|"revenue", "drop_pct": 하향 임계 %}} — 이번 분석 시점 컨센서스 대비 drop_pct% 이상 하향되면 충족
   - manual (위 유형으로 표현 불가한 정성 신호 — 뉴스/공시/업황/경쟁구도): {{"확인_방법": "무엇으로 확인하는지"}} — 확인 시점이 확정 가능하면 명시(예: "2026년 2분기 실적 공시 시 확인"), 불확정이면 "상시 뉴스/공시 확인". 존재하지 않는 날짜를 지어내지 말 것.
5. 앱이 계산해 넣은 파생 지표는 재계산하지 말고 그대로 인용할 것 — investor_flow의 frgn_pace/orgn_pace judgment 문자열, market의 상대수익률/relative_note, fx_usdkrw의 trend_note, per_ttm, pbr_band_5y 퍼센타일. 직접 나눗셈/비율 계산 금지.
6. PER 시점 구분: per_trailing은 직전 공시 실적 기준이라 실적 급변 구간에서 왜곡됨 — income_single_q 추세와 괴리가 크면 per_ttm(최근 4개 분기 합산)과 per_forward_consensus를 우선해 밸류를 평가할 것.
7. 환율은 외국인 수급의 공통 팩터 — 외국인 순매도가 fx_usdkrw 추세와 동행하는 시장 공통 요인인지, market의 상대수익률상 종목 고유 요인인지 구분해 서술할 것.
8. 출력은 아래 JSON 형식만. 백틱(```)이나 설명 문장 없이 JSON 객체 하나만 출력할 것.

[출력 JSON 형식]
{{
  "논거": "현재 상태 요약 — 입력 데이터 기반. 펀더멘털(분기 추세/수익성/재무구조) → 수급(페이스 판정·환율 컨텍스트 포함) → 밸류 순으로.",
  "단기_촉매": [{{"이벤트": "...", "예상_시점": "...", "성격": "노이즈|구조적"}}],
  "장기_논거": "중장기 투자 논거 — 구조적 변화 중심. 없으면 '뚜렷한 장기 논거 확인 불가'라고 쓸 것.",
  "무효화_조건": [{{"조건": "관측 가능한 신호 서술", "check_type": "flow|fx|valuation|earnings|consensus|manual", "params": {{...}}}}],
  "밸류_코멘트": "현재 밸류에이션 평가 — 자기 과거 PER 밴드(per_band_annual)와 PBR 5년 퍼센타일(pbr_band_5y) 대비 위치 중심. per_ttm/per_trailing 괴리가 크면 그 이유를 명시. 업종 대비는 데이터 없으면 언급하지 말 것.",
  "뉴스_출처": [{{"제목": "...", "매체": "...", "날짜": "YYYY-MM-DD", "url": "..."}}]
}}"""

_INVALIDATION_RETRY_SUFFIX = """

[재요청] 직전 응답에 무효화_조건이 비어 있었습니다. 무효화_조건은 이 분석에서 가장 중요한 필드입니다.
입력 데이터에서 현재 상태를 정의하는 핵심 수치(분기 영업이익률, 외국인 순매수 추세, PER 위치 등)를 골라,
그것이 꺾이는 관측 가능한 임계 신호를 최소 2개 이상, 규칙 4의 구조({"조건", "check_type", "params"} 객체)로 반드시 작성하세요."""

_NEWS_RECENCY_RETRY_SUFFIX = """

[재요청] 직전 응답의 뉴스_출처에 분석 기준일 최근 14일 내 기사가 하나도 없었습니다.
"{stock_name} 주가", "{stock_name} 공시", "{stock_name} 뉴스" 등으로 최근 2주 자료를 다시 검색하세요.
실제로 최근 기사가 존재하지 않으면 억지로 채우지 말고, 논거에 "최근 2주 뉴스 확인 불가"를 명시하세요."""


# ------------------------------------------------------------------ #
# 입력 데이터 수집 → 스냅샷
# ------------------------------------------------------------------ #

def _pct(cur: float | None, base: float | None) -> float | None:
    if cur is None or not base:
        return None
    return round((cur / base - 1) * 100, 2)


def _fmt_eok(v_million: float) -> str:
    """백만원 → '억'/'조' 표기 (판정 문자열용)."""
    eok = v_million / 100
    if abs(eok) >= 10000:
        return f"{eok / 10000:+,.2f}조"
    return f"{eok:+,.0f}억"


def _pace_judgment(avg5: float | None, avg30: float | None) -> dict | None:
    """5일 vs 30일 일평균 순매수(백만원/일) → 가속/둔화/전환 판정.

    판정을 앱에서 확정해 문자열로 넣는 이유: LLM에 나눗셈/비교를 시키면
    산수 오류·자의적 해석 여지가 생김. 수치와 판정을 함께 담아 그대로 인용시킨다.
    """
    if avg5 is None or avg30 is None:
        return None
    # 30일 평균이 5일 평균 대비 무시할 수준이면 비율 판정이 폭주 — 중립 취급
    neutral30 = abs(avg30) < max(abs(avg5) * 0.05, 100)
    if neutral30 and abs(avg5) < 100:
        label = "뚜렷한 방향 없음"
    elif neutral30:
        label = ("최근 5일 순매수 유입 (30일 평균은 중립)" if avg5 > 0
                 else "최근 5일 순매도 출회 (30일 평균은 중립)")
    elif avg5 * avg30 < 0:
        label = "매도→매수 전환 (최근 5일)" if avg5 > 0 else "매수→매도 전환 (최근 5일)"
    else:
        direction = "매수" if avg30 > 0 else "매도"
        ratio = abs(avg5) / abs(avg30)
        if ratio > 1.2:
            label = f"{direction} 가속"
        elif ratio < 0.8:
            label = f"{direction} 둔화"
        else:
            label = f"{direction} 지속 (페이스 유사)"
    return {
        "avg_5d": round(avg5, 0),
        "avg_30d": round(avg30, 0),
        "judgment": f"5일 일평균 {_fmt_eok(avg5)} vs 30일 일평균 {_fmt_eok(avg30)} → {label}",
    }


def _summarize_fx(client: KISClient) -> dict:
    """USD/KRW 3개월 추세 요약 — 외인 수급의 공통 팩터.

    레벨만으로는 부족: '1420→1500 상승 중'인지 '1550→1500 하락 중'인지가
    외인 순매도 해석의 핵심이라 1개월/3개월 전 레벨과 방향을 함께 담는다.
    """
    rows = client.get_fx_daily_closes(days=65)  # ≈ 3개월 거래일
    if not rows:
        return {"available": False}
    cur = rows[0]["close"]
    rate_1m = rows[20]["close"] if len(rows) > 20 else None
    rate_3m = rows[-1]["close"] if len(rows) >= 40 else None
    closes = [r["close"] for r in rows]
    chg_1m, chg_3m = _pct(cur, rate_1m), _pct(cur, rate_3m)
    if chg_3m is None:
        trend = None
    elif chg_3m >= 1.5:
        trend = "원/달러 상승 추세 — 원화 약세 진행 (외인 원화자산에 환손실 방향)"
    elif chg_3m <= -1.5:
        trend = "원/달러 하락 추세 — 원화 강세 진행 (외인 원화자산에 환차익 방향)"
    else:
        trend = "원/달러 횡보"
    return {
        "available": True,
        "pair": "USD/KRW",
        "current": cur,
        "as_of": rows[0]["date"],
        "rate_1m_ago": rate_1m,
        "rate_3m_ago": rate_3m,
        "change_1m_pct": chg_1m,
        "change_3m_pct": chg_3m,
        "high_3m": max(closes),
        "low_3m": min(closes),
        "trend_note": (f"3개월 전 {rate_3m} → 1개월 전 {rate_1m} → 현재 {cur} ({trend})"
                       if rate_3m and rate_1m else trend),
    }


def _summarize_market(client: KISClient, price: dict) -> dict:
    """KOSPI 레벨/추세 + 종목의 시장 대비 상대수익률 (앱 계산 — LLM 재계산 금지).

    '시장 전체가 빠지는가, 이 종목만 빠지는가'를 수치로 확정해 넣는다.
    """
    closes = client.get_index_daily_closes("0001", days=61)
    if not closes:
        return {"available": False}
    cur = closes[0]
    chg_1m = _pct(cur, closes[20]) if len(closes) > 20 else None
    chg_3m = _pct(cur, closes[60]) if len(closes) > 60 else None
    out = {
        "available": True,
        "kospi_level": cur,
        "kospi_change_1m_pct": chg_1m,
        "kospi_change_3m_pct": chg_3m,
    }
    r1, r3 = price.get("return_1m_pct"), price.get("return_3m_pct")
    rel1 = round(r1 - chg_1m, 2) if r1 is not None and chg_1m is not None else None
    rel3 = round(r3 - chg_3m, 2) if r3 is not None and chg_3m is not None else None
    out["stock_rel_return_1m_pct"] = rel1
    out["stock_rel_return_3m_pct"] = rel3
    if rel3 is not None:
        if rel3 <= -3:
            out["relative_note"] = f"3개월 KOSPI 대비 {rel3:+.1f}%p 언더퍼폼 — 종목 고유 약세 요인 존재"
        elif rel3 >= 3:
            out["relative_note"] = f"3개월 KOSPI 대비 {rel3:+.1f}%p 아웃퍼폼 — 종목 고유 강세"
        else:
            out["relative_note"] = f"3개월 KOSPI 대비 {rel3:+.1f}%p — 시장과 대체로 동행"
    return out


def _pbr_band_5y(client: KISClient, stock_code: str, pbr_now: float | None) -> dict:
    """5년 PBR 밴드 근사 — 월별 종가 ÷ 당시 최근 연간 BPS → 현재 PBR 퍼센타일.

    KIS가 PBR 이력을 직접 주지 않아 근사 계산. 자사주 소각/유상증자 구간은
    왜곡될 수 있어 note로 명시 (LLM이 '역사상 최고' 류 과단정 못 하게).
    """
    annual = client.get_financial_ratios(stock_code, quarterly=False)  # 최신순
    bars = client.get_ohlcv_monthly(stock_code, months=60)
    bps_by_period = [(r["period"], r["bps"]) for r in annual
                     if r.get("period") and r.get("bps") and r["bps"] > 0]
    if not bps_by_period or not bars:
        return {"available": False}
    series = []
    for b in bars:
        month = b.date[:6]
        bps = next((v for p, v in bps_by_period if p <= month), None)
        if bps:
            series.append(round(float(b.close) / bps, 2))
    if len(series) < 12:
        return {"available": False}
    cur = pbr_now if pbr_now else series[0]
    percentile = round(sum(1 for v in series if v <= cur) / len(series) * 100, 1)
    return {
        "available": True,
        "months": len(series),
        "pbr_current": cur,
        "pbr_5y_min": min(series),
        "pbr_5y_max": max(series),
        "pbr_5y_median": round(sorted(series)[len(series) // 2], 2),
        "pbr_percentile_5y": percentile,
        "note": "월별 종가 ÷ 당시 최근 연간 BPS 근사 — 자사주 소각/유상증자 구간 왜곡 가능",
    }


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

    def _avg(key: str, n: int) -> float | None:
        vals = [r[key] for r in rows[:n] if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    return {
        "available": True,
        "unit": "백만원",
        "frgn_net_5d": _cum("frgn_ntby_amt", 5),
        "frgn_net_20d": _cum("frgn_ntby_amt", 20),
        "frgn_net_30d": _cum("frgn_ntby_amt", 30),
        "orgn_net_5d": _cum("orgn_ntby_amt", 5),
        "orgn_net_20d": _cum("orgn_ntby_amt", 20),
        "orgn_net_30d": _cum("orgn_ntby_amt", 30),
        # 개인 = 외인/기관 물량의 반대편 — 분산 패턴 판단 근거로 명시적으로 포함
        "prsn_net_5d": _cum("prsn_ntby_amt", 5),
        "prsn_net_20d": _cum("prsn_ntby_amt", 20),
        "prsn_net_30d": _cum("prsn_ntby_amt", 30),
        # 페이스 판정은 앱이 확정 — judgment 문자열을 그대로 인용할 것 (재계산 금지)
        "frgn_pace": _pace_judgment(_avg("frgn_ntby_amt", 5), _avg("frgn_ntby_amt", 30)),
        "orgn_pace": _pace_judgment(_avg("orgn_ntby_amt", 5), _avg("orgn_ntby_amt", 30)),
        "recent_5d_daily": [
            {"date": r["date"], "frgn": r["frgn_ntby_amt"], "orgn": r["orgn_ntby_amt"],
             "prsn": r["prsn_ntby_amt"]}
            for r in rows[:5]
        ],
        "frgn_exhaust_rate_pct": holding.get("frgn_exhaust_rate") if holding else None,
    }


def collect_input_snapshot(client: KISClient, stock_code: str,
                           stock_name: str, sector: str | None,
                           db=None) -> dict:
    """분석 입력 데이터 일괄 수집. 이 dict가 그대로 프롬프트에 들어가고 DB에 저장된다.

    db를 넘기면 일별 수급을 investor_flow_daily에 적재하고 60/120일 누적도 읽는다.
    """
    holding = client.get_foreign_holding(stock_code)
    ratios = client.get_financial_ratios(stock_code, quarterly=True)
    income = client.get_income_statements(stock_code, quarterly=True)
    estimate = client.get_estimate_perform(stock_code)
    investor = client.get_investor_daily(stock_code)

    price = _summarize_price(client, stock_code, holding)
    quarters = _derive_quarters(income[:10])

    # ---- PER 시점 보정 (trailing은 구실적 분기 포함 — 실적 급변 구간에서 왜곡) ----
    mcap = holding.get("market_cap_eok") if holding else None  # 억원
    ni_last4 = [q["net_income_q"] for q in quarters[:4] if q.get("net_income_q") is not None]
    ttm_ni = round(sum(ni_last4), 1) if len(ni_last4) == 4 else None  # 억원
    per_ttm = round(mcap / ttm_ni, 2) if mcap and ttm_ni and ttm_ni > 0 else None
    last_q_ni = quarters[0].get("net_income_q") if quarters else None
    per_last_q_ann = (round(mcap / (last_q_ni * 4), 2)
                      if mcap and last_q_ni and last_q_ni > 0 else None)
    per_forward = None
    if estimate and estimate.get("periods") and estimate.get("per"):
        per_forward = [{"period": p, "per": v}
                       for p, v in zip(estimate["periods"], estimate["per"])
                       if p and v and "E" in str(p).upper()] or None

    # ---- 외부 소스: DART 공시(확정) + 네이버 뉴스(최신순) — 실패 시 data_flags 폴백 ----
    from app.services.dart.client import fetch_recent_disclosures
    from app.services.naver.news import fetch_recent_news
    disclosures = fetch_recent_disclosures(stock_code)
    recent_news = fetch_recent_news(stock_name)

    # ---- 수급: 30일 실측 요약 + 적재분 60/120일 + 적재 upsert ----
    flow = _summarize_flow(investor, holding)
    flow_extended = None
    if db is not None:
        try:
            from app.services.watchlist.flow_store import upsert_investor_flows, get_extended_flow
            upsert_investor_flows(db, stock_code, investor)  # 분석 자체가 히스토리 축적에 기여
            flow_extended = get_extended_flow(db, stock_code)
        except Exception as e:
            logger.warning("flow store failed for %s: %s", stock_code, e)

    snapshot = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "stock": {"code": stock_code, "name": stock_name, "sector": sector},
        "price": price,
        # 외인 수급의 공통 팩터 — 순매도가 환율 추세와 동행하는지 판단 재료
        "fx_usdkrw": _summarize_fx(client),
        # 시장 국면 + 종목의 시장 대비 상대수익률 (앱 계산)
        "market": _summarize_market(client, price),
        "valuation_current": {
            # KIS per/eps는 trailing(직전 공시 실적) 기준 — forward 아님
            "per_trailing": holding.get("per") if holding else None,
            "per_ttm": per_ttm,                  # 최근 4개 분기 합산 순이익 기준 (주 지표)
            "per_last_q_annualized": per_last_q_ann,  # 최근 분기 ×4 — 계절성/일회성 왜곡 주의
            "per_forward_consensus": per_forward,     # 컨센서스 추정연도 PER
            "per_note": "실적 급변 구간에서는 per_trailing이 왜곡됨 — per_ttm을 우선 사용",
            "pbr": holding.get("pbr") if holding else None,
            "eps_trailing": holding.get("eps") if holding else None,
            "bps": holding.get("bps") if holding else None,
            "market_cap_eok": mcap,
            "per_band_annual": {
                # estimate-perform의 연도별 PER (실적 3년 + 추정 2년) — 자기 과거 밴드 근사
                "periods": estimate.get("periods"),
                "per": estimate.get("per"),
            } if estimate else None,
            # 자기 과거 5년 대비 PBR 위치 (근사)
            "pbr_band_5y": _pbr_band_5y(client, stock_code,
                                        holding.get("pbr") if holding else None),
        },
        "fundamentals_quarterly": {
            "ratios": ratios[:8],              # ROE/부채비율/EPS/BPS/성장률 (최근 8분기)
            "income_single_q": quarters,       # 단일분기 차분 + YoY + 영업이익률
            "income_note": "손익 수치 단위: 억원. income_single_q는 KIS YTD 누적을 차분한 단일 분기 값",
        },
        "consensus_estimate": estimate,        # 연도별 추정 매출/영업익/EPS/PER/ROE, 투자의견
        "investor_flow": flow,
        "investor_flow_extended": flow_extended or {
            "available": False, "note": "적재분 조회 불가 (db 미제공)"},
        # 확정 외부 데이터 — 검색 "발견"이 아닌 공식 API 수집 (스펙: 출처와 함께 스냅샷 보존)
        "dart_disclosures": disclosures,
        "news_recent": recent_news,
        "data_flags": {
            "consensus_target_price": "KIS 미제공 — 검색으로만 확인 가능",
            "investor_flow_history": "KIS 직접조회는 최근 30거래일까지 — "
                                     "60/120일 누적은 investor_flow_extended(자체 적재분) 참조",
            "frgn_holding_trend": "현재 시점 소진율만 제공 — 변화 추세는 과거 분석 스냅샷 축적 필요",
            "peer_valuation_band": "동종업종 대비 PER/PBR 밴드 KIS 미제공",
        },
    }
    if not estimate:
        snapshot["data_flags"]["consensus_estimate"] = "이 종목은 KIS 추정실적 커버리지 없음"
    if not snapshot["fx_usdkrw"].get("available"):
        snapshot["data_flags"]["fx_usdkrw"] = "환율 조회 실패 — 이번 분석은 환율 컨텍스트 없이 수행됨"
    if not snapshot["valuation_current"]["pbr_band_5y"].get("available"):
        snapshot["data_flags"]["pbr_band"] = "PBR 5년 밴드 계산 불가 (BPS/월봉 데이터 부족)"
    if not disclosures.get("available"):
        snapshot["data_flags"]["dart_disclosures"] = (
            f"DART 공시 조회 실패 — 공시는 Gemini 검색으로만 확인됨: {disclosures.get('note')}")
    if not recent_news.get("available"):
        snapshot["data_flags"]["news_recent"] = (
            f"네이버 뉴스 조회 실패 — 최신 기사는 Gemini 검색에 의존: {recent_news.get('note')}")
    return snapshot


def _count_recent_news(sources: list | None, analysis_date: date, days: int = 14) -> int:
    """뉴스_출처 중 기준일 최근 N일 내 기사 수. 날짜는 LLM 서술이라 파싱 불가는 비최신 취급."""
    cutoff = analysis_date - timedelta(days=days)
    n = 0
    for s in sources or []:
        try:
            d = date.fromisoformat(str(s.get("날짜", ""))[:10])
        except ValueError:
            continue
        if d >= cutoff:
            n += 1
    return n


def _price_move_note(price: dict) -> str:
    """최근 주가 변동 요약 문장 — 앱이 수치 확정해 프롬프트에 주입 (LLM 재계산 방지)."""
    r1m = price.get("return_1m_pct")
    chg = price.get("change_pct_today")
    parts = []
    if r1m is not None:
        parts.append(f"최근 1개월 수익률 {r1m:+.1f}%")
    if chg is not None:
        parts.append(f"당일 등락률 {chg:+.2f}%")
    return ", ".join(parts) or "가격 데이터 결측"


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
    snapshot = collect_input_snapshot(client, stock_code, stock_name, sector, db=db)

    prompt = _ANALYSIS_PROMPT.format(
        stock_name=stock_name,
        stock_code=stock_code,
        sector=sector or "미분류",
        analysis_date=str(analysis_date),
        recent_window_start=str(analysis_date - timedelta(days=14)),
        price_move_note=_price_move_note(snapshot.get("price", {})),
        snapshot_json=json.dumps(snapshot, ensure_ascii=False, indent=1),
    )

    from app.services.watchlist.invalidation import normalize_conditions, send_condition_notice

    analyzer = GeminiAnalyzer()
    result, raw_text, model = analyzer.grounded_json(prompt, WATCHLIST_MODEL)

    # 무효화_조건은 이 일지의 핵심 — 정규화(구조 검증) 후 비어 있으면 1회 강제 재요청.
    # 구조가 불완전한 조건은 normalize가 manual로 강등하므로 여기서 유실되지 않는다.
    result["무효화_조건"] = normalize_conditions(result.get("무효화_조건"))
    if not result["무효화_조건"]:
        logger.warning("무효화_조건 누락 (%s) — 재요청", stock_code)
        result, raw_text, model = analyzer.grounded_json(
            prompt + _INVALIDATION_RETRY_SUFFIX, WATCHLIST_MODEL
        )
        result["무효화_조건"] = normalize_conditions(result.get("무효화_조건"))
        if not result["무효화_조건"]:
            raise ValueError("AI가 무효화_조건을 생성하지 못했습니다. 다시 시도해주세요.")

    # 뉴스 최신성 — 최근 14일 기사 0건이면 1회 재검색 요청.
    # 억지 인용은 강제하지 않음: 재요청 후에도 없으면 data_flags에 확인 실패만 명시하고 저장
    if _count_recent_news(result.get("뉴스_출처"), analysis_date) == 0:
        logger.warning("최근 14일 뉴스 없음 (%s) — 재검색 요청", stock_code)
        r2, t2, m2 = analyzer.grounded_json(
            prompt + _NEWS_RECENCY_RETRY_SUFFIX.format(stock_name=stock_name),
            WATCHLIST_MODEL,
        )
        r2["무효화_조건"] = normalize_conditions(r2.get("무효화_조건"))
        if r2["무효화_조건"]:  # 재요청 응답이 핵심 필드를 갖췄을 때만 교체
            result, raw_text, model = r2, t2, m2
        if _count_recent_news(result.get("뉴스_출처"), analysis_date) == 0:
            snapshot["data_flags"]["news_recency"] = (
                f"기준일 {analysis_date} 기준 14일 내 기사 확인 실패 — "
                "뉴스_출처가 전부 이전 자료이거나 날짜 불명"
            )

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

    # 조건 감시 안내 (자동 감시 대상 + 수동 확인 필요 목록) — 실패해도 분석은 유효
    try:
        send_condition_notice(db, user_id, stock_name, stock_code, result["무효화_조건"])
    except Exception as e:
        logger.warning("condition notice failed for %s: %s", stock_code, e)
    return analysis
