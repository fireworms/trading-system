"""
AI 리서치 서비스 — 자유 텍스트 질문 기반 종목 리서치 (참고용, 매매 시그널 아님).

관심종목 분석(watchlist/analyzer.py)의 입력 수집기(collect_input_snapshot)를 그대로
재사용하고, 출력만 질문 맞춤 마크다운 자유 서술로 바꾼 변형이다.

핵심 원칙 (관심종목 탭과 동일):
- 앱이 계산한 파생 지표는 재계산 금지, 그대로 인용.
- 매수/매도 추천·목표주가 제시·방향 단언 금지 — 시나리오와 확인 포인트로 서술.
- 결측은 결측으로 명시 (data_flags).
- 결과는 스냅샷과 함께 저장 → 사후 재구성 가능.
"""
import logging
import re
import uuid

from sqlalchemy import select

from app.models.research import ResearchNote
from app.models.stock_master import StockMaster

logger = logging.getLogger(__name__)

RESEARCH_MODEL = "gemini-2.5-flash"          # 검색 그라운딩 필요
_EXTRACT_MODEL = "gemini-3.1-flash-lite"     # 질문에서 회사명 추출 (그라운딩 불필요, RPD 여유)

_RESEARCH_PROMPT = """당신은 한국 주식 리서치 어시스턴트입니다. 아래 사용자 질문에 데이터 기반으로 답하세요.
이 답변은 투자 판단의 참고 자료이며 매매 지시가 아닙니다.

[사용자 질문]
{question}

[대상 종목] {stock_name}({stock_code}) — 섹터: {sector}
[기준일] {today}

[입력 데이터 — KIS 실측 (이 수치만 신뢰하고, 여기 없는 수치는 검색으로 확인된 것만 사용)]
{snapshot_json}

[확정 외부 데이터 — 입력 스냅샷에 포함됨, 재검색 불필요]
- dart_disclosures: 공식 DART API 최근 14일 공시 (확정). 이 목록에 없는 공시를 만들어내지 말 것.
- news_recent: 네이버 뉴스 API 최신순 기사 (날짜 신뢰 가능). 최우선 참조하고, 검색은 내용 보강 용도.

[검색으로 보강할 것 — 확인된 사실만, 출처 필수]
- 질문이 요구하는 주제 (업황/경쟁사/신사업/실적 전망 등)의 최신 자료. 최근 14일({recent_window_start} ~ {today}) 우선, 이전 자료는 날짜 명시.
- 최근 주가 변동의 동인: {price_move_note} — dart_disclosures·news_recent에서 먼저 찾고, 없으면 검색. 확인 안 되면 "동인 확인 불가"로 명시.
- 시황/매크로는 이 종목·섹터에 실질 영향 있는 것만.
- 증권사 목표주가/컨센서스는 검색으로 확인된 것만 인용, 확인 안 되면 "확인 불가". 수치를 만들어내지 말 것.

[규칙]
1. 질문에 직접 답하되, 매수/매도 추천·목표주가 제시·"상승할 것" 류 방향 단언은 금지. 대신 긍정 시나리오 / 부정 시나리오·리스크 / 그 갈림길을 판정할 관측 가능한 확인 포인트로 서술할 것.
2. 데이터가 말해주는 것과 말해주지 않는 것을 구분할 것. data_flags의 결측 항목은 결측으로 다루고, 판단에 중요하면 명시할 것.
3. 앱이 계산해 넣은 파생 지표는 재계산 금지, 그대로 인용 — investor_flow의 frgn_pace/orgn_pace judgment, market의 상대수익률/relative_note, fx_usdkrw의 trend_note, per_ttm, pbr_band_5y 퍼센타일, valuation_scenarios의 함의주가. 직접 나눗셈/비율 계산 금지.
4. PER 시점 구분: per_trailing은 직전 공시 실적 기준이라 실적 급변 구간에서 왜곡 — per_ttm과 per_forward_consensus를 우선할 것.
5. 환율은 외국인 수급의 공통 팩터 — 시장 공통 요인인지 종목 고유 요인인지 구분해 서술할 것.
5-1. 수급 세 주체(개인·외국인·기관계) 합은 0이 아니다 — KIS가 기타법인·기타외국인을 안 주기 때문이고, 앱이 investor_flow.other_net_*으로 역산해 넣었다. 대량 순매도를 서술할 때 **반대편이 누구인지** other_net_*으로 함께 확인하고, 크면 buyback_context(자사주 공시)를 확인해 인용할 것. 확인되면 "분산/투매"로 단정 금지. 누적 창의 실제 거래일 수는 window_days를 인용할 것(라벨 5d/30d 아님). 서술에는 필드명(other_net_*)을 그대로 쓰지 말고 "기타법인·기타외국인"으로 풀어 쓸 것. frgn_pace/orgn_pace의 judgment에 "(단, …하루가 …%)" 단서가 붙어 있으면 반드시 함께 인용할 것 — 단일 대형일이 평균을 주도한 구간을 "추세 전환"으로 단정하면 안 된다.
5-2. 스냅샷이 서술하려는 방향과 반대되는 값을 보이면 그 사실을 명시할 것 — 예: 환율이 외국인에게 우호적인데 실제 외국인 수급은 순매도면, 우호 요인만 쓰지 말고 "데이터는 아직 그 방향을 뒷받침하지 않는다"고 쓸 것. 지표와 결론을 기계적으로 연결하지 말 것.
6. 언급하는 뉴스/이벤트는 "일시적 노이즈"인지 "구조적 변화"인지 구분할 것.
7. valuation_scenarios는 앱이 배수 밴드에서 역산한 산술값이다(목표주가 아님). 인용할 때는 반드시 그 행의 "전제"와 warnings(이익 피크 구간·영업외 요인·장부가 시점차)를 함께 밝히고, 새 목표주가를 만들어내지 말 것.
8. 날짜를 지어내지 말 것 — 실적 발표일·공시일은 dart_disclosures의 rcept_dt, 기사 날짜는 news_recent에서만 인용. 정기보고서 법정 제출기한은 실제 발표일이 아니다.

[출력 형식 — 마크다운만, 코드펜스(```) 금지]
- 구성은 질문에 맞게 자유롭게 하되, 마지막에 다음 두 섹션은 반드시 포함:
  - "## 리스크 · 확인 포인트" — 이 판단이 틀렸음을 알려줄 관측 가능한 신호들
  - "## 출처" — `- [제목 — 매체, YYYY-MM-DD](URL)` 형식 목록. 검색으로 실제 확인한 자료만.
- 사용 가능 문법: ## / ### 제목, 굵게(**), 목록(-, 1.), 파이프 표(|). 각주·이미지·HTML 금지.
- 첫 줄은 질문에 대한 2~3문장 핵심 요약으로 시작할 것."""

