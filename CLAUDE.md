# Trading System - AI 기반 자동매매 시스템

## 프로젝트 개요
한국투자증권(KIS) API + Gemini AI를 활용한 자동매매 시스템
- AI 4단계 파이프라인으로 매크로 분석 + 역사적 패턴 매칭 + 장중 확인 후 종목 매수
- 여러 투자 전략 동시 운영 및 성과 비교 (백테스트 포함)
- 멀티 유저, 유저별 전략 구독 및 자동매매
- 뉴스 감시 → 시장 충격 감지 시 자동매매 일시 중단

## 기술 스택
- **Backend**: Python 3.11+ / FastAPI
- **DB**: PostgreSQL + SQLAlchemy + Alembic
- **AI**: Gemini API (`google-genai` 신규 SDK)
- **증권**: 한국투자증권 KIS API (httpx 네이티브 직접 호출, pykis 제거)
- **스케줄러**: APScheduler
- **프론트엔드**: Next.js + TypeScript + Tailwind CSS (frontend/)
- **실시간**: KIS WebSocket (H0STCNT0 가격, H0STCNI0 체결통보)
- **알림**: Telegram Bot API

## 프로젝트 구조
```
trading_system/
├── CLAUDE.md
├── .env
├── requirements.txt
├── alembic.ini
├── app/
│   ├── main.py                  # FastAPI 앱, lifespan (스케줄러+WebSocket 초기화)
│   ├── core/
│   │   ├── config.py            # Settings (pydantic)
│   │   ├── database.py          # SessionLocal, Base
│   │   ├── security.py          # JWT, bcrypt, Fernet 암호화
│   │   └── config_store.py      # AppConfig key-value (DB 기반 동적 설정)
│   ├── models/
│   │   ├── user.py              # User, BrokerAccount (hts_id 포함)
│   │   ├── strategy.py          # Strategy, UserStrategy
│   │   ├── recommendation.py    # RecommendationRun, MacroAnalysis, Recommendation
│   │   ├── position.py          # Position (peak_price 포함)
│   │   ├── stock_master.py      # StockMaster (KIS MST 기반 종목 풀)
│   │   ├── app_config.py        # AppConfig (key-value 설정 테이블)
│   │   └── news_event.py        # NewsEvent (뉴스 감시 + 시장 영향 누적)
│   ├── api/
│   │   ├── users.py             # 회원가입, 로그인, 브로커계좌 CRUD (hts_id 수정 포함)
│   │   ├── strategies.py        # 전략 CRUD, 구독 관리
│   │   ├── recommendations.py   # 추천 조회, 통계
│   │   ├── positions.py         # 포지션 CRUD, 수동매수/청산 (실 체결가 반영), GET /positions/stats (수익통계)
│   │   ├── market.py            # 시세 조회 API
│   │   ├── admin.py             # 수동 트리거, 스케줄러 상태
│   │   ├── prompt_versions.py   # 프롬프트 버전 관리
│   │   ├── stock_master.py      # 종목 풀 검색/통계
│   │   ├── backtest.py          # 백테스트 실행/결과
│   │   └── ws.py                # WebSocket /ws/prices (실시간 가격)
│   ├── services/
│   │   ├── kis/
│   │   │   ├── client.py        # KISClient (httpx 네이티브)
│   │   │   └── realtime.py      # KIS WebSocket 클라이언트 (H0STCNT0 + H0STCNI0)
│   │   ├── gemini/
│   │   │   ├── analyzer.py      # GeminiAnalyzer (4단계 + confirm_buys)
│   │   │   └── prompts.py       # 프롬프트 템플릿 (STAGE1~4, BUY_CONFIRM)
│   │   ├── news/
│   │   │   └── watcher.py       # 뉴스 감시, news_events 저장, 사후 검증
│   │   ├── stock_master/
│   │   │   ├── updater.py       # KIS MST 파일 파싱 → stock_master 갱신
│   │   │   └── index_constituents.py  # KOSPI200/KOSDAQ150 구성종목
│   │   ├── telegram/
│   │   │   └── notifier.py      # TelegramNotifier (멀티유저, chat_id별 전송)
│   │   └── trading/
│   │       ├── scheduler.py     # APScheduler 잡 정의
│   │       ├── runner.py        # StrategyRunner (AI 파이프라인, 분석만)
│   │       ├── executor.py      # TradeExecutor (매수/매도/모니터링)
│   │       └── verifier.py      # 추천 결과 사후 검증
│   └── schemas/
│       ├── user.py
│       ├── strategy.py
│       └── recommendation.py    # PositionOut (target_price, trailing_stop_price 포함)
├── frontend/                    # Next.js 프론트엔드
├── scripts/                     # seed 스크립트
├── migrations/                  # Alembic 마이그레이션
└── tests/                       # 연동 테스트 스크립트
```

