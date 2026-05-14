"""
4단계 AI 분석 파이프라인 프롬프트 템플릿.
모든 응답은 JSON 형식으로 반환하도록 지시한다.
"""

STAGE1_MACRO = """
당신은 거시경제 분석 전문가입니다.
오늘 날짜: {today}

현재 한국 및 글로벌 거시경제 상황을 분석하여 다음 JSON 형식으로만 응답하세요.
다른 설명 없이 JSON만 반환하세요.

{{
  "macro_summary": "현재 경제 상황 3-5문장 요약",
  "key_factors": ["주요 요인1", "주요 요인2", "주요 요인3"],
  "market_theme": "현재 시장 지배 테마 (예: 금리인하 기대, AI 수혜, 수출 회복 등)",
  "risk_factors": ["리스크1", "리스크2"],
  "sector_outlook": {{
    "positive": ["수혜 예상 섹터1", "수혜 예상 섹터2"],
    "negative": ["불리 예상 섹터1", "불리 예상 섹터2"]
  }}
}}
"""

STAGE2_HISTORICAL = """
당신은 주식시장 역사 분석 전문가입니다.

현재 매크로 상황:
{macro_summary}
핵심 요인: {key_factors}
시장 테마: {market_theme}

위 현재 상황과 유사한 과거 시기(1980년~현재)를 3개 찾아서 다음 JSON 형식으로만 응답하세요.
다른 설명 없이 JSON만 반환하세요.

{{
  "historical_matches": [
    {{
      "period": "YYYY.MM ~ YYYY.MM",
      "similarity_score": 0.85,
      "similar_points": ["유사점1", "유사점2"],
      "different_points": ["차이점1"],
      "what_happened": "당시 주식시장/경제에서 일어난 일 요약",
      "duration_months": 6,
      "market_return": "코스피 기준 수익률 (예: +23%)"
    }}
  ]
}}
"""

STAGE3_INDUSTRY = """
당신은 산업 분석 전문가입니다.

현재 매크로 상황: {macro_summary}
시장 테마: {market_theme}

유사 과거 시기들:
{historical_matches}

과거 유사 시기의 산업별 흐름을 분석하여 현재에 적용하세요.
다음 JSON 형식으로만 응답하세요. 다른 설명 없이 JSON만 반환하세요.

{{
  "past_winners": [
    {{
      "industry": "반도체",
      "reason": "수혜 이유",
      "avg_return": "+35%"
    }}
  ],
  "past_losers": [
    {{
      "industry": "건설",
      "reason": "부진 이유",
      "avg_return": "-12%"
    }}
  ],
  "sector_mapping": {{
    "description": "과거→현재 산업 매핑 설명",
    "mappings": [
      {{
        "past_industry": "과거 수혜 산업",
        "current_equivalent": "현재 대응 산업/섹터",
        "confidence": "HIGH/MEDIUM/LOW",
        "reasoning": "매핑 근거"
      }}
    ]
  }},
  "expected_beneficiary": "최종 수혜 예상 섹터 요약"
}}
"""

_FILTER_GUIDANCE = {
    "volume": (
        "【종목 선별 우선 기준: 거래량 위주 / 단기 고수익】\n"
        "- 거래량 급증, 단기 모멘텀, 테마 수혜 종목을 최우선 고려\n"
        "- RSI 40~70 범위, 최근 5일 거래량이 20일 평균의 1.5배 이상인 종목 가점\n"
        "- 외국인·기관 동반 순매수가 확인되면 강력 가점\n"
        "- 단기(hold_days 이내) 급등 가능성이 높은 종목 위주로 선정"
    ),
    "largecap": (
        "【종목 선별 우선 기준: 대형주 / 안정적 수익】\n"
        "- 시총 상위 KOSPI200·KOSDAQ150 구성 종목을 우선 고려\n"
        "- 유동성이 충분하고 변동성이 과도하지 않은 종목 선호\n"
        "- RSI 35~60, 현재가가 MA60 위에 있거나 근접한 종목 가점\n"
        "- 기관 순매수 지속, 실적 개선 섹터 소속 종목 우선"
    ),
    "mixed": (
        "【종목 선별 기준: 균형 접근 / 모멘텀 + 안정성】\n"
        "- 대형주의 안정성과 중소형 모멘텀 종목을 균형 있게 혼합\n"
        "- RSI 30~65 범위, 현재가가 MA20 대비 -10%~+5%인 종목 우선\n"
        "- 외국인·기관 순매수 연속 3일 이상이면 추가 가점\n"
        "- 매크로 수혜 섹터와 기술적 지표가 동시에 긍정적인 종목 선정"
    ),
}