_EXTRACT_PROMPT = """다음 문장에서 언급된 한국 상장기업(코스피/코스닥) 이름을 모두 찾아 JSON으로 반환하세요.
- 약칭·별칭은 정식 종목명으로 바꿀 것 (예: 삼전→삼성전자, 하이닉스→SK하이닉스, 현차→현대차)
- 기업이 아닌 일반 명사(반도체, 시장 등)는 제외
- 없으면 빈 배열

문장: {question}

출력 (JSON만): {{"companies": ["정식명", ...]}}"""


# ------------------------------------------------------------------ #
# 종목 식별
# ------------------------------------------------------------------ #

def identify_stocks(db, question: str) -> list[StockMaster]:
    """자유 텍스트에서 대상 종목 후보 식별 (국내 종목만 — 스냅샷 수집기가 국내 KIS 전용).

    1) 6자리 코드 직접 언급 → 즉시 매칭
    2) stock_master 종목명이 질문에 부분 문자열로 등장 → 매칭
       (긴 이름 우선 — "한화에어로스페이스" 매칭 시 "한화"는 제거)
    3) 둘 다 실패 시 Gemini로 회사명 추출 → ILIKE 검색 (별칭 대응: 삼전→삼성전자)
    """
    domestic = StockMaster.market.in_(("KOSPI", "KOSDAQ"))

    codes = re.findall(r"\b(\d{6})\b", question)
    if codes:
        rows = db.scalars(
            select(StockMaster).where(
                StockMaster.stock_code.in_(codes),
                StockMaster.is_active == True,  # noqa: E712
                domestic,
            )
        ).all()
        if rows:
            return rows

    # 전 종목명 스캔 (KOSPI+KOSDAQ ≈ 2,600개 — 인메모리 부분 문자열 매칭으로 충분)
    all_rows = db.scalars(
        select(StockMaster).where(StockMaster.is_active == True, domestic)  # noqa: E712
    ).all()

    def _word_start_match(name: str) -> bool:
        """앞 경계만 검사 — "하이닉스" 안의 "이닉스" 오매칭 방지.
        뒷 경계는 검사 안 함: 한국어 조사("삼성전자를")가 바로 붙기 때문."""
        start = 0
        while (pos := question.find(name, start)) != -1:
            if pos == 0 or not question[pos - 1].isalnum():
                return True
            start = pos + 1
        return False

    matched = [r for r in all_rows if len(r.stock_name) >= 2 and _word_start_match(r.stock_name)]
    # "한화" vs "한화에어로스페이스"처럼 포함 관계면 긴 쪽만 유지
    matched = [
        m for m in matched
        if not any(o.stock_name != m.stock_name and m.stock_name in o.stock_name
                   for o in matched)
    ]
    if matched:
        return sorted(matched, key=lambda r: (r.market != "KOSPI", r.stock_name))

    # Gemini 별칭 추출 fallback — 실패해도 식별 실패로만 처리 (분석 흐름은 안 죽음)
    from app.services.gemini.analyzer import GeminiAnalyzer
    try:
        data = GeminiAnalyzer().plain_json(
            _EXTRACT_PROMPT.format(question=question[:500]), _EXTRACT_MODEL
        )
        names = [n for n in data.get("companies", []) if isinstance(n, str) and len(n) >= 2]
    except Exception as e:
        logger.warning("research stock extraction failed: %s", e)
        return []

    found: dict[str, StockMaster] = {}
    for name in names[:5]:
        rows = db.scalars(
            select(StockMaster).where(
                StockMaster.is_active == True,  # noqa: E712
                domestic,
                StockMaster.stock_name.ilike(f"%{name}%"),
            ).limit(5)
        ).all()
        # 추출명과 완전 일치가 있으면 그것만 (부분 일치 노이즈 제거)
        exact = [r for r in rows if r.stock_name == name]
        for r in (exact or rows):
            found[r.stock_code] = r
    return sorted(found.values(), key=lambda r: (r.market != "KOSPI", r.stock_name))