## DB 스키마

### users
- user_id (PK, UUID), username, email, password_hash
- role: SUPER_ADMIN / ADMIN / TRADER / VIEWER
- telegram_chat_id: 텔레그램 알림용
- is_active, created_at

### broker_accounts
- account_id (PK, UUID), user_id (FK)
- broker: KIS
- account_no, api_key_enc(Fernet), api_secret_enc(Fernet)
- **hts_id**: KIS HTS 아이디 (H0STCNI0 체결통보 WebSocket용, nullable)
- account_type: REAL / PAPER
- is_active

### strategies
- strategy_id (PK, UUID), created_by (FK)
- name, description
- hold_days, target_pct, stop_loss_pct, min_probability, pick_count, run_interval_days
- **candidate_filter**: volume / largecap / mixed (기본 mixed)
- **candidate_market**: KOSPI / KOSDAQ / NAS / ALL (기본 ALL)
- is_active, created_at

### user_strategies
- user_id (FK), strategy_id (FK), account_id (FK)
- invest_amount_per_pick, is_auto_trade, is_active, subscribed_at

### recommendation_runs
- run_id (PK, UUID), strategy_id (FK), run_date
- ai_model_used, raw_response (JSONB — macro/historical/industry/picks/random_baseline)

### recommendations
- rec_id (PK, UUID), run_id (FK)
- stock_code, stock_name, target_price, stop_loss_price
- ai_probability, ai_reason, historical_basis, risk_factors, rank
- **current_price_at_rec**: 추천 당시 현재가 (pnl 기준가)

### positions
- position_id (PK, UUID)
- user_id, strategy_id (nullable), rec_id (nullable), account_id
- stock_code, entry_price, entry_date, quantity
- **peak_price**: 트레일링 스탑 기준 고점 (매수 직후 실 체결가로 초기화)
- **target_hit_at**: 목표가 최초 도달 시각 (트레일링 모드 전환 시점, nullable)
- **target_hit_peak**: 트레일링 전환 시점의 peak_price (신고점 갱신 여부 판단 기준, nullable)
- status: HOLDING / TARGET_HIT / STOP_LOSS / EXPIRED / MANUAL_EXIT
- exit_price, exit_date, pnl_pct

### verifications
- verify_id, rec_id (FK), verified_at, price_at_verify
- max_high, max_low, result: SUCCESS/FAIL, pnl_pct

### stock_master
- stock_code, stock_name, market (KOSPI/KOSDAQ/NAS), country, sector
- is_active, updated_at
- KOSPI 894개, KOSDAQ 1760개, NAS 5119개 (주 1회 갱신)

### news_events ← NEW
- event_id (PK, UUID), detected_at
- severity: NORMAL / WARNING / CRITICAL
- event_description, keywords (JSONB), ai_confidence
- kospi_at_detection, kosdaq_at_detection  ← 감지 시점 지수 레벨
- kospi_change_1d/3d, kosdaq_change_1d/3d  ← 사후 시장 영향 (16:00 잡이 채움)
- verified_1d_at, verified_3d_at

### app_config (key-value)
- key: news_auto_trade_paused, news_pause_reason, news_last_check_at 등

### prompt_versions
- stage(1~4), version_no, prompt_text, performance_score

## 자동매매 흐름

