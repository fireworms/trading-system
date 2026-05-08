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

STAGE4_PICKS = """
당신은 퀀트 트레이딩 전문가입니다.

=== 매크로 분석 ===
{macro_summary}
테마: {market_theme}

=== 수혜 예상 섹터 ===
{expected_beneficiary}

=== 기술적 데이터 ===
{stocks_data}

각 종목의 데이터 설명:
- rsi_14: RSI(14일), 30 이하=과매도, 70 이상=과매수
- ma5/ma20/ma60: 5/20/60일 이동평균
- frgn_net_buy_1d: 당일 외국인 순매수 수량 (양수=순매수, 음수=순매도)
- frgn_net_buy_5d: 최근 5거래일 외국인 순매수 수량 누적
- orgn_net_buy_1d: 당일 기관 순매수 수량
- orgn_net_buy_5d: 최근 5거래일 기관 순매수 수량 누적

=== 전략 파라미터 ===
- 보유기간: {hold_days}일
- 목표수익률: {target_pct}%
- 손절라인: {stop_loss_pct}%
- AI 최소 확률: {min_probability}%
- 선정 종목 수: {pick_count}개

위 정보를 종합하여 최적 종목을 선정하세요.
기술적 분석 조건: RSI 30~65, 현재가가 MA20 대비 -10%~+5% 범위 우선.
외국인/기관 순매수 연속 3일 이상이면 추가 가점.
다음 JSON 형식으로만 응답하세요. 다른 설명 없이 JSON만 반환하세요.

{{
  "picks": [
    {{
      "rank": 1,
      "stock_code": "005930",
      "stock_name": "삼성전자",
      "current_price": 75000,
      "target_price": 82500,
      "stop_loss_price": 71250,
      "ai_probability": 78.5,
      "ai_reason": "선정 근거 2-3문장",
      "historical_basis": "역사적 유사 사례 근거",
      "risk_factors": "주요 리스크"
    }}
  ],
  "excluded_reason": "제외된 종목 또는 min_probability 미달 이유 간략 설명"
}}
"""