STAGE4_PICKS = """
당신은 퀀트 트레이딩 전문가입니다.

=== 매크로 분석 ===
{macro_summary}
테마: {market_theme}

=== 수혜 예상 섹터 ===
{expected_beneficiary}

=== 선택 가능한 종목 목록 (반드시 이 목록의 코드만 사용) ===
{valid_codes}

=== 기술적 데이터 ===
{stocks_data}

각 종목의 데이터 설명:
- current_price: 현재가
- rsi_14: RSI(14일), 30 이하=과매도, 70 이상=과매수
- ma5/ma20/ma60: 5/20/60일 이동평균
- frgn_net_buy_1d/5d: 외국인 1일/5일 누적 순매수 수량 (양수=순매수)
- orgn_net_buy_1d/5d: 기관 1일/5일 누적 순매수 수량

=== 전략 파라미터 ===
- 보유기간: {hold_days}일
- 목표수익률: +{target_pct}%
- 손절라인: -{stop_loss_pct}%
- AI 최소 확률: {min_probability}% (이 확률 미달 종목은 제외)
- 선정 종목 수: {pick_count}개

{filter_guidance}

위 정보를 종합하여 최적 종목을 {pick_count}개 선정하세요.

【중요 규칙】
1. stock_code는 반드시 위 "선택 가능한 종목 목록"에 있는 코드를 그대로 복사하세요.
2. 목록에 없는 코드는 절대 사용하지 마세요.
3. 응답에 종목명·가격·목표가·손절가를 포함하지 마세요. 분석 근거만 반환합니다.

다음 JSON 형식으로만 응답하세요. 다른 설명 없이 JSON만 반환하세요.

{{
  "picks": [
    {{
      "rank": 1,
      "stock_code": "005930",
      "ai_probability": 78.5,
      "ai_reason": "선정 근거 2-3문장",
      "historical_basis": "역사적 유사 사례 근거",
      "risk_factors": "주요 리스크"
    }}
  ],
  "excluded_reason": "min_probability 미달 또는 제외 이유 간략 설명"
}}
"""

STAGE4A_ANALYSIS = """
당신은 퀀트 트레이딩 전문가입니다.

=== 매크로 분석 ===
{macro_summary}
테마: {market_theme}

=== 수혜 예상 섹터 ===
{expected_beneficiary}

=== 분석 대상 종목 ===
{stocks_data}

각 종목의 데이터 설명:
- current_price: 현재가
- rsi_14: RSI(14일), 30 이하=과매도, 70 이상=과매수
- ma5/ma20/ma60: 5/20/60일 이동평균
- frgn_net_buy_1d/5d: 외국인 1일/5일 순매수 수량 (양수=순매수)
- orgn_net_buy_1d/5d: 기관 1일/5일 순매수 수량

=== 전략 파라미터 ===
- 보유기간: {hold_days}일
- 목표수익률: +{target_pct}%
- 손절라인: -{stop_loss_pct}%
- AI 최소 확률: {min_probability}% (미달 종목은 언급 생략)
- 선정 종목 수: {pick_count}개

{filter_guidance}

위 종목들을 분석하여 상위 {pick_count}개를 선정하고 근거를 서술하세요.

【작성 규칙】
- 종목을 언급할 때 반드시 코드와 이름을 함께 쓰세요. 예: 330860(네패스아크)
- 각 종목의 선정 근거, 역사적 유사 사례, 리스크를 포함하세요.
- JSON 없이 자연스러운 분석 텍스트로 작성하세요.
"""

STAGE4B_EXTRACT = """
아래 분석 텍스트에서 추천 종목을 추출하여 JSON으로 정리하세요.

=== 유효 종목 코드 목록 (이 목록에 있는 코드만 사용) ===
{valid_codes}

=== 분석 텍스트 ===
{analysis_text}

【추출 규칙】
1. stock_code는 반드시 위 유효 코드 목록에서만 선택하세요.
2. 목록에 없는 코드는 절대 사용하지 마세요.
3. ai_probability: 분석 내용 기반 성공 확률 (0~100)
4. ai_reason: 해당 종목의 핵심 선정 근거 1~2문장
5. historical_basis: 텍스트에 언급된 역사적 근거 (없으면 빈 문자열)
6. risk_factors: 텍스트에 언급된 리스크 (없으면 빈 문자열)

최대 {pick_count}개를 다음 JSON 형식으로만 응답하세요. 다른 설명 없이 JSON만 반환하세요.

{{
  "picks": [
    {{
      "rank": 1,
      "stock_code": "330860",
      "ai_probability": 78.5,
      "ai_reason": "선정 근거 1~2문장",
      "historical_basis": "역사적 근거",
      "risk_factors": "주요 리스크"
    }}
  ],
  "excluded_reason": "제외 이유 간략 설명"
}}
"""

BUY_CONFIRM = """
당신은 한국 주식 단기 트레이딩 전문가입니다.
오전 9시 20분, 아침 개장 광기가 걷힌 시점입니다.
아래 종목들에 대해 지금 시장가 매수를 할지 판단하세요.

=== 시장 지수 현황 ===
{market_status}

=== 매수 후보 종목 ===
{stocks_json}

=== 판단 원칙 ===
1. 현재가가 시가(open_price) 아래에 있으면 무조건 SKIP (약세 신호)
2. 전일 전체 거래량 대비 현재 누적 거래량(volume_ratio)이 0.3 이상이면 강한 수급으로 간주
3. 체결강도(transaction_strength)가 100 이하로 하락 추세면 SKIP
4. 당일 고가 대비 현재가 밀림이 3% 초과면 차익 실현 매물로 간주 → SKIP 가중
5. 지수가 -1% 이상 하락 중이면 매수 판단에 보수적으로 접근
6. 남은 업사이드(remaining_upside_pct)가 손절라인(stop_loss_pct)의 1.5배 미만이면 SKIP

각 종목에 대해 다음 JSON 형식으로만 응답하세요. 다른 설명 없이 JSON만 반환하세요.

{{
  "decisions": [
    {{
      "stock_code": "종목코드",
      "action": "buy 또는 skip",
      "reason": "판단 근거 1-2문장"
    }}
  ]
}}
"""
