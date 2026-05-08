# Trading System - AI 기반 자동매매 시스템

## 프로젝트 개요
한국투자증권(KIS) API + Gemini AI를 활용한 자동매매 시스템
- AI가 매크로 분석 + 역사적 패턴 매칭으로 종목 선정
- 여러 투자 전략을 동시 운영 및 성과 비교
- 멀티 유저 지원, 유저별 전략 구독 및 자동매매

## 기술 스택
- Backend: Python 3.11+ / FastAPI
- DB: PostgreSQL + SQLAlchemy + Alembic
- AI: Gemini API (google-generativeai)
- 증권: 한국투자증권 KIS API (pykis)
- 스케줄러: APScheduler
- 환경변수: python-dotenv

## 프로젝트 구조
trading_system/
├── CLAUDE.md
├── .env
├── requirements.txt
├── alembic.ini
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── database.py
│   │   └── security.py
│   ├── models/
│   │   ├── user.py
│   │   ├── strategy.py
│   │   ├── recommendation.py
│   │   └── position.py
│   ├── api/
│   │   ├── users.py
│   │   ├── strategies.py
│   │   ├── recommendations.py
│   │   └── positions.py
│   ├── services/
│   │   ├── kis/
│   │   │   └── client.py
│   │   ├── gemini/
│   │   │   ├── analyzer.py
│   │   │   └── prompts.py
│   │   └── trading/
│   │       ├── scheduler.py
│   │       └── executor.py
│   └── schemas/
│       ├── user.py
│       ├── strategy.py
│       └── recommendation.py
├── migrations/
└── tests/
## DB 스키마

### users
- user_id (PK, UUID)
- username, email, password_hash
- role: SUPER_ADMIN / ADMIN / TRADER / VIEWER
- is_active, created_at

### permissions
- permission_id (PK)
- user_id (FK)
- menu_key (예: "strategy.create", "trading.execute")
- is_allowed (bool)
- 현재는 모든 유저 모든 권한 허용, 나중에 세분화

### broker_accounts
- account_id (PK, UUID)
- user_id (FK)
- broker: KIS
- account_no, api_key(암호화), api_secret(암호화)
- account_type: REAL / PAPER
- is_active

### strategies
- strategy_id (PK, UUID)
- created_by (FK → users)
- name, description
- hold_days: 보유기간(일)
- target_pct: 목표수익률(%)
- stop_loss_pct: 손절라인(%)
- min_probability: AI 최소 확률(%)
- pick_count: 선정 종목 수
- run_interval_days: 실행 주기(일)
- is_active, created_at

### user_strategies (유저-전략 구독)
- id (PK)
- user_id (FK), strategy_id (FK), account_id (FK)
- invest_amount_per_pick: 종목당 투자금액
- is_auto_trade: 자동매매 ON/OFF
- is_active, subscribed_at

### recommendation_runs (추천 회차)
- run_id (PK, UUID)
- strategy_id (FK)
- run_date
- ai_model_used, prompt_version
- raw_response (JSON)

### macro_analysis (매크로 분석)
- analysis_id (PK, UUID)
- run_id (FK)
- current_situation: 현재 상황 요약
- historical_matches: 유사 과거 시기 (JSON)
- industry_mapping: 과거→현재 산업 매핑 (JSON)
- expected_beneficiary: 수혜 예상 산업/섹터
- created_at

### recommendations (추천 종목)
- rec_id (PK, UUID)
- run_id (FK)
- stock_code, stock_name
- target_price, stop_loss_price
- ai_probability: AI 추정 확률
- ai_reason, historical_basis, risk_factors
- rank

### positions (유저별 실제 매매)
- position_id (PK, UUID)
- user_id (FK), strategy_id (FK), rec_id (FK), account_id (FK)
- stock_code, entry_price, entry_date, quantity
- status: HOLDING / TARGET_HIT / STOP_LOSS / EXPIRED / MANUAL_EXIT
- exit_price, exit_date, pnl_pct

### verifications (검증 결과)
- verify_id (PK, UUID)
- rec_id (FK)
- verified_at, price_at_verify
- max_high, max_low
- result: SUCCESS / FAIL
- pnl_pct

### prompt_versions (프롬프트 버전 관리)
- version_id (PK)
- stage: 1~4
- version_no: "v1.0"
- prompt_text
- created_at
- performance_score: 나중에 승률 기반으로 채워짐

## AI 분석 4단계 파이프라인

### 1단계: 현재 매크로 상황 파악
- 모델: Gemini 2.5 Flash (검색 그라운딩)
- 입력: 현재 날짜
- 출력: macro_summary, key_factors, market_theme, risk_factors (JSON)

### 2단계: 역사적 유사 시기 탐색
- 모델: Gemini 3 Flash
- 입력: 1단계 결과
- 출력: historical_matches (유사도점수, 유사점, 차이점, 당시결과) (JSON)

### 3단계: 과거 산업 흐름 분석
- 모델: Gemini 3 Flash
- 입력: 1단계 + 2단계 결과
- 출력: past_winners, past_losers, sector_mapping (JSON)

### 4단계: 종목 선정 (기술적 분석 교차검증)
- 모델: Gemini 3 Flash
- 입력: 1~3단계 결과 + KIS API 실시간 데이터
- 출력: picks (종목코드, 목표가, 손절가, 확률, 근거) (JSON)
- 전략 파라미터(hold_days, target_pct, stop_loss_pct 등) 적용

## Gemini 모델 운용 전략 (무료 티어)
- Gemini 2.5 Flash: 뉴스/검색 그라운딩 (RPD 20)
- Gemini 3 Flash: 메인 분석 (RPD 20)
- Gemini 3.1 Flash Lite: 1차 필터링 (RPD 500)
- Gemma 4: 단순 데이터 정제 (RPD 1500)
- 모델별 사용량 추적해서 RPD 초과 시 자동 fallback

## KIS API 사용 범위
- 실전 계좌 사용
- 국내 주식만 (해외는 추후 확장)
- 주요 데이터: 현재가, 거래량, 외국인/기관 순매수, RSI, 이평선
- 주문: 시장가 매수/매도

## 스케줄러 동작
- 3일마다: 각 전략별 AI 분석 실행 → 종목 추천 → DB 저장
- 매일 장중: 보유 포지션 모니터링 (목표가/손절가 체크)
- 10일 후: 추천 종목 검증 자동 실행

## 환경변수 (.env)
KIS_APP_KEY=
KIS_APP_SECRET=
KIS_ACCOUNT_NO=
GEMINI_API_KEY=
DATABASE_URL=
SECRET_KEY=

## 개발 우선순위
1. DB 스키마 + SQLAlchemy 모델
2. 유저/권한 CRUD API
3. 전략 관리 API
4. KIS API 연동 (시세 조회)
5. Gemini 4단계 분석 파이프라인
6. 스케줄러
7. 포지션 관리 + 자동매매
8. 검증 시스템

## 주의사항
- API 키는 절대 코드에 하드코딩 금지, 반드시 .env 사용
- broker_accounts의 api_key, api_secret은 DB 저장 시 암호화
- 모든 금액/수량은 Decimal 타입 사용 (float 금지)
- 자동매매 실행 전 반드시 is_auto_trade 플래그 확인