### 분석 잡 (08:30 Mon/Wed/Fri)
1. `_should_run()` — run_interval_days 경과한 전략만 선택
2. stock_master에서 candidate_filter 기준 50~200개 종목 샘플링
   - **largecap**: KOSPI200 시총 내림차순 상위 90% + stride 다양성 10% (시총 상위 종목 항상 포함 보장)
   - **mixed**: largecap 우선 + stride (순서 미보장 — 단타 다양성 유지)
   - **volume**: KIS 시총순위 API 실시간 호출
3. KIS API로 실시간 데이터 수집 (현재가/RSI/이평선/외국인+기관 순매수)
4. Gemini 4단계 파이프라인 실행 → recommendations + RecommendationRun 저장
5. 텔레그램 구독자 알림

### 매수 잡 (09:20 평일)
1. auto_trade=ON 구독자 중 "오늘 분석 완료됐는데 포지션 없는 것" 탐색
2. KOSPI/KOSDAQ 지수 현황 조회
3. 추천 종목별 장중 스냅샷 수집 (시가/고가/체결강도/거래량비율)
4. Gemini Flash Lite → buy/skip 판정 (BUY_CONFIRM 프롬프트)
5. buy 판정 종목만 시장가 매수
6. TTTC8001R로 실 체결가 즉시 조회 → Position(entry_price=fill_price, peak_price=fill_price)

### 포지션 모니터링 (09:05, 12:00, 14:50)
- 09:05: update_entry_prices_from_balance() 백업 실행 (폴링 fallback)
- 목표가 도달 → 즉시 익절 대신 트레일링 모드 전환 (target_hit_at, target_hit_peak 기록)
- +1거래일 14:30까지 신고점(peak_price) 갱신 없으면 TARGET_HIT으로 강제 청산
- 트레일링 스탑: `peak_price × (1 - stop_loss_pct/100)` 이탈 → 손절
- Time-based Stop: 5일 후에도 손실 중 → 조기 청산
- 만료(hold_days 경과) → 시장가 청산

### 매수 스킵 조건
- AI 09:20 확인에서 skip 판정
- remaining_upside ≤ stop_loss_pct (리스크/리워드 불균형)
- RSI > 70 (과매수)
- 동일 섹터 2종목 초과 (MAX_PER_SECTOR=2)
- 잔고 부족

## 스케줄러 잡 목록
| 잡 ID | 시각 | 역할 |
|-------|------|------|
| run_strategies | 08:30 Mon/Wed/Fri | AI 분석 (매수 없음) |
| execute_pending_buys | 09:20 평일 | AI 장중 확인 + 매수 |
| monitor_905/1200/1450 | 09:05, 12:00, 14:50 평일 | 포지션 손절/익절 모니터링 |
| verify_recommendations | 00:10 매일 | 추천 결과 사후 검증 |
| verify_news_events | 16:00 평일 | 뉴스 이벤트 시장 영향 검증 |
| news_watch_tick | 09:00~15:30 10분마다 평일 | 뉴스 감시 tick (40분마다 실행) |
| update_stock_master | 03:00 일요일 | stock_master + 지수캐시 갱신 |

## Gemini 모델 체인
| 용도 | 모델 | Fallback |
|------|------|----------|
| Stage1 (매크로+그라운딩) | gemini-2.5-flash | - |
| Stage2 (역사 분석) | gemini-3-flash-preview | gemini-3.1-flash-lite |
| Stage3 (산업 분석) | gemini-3.1-flash-lite | gemini-2.5-flash-lite |
| Stage4-A (자유형식 분석) | gemini-3-flash-preview | gemini-3.1-flash-lite → gemini-2.5-flash-lite |
| Stage4-B (코드 추출) | gemini-3.1-flash-lite | gemini-2.5-flash-lite |
| BUY_CONFIRM (09:20 확인) | gemini-3.1-flash-lite | gemini-2.5-flash-lite |
| 뉴스 감시 | gemini-2.5-flash | - |
| JSON 정제 | gemma-4-31b-it | - |

