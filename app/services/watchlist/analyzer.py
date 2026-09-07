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

_NI_OP_GAP_RATIO = 1.2   # 순이익/영업이익 이 배수 초과 시 영업외 요인 플래그
_PBR_BASIS_GAP_PCT = 15  # 연간 BPS vs 최근 분기 BPS 기준 PBR 괴리 경고 임계 (%)

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
   ▸ 임계값 품질 기준 — 앱이 생성 직후 결정론으로 검사하고, 위반 조건은 자동 감시에서 제외한다:
     · 이미 충족됐거나 임계의 70% 이상 도달한 조건 금지. 그건 조건이 아니라 예정된 사건이다 — 입력 데이터의 현재 수치(investor_flow 누적, pbr_percentile_5y, fx 현재값)를 먼저 확인하고 거기서 의미 있게 떨어진 지점을 잡을 것.
     · earnings의 YoY 성장률(op_yoy_pct/revenue_yoy_pct/ni_yoy_pct) 임계는 전년 동기 수준에 종속돼 논거와 무관하게 충족/붕괴한다. op_margin_q_pct 같은 절대 수준 지표를 우선하고, YoY를 쓰더라도 임계 절대값 100%를 넘기지 말 것.
     · valuation은 above(고평가 진입) 방향만 의미가 있다. 밸류가 싸지는 것(below)은 강세 논거의 반증이 아니다.
     · fx 임계는 fx_usdkrw의 최근 1개월 진폭(low_1m~high_1m) 바깥이어야 이례 신호다. 그 안의 레벨은 평상시 변동이다. 단 너무 멀면(1개월 지평 변동성 대비 3.5σ 초과) 영원히 발동하지 않으니 도달 가능한 범위로 잡을 것 — high_3m/low_3m은 추세 이동을 담고 있어 그 바깥을 요구하면 도달 불가 임계가 되기 쉽다.
     · 반대편도 같은 결함이다 — 현재 수준에서 지나치게 먼 임계(예: 현재 영업이익률 76%인데 기준 50%)는 실적이 무너져도 켜지지 않는다. 항상 켜져 있거나 절대 안 켜지는 조건은 둘 다 신호가 아니다.
   ▸ 임계값의 최종 결정은 앱이 한다: 앱이 적재 수급 분포·환율 변동성·분기 마진 변동폭에서 "과거 10%에서만 발동하는 지점"을 계산해 params의 수치를 덮어쓴다. 당신이 정할 것은 **무엇을 감시할지**(투자자/방향/지표/대상 기간)이고, 수치는 입력 데이터에서 도출한 합리적 초기값이면 된다. 숫자를 정교하게 맞추려 애쓰기보다 감시 대상 선택이 논거와 정합적인지에 집중할 것.