# ------------------------------------------------------------------ #
# 리서치 실행
# ------------------------------------------------------------------ #

def _extract_sources(answer_md: str) -> list[dict]:
    """답변의 '출처' 섹션 마크다운 링크 → [{title, url}]. 실패 시 빈 배열 (치명 아님)."""
    m = re.search(r"^#{2,3}\s*출처.*$", answer_md, flags=re.MULTILINE)
    section = answer_md[m.end():] if m else ""
    return [
        {"title": t.strip(), "url": u}
        for t, u in re.findall(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", section)
    ]


def run_research(db, user_id: uuid.UUID, question: str, stock_code: str,
                 stock_name: str, sector: str | None) -> ResearchNote:
    """수집(관심종목 수집기 재사용) → Gemini 그라운딩 마크다운 답변 → 저장."""
    import json
    from datetime import date, timedelta
    from app.services.kis.client import get_kis_client
    from app.services.gemini.analyzer import GeminiAnalyzer
    from app.services.watchlist.analyzer import collect_input_snapshot, _price_move_note

    client = get_kis_client(db)
    # db 전달 → 수급 적재 + 60/120일 누적 포함 (리서치도 히스토리 축적에 기여)
    snapshot = collect_input_snapshot(client, stock_code, stock_name, sector, db=db)

    today = date.today()
    prompt = _RESEARCH_PROMPT.format(
        question=question,
        stock_name=stock_name,
        stock_code=stock_code,
        sector=sector or "미분류",
        today=str(today),
        recent_window_start=str(today - timedelta(days=14)),
        price_move_note=_price_move_note(snapshot.get("price", {})),
        snapshot_json=json.dumps(snapshot, ensure_ascii=False, indent=1),
    )

    answer_md, model = GeminiAnalyzer().grounded_text(prompt, RESEARCH_MODEL)
    if not answer_md or not answer_md.strip():
        raise RuntimeError("AI 응답이 비어 있습니다. 다시 시도해주세요.")
    answer_md = answer_md.strip()
    # 프롬프트에서 금지했지만 모델이 코드펜스로 감싸는 경우 방어
    answer_md = re.sub(r"^```(?:markdown|md)?\s*\n?|```\s*$", "", answer_md).strip()

    note = ResearchNote(
        user_id=user_id,
        stock_code=stock_code,
        stock_name=stock_name,
        question=question,
        answer_md=answer_md,
        gemini_model=model,
        sources=_extract_sources(answer_md),
        input_snapshot=snapshot,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    logger.info("Research note saved: %s(%s) q=%.40s [%s]",
                stock_name, stock_code, question, model)
    return note