## Stage4 환각 방어 구조
Stage4는 종목코드-이름 환각을 막기 위해 3겹 방어:
1. **사전필터**: KIS 75개 → RSI·수급·거래량 기준 20개 압축 (runner._prefilter_stocks)
2. **그룹 분할**: 10개씩 2그룹, 각 그룹 독립 실행 (runner._run_stage4_grouped)
3. **2단계 생성**:
   - Stage4-A: Flash-preview가 자유형식 텍스트로 분석 ("330860(네패스아크) 기관 순매수...")
   - Stage4-B: Flash-lite가 텍스트에서 코드 추출 (패턴 매칭, 창의적 판단 불필요)
4. **서버 검증**: price_map 외 코드 저장 거부 + stock_master 이름 교정 + KIS 가격 덮어쓰기
- stock_data에 stock_name 사전 주입 (AI 훈련 기억 대신 DB 이름 사용)
- raw_response.price_snapshot: KIS 수집 시점 가격 감사 로그 저장

## Circuit Breaker
- 직전 4건 청산이 전부 손실이면 해당 유저 매수 자동 차단 (4건 미만은 체크 안 함)
- app_config: `cb_paused_{user_id}`, `cb_reason_{user_id}`
- 트리거 시 어드민 텔레그램 알림, 수동 해제만 가능
- GET /admin/circuit-breaker/status, POST /admin/circuit-breaker/resume/{user_id}

## KIS API 주요 엔드포인트
- `FHKST01010100` inquire-price: 현재가 + 시가/고가/체결강도(cttr)/거래량
- `FHKST03010100` inquire-daily-itemchartprice: OHLCV (일봉)
- `FHPUP02100000` inquire-index-price: 지수 현재가/등락률 (0001=KOSPI, 1001=KOSDAQ)
- `TTTC8434R` inquire-balance: 잔고 조회 (avg_price=pchs_avg_pric)
- `TTTC0802U` order-cash (매수): 시장가 주문
- `TTTC0801U` order-cash (매도): 시장가 주문
- `TTTC8001R` inquire-daily-ccld: 당일 주문 체결 조회 (실 체결가 확인용)
  - `get_today_fill_price(stock_code, side="02")` — side "02"=매수, "01"=매도
  - 매수 직후 entry_price, 매도 직후 exit_price에 실 체결가 반영
- `CTPF1002R` search-stock-info: 종목 기본정보 (섹터)
- `FHPST01740000` 시총순위: KOSPI200/KOSDAQ150 구성종목 근사치

## 뉴스 감시 시스템
- **주기**: 장중(09:00~15:30) 40분마다 Gemini+검색그라운딩으로 체크
- **히스토리 컨텍스트**: 최근 15건 이벤트 + 실제 시장 영향이 프롬프트에 포함 → 판단 자동 보정
- **저장**: NORMAL 포함 모든 이벤트 news_events에 저장 (감지 시점 KOSPI/KOSDAQ 레벨 포함)
- **사후 검증**: 16:00 잡이 1일/3일 경과분의 실제 KOSPI/KOSDAQ 변화율 자동 계산
- **WARNING 감지 시**: news_auto_trade_paused=true + 텔레그램 어드민 알림 → 수동 재개

## 실시간 WebSocket
- **가격 스트림**: H0STCNT0 → /ws/prices 엔드포인트 → 프론트 포지션 페이지 LIVE 표시
  - H0STCNT0 필드: [0]코드, [2]현재가, [3]전일대비부호, [4]전일대비, [5]등락률, [11]매수호가1(bid), [13]누적거래량
  - 프론트 미실현 손익: bid_price 기준 계산 (시장가 매도 실체결 기준), 퍼센트+원화 금액 표시
- **체결통보**: H0STCNI0 — 멀티유저 구조
  - `_exec_canos: set[str]` — 등록된 모든 계좌 hts_id 동시 구독
  - 체결 데이터 f[0](hts_id) → account_id → 해당 유저 포지션만 entry_price/peak_price 업데이트
  - hts_id 저장/변경 시 서버 재시작 없이 즉시 구독 반영 (users API)
  - hts_id 미등록 시: REST 방식(TTTC8001R) fallback으로 체결가 조회