5. 앱이 계산해 넣은 파생 지표는 재계산하지 말고 그대로 인용할 것 — investor_flow의 frgn_pace/orgn_pace judgment 문자열, market의 상대수익률/relative_note, fx_usdkrw의 trend_note, per_ttm, pbr_band_5y 퍼센타일, valuation_scenarios의 함의주가. 직접 나눗셈/비율 계산 금지.
6. PER 시점 구분: per_trailing은 직전 공시 실적 기준이라 실적 급변 구간에서 왜곡됨 — income_single_q 추세와 괴리가 크면 per_ttm(최근 4개 분기 합산)과 per_forward_consensus를 우선해 밸류를 평가할 것.
7. 환율은 외국인 수급의 공통 팩터 — 외국인 순매도가 fx_usdkrw 추세와 동행하는 시장 공통 요인인지, market의 상대수익률상 종목 고유 요인인지 구분해 서술할 것.
8. valuation_scenarios는 앱이 배수 밴드에서 역산한 산술값(현재가 × 목표배수 ÷ 현재배수)이다. 새 목표주가를 만들어내지 말고, 이 표에서 현재 논거와 정합적인 행을 고르고 그 행의 "전제"가 성립할 조건을 서술할 것. warnings에 담긴 경고(이익 피크 구간·영업외 요인·장부가 시점차)는 반드시 반영할 것 — 경고를 무시한 상단 인용 금지. 행에 "신뢰도"가 붙어 있으면 그 행을 인용할 때 반드시 사유를 함께 밝히고, 신뢰도 낮은 행만 남아 있으면 "현재 국면에서는 밴드 회귀 역산이 유효하지 않다"고 명시할 것 — 근거 없는 숫자를 만들어 채우지 말 것.
9. 날짜를 지어내지 말 것. 실적 발표일·공시일은 dart_disclosures의 rcept_dt에서, 기사 날짜는 news_recent에서만 인용한다. 정기보고서 법정 제출기한은 실제 실적 발표일이 아니다 — 둘을 혼동하지 말 것.
10. 단기_촉매는 분석 기준일 이후에 발생할 이벤트만 쓸 것. 이미 발표·확정된 건은 논거의 배경으로 서술하고 촉매에 넣지 말 것. 예상_시점은 미래 날짜 또는 확정된 기한이어야 한다.
11. 장기_논거에 인용한 이벤트가 단기 수급에 반대로 작용하는지 반드시 검토할 것 — 같은 사건이 장기적으로 긍정이면서 단기적으로는 물량 부담(신주 희석·보호예수 해제·차익거래 유인)일 수 있다. 한쪽 방향만 서술하지 말 것.
12. 출력은 아래 JSON 형식만. 백틱(```)이나 설명 문장 없이 JSON 객체 하나만 출력할 것.

[출력 JSON 형식]
{{
  "핵심_주장": "이 분석이 성립하려면 참이어야 하는 명제 한 문장 — 검증 가능한 형태로 (예: '메모리 가격 결정력이 유지돼 분기 영업이익률 60% 이상이 지속된다'). 무효화_조건은 이 명제를 반증하는 신호여야 한다. 주가 방향·목표가 단언 금지.",
  "논거": "현재 상태 요약 — 입력 데이터 기반. 펀더멘털(분기 추세/수익성/재무구조) → 수급(페이스 판정·환율 컨텍스트 포함) → 밸류 순으로.",
  "단기_촉매": [{{"이벤트": "...", "예상_시점": "...", "성격": "노이즈|구조적"}}],
  "장기_논거": "중장기 투자 논거 — 구조적 변화 중심. 없으면 '뚜렷한 장기 논거 확인 불가'라고 쓸 것.",
  "무효화_조건": [{{"조건": "관측 가능한 신호 서술", "check_type": "flow|fx|valuation|earnings|consensus|manual", "params": {{...}}}}],
  "밸류_시나리오_코멘트": "valuation_scenarios 표에서 현재 논거와 정합적인 행을 고르고, 그 행의 전제와 warnings를 함께 서술. 표의 함의주가를 그대로 인용하고 새 수치를 만들지 말 것. 시나리오 불가(available=false)면 '역산 불가'로 명시.",
  "밸류_코멘트": "현재 밸류에이션 평가 — 자기 과거 PER 밴드(per_band_annual)와 PBR 5년 퍼센타일(pbr_band_5y) 대비 위치 중심. per_ttm/per_trailing 괴리가 크면 그 이유를 명시. 업종 대비는 데이터 없으면 언급하지 말 것.",
  "뉴스_출처": [{{"제목": "...", "매체": "...", "날짜": "YYYY-MM-DD", "url": "..."}}]
}}"""

_FALSIFICATION_PROMPT = """당신은 아래 명제를 **반증하는 것만**이 임무인 검증 담당자입니다. 이 명제를 지지하는 논거는 의도적으로 제공되지 않습니다.

[종목] {stock_name}({stock_code}) — 섹터: {sector}
[분석 기준일] {analysis_date}

[검증 대상 명제]
{claim}

[입력 데이터 — KIS 실측 + 확정 공시/뉴스]
{snapshot_json}

[임무]
위 명제가 **틀렸을 경우 지금 데이터에 이미 나타나 있을 흔적**을 찾으세요. 같은 수치를 약세로 읽는 방법, 낙관 서사가 빠뜨리기 쉬운 항목(경쟁 구도, 이익의 질, 수급 주체 이탈, 밸류 기저, 희석 요인)을 우선 보세요.

[규칙]
1. 모든 항목은 입력 데이터의 **구체적 근거를 인용**해야 합니다 — 스냅샷 필드명(예: investor_flow.frgn_ntby_30d, income_single_q, per_band_annual) 또는 dart_disclosures/news_recent의 실제 항목(제목+날짜). 인용할 근거가 없으면 그 항목은 쓰지 마세요.
2. 근거 없는 일반론 금지 — "경쟁 심화 가능성", "업황 둔화 우려" 같은 어디에나 붙는 서술은 그 자체로 무효입니다. 이 종목 이 시점의 데이터에서만 나올 수 있는 관찰이어야 합니다.
3. 검색은 명제에 불리한 사실 확인에만 쓰세요. 확인된 사실만 인용하고 날짜를 명시하세요. 반증 재료가 실제로 빈약하면 억지로 채우지 말고 적은 수만 쓰세요 (빈 배열도 허용).
4. 주가 방향 단언·목표가 생성 금지. 당신은 "이 명제가 깨지는 경로"를 서술할 뿐입니다.
5. 앱이 계산해 넣은 파생 지표(judgment 문자열, 상대수익률, per_ttm, 퍼센타일, valuation_scenarios)는 재계산하지 말고 그대로 인용하세요.
6. 무효화_조건의 params 수치 임계는 앱이 분포에서 재계산해 덮어씁니다. 무엇을 감시할지에 집중하세요.
7. 출력은 아래 JSON 형식만. 백틱이나 설명 문장 없이 JSON 객체 하나만.

[출력 JSON 형식]
{{
  "반대_해석": [{{"관측": "데이터에서 확인되는 사실", "근거": "스냅샷 필드명 또는 공시/기사 제목+날짜", "약세_해석": "이 사실이 명제를 어떻게 위협하는가"}}],
  "무효화_조건": [{{"조건": "관측 가능한 신호 서술", "check_type": "flow|fx|valuation|earnings|consensus|manual", "params": {{...}}}}]
}}

[무효화_조건 params 구조 — 위 형식과 동일하게 쓸 것]
- flow: {{"investor": "frgn"|"orgn", "direction": "sell"|"buy", "metric": "consecutive_days"|"cum_amount", "days": 거래일수, "amount_eok": 억원 (cum_amount일 때만)}}
- fx: {{"op": "above"|"below", "level": 원 단위 숫자}}
- valuation: {{"metric": "pbr_percentile_5y", "op": "above", "value": 0~100}}
- earnings: {{"period": "YYYYMM", "metric": "op_margin_q_pct"|"op_yoy_pct"|"revenue_yoy_pct"|"ni_yoy_pct", "op": "below"|"above", "value": 숫자}}
- consensus: {{"year": "YYYY", "metric": "operating_profit"|"eps"|"revenue", "drop_pct": 하향 임계 %}}
- manual: {{"확인_방법": "무엇으로 확인하는지"}}"""


_INVALIDATION_RETRY_SUFFIX = """

[재요청] 직전 응답에 무효화_조건이 비어 있었습니다. 무효화_조건은 이 분석에서 가장 중요한 필드입니다.
입력 데이터에서 현재 상태를 정의하는 핵심 수치(분기 영업이익률, 외국인 순매수 추세, PER 위치 등)를 골라,
그것이 꺾이는 관측 가능한 임계 신호를 최소 2개 이상, 규칙 4의 구조({"조건", "check_type", "params"} 객체)로 반드시 작성하세요."""

_CONDITION_QUALITY_RETRY_SUFFIX = """

[재요청] 직전 응답의 무효화_조건 중 아래 항목이 규칙 4의 임계값 품질 기준에 걸렸습니다.
{defects}

해당 조건들을 규칙 4의 품질 기준에 맞게 다시 작성하세요. 임계값은 입력 데이터의 현재 수치를
먼저 확인하고, 거기서 의미 있게 떨어진 — 아직 충족되지 않았고 임박하지도 않은 — 지점으로 잡으세요.
문제 없던 조건은 그대로 유지하고, 전체 무효화_조건 배열을 다시 출력하세요."""

_NEWS_RECENCY_RETRY_SUFFIX = """

[재요청] 직전 응답의 뉴스_출처에 분석 기준일 최근 14일 내 기사가 하나도 없었습니다.
"{stock_name} 주가", "{stock_name} 공시", "{stock_name} 뉴스" 등으로 최근 2주 자료를 다시 검색하세요.
실제로 최근 기사가 존재하지 않으면 억지로 채우지 말고, 논거에 "최근 2주 뉴스 확인 불가"를 명시하세요."""


# ------------------------------------------------------------------ #
# 입력 데이터 수집 → 스냅샷
# ------------------------------------------------------------------ #

def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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


def _pace_judgment(recent: tuple[float, int] | None,
                   base: tuple[float, int] | None) -> dict | None:
    """최근 단기 vs 장기 일평균 순매수(백만원/일) → 가속/둔화/전환 판정.

    판정을 앱에서 확정해 문자열로 넣는 이유: LLM에 나눗셈/비교를 시키면
    산수 오류·자의적 해석 여지가 생김. 수치와 판정을 함께 담아 그대로 인용시킨다.

    입력은 (일평균, 실제 거래일 수) 튜플 — 결측일이 섞이면 분모가 창 길이보다
    작아지므로 라벨에 실제 일수를 그대로 노출한다. "5일 일평균"이라고 써놓고
    4로 나눈 값을 넣으면 판정은 맞아도 수치가 거짓말이 된다.
    """
    if recent is None or base is None:
        return None
    avg5, n5 = recent
    avg30, n30 = base
    # 30일 평균이 5일 평균 대비 무시할 수준이면 비율 판정이 폭주 — 중립 취급
    neutral30 = abs(avg30) < max(abs(avg5) * 0.05, 100)
    if neutral30 and abs(avg5) < 100:
        label = "뚜렷한 방향 없음"
    elif neutral30:
        label = (f"최근 {n5}거래일 순매수 유입 ({n30}거래일 평균은 중립)" if avg5 > 0
                 else f"최근 {n5}거래일 순매도 출회 ({n30}거래일 평균은 중립)")
    elif avg5 * avg30 < 0:
        label = (f"매도→매수 전환 (최근 {n5}거래일)" if avg5 > 0
                 else f"매수→매도 전환 (최근 {n5}거래일)")
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
        "avg_recent_daily": round(avg5, 0),
        "recent_days": n5,
        "avg_base_daily": round(avg30, 0),
        "base_days": n30,
        "judgment": (f"최근 {n5}거래일 일평균 {_fmt_eok(avg5)} vs "
                     f"{n30}거래일 일평균 {_fmt_eok(avg30)} → {label}"),
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
    # 일간 변동성 — 환율 무효화_조건 임계를 σ 기준으로 잡기 위한 앱 파생값
    # (레벨만 있으면 "밴드 밖"이라는 이유로 도달 불가능한 임계가 통과한다)
    daily_rets = [(closes[i] / closes[i + 1] - 1) * 100
                  for i in range(len(closes) - 1) if closes[i + 1]]
    daily_vol = None
    if len(daily_rets) >= 20:
        mean = sum(daily_rets) / len(daily_rets)
        daily_vol = round(
            (sum((r - mean) ** 2 for r in daily_rets) / (len(daily_rets) - 1)) ** 0.5, 3)
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
        # 1개월 진폭 — 무효화 임계의 "평상시 변동" 기준. 3개월 밴드는 추세 이동을
        # 담고 있어(예: 1551→1346) 밴드 밖을 요구하면 도달 불가 임계가 된다.
        "high_1m": max(closes[:21]),
        "low_1m": min(closes[:21]),
        "daily_vol_pct": daily_vol,
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


def _multiple_band(bars, by_period: list, cur_multiple: float | None,
                   basis: str) -> dict:
    """월별 종가 ÷ 당시 최근 연간 기준값(BPS/EPS) → 배수 시계열 + 현재 퍼센타일.

    과거 점과 현재 점이 동일하게 "직전 확정 연간 값" 기준이라 퍼센타일 자체는
    내부 일관성이 있다. 다만 반기 이익이 기준값을 크게 바꾸는 구간에서는 그 시점차가
    이례적으로 커지므로, 호출부가 최근 분기 기준 배수를 병기해 함께 읽게 한다.
    """
    if not by_period or not bars:
        return {"available": False}
    series = []
    for b in bars:
        base = next((v for prd, v in by_period if prd <= b.date[:6]), None)
        if base:
            series.append(round(float(b.close) / base, 2))
    if len(series) < 12:
        return {"available": False}
    cur = cur_multiple if cur_multiple else series[0]
    ordered = sorted(series)

    def _q(pct: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(len(ordered) * pct))], 2)

    return {
        "available": True,
        "months": len(series),
        "current": cur,
        # min/max는 참고용 — 시나리오 역산에는 p20/p80을 쓴다.
        # 사이클 종목의 PER 밴드 상단은 "이익 바닥" 시기에 만들어지므로(EPS가 0에 가까우면
        # PER이 폭증) 그 배수를 피크 이익에 곱하면 이중 계상이 된다. 꼬리를 잘라야 한다.
        "min": min(series),
        "max": max(series),
        "p20": _q(0.20),
        "p80": _q(0.80),
        "median": round(ordered[len(ordered) // 2], 2),
        "percentile": round(sum(1 for v in series if v <= cur) / len(series) * 100, 1),
        "basis": basis,
    }


def _valuation_bands(client: KISClient, stock_code: str, pbr_now: float | None,
                     per_now: float | None) -> tuple[dict, dict]:
    """PBR·PER 5년 밴드를 한 번의 조회로 함께 산출 (연간 재무 1회 + 월봉 1회).

    KIS estimate-perform의 per 행은 종목에 따라 통째로 비어 오는 경우가 있어
    (2026-08-28 SK하이닉스 실측: per=None) 컨센서스 PER 밴드에 의존할 수 없다.
    자체 계산 밴드가 1차, estimate의 per는 보조다.
    """
    annual = client.get_financial_ratios(stock_code, quarterly=False)  # 최신순
    bars = client.get_ohlcv_monthly(stock_code, months=60)
    bps_by = [(r["period"], r["bps"]) for r in annual
              if r.get("period") and r.get("bps") and r["bps"] > 0]
    # 적자 연도는 PER이 무의미 — 밴드에서 제외 (음수 PER로 밴드가 오염되면 역산이 깨진다)
    eps_by = [(r["period"], r["eps"]) for r in annual
              if r.get("period") and r.get("eps") and r["eps"] > 0]

    pbr = _multiple_band(bars, bps_by, pbr_now, "직전 확정 연간 BPS (분기 실적 미반영)")
    per = _multiple_band(bars, eps_by, per_now, "직전 확정 연간 EPS (적자 연도 제외)")

    out_pbr = {"available": False}
    if pbr.get("available"):
        out_pbr = {
            "available": True, "months": pbr["months"],
            "pbr_current": pbr["current"], "pbr_5y_min": pbr["min"],
            "pbr_5y_max": pbr["max"], "pbr_5y_median": pbr["median"],
            "pbr_5y_p20": pbr["p20"], "pbr_5y_p80": pbr["p80"],
            "pbr_percentile_5y": pbr["percentile"], "basis": pbr["basis"],
            "price_high_5y": max(float(b.high) for b in bars),
            "price_low_5y": min(float(b.low) for b in bars),
            "note": "월별 종가 ÷ 당시 최근 연간 BPS 근사 — 자사주 소각/유상증자 구간 왜곡 가능",
        }
    out_per = {"available": False}
    if per.get("available"):
        out_per = {
            "available": True, "months": per["months"],
            "per_current": per["current"], "per_5y_min": per["min"],
            "per_5y_max": per["max"], "per_5y_median": per["median"],
            "per_5y_p20": per["p20"], "per_5y_p80": per["p80"],
            "per_percentile_5y": per["percentile"], "basis": per["basis"],
            "note": "월별 종가 ÷ 당시 최근 연간 EPS 근사 — 적자 연도는 밴드에서 제외됨",
        }
    return out_pbr, out_per


def _pbr_band_5y(client: KISClient, stock_code: str, pbr_now: float | None) -> dict:
    """PBR 밴드 단독 조회 (무효화_조건 valuation 체크용 — PER은 필요 없다)."""
    return _valuation_bands(client, stock_code, pbr_now, None)[0]


def _actual_band(periods: list | None, values: list | None) -> dict | None:
    """연도별 배수 배열 → 실적연도(확정) 밴드 통계. 'E' 접미사 = 추정이라 제외."""
    pairs = [(str(p), _f(v)) for p, v in zip(periods or [], values or [])
             if p and _f(v) and _f(v) > 0 and "E" not in str(p).upper()]
    if len(pairs) < 2:
        return None
    vals = sorted(v for _, v in pairs)
    return {
        "years": [p for p, _ in pairs],
        "min": vals[0],
        "median": vals[len(vals) // 2],
        "max": vals[-1],
    }


def _valuation_scenarios(price_now: float | None, per_ttm: float | None,
                         per_forward: list | None, estimate: dict | None,
                         pbr_band: dict, ttm_ni: float | None,
                         latest_q: dict | None, pbr_recent_q: float | None,
                         per_band_5y: dict | None = None) -> dict:
    """멀티플 밴드 → 함의주가 역산. **예측이 아니라 산술**이다.

    "현재 이익 수준이 유지되고 멀티플이 자기 과거 밴드로 회귀하면 주가가 얼마인가"를
    앱이 확정해 넣는다. LLM에 목표주가를 생각해내게 하지 않는 이유는 ai_probability
    폐기 근거와 같다 — LLM이 만든 수치에는 예측력이 없다. 여기서 LLM의 역할은
    이 표에서 어느 시나리오가 자기 논거와 정합적인지 고르고 그 전제를 밝히는 것뿐.

    함의주가는 주식수를 거치지 않고 배수 비율로 환산한다 (현재가 × 목표배수 ÷ 현재배수).
    상단만 내면 그 자체가 편향이라 밴드 하단도 같은 산식으로 대칭 생성한다.
    """
    out = {"available": False, "scenarios": [], "warnings": [],
           "note": "멀티플 밴드 회귀를 가정한 산술 환산 — 주가 예측이 아님. "
                   "각 행의 '전제'가 깨지면 그 행은 무효"}
    if not price_now:
        return out

    def _row(label, basis, target, current, premise):
        if not target or not current or current <= 0:
            return None
        implied = round(price_now * target / current, 0)
        return {"기준": label, "배수_기준": basis,
                "목표_배수": round(target, 2), "현재_배수": round(current, 2),
                "함의주가": implied,
                "현재가_대비_pct": round((implied / price_now - 1) * 100, 1),
                "전제": premise}

    rows = []
    # 자체 계산 5년 PER 밴드가 1차 — KIS estimate의 per 행은 통째로 비어 오는 종목이 있다
    # (2026-08-28 SK하이닉스 실측: estimate.per = None → PER 시나리오가 전부 누락됐음)
    per_band, yrs = None, ""
    if (per_band_5y or {}).get("available"):
        per_band = {k: per_band_5y[f"per_5y_{k}"] for k in ("p20", "median", "p80")}
        yrs = f"5년 월봉 PER 밴드({per_band_5y['months']}개월, 꼬리 절사)"
    else:
        fallback = _actual_band((estimate or {}).get("periods"), (estimate or {}).get("per"))
        if fallback:
            per_band = {"p20": fallback["min"], "median": fallback["median"],
                        "p80": fallback["max"]}
            yrs = f"실적연도 {fallback['years'][0]}~{fallback['years'][-1]} PER"

    # 이익 피크 구간에서는 과거 배수 자체가 다른 이익 국면의 산물 — 곱하면 이중 계상.
    # 경고문만으로는 부족해 행마다 직접 표시한다 (PBR 장부가 시점차와 같은 처리).
    ni_actual = _actual_band((estimate or {}).get("periods"),
                             (estimate or {}).get("net_income"))
    at_peak = bool(ttm_ni and ni_actual and ttm_ni > ni_actual["max"])

    if per_band and per_ttm:
        for key, label in (("p80", "PER 밴드 상단(80%)"), ("median", "PER 밴드 중앙"),
                           ("p20", "PER 밴드 하단(20%)")):
            row = _row(label, yrs, per_band[key], per_ttm,
                       "최근 4개 분기(TTM) 이익 수준이 유지될 것")
            if row and at_peak:
                row["신뢰도"] = "낮음 (이익 피크 × 과거 이익국면 배수)"
            rows.append(row)

    # 컨센서스 반영 — forward 이익이 실현되고 멀티플이 과거 중앙으로 회귀하는 경우
    fwd = next((x for x in (per_forward or []) if x.get("per")), None)

    # 과거 배수를 전혀 쓰지 않는 행 — "멀티플은 지금 그대로, 이익만 컨센대로 실현되면".
    # 이익 국면 불일치(피크 이익 × 바닥 시기 배수)도, 장부가 시점차도 타지 않아
    # 사이클 정점 구간에서 유일하게 성립하는 역산이다. 사용자 질문("현재 상황 그대로
    # 흘러갈 시 얼마까지")의 문자 그대로의 답이기도 하다.
    # 추정 연도별로 각각 — 이익 실현 시점에 따른 함의주가 차이가 판단 재료다
    for f in (per_forward or []):
        if f.get("per") and per_ttm:
            rows.append(_row(f"현재 배수 유지 × {f['period']} 컨센서스",
                             f"현재 PER {per_ttm} 고정 · {f['period']} 컨센서스 이익",
                             per_ttm, f["per"],
                             f"시장이 현재 배수를 유지하고 {f['period']} 컨센서스 이익이 실현될 것"))

    if per_band and fwd:
        row = _row(f"PER 밴드 중앙 × {fwd['period']} 컨센서스",
                   f"{fwd['period']} 컨센서스 이익", per_band["median"], fwd["per"],
                   f"{fwd['period']} 컨센서스 이익이 실현될 것")
        if row and at_peak:
            row["신뢰도"] = "낮음 (이익 피크 × 과거 이익국면 배수)"
        rows.append(row)

    if pbr_band.get("available") and pbr_band.get("pbr_current"):
        basis = pbr_band.get("basis", "직전 확정 연간 BPS")
        # 장부가 시점차가 크면(최근 분기 이익이 자본을 크게 늘린 구간) 밴드 기준 PBR과
        # 실제 PBR이 벌어져 이 행들의 함의주가가 통째로 왜곡된다. 행에 직접 표시한다 —
        # 표 밖 경고문만으로는 -85% 같은 숫자가 그대로 읽힌다.
        stale = (pbr_recent_q and abs(pbr_recent_q / pbr_band["pbr_current"] - 1) * 100
                 >= _PBR_BASIS_GAP_PCT)
        premise = f"장부가 기준이 밴드와 동일({basis})할 것"
        if stale:
            premise += f" — 최근 분기 BPS 기준 PBR {pbr_recent_q:.2f}와 괴리 큼, 참고용"
        for key, label in (("pbr_5y_p80", "PBR 밴드 상단(80%)"),
                           ("pbr_5y_median", "PBR 밴드 중앙"),
                           ("pbr_5y_p20", "PBR 밴드 하단(20%)")):
            row = _row(label, basis, pbr_band.get(key), pbr_band["pbr_current"], premise)
            if row and stale:
                row["신뢰도"] = "낮음 (장부가 시점차)"
            rows.append(row)

    out["scenarios"] = [r for r in rows if r]
    out["available"] = bool(out["scenarios"])
    out["reference_prices"] = {
        "high_5y": pbr_band.get("price_high_5y"),
        "low_5y": pbr_band.get("price_low_5y"),
    }

    # --- 시나리오를 무력화하는 결정론 경고 (LLM 판단에 맡기지 않는다) ---
    if at_peak:
        out["peak_earnings"] = True
        out["warnings"].append(
            f"이익 피크 구간 — TTM 순이익 {ttm_ni:,.0f}억이 과거 실적연도 최고치 "
            f"{ni_actual['max']:,.0f}억을 상회. 사이클 종목은 이익 정점에서 멀티플이 "
            f"밴드 하단에 형성되는 것이 정상(peak earnings = trough multiple)이므로, "
            f"PER 밴드 회귀 시나리오는 이익 지속성이 선행 조건이다. "
            f"반대로 과거 밴드 상단은 이익 바닥 시기에 만들어진 배수라 지금 이익에 "
            f"곱하면 이중 계상 — PER 행 전체를 신뢰도 낮음으로 본다")
    elif ttm_ni and ni_actual:
        out["peak_earnings"] = False

    if latest_q and latest_q.get("ni_over_op_note"):
        out["warnings"].append(
            "TTM 이익에 영업외 요인이 섞여 있음 — per_ttm 분모가 일회성으로 부풀려졌다면 "
            "PER 기반 함의주가 전체가 과대. income_single_q.ni_over_op_note 참조")

    if pbr_recent_q and pbr_band.get("available") and pbr_band.get("pbr_current"):
        gap = abs(pbr_recent_q / pbr_band["pbr_current"] - 1) * 100
        if gap >= _PBR_BASIS_GAP_PCT:
            out["warnings"].append(
                f"PBR 시나리오는 {pbr_band.get('basis')} 기준 — 최근 분기 BPS를 반영한 "
                f"현재 PBR은 {pbr_recent_q:.2f}로 밴드 기준({pbr_band['pbr_current']:.2f})과 "
                f"{gap:.0f}% 괴리. 장부가 갱신분만큼 PBR 상단 시나리오가 과대평가된다")
    return out


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
            "ni_margin_q_pct": round(cur["net_income"] / cur["revenue"] * 100, 2)
                               if cur.get("net_income") is not None and cur.get("revenue") else None,
        }
        # 순이익 > 영업이익 = 영업외 요인(평가이익/이연법인세/지분법 등) 개입.
        # per_ttm의 분모가 일회성으로 부풀려졌을 수 있어 앱이 플래그를 세운다.
        # per_trailing 왜곡만 의심하고 per_ttm 왜곡은 검증하지 않던 비대칭 교정.
        # YTD 차분이 깨져도 같은 신호가 뜨므로 차분 회귀 감시도 겸한다.
        op_q, ni_q = cur.get("operating_profit"), cur.get("net_income")
        if op_q is not None and ni_q is not None and op_q > 0 and ni_q > op_q * _NI_OP_GAP_RATIO:
            entry["ni_over_op_note"] = (
                f"순이익({ni_q:,.0f}억)이 영업이익({op_q:,.0f}억)을 "
                f"{ni_q / op_q:.2f}배 초과 — 영업외 요인 개입. "
                f"일회성이면 이 분기를 포함한 TTM 이익 기반 밸류(per_ttm)가 과소평가된다")

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

    def _avg(key: str, n: int) -> tuple[float, int] | None:
        """(일평균, 실제 데이터가 있는 거래일 수) — 분모를 호출부에 그대로 넘긴다."""
        vals = [r[key] for r in rows[:n] if r.get(key) is not None]
        return (sum(vals) / len(vals), len(vals)) if vals else None

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
    if per_forward is None and estimate and estimate.get("eps") and price.get("current_price"):
        # KIS가 per 행을 통째로 안 주는 종목이 있다 (2026-08-28 SK하이닉스 실측).
        # 컨센서스 EPS는 오므로 현재가 ÷ 추정 EPS로 forward PER을 직접 만든다.
        cur_px = price["current_price"]
        per_forward = [{"period": p, "per": round(cur_px / v, 2), "source": "현재가 ÷ 컨센 EPS"}
                       for p, v in zip(estimate["periods"], estimate["eps"])
                       if p and v and v > 0 and "E" in str(p).upper()] or None

    # ---- PBR 기준 시점 보정 (KIS pbr은 직전 확정 장부가 — 분기 실적 미반영) ----
    # 밴드(pbr_band_5y)는 과거·현재 모두 연간 BPS 기준이라 내부 일관성이 있지만,
    # 반기 이익이 자본의 상당분인 구간에서는 시점차가 이례적으로 커진다.
    # PER 4종 병기와 같은 방식으로 최근 분기 BPS 기준 PBR을 병기해 판단 재료를 준다.
    pbr_band, per_band = _valuation_bands(
        client, stock_code, holding.get("pbr") if holding else None, per_ttm)
    price_now = price.get("current_price")
    bps_q = next(((r.get("period"), r["bps"]) for r in ratios
                  if r.get("bps") and r["bps"] > 0), None)
    pbr_recent_q = (round(price_now / bps_q[1], 2)
                    if bps_q and price_now and bps_q[1] else None)

    scenarios = _valuation_scenarios(
        price_now, per_ttm, per_forward, estimate, pbr_band, ttm_ni,
        quarters[0] if quarters else None, pbr_recent_q, per_band_5y=per_band)

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
            "pbr_recent_q": pbr_recent_q,          # 최근 분기 BPS 기준 (장부가 갱신 반영)
            "bps_recent_q": bps_q[1] if bps_q else None,
            "bps_recent_q_period": bps_q[0] if bps_q else None,
            "pbr_note": "pbr은 직전 확정 장부가 기준(KIS 제공) — 실적 급변 구간에서는 "
                        "pbr_recent_q(최근 분기 BPS 기준)와 함께 읽을 것",
            "eps_trailing": holding.get("eps") if holding else None,
            "bps": holding.get("bps") if holding else None,
            "market_cap_eok": mcap,
            "per_band_annual": {
                # estimate-perform의 연도별 PER (실적 3년 + 추정 2년) — 자기 과거 밴드 근사
                "periods": estimate.get("periods"),
                "per": estimate.get("per"),
            } if estimate else None,
            # 자기 과거 5년 대비 PBR/PER 위치 (근사 — 월봉 ÷ 당시 확정 연간값)
            "pbr_band_5y": pbr_band,
            "per_band_5y": per_band,
        },
        # 멀티플 밴드 회귀 가정하의 함의주가 — 앱이 역산한 산술값 (예측 아님)
        "valuation_scenarios": scenarios,
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
    if not scenarios.get("available"):
        snapshot["data_flags"]["valuation_scenarios"] = (
            "밸류 시나리오 역산 불가 — 연도별 PER 밴드/PBR 밴드 데이터 부족")
    for w in scenarios.get("warnings", []):
        snapshot["data_flags"].setdefault("valuation_scenario_warnings", []).append(w)
    latest_q_note = (quarters[0].get("ni_over_op_note") if quarters else None)
    if latest_q_note:
        snapshot["data_flags"]["net_income_quality"] = latest_q_note
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
# 반증 전용 패스 — 강세 논거를 감춘 채 핵심_주장만 반박시킨다
# ------------------------------------------------------------------ #

_MAX_CONDITIONS = 8   # 조건이 많아지면 감시가 아니라 목록이 된다


def _cond_key(c: dict):
    """중복 판정 키 — 같은 대상을 두 번 감시하지 않도록."""
    ct, p = c.get("check_type"), c.get("params") or {}
    if ct == "flow":
        return (ct, p.get("investor"), p.get("direction"), p.get("metric"))
    if ct == "fx":
        return (ct, p.get("op"))
    if ct == "valuation":
        return (ct,)
    if ct == "earnings":
        return (ct, p.get("period"), p.get("metric"))
    if ct == "consensus":
        return (ct, p.get("year"), p.get("metric"))
    return (ct, str(c.get("조건", ""))[:40])


def _run_falsification_pass(db, analyzer, result: dict, snapshot: dict, stock_code: str,
                            stock_name: str, sector: str | None, analysis_date: date) -> None:
    """핵심_주장만 넘겨 반증 관점을 별도 생성 → 반대_해석 저장 + 무효화_조건 병합.

    논거/장기_논거/밸류_코멘트는 **의도적으로 넘기지 않는다** — 주장은 봐야 정밀하게
    반박하고, 지지 논거 체인을 보면 거기에 끌려간다. result를 제자리에서 갱신한다.
    """
    from app.services.watchlist.calibration import calibrate_conditions
    from app.services.watchlist.invalidation import (
        downgrade_rejected, normalize_conditions, screen_conditions)

    claim = (result.get("핵심_주장") or "").strip()
    if not claim:
        logger.info("핵심_주장 없음 (%s) — 반증 패스 스킵", stock_code)
        return

    prompt = _FALSIFICATION_PROMPT.format(
        stock_name=stock_name,
        stock_code=stock_code,
        sector=sector or "미분류",
        analysis_date=str(analysis_date),
        claim=claim,
        snapshot_json=json.dumps(snapshot, ensure_ascii=False, indent=1),
    )
    bear, _, bear_model = analyzer.grounded_json(prompt, WATCHLIST_MODEL)

    # 근거 인용이 없는 항목은 버린다 — 일반론을 남기면 반증 섹션이 장식이 된다
    counters = [
        c for c in (bear.get("반대_해석") or [])
        if isinstance(c, dict) and str(c.get("근거") or "").strip()
        and str(c.get("관측") or "").strip()
    ]
    result["반증_관점"] = {"model": bear_model, "항목": counters}

    conds, _ = calibrate_conditions(
        db, stock_code, snapshot, normalize_conditions(bear.get("무효화_조건")))
    if not conds:
        return
    kept, rejected = screen_conditions(db, stock_code, snapshot, conds)
    merged = list(result.get("무효화_조건") or [])
    seen = {_cond_key(c) for c in merged}
    added = 0
    for c in kept + downgrade_rejected(rejected):
        if len(merged) >= _MAX_CONDITIONS:
            break
        key = _cond_key(c)
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(c, origin="반증"))
        added += 1
    result["무효화_조건"] = merged
    logger.info("반증 패스 (%s): 반대_해석 %d건, 무효화_조건 %d건 추가",
                stock_code, len(counters), added)


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

    from app.services.watchlist.calibration import calibrate_conditions
    from app.services.watchlist.invalidation import (
        downgrade_rejected, normalize_conditions, screen_conditions, send_condition_notice)

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

    # 임계값 캘리브레이션 — 감시 대상은 LLM 판단, 임계 수치는 앱이 분포에서 재계산.
    # LLM은 입력의 숫자를 읽지만 서로 곱하고 나누지 않는다 (2026-09-07 실측: "외인 5일
    # 순매도 1.5조"가 과거 5일 창의 58%에서 발동). 임계 산정은 애초에 앱의 일이다.
    result["무효화_조건"], calib_notes = calibrate_conditions(
        db, stock_code, snapshot, result["무효화_조건"])
    if calib_notes:
        logger.info("무효화_조건 임계 재조정 %d건 (%s): %s",
                    len(calib_notes), stock_code, " | ".join(calib_notes))

    # 무효화_조건 품질 스크리닝 — 구조는 유효하지만 감시 가치가 없는 조건(이미 충족/임박,
    # 평상시 발동률, 도달 불가 임계, 기저효과 종속 YoY, 방향 역전 밸류)을 앱이 결정론으로
    # 골라 1회 재요청. 재요청 후에도 남으면 삭제하지 않고 manual 강등 — 서술은 보존하되
    # 자동 감시에서만 뺀다.
    kept, rejected = screen_conditions(db, stock_code, snapshot, result["무효화_조건"])
    if rejected:
        logger.warning("무효화_조건 품질 결함 %d건 (%s) — 재요청", len(rejected), stock_code)
        defects = "\n".join(f'- "{c.get("조건", "")}" → {why}' for c, why in rejected)
        try:
            r3, t3, m3 = analyzer.grounded_json(
                prompt + _CONDITION_QUALITY_RETRY_SUFFIX.format(defects=defects),
                WATCHLIST_MODEL,
            )
            retry_conds, _ = calibrate_conditions(
                db, stock_code, snapshot, normalize_conditions(r3.get("무효화_조건")))
            if retry_conds:
                k3, rej3 = screen_conditions(db, stock_code, snapshot, retry_conds)
                if len(k3) > len(kept):  # 자동 감시 가능 조건이 늘었을 때만 교체
                    result, raw_text, model = r3, t3, m3
                    kept, rejected = k3, rej3
        except Exception as e:  # 재요청 실패가 분석을 죽이지 않는다
            logger.warning("condition quality retry failed for %s: %s", stock_code, e)
    result["무효화_조건"] = kept + downgrade_rejected(rejected)

    # 반증 전용 패스 — 강세 논거를 감춘 채 핵심_주장만 주고 반대편을 따로 생성.
    # 같은 컨텍스트에서 논거를 쓴 뒤 무효화_조건을 이어 쓰면 방금 세운 논리를 진지하게
    # 공격하지 못한다 (2026-09-07: DB증권 리포트에서 "HBM4 판가 +70%"는 인용하고 같은
    # 문단의 "경쟁사 대비 제한적 상승률 우려"는 버린 사례). 실패해도 분석은 유효.
    try:
        _run_falsification_pass(db, analyzer, result, snapshot, stock_code,
                                stock_name, sector, analysis_date)
    except Exception as e:
        logger.warning("falsification pass failed for %s: %s", stock_code, e)

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