## 환경변수 (.env)
```
GEMINI_API_KEY=
DATABASE_URL=postgresql+asyncpg://...
SECRET_KEY=
TELEGRAM_BOT_TOKEN=      # 선택
```
- KIS API 키/계좌번호는 .env 사용 안 함 → DB broker_accounts에 Fernet 암호화 저장
- HTS 아이디는 DB broker_accounts.hts_id (프론트 포지션 페이지 > 계좌 설정에서 입력)

## 주의사항
- API 키는 절대 코드에 하드코딩 금지
- broker_accounts의 api_key, api_secret은 Fernet 암호화 (security.py)
- 모든 금액/수량은 Decimal 타입 사용 (float 금지)
- 자동매매 실행 전 is_auto_trade + news_auto_trade_paused + cb_paused_{user_id} 플래그 확인
- raw_response['macro']['market_theme'] 에서 하락장 판단 (MacroAnalysis 모델에는 없음)
- 매수 스킵 fallback: AI 확인 실패 시 전종목 skip (안전 방향)
- HTTP 클라이언트: 전체 코드 httpx 통일 (requests 사용 금지)
- 매도 후 exit_price: 반드시 `get_today_fill_price(side="01")`로 실 체결가 조회 (현재가 사용 금지)
- 수동 매수 + 전략 선택 시 실제 자동 청산 편입 (monitor_positions가 HOLDING 전체 순회)
- _check_position(): rec 없으면 strategy.target_pct × entry_price로 목표가 계산 (수동매수 포함)
- 전략 없이 수동매수 시 자동 청산 미작동 — 수동매수는 반드시 전략 선택 필요
- _enrich(): rec_id 없어도 strategy.target_pct × entry_price로 익절가 계산
- systemd 서비스: trading-backend (uvicorn), trading-frontend (npm run dev) — WSL2 부팅 시 자동 시작
- 손절 2단계: Phase1(목표가 전)=고정 stop_loss_pct, Phase2(trailing mode)=ATR 2.5× 트레일링
- 전략 검증: pick_count≤4, 일평균≤0.7%/일, R/R≥1.5, min_probability≥55 (API+프론트 동일 기준)

## 협업 원칙

### 원칙 1: 커밋/메모리/설계도 동시 업데이트
사용자가 아래 중 하나를 요청하면 **명시적으로 범위를 한정하지 않은 경우** 세 가지를 모두 실행한다:
- 커밋해줘 → git commit + memory 업데이트 + CLAUDE.md 업데이트
- 메모리 업데이트해줘 → memory 업데이트 + CLAUDE.md 업데이트 + git commit
- 설계도 업데이트해줘 → CLAUDE.md 업데이트 + memory 업데이트 + git commit

단, "메모리만 업데이트해줘", "CLAUDE.md만 바꿔줘"처럼 범위를 명시하면 그것만 한다.

### 원칙 2: 퀀트 관점 의견 제안
사용자가 **전략 변경 또는 기능 추가**를 제안할 때(버그 수정/UI 변경 제외), 구현 전에 반드시 아래를 짚는다:
1. **실전 퀀트 관점에서 좋은 점** — 전략적 타당성, 어떤 엣지를 노리는지
2. **잠재적 문제** — 과최적화 가능성, 이 시스템의 목적과 맞지 않는 부분, 숨겨진 가정
3. **이 환경에서 실현 가능성** — KIS API 제약, Gemini RPD, 데이터 충분성

단, 백테스트 수치는 제시할 수 없고 논리적 타당성 기준으로 판단한다.
의견 제시 후 사용자가 진행을 결정하면 구현한다.

### 원칙 3: 멀티유저 기본 설계
모든 기능 설계는 **멀티유저 환경을 기본으로** 한다:
- DB 조회/업데이트는 반드시 `user_id` 또는 `account_id` 기준으로 스코핑
- 스케줄러 잡은 전체 활성 구독자를 순회하는 구조 유지
- 전역 상태(싱글턴, 캐시, 설정값)가 특정 유저에 종속되지 않도록 설계
- API 엔드포인트는 `current_user` 기준으로 데이터 격리
- "첫 번째 계좌", "대표 계좌" 같은 단수 가정은 시장데이터 조회 등 명백히 공용인 경우에만 허용
