# Trading System - AI 기반 자동매매 시스템

> 관심종목 분석 탭(중장기 수동매매) 스펙은 docs/watchlist_spec.md 참조. 해당 탭 관련 작업 시 반드시 먼저 읽을 것.

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
│   │   ├── config_store.py      # AppConfig key-value (DB 기반 동적 설정)
│   │   └── loop.py              # async 이벤트루프 싱글턴 (APScheduler 스레드↔async 브리지)
│   ├── models/
│   │   ├── user.py              # User, BrokerAccount (hts_id 포함)
│   │   ├── strategy.py          # Strategy, UserStrategy
│   │   ├── recommendation.py    # RecommendationRun, MacroAnalysis, Recommendation
│   │   ├── position.py          # Position (peak_price 포함)
│   │   ├── stock_master.py      # StockMaster (KIS MST 기반 종목 풀)
│   │   ├── app_config.py        # AppConfig (key-value 설정 테이블)
│   │   ├── news_event.py        # NewsEvent (뉴스 감시 + 시장 영향 누적)
│   │   ├── watchlist.py         # WatchlistStock, StockAnalysis (중장기 수동매매 일지)
│   │   ├── investor_flow.py     # InvestorFlowDaily (관심종목 일별 수급 적재, 공용)
│   │   ├── daily_price.py       # DailyPrice (KRX 일별 전종목 시세, 공용 — 백테스트 튜닝용)
│   │   └── research.py          # ResearchNote (AI 리서치 탭 — 자유 질문 리서치 기록)
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
│   │   ├── watchlist.py         # 관심종목 CRUD + 분석 실행/이력 (중장기 탭)
│   │   ├── research.py          # AI 리서치 — 자유 질문 종목 리서치 (식별→분석→이력)
│   │   └── ws.py                # WebSocket /ws/prices (실시간 가격)
│   ├── services/
│   │   ├── kis/
│   │   │   ├── client.py        # KISClient (httpx 네이티브)
│   │   │   └── realtime.py      # KIS WebSocket 클라이언트 (H0STCNT0 + H0STCNI0)
│   │   ├── gemini/
│   │   │   ├── analyzer.py      # GeminiAnalyzer (4단계)
│   │   │   └── prompts.py       # 프롬프트 템플릿 (STAGE1~3, STAGE4A/B, BUY_CONFIRM 미사용)
│   │   ├── news/
│   │   │   └── watcher.py       # 뉴스 감시, news_events 저장, 사후 검증
│   │   ├── stock_master/
│   │   │   ├── updater.py       # KIS MST 파일 파싱 → stock_master 갱신
│   │   │   └── index_constituents.py  # KOSPI200/KOSDAQ150 구성종목
│   │   ├── watchlist/
│   │   │   ├── analyzer.py      # 관심종목 분석 (수집→스냅샷→Gemini 구조화→저장)
│   │   │   ├── flow_store.py    # 일별 수급 적재/60·120일 누적 (KIS 30거래일 한계 보완)
│   │   │   ├── invalidation.py  # 무효화_조건 자동 판정 (결정론 체커 + 16:20 잡 + 전이 알림)
│   │   │   ├── calibration.py   # 무효화_조건 임계 캘리브레이션 (분포 기반 — 임계는 LLM이 아닌 앱이 정함)
│   │   │   └── events.py        # 이벤트 자동 감지 (DART 공시/수급·주가 급변/실적 캘린더 + 자동 분석, 16:30 잡)
│   │   ├── research/
│   │   │   └── analyst.py       # AI 리서치 (질문→종목 식별→관심종목 스냅샷 재사용→마크다운 답변)
│   │   ├── krx/
│   │   │   └── client.py        # KRX 오픈API 어댑터 (일별 전종목 벌크 — 호스트 data-dbg.krx.co.kr)
│   │   ├── dart/
│   │   │   └── client.py        # DART OpenDART 공시 어댑터 (corp_code 매핑 캐시 + 최근 14일 공시)
│   │   ├── naver/
│   │   │   └── news.py          # 네이버 뉴스 검색 어댑터 (최신순 + 제목 중복 제거)
│   │   ├── telegram/
│   │   │   └── notifier.py      # TelegramNotifier (멀티유저, chat_id별 전송)
│   │   │                        # notify_admins_warning: 정책 경고 (⚠️ [WARNING])
│   │   │                        # notify_admins_error: 코드 오류·긴급 조치 (🚨 [ERROR])
│   │   └── trading/
│   │       ├── virtual_broker.py    # 가상계좌 체결 시뮬레이터 + get_trading_client 팩토리
│   │       ├── realtime_monitor.py  # 실시간 포지션 모니터 (서버사이드 상시 구독, 즉시 손절/익절)
│   │       ├── scheduler.py     # APScheduler 잡 정의
│   │       ├── runner.py        # StrategyRunner (AI 파이프라인, 분석만)
│   │       ├── executor.py      # TradeExecutor (매수/매도/모니터링)
│   │       ├── verifier.py      # 추천 결과 사후 검증 (simulate_exit_pnl = 청산 모델 단일 진실 공급원)
│   │       ├── market_regime.py # 시장 국면 판정 (KOSPI 20일선 위/아래, 기록 전용)
│   │       └── rule_selector.py # 규칙 기반 종목 선정 (Gemini 미사용, AI 대조군)
│   └── schemas/
│       ├── user.py
│       ├── strategy.py
│       ├── recommendation.py    # PositionOut (target_price, trailing_stop_price 포함)
│       ├── watchlist.py
│       └── research.py
├── docs/
│   ├── watchlist_spec.md        # 관심종목 분석 탭 스펙 (관련 작업 시 필독)
│   └── setup.md                 # 설치/마이그레이션 가이드 (README에서 분리)
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
- account_type: REAL / PAPER / **VIRTUAL** (가상계좌 — KIS 키 없음, 자체 체결 시뮬레이션, 2026-07-16)
- **virtual_cash / virtual_cash_initial**: VIRTUAL 전용 가상 예수금 (매수 차감/매도 복원)
- is_active

### 가상계좌 (VIRTUAL) — 자체 모의투자 (2026-07-16)
- KIS 모의투자(VTS) 미사용 — 실시세 기반 자체 시뮬레이션. `services/trading/virtual_broker.py`
- **체결 모델 (보수적)**: 매수=매도호가1(ask1), 매도=매수호가1(bid1) → 스프레드 비용 반영. 호가 실패 시 현재가 ±10bp. 매도 시 왕복 수수료·거래세(_COMMISSION) 예수금 차감 → virtual_cash 곡선이 pnl_pct 누적과 정합
- **`get_trading_client(account)` 팩토리** (virtual_broker.py): VIRTUAL이면 VirtualBroker, 아니면 KISClient 싱글턴. executor/positions API/realtime_monitor/thesis 재검증 전부 이 팩토리 사용 — **브로커 호출 지점 추가 시 get_kis_client_from_account 직접 호출 금지**
- VirtualBroker는 시세를 실전 클라이언트에 위임(__getattr__), 잔고 차감은 단일 UPDATE(레이스 방지), 잔고부족 시 주문 거부. **장외/휴장 시 주문 거부** (실계좌 충실도 — 휴장일 전일 잔상 호가로 허구 체결되던 7/17 사례 교정, 2026-07-20). get_balance는 HOLDING 포지션 기반(avg=entry라 체결가 보정 no-op)
- **실+가상 병행 구독 가능**: 매수 중복 체크가 계좌 단위 (user_id+account_id+rec_id)
- **Circuit Breaker는 실계좌 전용**: 가상 청산은 CB 집계 제외 + 가상 매수는 CB 차단 안 받음 (자본 보호 레이어이지 전략 평가 대상 아님)
- **통계 격리**: GET /positions/stats?scope=real(기본)|virtual|all — 가상 손익이 실계좌 KPI에 안 섞임. PositionOut.account_type으로 프론트 "가상" 배지
- 시장데이터 팩토리(_client_from_db, market.py _user_account)는 VIRTUAL 제외/REAL 우선 — VIRTUAL엔 KIS 키가 없어 get_kis_client_from_account가 RuntimeError
- 자동매수(09:20)·실시간 트레일링/손절·만료·thesis 청산 모두 가상계좌 동일 동작. 텔레그램 청산 알림 [가상] 태그
- 생성: POST /users/{id}/accounts/virtual (initial_cash) 또는 포지션 페이지 > 계좌 설정

### strategies
- strategy_id (PK, UUID), created_by (FK)
- name, description
- hold_days, target_pct, stop_loss_pct, pick_count, run_interval_days
- **candidate_filter**: volume / largecap / mixed (기본 mixed)
- **candidate_market**: KOSPI / KOSDAQ / NAS / ALL (기본 ALL)
- **selection_mode**: momentum(기본) / earnings_catalyst / **rule_breakout** / **rule_oversold** — 전략 단위 선정 로직 분기.
  `rule_*`는 규칙 기반으로 **Stage1~4 전체를 스킵**(Gemini 호출 0회), 나머지는 Stage4 프롬프트 변형
- is_active, created_at

### user_strategies
- user_id (FK), strategy_id (FK), account_id (FK)
- invest_amount_per_pick, is_auto_trade, is_active, subscribed_at

### recommendation_runs
- run_id (PK, UUID), strategy_id (FK), run_date
- ai_model_used, raw_response (JSONB — macro/historical/industry/picks/random_baseline)
- **kospi_at_run, kosdaq_at_run**: 분석 실행 시점 지수 레벨 (Stage1 정확도 검증용)
- **kospi_change_1d, kosdaq_change_1d**: 다음날 실제 등락률 (16:00 잡이 채움)
- **verified_1d_at**: 검증 완료 시각
- **stage4_skipped**: A-gate 발동으로 Stage4 스킵됐는지 여부
- **kospi_close, kospi_ma20, kospi_ma20_state**: 진입 시점 시장 국면 (above/below). 성과를 국면별로 분리해 보기 위한 **기록 전용** — 매매 차단에 관여 안 함. 픽 단위가 아닌 run 단위인 이유: 같은 run의 픽들은 같은 날 같은 지수 상태라 픽마다 저장하면 중복. `kospi_at_run`(실행 시점 조회값)과 별도인 이유: MA20과 시점을 맞추려면 전일 종가 기준이어야 함

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
- 검증 로직: 일봉 날짜순 순회 → 손절가 터치 먼저면 FAIL, 목표가 터치 먼저면 SUCCESS
  - 같은 날 둘 다 터치: 손절 우선 (보수적 convention)
  - pnl_pct: 실제 exit_price(목표가/손절가/기간말 종가) 기준, 현재가 아님
  - 기간 필터: bar.date는 "YYYYMMDD" 포맷 — period_start/end도 strftime("%Y%m%d") 사용 필수 (ISO 포맷과 혼용 시 전체 필터 실패)

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
- key: news_auto_trade_paused, news_pause_reason, news_pause_at, news_last_check_at 등

### prompt_versions
- stage(1~4), version_no, prompt_text, performance_score

### watchlist_stocks / stock_analyses ← 관심종목 분석 탭 (중장기 수동매매 일지)
- watchlist_stocks: watch_id (PK), user_id (FK), stock_code, stock_name, sector, memo, added_at
  - UNIQUE(user_id, stock_code) — 유저별 스코핑
- stock_analyses: analysis_id (PK), user_id (FK), stock_code, stock_name, analysis_date, trigger_type(manual/earnings/disclosure/flow_spike/price_spike), gemini_model
  - **result** (JSONB): 논거/단기_촉매/장기_논거/**무효화_조건**(핵심, falsifiable 강제)/밸류_코멘트/뉴스_출처
  - **input_snapshot** (JSONB): 분석 시점 KIS 지표/재무/수급/추정실적 + data_flags(결측 명시) — 사후 재구성용
  - **condition_status** (JSONB, 2026-07-16): 무효화_조건 자동 체크 상태 — {checked_at, items:[{state, detail, check_type, triggered_at, notified_at}]}, items는 무효화_조건과 위치 정렬. 16:20 잡이 갱신
  - **watchlist_stocks에 FK 없음** — 관심종목 삭제해도 일지 영구 보존
- 상세 설계·KIS 필드 디코딩 근거는 docs/watchlist_spec.md + 메모리 watchlist_tab.md 참조
- **임계 캘리브레이션 + 반증 패스 (2026-09-07)**: 무효화_조건 params의 수치 임계를 앱이 분포에서 재계산해 덮어씀(`calibration` 필드에 원안+근거 보존). `반증_관점`(강세 논거를 감춘 별도 호출의 반대 해석 목록) 필드 추가, 반증 패스가 낸 조건은 `origin: "반증"`. 스냅샷 fx에 `high_1m`/`low_1m`/`daily_vol_pct` 추가
- **판단 구조 보강 (2026-08-28)**: `핵심_주장`(무효화_조건이 반증할 명제 — 매수/관망 결론·진입가 강제는 미채택, ai_probability 폐기 근거와 충돌) + `밸류_시나리오_코멘트` 필드 추가. 스냅샷에 `valuation_scenarios`(멀티플 밴드 역산 함의주가, 상단·하단 대칭 + peak_earnings/영업외요인/장부가시점차 경고) · `pbr_recent_q`(최근 분기 BPS 기준 PBR 병기) · `ni_margin_q_pct`+`ni_over_op_note`(순이익>영업이익 시 per_ttm 왜곡 플래그, YTD 차분 회귀 감시 겸용) 추가. 수급 일평균은 결측일 제외한 실제 거래일 수를 분모로 노출
- **스냅샷 v2 (2026-07-02, 실검증 2026-07-03 완료)**: fx_usdkrw(USD/KRW 3개월 추세) / market(KOSPI 레벨·1/3개월 + 종목 상대수익률) / PER 4종 병기(trailing·TTM·최근분기 연환산·컨센서스 forward — trailing 왜곡 대응) / 수급 페이스 판정 문자열(5일 vs 30일 일평균, 앱이 확정 — LLM 재계산 금지) / 개인 순매수 5/20/30일 / PBR 5년 밴드 근사(월봉÷당시 연간 BPS, 근사 명시)

### investor_flow_daily ← 관심종목 수급 적재 (2026-07-02)
- flow_id (PK), stock_code (idx), trade_date, frgn/orgn/prsn_ntby_amt (백만원), close
- UNIQUE(stock_code, trade_date), 유저 스코핑 없음 (공용 시장 데이터)
- KIS FHKST01010900이 최근 30거래일만 반환 → 16:10 잡 + 분석 실행이 매일 upsert해 60/120일 누적 구축
- **백필 불가** — 2026-07-02부터 축적, 커버리지 미달 구간은 부분합으로 위장하지 않고 None + 일수 명시

### daily_price ← KRX 일별 전종목 시세 (2026-09-03)
- 복합 PK (stock_code, trade_date), upsert. stock_name/market/sector_type/OHLC/change/change_pct/volume/trade_value/market_cap/listed_shares
- 인덱스: trade_date, (trade_date, trade_value) — 거래대금 순위 조회용
- KIS는 종목당 1회 호출이라 전종목 히스토리가 비현실적인데 **KRX는 하루치 전종목(2765행)을 1회 호출**로 준다
- 용도: 규칙 전략 파라미터(신고가 기간·거래대금 컷) 백테스트 튜닝. **라이브 전략은 KRX 없이 KIS만으로 동작** — 전제조건 아님
- 금액 단위 원 (명세서에 단위 미기재, 실측 대조로 확정 — SK하이닉스 시총 1,178조)

### research_notes ← AI 리서치 탭 (자유 질문 종목 리서치, 2026-08-10)
- research_id (PK, UUID), user_id (FK, CASCADE), stock_code (idx), stock_name
- question, **answer_md** (마크다운 자유 서술 — 시나리오+확인 포인트, 방향 단언·목표주가 금지)
- gemini_model, sources (JSONB — 출처 섹션 링크 추출), input_snapshot (JSONB — 관심종목과 동일 수집기), created_at
- **stock_analyses와 별도 테이블** — 16:20 무효화 판정 잡이 종목별 최신 분석을 읽으므로 무효화_조건 없는 자유 서술이 섞이면 조건 감시가 깨짐
- 참고용 프레이밍: 매매 시그널 아님 (UI 경고 배너), 자동매매 개입 없음

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
1. morning_gate_paused / news_auto_trade_paused 체크 → 차단 시 전체 스킵
2. auto_trade=ON 구독자 중 "오늘 분석 완료됐는데 포지션 없는 것" 탐색
3. 크로스 시그널 맵 사전 계산 — 오늘 모든 전략 추천 집계, 종목별 다양성 점수
4. KOSPI/KOSDAQ 지수 현황 조회 (-2% 이상 급락 시 전체 보류)
5. cross_signal_bonus 우선 정렬, 동점이면 AI 추천 rank 순으로 매수 (ai_probability 미사용)
6. TTTC8001R로 실 체결가 즉시 조회 → Position(entry_price=fill_price, peak_price=fill_price)

### 포지션 모니터링 (09:05, 12:00, 14:50)
- 09:05: update_entry_prices_from_balance() 백업 실행 (폴링 fallback)
- 목표가 도달 → 즉시 익절 대신 트레일링 모드 전환 (target_hit_at, target_hit_peak 기록)
- +1거래일 14:30까지 신고점(peak_price) 갱신 없으면 TARGET_HIT으로 강제 청산
- 트레일링 스탑: `peak_price × (1 - stop_loss_pct/100)` 이탈 → 손절
- Time-based Stop: 5일 후에도 손실 중 → 조기 청산
- 만료(hold_days 경과) → 시장가 청산

### 매수 스킵 조건
- morning_gate_paused=true (08:00 게이트 발동)
- news_auto_trade_paused=true (장중 뉴스 감시 발동)
- remaining_upside ≤ stop_loss_pct (리스크/리워드 불균형)
- RSI > 70 (과매수)
- 동일 섹터 2종목 초과 (MAX_PER_SECTOR=2)
- 잔고 부족

### 뉴스 감시 듀얼 시그널 조치
장중 뉴스 감시(2시간마다)에서 WARNING/CRITICAL 감지 시 실시간 KOSPI 등락률로 교차 검증:
- `CRITICAL + KOSPI ≤ -2%` → 전략 포지션 즉시 청산 (MANUAL_EXIT) + 텔레그램
- `WARNING/CRITICAL + KOSPI ≤ -1%` → 수익 중 전략 포지션 현재가 기준 trailing 전환 + 텔레그램
- AI 단독 신호 (KOSPI 멀쩡) → 알림만 (오탐 방지)
- **3중 보강 (2026-07-20)**:
  - **휴장일/장외 가드**: 조치 전 `is_market_open_now()` 확인 — 휴장이면 무조치. 배경: 7/17 제헌절 휴장에 KIS 지수 API가 직전 거래일(7/16 -6.4%) 등락률을 그대로 반환 → CRITICAL과 교차돼 가상 포지션이 전일 잔상 호가로 오청산됨
  - **스코프 = 전략 포지션만**: 청산/손절강화는 `strategy_id` 있는 포지션만. 무전략 수동매수는 소유자에게 "직접 판단" 알림만 (자동 진입은 자동 방어, 수동 진입은 사람 판단 — 관심종목 역발상 매수를 시스템이 뒤집지 않음)
  - **당일 재발동 억제**: 긴급 청산 발동 시 KOSPI 등락률을 app_config(`news_emergency_close_date/kospi`)에 기록, 같은 날은 직전 대비 1%p 추가 악화 시에만 재청산 (동일 이벤트 지속 CRITICAL이 매 틱 재청산 → 장중 신규 포지션까지 쓸리던 문제)

### Thesis 재검증 (10:00, 14:00)
- 대상: 2일+ 보유 HOLDING 포지션
- 8개씩 그룹 분할 → gemini-2.5-flash + google_search (환각 방지)
- `invalid + confidence≥0.7 + 손실` → 조기 청산 (MANUAL_EXIT)
- `invalid + confidence≥0.7 + 수익` → 현재가 기준 trailing 손절 전환
- `partial` 또는 낮은 confidence → 텔레그램 알림만

### 크로스 시그널 보너스
- 오늘 복수 전략이 같은 종목 추천 시 ai_probability에 보너스 가산
- 다른 (candidate_filter, candidate_market) 조합 전략 = 1.0점 → +7%
- 같은 조합 전략 = 0.5점 → +3.5%, 상한 +10%

## 스케줄러 잡 목록
| 잡 ID | 시각 | 역할 |
|-------|------|------|
| morning_gate | 08:00 평일 | 개장 전 야간 리스크 체크 (미국 선물/지정학), 이상 시 09:20 매수 차단 |
| run_strategies | 08:30 평일 | AI/규칙 분석 (매수 없음, morning_gate와 무관하게 실행). **잡은 매 평일 뜨고 전략별 간격은 각자의 run_interval_days가 통제** — mon,wed,fri 고정이면 run_interval_days=1 전략이 주 3회로 묶여 무력화됨 (2026-09-03 변경) |
| execute_pending_buys | 09:20 평일 | 크로스 시그널 보너스 적용 후 매수 (morning_gate/news 차단 시 스킵) |
| monitor_positions | 09:05~15:55 매 10분 평일 | 포지션 손절/익절 모니터링 |
| thesis_check | 10:00, 14:00 평일 | 보유 포지션 thesis 재검증 (8개씩 그룹 grounding) |
| verify_recommendations | 00:10 매일 | 추천 결과 사후 검증 |
| verify_news_events | 16:00 평일 | 뉴스 이벤트 + recommendation_runs 실제 시장 영향 검증 |
| collect_watchlist_flows | 16:10 평일 | 관심종목 일별 수급 적재 (60/120일 누적, 2026-07-02 시작) |
| check_invalidations | 16:20 평일 | 관심종목 무효화_조건 자동 판정 (수급 적재 직후, 충족 전이 시 유저 알림) |
| watchlist_event_scan | 16:30 평일 | 관심종목 이벤트 감지 (공시/수급·주가 급변 → 알림 + 트리거급은 자동 분석, 휴장일 스킵) |
| news_watch_tick | 09:00~15:30 10분마다 평일 | 뉴스 감시 tick (120분마다 실행) |
| update_stock_master | 03:00 일요일 | stock_master + 지수캐시 갱신 |

## Gemini 모델 체인
| 용도 | 모델 | Fallback |
|------|------|----------|
| Stage1 (매크로+그라운딩) | gemini-2.5-flash | - |
| Stage2 (역사 분석) | gemini-3-flash-preview | gemini-3.1-flash-lite |
| Stage3 (산업 분석) | gemini-3.1-flash-lite | gemini-2.5-flash-lite |
| Stage4-A (자유형식 분석) | gemini-3-flash-preview | gemini-3.1-flash-lite → gemini-2.5-flash-lite |
| Stage4-B (코드 추출) | gemini-3.1-flash-lite | gemini-2.5-flash-lite |
| BUY_CONFIRM (미사용, prompts.py에만 존재) | — | — |
| 실적 카탈리스트 탐지 (earnings_catalyst 전략) | gemini-2.5-flash | - (검색 그라운딩, 실패 시 빈 결과) |
| 뉴스 감시 (장중 2시간마다) | gemini-2.5-flash | - |
| 관심종목 분석 (수동 트리거) | gemini-2.5-flash | - (검색 그라운딩, 파싱 실패 시 gemma 정제) |
| AI 리서치 (수동 트리거) | gemini-2.5-flash | - (검색 그라운딩, 마크다운 출력). 종목 별칭 추출은 gemini-3.1-flash-lite |
| 모닝 게이트 (08:00) | gemini-2.5-flash | - |
| Thesis 재검증 (10:00, 14:00) | gemini-2.5-flash | - |
| JSON 정제 | gemma-4-31b-it | - |

## Stage4 선정 의도 — 탑다운 매크로 모멘텀 (2026-06-02 복원)
- **선정 철학**: Stage1~3가 짚은 "수혜 예상 섹터"를 Stage4가 그대로 이어받아 **그 섹터의 추세 강한 종목을 탄다** (탑다운 매크로 모멘텀). 역발상/눌림목 매수 아님
- **드리프트 교정 배경**: 5/14~5/28 사이 STAGE4A 본문에 【하방안정성 우선】(RSI 30~55 눌린 종목) 역발상 기준이 들어가 Stage1~3 모멘텀 의도와 충돌 → 매크로 무시하고 소외 소형주 픽 → 강세장 승률 22.5%. 검증 528건 분석 후 (A) 모멘텀으로 복원
  - STAGE4A 본문: 【매크로 수혜 + 추세 모멘텀】 — 수혜섹터 정합 / 현재가>MA20≥MA60 정배열 / 수급유입 / RSI 50~70 (RSI<45 추세미형성 제외, >75 과열 자제)
  - `_prefilter_stocks`: RSI~60 + 추세정배열 가점 + 거래량, RSI 밴드 45~78 (fallback 40~82)
  - `_FILTER_GUIDANCE` mixed: 눌림목 유도("MA20 −10%~+5%") 제거 → "MA20 위·근접, 추세 살아있는"
- **하방방어는 선정이 아닌 다른 레이어**: A-gate(하락장 키워드 시 Stage4 스킵), morning_gate, 뉴스 듀얼시그널, 손절/trailing/Circuit Breaker가 담당. Stage4 선정 기준에 역발상을 다시 넣지 말 것

## Stage4 억지 픽 방어 구조 (Gemini 성향 대응)
- **확률 폐기, 순위 기반 구조로 전환** (2026-05-28): verifier 데이터 515건에서 ai_probability와 실제 승률 간 상관관계 없음 확인 (60~70%→22.9%, 80~90%→18.3%). LLM은 종목 선별(큐레이션)만 담당, 수치 확률 산출 완전 제거
  - STAGE4A: ai_probability 제거, 서술 순서가 곧 추천 순위
  - STAGE4B: ai_probability 필드 제거, rank(언급 순서)만 추출
  - executor 정렬: `ai_probability + cross_signal_bonus` → `cross_signal_bonus 우선, 동점이면 rank`
  - min_probability 필터 제거 → **잔재까지 전면 제거 (2026-09-10)**: `strategies.min_probability` 컬럼 드롭(마이그레이션 c3d4e5f6a7b9), 프론트 "최소확률" 입력·검증·전략카드 표시 제거, 추천 테이블 확률 열 제거, 텔레그램 추천 알림 확률 표기 제거, 죽은 `STAGE4_PICKS` 템플릿(확률 요구 프롬프트) 삭제, analyzer·runner·백테스터의 min_probability 인자와 ai_probability 쓰기 제거
  - **`recommendations.ai_probability` 컬럼은 남긴다** — 폐기 이전 값이 곧 무상관 분석 515건의 원본 근거다. 신규 쓰기·API 노출·UI 표시 없는 **동결 기록**이고 되살리지 말 것
  - 확률을 다시 넣고 싶어지면 먼저 그 컬럼으로 재현할 것: 확률 구간과 실제 승률이 무관했다 (60~70%→22.9%, 80~90%→18.3%)
- **B-gate** (항상 동작): Stage4A/B 프롬프트에 "0개 반환 허용" 명시 — pick_count 충족 위한 억지 선정 금지
- **A-gate** (키워드 OR 수치, 둘 중 하나면 Stage4 스킵 + 어드민 알림):
  - 매 run마다 `kospi_at_run` 저장, 16:00 잡이 `kospi_change_1d` 채움
  - **키워드 게이트** (verified 20건 이상 시): `_BEAR_KEYWORDS` 가 market_theme에 있으면 스킵
  - `_BEAR_KEYWORDS`: 하락장/폭락/급락/약세/하락세/조정장/침체/위기/crash/bear/매도세
  - **수치 게이트** (2026-06-10 도입, 항상 활성): 전일 KOSPI ≤ -2.5% 또는 3거래일 누적 ≤ -4% → 스킵. `runner._is_index_unfavorable()` + `client.get_index_daily_closes()` (FHKUP03500100 지수 일봉, 당일 미확정 봉 제외). 지수 조회 실패 시 게이트 미적용(분석 차단 안 함 — 09:20 잡의 당일 -2% 체크가 별도 존재)
  - 도입 배경: 6/5 폭락장(전일 -6%)에서 Stage1이 "AI 슈퍼사이클 호황" 강세 테마 서술 → 키워드 게이트 첫 실전 미스. LLM 서술 비의존 수치 판정 병행. 임계값은 표본 1 기반 보수적 시작값 — 데이터 축적 후 조정

## Stage4 환각 방어 구조
Stage4는 종목코드-이름 환각을 막기 위해 3겹 방어:
1. **사전필터**: KIS 75개 → 추세(MA정배열)·RSI·수급·거래량 기준 20개 압축 (runner._prefilter_stocks)
2. **그룹 분할**: 10개씩 2그룹, 각 그룹 독립 실행 (runner._run_stage4_grouped)
3. **2단계 생성**:
   - Stage4-A: Flash-preview가 자유형식 텍스트로 분석 ("330860(네패스아크) 기관 순매수...")
   - Stage4-B: Flash-lite가 텍스트에서 코드 추출 (패턴 매칭, 창의적 판단 불필요)
4. **서버 검증**: price_map 외 코드 저장 거부 + stock_master 이름 교정 + KIS 가격 덮어쓰기
- stock_data에 stock_name 사전 주입 (AI 훈련 기억 대신 DB 이름 사용)
- raw_response.price_snapshot: KIS 수집 시점 가격 감사 로그 저장
- **PER/EPS 참고 필드 (2026-06-19)**: stock_data에 per/eps 동봉 → STAGE4A 프롬프트에 "참고용"으로 노출. 가드레일 6번: 저PER이라는 이유로 추세 없는 종목 선정 금지 / 고PER이라는 이유로 추세·수급 강한 종목 제외 금지 — 선정은 매크로·추세·수급·모멘텀(1~4번) 절대 우선. **prefilter 점수엔 미반영**(저PER 가점 = 밸류 드리프트 = 5/28에 걷어낸 큐레이션 회귀). 실데이터 검증(6/19): PER 싼 순서(NAVER 18<하이닉스 47<삼성 54<한미 132)와 모멘텀(RSI) 순서가 역상관 → PER 가점 시 추세 죽은 종목을 위로 올렸을 것. 단타 시간축에선 밸류로 익절/손절가 잡는 것도 부적합(재평가는 수개월 단위)

## 전략 선정 변형 — selection_mode (2026-06-19)
기존 전략은 파라미터·종목풀만 다르고 **Stage4 선정 프롬프트는 전역 공유**였음. `Strategy.selection_mode`로 전략 단위 선정 로직 분기 도입:
- **momentum**(기본): STAGE4A_ANALYSIS (탑다운 매크로 모멘텀, 기존 (A))
- **earnings_catalyst**: STAGE4A_EARNINGS — 모멘텀 기준(추세·수급·RSI) 위에 **실적 카탈리스트(서프라이즈/가이던스 상향/추정치 상향)를 최우선**. 단 카탈리스트만으로 선정 금지(추세·수급 동반 필수). forward EPS 숫자를 만들지 않고 *이벤트 유무*만 사용 — LLM 수치 환각·false precision 회피(ai_probability 폐기와 동일 철학)
  - 흐름: `_run_stage4_grouped`가 사전필터 직후 `detect_earnings_catalysts`(그라운딩 1회) → stock_data에 earnings_catalyst 주입 → STAGE4A_EARNINGS로 선정
  - 탐지 실패 시 빈 dict → 모멘텀 기준으로 진행(fail-safe). 검색 확인 사실만(없으면 빈 목록, 환각 금지)
- **첫 적용**: `[TEST] 실적 카탈리스트` 전략 = `KOSPI 대형주 스윙`(hold 20/target 6/stop 3/largecap·KOSPI/pick 3) 파라미터 **그대로 복제** + selection_mode만 변경 → 선정 로직만 분리된 A/B. largecap 샘플링은 KOSPI200 시총순위 기반이라 두 전략이 거의 동일 풀 → 깨끗한 비교. 구독 없음(관찰 모드), 08:30 잡이 활성 전략 전체 실행 + verifier가 auto_trade 무관 채점이라 데이터 자동 누적
  - earnings_catalyst를 단타(hold 7)가 아닌 대형주 스윙(hold 20)에 얹은 이유: 실적 카탈리스트는 대형주에서 데이터 풍부 + PEAD 드리프트가 수주 단위라 시간축 정합
  - 씨드: `scripts/seed_earnings_catalyst_strategy.py` (대형주 템플릿 우선 복제, 멱등)
- **시장 축 변형 (2026-08-05)**: `[TEST] KOSDAQ150 대형주 스윙` — 대형주 스윙의 candidate_market만 KOSDAQ(150)으로 바꾼 관찰 전략(구독 없음). 배경: 8/5 점검에서 largecap×hold20×momentum 셀만 플러스(승률 41.7%, +0.75%) → KOSPI 특수인지 대형주 스윙 일반인지 검증. target/stop 8/4는 KOSDAQ 변동성(KOSPI200 대비 1.3~1.5배) 조정 — R/R 2 유지, 엣지 부재와 손절 과민을 구분하기 위함. 씨드: `scripts/seed_kosdaq_swing_strategy.py` (멱등)
- **규칙 기반 대조군 (2026-09-03)**: `rule_breakout` / `rule_oversold` — `services/trading/rule_selector.py`. **Gemini 호출 0회**, `run_strategy`가 데이터 수집 직후 `_run_rule_strategy`로 분기해 Stage1~4를 통째로 스킵. 조건 충족이 pick_count에 못 미치면 **모자란 채로 저장**(억지 선정 금지 — B-gate와 같은 철학, 실제로 rule_oversold는 0건이 흔함)
  - **rule_breakout**: 종가가 직전 20거래일 종가 최고 갱신 + 종가 > MA5. 정렬은 돌파 폭 큰 순
  - **rule_oversold**: RSI(14) ≤ 35 + 거래대금 전일 대비 ≥ 1.5배. 정렬은 RSI 낮은 순
  - 입력은 `_collect_stock_data`가 이미 수집한 값만 사용 — **추가 API 호출 0회**. `close_high_20d` / `turnover_ratio`를 `_get_domestic_stock_info`가 이미 받아둔 일봉에서 계산해 동봉
  - 지시서의 "거래대금 상위 100위" 조건은 **의도적으로 제외** — KOSPI200 유니버스에선 거의 항상 통과라 무효 필터(전체시장용 유동성 스크린)이고, 조건이 늘면 대조군의 변수만 늘어남

## 관찰 전략 구성 (2026-09-03 개편)
랜덤 벤치마크 버그를 고치자 AI 우위가 0으로 수렴(KOSPI 대형주 스윙 AI -0.29% vs 랜덤 -0.20% = **-0.09%p**). 랜덤은 너무 약한 대조군이라 판정 불가 → 대조군을 강화하고 표본 축적을 가속하는 방향으로 재편. 전부 **구독 없는 관찰 모드**(활성 전략 row만으로 verifier가 채점). 씨드: `scripts/seed_control_strategies.py` (멱등)
- **`[TEST] 규칙 모멘텀 (AI 대조군)`**: `KOSPI 대형주 스윙` 파라미터·유니버스 **완전 복제** + `rule_breakout`. 변수는 "AI 사용 여부" 하나 → "Gemini가 단순 규칙 대비 값을 하는가"를 직접 겨냥. 현재 시스템에서 가장 중요한 미해결 질문
- **`[TEST] KOSPI 대형주 스윙 (주기1일)`**: 원본 복제 + `run_interval_days=1`. **보유기간·목표·손절은 원본 유지** — 5일/3%/1.5%로 줄이는 안은 미채택: 왕복 비용(수수료+거래세 ~0.2~0.25%)이 고정이라 폭을 절반으로 줄이면 세후 본전 승률이 36%→39%로 올라가고, "5일 안에 +3%"는 "20일 안에 +6%"와 다른 능력이라 원 전략 검증이 아니라 별개 전략 측정이 됨. 주기만 줄이면 회전율·비용은 그대로고 측정 대상이 원 전략 그대로다
  - 이 전략 때문에 `run_strategies` 잡을 mon,wed,fri → 평일로 변경 (잡이 주 3회면 interval=1이 무의미)
  - **표본 겹침 주의**: 주기를 줄이면 연속 run이 같은 시장 구간을 공유 → 200건이 200개 독립 표본이 아님. 승률 표준오차를 √n으로 계산하면 과신
- **`[TEST] 과매도 반등 (규칙)`**: `rule_oversold`, hold 10 / target 7 / stop 3.5 / pick 3 / KOSPI largecap. 기존 전략이 전부 모멘텀 방향이라 같은 국면에서 동시에 죽는 문제의 상관 분산용
  - target 7%인 이유: `_validate_strategy`의 **일평균 0.7%/일 상한**에 걸려 hold 10일에서 가능한 최대치. 손절 3.5%는 3%보다 넓게 — 하락 추세 종목은 진입 직후 추가 하락이 흔해 좁은 손절이 구조적으로 불리
  - RSI 35 / 거래대금 1.5배는 지시서 원안(30 / 2배) 완화 — 원안은 KOSPI200에서 몇 주씩 0건. 완화 후에도 발동이 드물어 **표본 축적은 느릴 것으로 전제**
- **`[TEST] 실적 카탈리스트` 비활성화**: 관찰 슬롯·Gemini RPD가 유한한데 규칙 대조군이 더 나은 실험이라 교체. **삭제 금지** — `recommendation_runs.strategy_id`가 `ondelete=CASCADE`라 전략을 지우면 과거 run·추천·검증이 전부 소멸. `is_active=False`만

## 랜덤 벤치마크 설계 원칙 (2026-09-03 교정)
대시보드 `AI 우위 = AI 평균수익 − 랜덤 평균수익`. 이 지표가 **종목 선정력**을 재려면 랜덤 쪽이 AI와 청산 규칙까지 같아야 한다.
- **버그**: `verifier._verify_random_baselines`가 목표/손절 없이 만기 종가만 썼음 → 랜덤만 단순 보유 수익률. KOSPI 대형주 스윙 랜덤이 **-4.05%**(손절 -3% 규칙에선 불가능한 값)로 나와 발각. 이 상태의 "AI 우위"는 선정력이 아니라 **손절 로직의 효과**를 재고 있었음. 소급 재계산 후 우위 +3.76%p → **-0.09%p**
- **백테스터(`_compute_random_baseline`)는 원래 맞았고 라이브 경로만 틀렸다** — 청산 로직이 두 군데 복제돼 한쪽만 드리프트. 교정: `verifier.simulate_exit_pnl()`을 **단일 진실 공급원**으로 두고 AI 검증·랜덤 대조군·백테스터가 전부 이 함수를 호출. 청산 모델을 새로 짜지 말 것
- **표본 크기**: 랜덤은 실매매가 아니라 벤치마크이므로 pick_count(=3)에 맞출 이유가 없다. 3개면 벤치마크 자체의 표준오차가 AI만큼 커져 우위의 오차가 √2배로 부풀어남 → `_RANDOM_BASELINE_N = 40`
- **유니버스**: 랜덤은 prefilter **이전** 풀(~75개)에서 추출. prefilter도 우리가 만든 로직이라 파이프라인 전체의 엣지를 재는 게 맞음 (의도적 선택)
- **기간 경계**: `period_start <= b.date <= period_end` — 진입 당일 봉 **포함**. 08:30 실행이라 진입가는 전일 종가이고 당일 봉은 전부 미래 구간. 랜덤만 `<`로 당일을 버리던 것도 교정
- 소급 재계산: `scripts/recompute_random_baseline.py` (run_id 시드로 재현 가능, `--dry-run` 지원). 검증 기준 = 평균이 `[-손절%, +목표%]` 안에 드는가 (정수 반올림 여유 0.5%p)

## 시장 국면 기록 (2026-09-03)
전략 성과가 종목 선정 탓인지 시장 국면 탓인지 분리해 보기 위한 **기록 전용** 레이어. `services/trading/market_regime.py`
- 판정: 진입일(run_date) 기준 **직전 거래일 종가 vs 20일 이동평균** → `above` / `below`. run_date 당일 봉은 제외(08:30 실행 시점 미확정 = 미래 정보 유입 방지)
- 저장 위치는 `recommendation_runs` (픽 단위 아님). 기준을 나중에 바꿀 수 있게 판정 결과와 원본값(close/ma20)을 함께 저장
- 소급: `scripts/backfill_market_regime.py` — KIS 지수 일봉을 **1회만** 조회해 date→close 맵으로 캐시하고 run별 로컬 계산 (run마다 다시 긁으면 rate limit 낭비). KRX API 불필요
- 표시: 전략 상세 화면 국면별 승률/평균수익/랜덤 대비. **건수 병기 + 30건 미만은 흐리게** — 63건을 above/below로 쪼개면 각 30건, 승률 표준오차 ±9%p라 숫자만 보면 국면 차이로 오독하기 쉬움
- **매매 차단에 쓰지 말 것** — 하방 방어는 A-gate / morning_gate / 뉴스 듀얼시그널 / 손절 담당

## KRX 오픈API (2026-09-03 실검증)
전종목 히스토리 벌크 수집용. `app/services/krx/client.py` + `scripts/load_krx_daily.py`

**실검증에서 문서·통념과 달랐던 것 (재삽질 방지)**
- **호스트는 `data-dbg.krx.co.kr`** — `openapi.krx.co.kr`은 포털(로그인 UI) 전용이라 모든 API 경로가 404다. 404가 HTML 에러페이지로 오기 때문에 인증 문제로 오인하기 쉽다
- **401 = 해당 서비스 미이용신청 / 404 = 경로 오류**로 구분된다. 서비스별 개별 신청 필요 (2026-09-03 기준 `idx/kosdaq_dd_trd`만 미신청)
- **`ISU_CD`의 의미가 엔드포인트마다 다르다**: 일별매매=6자리 단축코드, 종목기본정보=12자리 ISIN(단축코드는 `ISU_SRT_CD`). 조인 시 주의
- **`idx/krx_dd_trd`에 코스피 지수가 없다** — KRX 시리즈 40종(밸류업·KRX 300 등)만. 코스피는 `idx/kospi_dd_trd` 별도. 시장 국면 소급은 KIS `get_index_daily_series`로 이미 해결돼 KRX 불필요
- **파라미터는 `basDd` 하나뿐** — 기간 조회가 없어 하루씩 호출해야 한다 (명세서 확인)
- 주말·공휴일·미래 날짜는 에러가 아니라 **`200 + 빈 배열`**
- **당일 데이터 공표는 20:23 이후** (당일 20:23 조회 시 0행). 스케줄 잡을 붙이면 밤늦게 돌릴 것
- **호출 제한은 명세서에 없음** — sleep 0.5초로 44회 연속 호출 시 실패 0. 로더가 실패 누적 시 자동 백오프(최대 8초, 5회 연속 실패면 중단)
- 응답 래퍼는 항상 `OutBlock_1`, 모든 값이 문자열. 데이터 제공 시작 2010-01-04

**적재 운영**
- 진행 지점을 app_config(`krx_load_last_date`)에 저장 → `--resume`으로 이어받기
- `--status`로 적재 현황, `--dry-run`으로 DB 미반영 조회
- 첫 실행 완료(2026-09-03): 2026-08-04~09-02, 21거래일 58,045행
- 스펙 문서(docx)는 `docs/krx/`에 있으나 **git 미추적** (KRX 배포 문서라 공개 레포에 싣지 않음)

## 무효화_조건 임계 설계 원칙 (2026-09-07)
관심종목 탭의 무효화_조건이 **형식만 갖추고 기능은 없던** 상태를 교정. 적재 수급 64거래일 롤링 검증에서 LLM이 낸 "외인 5거래일 순매도 1.5조"가 60개 창 중 **35개(58%)**에서 충족됐다 — 5일 누적 중앙값이 -1.93조라 평상시보다 나은 상태를 무효화 신호로 부르고 있었다. 반대로 "환율 1560원"(현재 1346 대비 7.9σ)·"영업이익률 50%"(현재 76.3% 대비 5.3σ)는 도달 불가. **항상 켜져 있거나 절대 안 켜지는 조건은 둘 다 신호가 아니다.**
- **역할 분리**: LLM은 *무엇을 감시할지*(투자자/방향/지표/기간 — 논거에서 나오는 정성 판단), 앱은 *얼마에서 켤지*(임계 수치). LLM은 입력의 숫자를 읽지만 서로 곱하고 나누지 않는다 — 분포 계산은 애초에 앱 일이다. `services/watchlist/calibration.py`가 목표 발동률 10% 지점을 계산해 params를 덮어쓰고, **조건 서술도 `condition_text`로 재생성**한다 (임계만 바꾸면 텍스트가 거짓말을 한다). 근거는 `cond["calibration"]`에 보존 → 프론트 노출 + 사후 검증
- **양쪽 꼬리 게이트**: `invalidation._screen_reason`의 기존 ①~④(너무 쉬운 조건)에 ⑤~⑧ 추가 — 과거 창 발동률 `FIRE_RATE_MAX`(30%) 초과 또는 0%, 환율 3.5σ 밖, 분기 마진 3σ 밖, 컨센 하향 25% 초과. 캘리브레이션이 못 미치는 경로(표본 부족·미지원 지표)의 백스톱
- **환율 기준은 1개월 진폭** (`high_1m`/`low_1m`) — 3개월 밴드는 추세 이동을 담고 있어 "밴드 밖"이 곧 도달 불가가 되는 구간이 있다 (2026-09-07 실측: 밴드 폭 205원 = 월 변동성의 7.6σ). 구 스냅샷은 3개월 폴백
- **null 창 금지**: 수급 창은 `calibration.flow_series`(확정 데이터만)로 채운다. 미확정 당일 행이 자리를 차지하면 "5거래일 누적"이 4일치로 판정되고 연속일 카운트는 0으로 리셋된다
- **레짐 종속 한계**: 외인이 3개월 내내 판 구간에선 p10 자체가 커져 임계가 느슨해진다("절대적 이례"가 아니라 "최근 N거래일 대비 이례" — 근거 문구에 명시). 창 30개 미만이면 **캘리브레이션을 포기**한다 (임계를 지어내지 않음 — flow_store의 부분합 위장 금지와 같은 철학)
- **자동 청산에 쓰지 말 것** — 감시는 기계, 매매 판단은 사람 (중장기 수동매매 탭 성격 유지)

## 반증 전용 패스 (2026-09-07)
같은 컨텍스트에서 강세 논거를 쓴 뒤 무효화_조건을 이어 쓰면 **방금 세운 논리를 진지하게 공격하지 못한다**. 실사례: 증권사 리포트에서 "HBM4 판가 +70%"는 인용하고 같은 문단의 "경쟁사 대비 제한적 상승률 우려"는 버렸다 — 같은 숫자를 반대 프레임으로 읽은 것.
- `analyzer._run_falsification_pass`: 핵심_주장 + 스냅샷 + 공시/뉴스만 넘기고 **논거/장기_논거/밸류_코멘트는 감춘다**. 주장은 봐야 정밀하게 반박하고, 지지 논거 체인을 보면 거기에 끌려간다
- **근거 인용 강제**: 각 항목은 스냅샷 필드명 또는 dart_disclosures/news_recent의 실제 항목을 인용해야 한다. 인용 없는 일반론("경쟁 심화 가능성")은 앱이 제거 — 안 그러면 반증 섹션이 장식이 된다
- 결과 조건은 정규화→캘리브레이션→스크리닝을 거쳐 본 분석 조건에 병합(`origin: "반증"`, 총 `_MAX_CONDITIONS=8` 상한, `_cond_key`로 중복 제거). 실패해도 분석은 유효
- 비용은 분석당 Gemini +1회 — 수동/이벤트 트리거라 RPD 영향 없음
- **인용 문단 보존은 미채택**: 그라운딩 검색 원문을 통제할 수 없고 네이버 어댑터는 제목만 준다. 반증 패스가 같은 문제를 실질적으로 해결

## 청산 실패는 반드시 알림으로 드러낸다 (2026-09-10)
9/3 커밋(`try_claim` 도입) 후 **서버를 재시작하지 않아** 2026-09-08 14:53부터 9/10 11:08까지 실시간·폴링 청산이 **23,990회 연속 실패**했다. 로그에만 쌓이고 알림이 없어 아무도 몰랐다. 그 사이 018260 포지션이 손절선(-3%)을 뚫고 매달려 **-7.41%에 청산**됐다 (가상계좌라 실손실 없음, 다만 가상 성과 통계는 이 건만큼 오염 — 대조군 판정 시 장애분으로 제외할 것)
- **원인은 신구 코드 혼재**: `_close_position_sync`가 executor를 **함수 내부에서 지연 import** 한다. 프로세스는 9/3 18:56 기동이라 monitor는 구 클래스인데, 첫 청산 시점에 import된 executor는 20:34 수정된 새 소스 → 새 executor가 구 monitor의 없는 메서드(`try_claim`)를 호출. **커밋 후 재시작을 빼먹으면 "구 코드로 계속 돈다"가 아니라 "한 프로세스 안에서 신구가 섞인다"**
- **조치**: `monitor.record_close_failure()` — 실패 3회 연속 시 어드민 알림, 1시간 간격 재알림. 3개 경로(실시간/매도주문/폴링) 공유
- **앱 로그 INFO가 저널에 안 올라간다** — 로그 레벨이 WARNING 이상만 통과해 `RT closed`·`Monitoring N positions` 같은 정상 기록이 안 보인다. 그래서 이번 건도 ERROR라서 보인 것. 청산 성공 여부를 로그로 추적하려면 로깅 설정부터 손봐야 함 (미조치)

## Circuit Breaker
- 직전 4건 청산이 전부 손실이면 해당 유저 매수 자동 차단 (4건 미만은 체크 안 함)
- app_config: `cb_paused_{user_id}`, `cb_reason_{user_id}`
- 트리거 시 어드민 텔레그램 알림, 수동 해제만 가능
- GET /admin/circuit-breaker/status, POST /admin/circuit-breaker/resume/{user_id}
- GET /admin/realtime/status — KIS WS 연결 여부 + realtime_monitor 감시 종목 수

## KIS API 주요 엔드포인트
- `FHKST01010100` inquire-price: 현재가 + 시가/고가/체결강도(cttr)/거래량 + **per/eps/pbr/bps**(밸류, 응답에 동봉 — 추가 호출 불필요)
- `FHKST01010200` inquire-asking-price-exp-ccn: 호가 (askp1/bidp1) — 가상계좌 체결 시뮬레이션용 (`get_quote`)
- `FHKST03010100` inquire-daily-itemchartprice: OHLCV (일봉)
- `FHPUP02100000` inquire-index-price: 지수 현재가/등락률 (0001=KOSPI, 1001=KOSDAQ)
- `TTTC8434R` inquire-balance: 잔고 조회 (avg_price=pchs_avg_pric)
- `TTTC0802U` order-cash (매수): 시장가 주문
- `TTTC0801U` order-cash (매도): 시장가 주문
- `TTTC8001R` inquire-daily-ccld: 당일 주문 체결 조회 (실 체결가 확인용)
  - `get_today_fill_price(stock_code, side="02")` — side "02"=매수, "01"=매도
  - 매수 직후 entry_price, 매도 직후 exit_price에 실 체결가 반영
- `CTPF1002R` search-stock-info: 종목 기본정보 (섹터)
- `CTCA0903R` chk-holiday: 국내휴장일조회 — `is_market_open_day()` (날짜별 캐시). 휴장일엔 지수/호가 API가 직전 거래일 잔상을 반환하므로 시장 조치·가상 체결 전 필수 판정 (2026-07-20)
- `FHPST01740000` 시총순위: KOSPI200/KOSDAQ150 구성종목 근사치
- `FHKST66430300` financial-ratio: 분기 ROE/부채비율/EPS/BPS/성장률 (관심종목 탭)
- `FHKST66430200` income-statement: 분기 손익 — **YTD 누적**이라 단일 분기는 차분 필요
- `HHKST668300C0` estimate-perform: 컨센서스 추정실적 — output2/3 행 순서가 항목, 비율·EPS ×10 스케일, 목표주가 없음
- `FHKST03030100` 해외 종목/지수/환율 기간별시세: FID_COND_MRKT_DIV_CODE='X' + 'FX@KRW' = USD/KRW 일봉 (관심종목 환율 컨텍스트, 2026-07-02 실검증)
- `FHKUP03500100` 지수 일봉: **호출당 ~50행 제한** — get_index_daily_closes가 날짜 구간 청크 연속 조회로 보완
- `FHKST03010100` FID_PERIOD_DIV_CODE='M'으로 월봉 조회 가능 (PBR 5년 밴드 근사용, 1회 호출 60개월)

## 뉴스 감시 시스템
- **주기**: 장중(09:00~15:30) 40분마다 Gemini+검색그라운딩으로 체크
- **히스토리 컨텍스트**: 최근 15건 이벤트 + 실제 시장 영향이 프롬프트에 포함 → 판단 자동 보정
- **저장**: NORMAL 포함 모든 이벤트 news_events에 저장 (감지 시점 KOSPI/KOSDAQ 레벨 포함). 단 **체크 실패는 저장 안 함** (아래)
- **실패 처리** (2026-06-10 fail-open 교정): Gemini 호출/파싱 실패가 NORMAL(conf 0)로 저장돼 히스토리 오염 + 감시 공백을 은폐하던 버그 수정 — 20초 후 1회 재시도(같은 모델, 검색 그라운딩 유지, fallback 모델 없음), 최종 실패 시 `check_failed` 마커 반환 → 이벤트 미저장 + `news_consec_failures` 카운트, 3연속 실패 시 어드민 🚨 알림. 성공 시 카운터 리셋. 모닝게이트 체크 실패도 어드민 알림 (그날 매수가 게이트 평가 없이 진행됨을 가시화)
- **severity 정확도 (2026-06-10, 검증 145건)**: WARNING 양호(7/11 익일 -1% 적중), CRITICAL 과잉(13/33 적중, 16/33 익일 상승) — 듀얼시그널이 실조치를 막아 실해는 알림 노이즈. ai_confidence는 전부 0.9+로 변별력 없음(ai_probability와 동일 패턴) — calibration 로직 만들지 말 것
- **사후 검증**: 16:00 잡이 1일/3일 경과분의 실제 KOSPI/KOSDAQ 변화율 자동 계산
- **WARNING 감지 시**: news_auto_trade_paused=true + news_pause_at(KST 날짜) 기록 + 텔레그램 어드민 알림
  - **익일 자동 해제**: 다음 거래일 08:00 morning_gate가 news_pause_at ≠ 오늘이면 news_auto_trade_paused=false로 자동 해제 (WARNING은 시점 이벤트인데 수동 재개만 가능해 상시 지정학 노이즈로 무한 정지되던 문제 교정). 오늘 진짜 야간 리스크면 같은 게이트가 morning_gate_paused로 재차단 → 안전망 유지
  - 사용자 수동 정지(user_strategies.is_auto_trade=false)는 별개 레이어 — executor가 먼저 검사, 글로벌 해제와 무관하게 유지됨

## 실시간 WebSocket
- **서버사이드 포지션 모니터** (`realtime_monitor.py`): 프론트 연결 무관하게 HOLDING 포지션 종목 상시 KIS 구독
  - 서버 시작 시 `load_all()` → HOLDING 전부 인메모리 등록 + KIS H0STCNT0 구독
  - 매 가격 틱: bid_price 기준 손절가/목표가 즉시 체크 → 조건 충족 시 `asyncio.create_task`로 즉시 청산
  - 10분 폴링은 만료/time-based stop 처리 + WebSocket 끊김 구간 fallback으로 유지
  - 중복 청산 방지: `_closing` set + DB `status != HOLDING` 체크
  - `core/loop.py`: APScheduler 스레드 → async 루프 브리지 (`run_coroutine_threadsafe`)
- **KIS WS 안정성**: `ping_interval=None` + 30초 자체 하트비트 (`ws.ping()`) — 서버 idle 끊김 방지
  - KIS 자체 PINGPONG 텍스트 프로토콜 별도 처리 (`_handle`에서 PONG 응답)
  - 끊기면 5초~60초 백오프 후 재연결, 재연결 시 `_subscribed` 전체 자동 재구독
  - 상태 조회: GET /admin/realtime/status (kis_ws_connected, subscribed_codes, monitor_holding_count)
- **가격 스트림**: H0STCNT0 → /ws/prices 엔드포인트 → 프론트 포지션 페이지 LIVE 표시
  - H0STCNT0 필드: [0]코드, [2]현재가, [3]전일대비부호, [4]전일대비, [5]등락률, [11]매수호가1(bid), [13]누적거래량
  - 프론트 미실현 손익: bid_price 기준 계산 (시장가 매도 실체결 기준), 퍼센트+원화 금액 표시
  - 삼성전자(005930) 항상 구독 → 포지션 없어도 프론트 WS 헬스체크 가능
  - LIVE 배지 2개: 구독(프론트 WS), 서버(KIS WS + realtime_monitor 감시 종목 수, 30초 폴링)
  - API/WS 주소는 접속 호스트 기준 자동 유도 (고정 필요 시 .env.local NEXT_PUBLIC_WS_URL)
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
DART_API_KEY=            # 선택 — 관심종목 공시 어댑터 (미설정 시 data_flags 폴백)
NAVER_CLIENT_ID=         # 선택 — 관심종목 뉴스 어댑터
NAVER_CLIENT_SECRET=
KRX_API_KEY=             # 선택 — KRX 오픈API (일별 전종목 벌크 적재)
```
- KIS API 키/계좌번호는 .env 사용 안 함 → DB broker_accounts에 Fernet 암호화 저장
- HTS 아이디는 DB broker_accounts.hts_id (프론트 포지션 페이지 > 계좌 설정에서 입력)
- **frontend/.env.local (git 미추적)**: `ALLOWED_DEV_ORIGINS=<쉼표구분 호스트>` — next.config.ts allowedDevOrigins의 실값. 새 접속 경로(도메인/IP) 추가 시 여기에 등록 후 프론트 재시작

## 주의사항
- **레포는 GitHub 공개 (2026-07-16 전환, 포트폴리오용)** — 커밋에 실계좌번호·API 키·내부 IP/호스트명 절대 금지. 호스트 고유 값은 git 미추적 .env.local에만. 공개 전 filter-repo로 히스토리 치환 완료 상태를 되돌리는 커밋 금지
- API 키는 절대 코드에 하드코딩 금지
- broker_accounts의 api_key, api_secret은 Fernet 암호화 (security.py)
- 모든 금액/수량은 Decimal 타입 사용 (float 금지)
- 자동매매 실행 전 is_auto_trade + news_auto_trade_paused + cb_paused_{user_id} 플래그 확인
- raw_response['macro']['market_theme'] 에서 하락장 판단 (MacroAnalysis 모델에는 없음)
- 매수 스킵 fallback: AI 확인 실패 시 전종목 skip (안전 방향)
- `get_index_change_pct()`는 실패 시 해당 지수 None 반환 (0.0으로 위장 금지) — 호출부는 None을 "확인 불가"로 보고 안전 방향 처리 (09:20 매수: 전체 스킵+알림 / 듀얼시그널: 어드민 수동확인 알림). 2026-06-10 fail-open 교정
- 하락장 매수금 절반은 `execute_buys_for_run(invest_override=...)` 일회성 파라미터 사용 — `sub.invest_amount_per_pick` 모델 직접 수정 금지 (중간 커밋/예외 시 구독 설정 영구 오염)
- HTTP 클라이언트: 전체 코드 httpx 통일 (requests 사용 금지)
- KIS API rate limit: client.py `_RateLimiter(18/초)` 전역 싱글턴 — _get/_post 모든 호출 자동 적용
- KIS 토큰 캐시: `~/.kis_token_cache.json` (재부팅 후에도 유지). `get_kis_client_from_account()`는 account_id 기준 싱글턴 반환 — 인메모리 토큰 공유로 중복 발급 방지
- 매도 후 exit_price: 반드시 `get_today_fill_price(side="01")`로 실 체결가 조회 (현재가 사용 금지)
- 수동 매수 + 전략 선택 시 실제 자동 청산 편입 (monitor_positions가 HOLDING 전체 순회)
- _check_position(): rec 없으면 strategy.target_pct × entry_price로 목표가 계산 (수동매수 포함)
- 전략 없이 수동매수 시 자동 청산 미작동 — 수동매수는 반드시 전략 선택 필요
- _enrich(): rec_id 없어도 strategy.target_pct × entry_price로 익절가 계산
- systemd 서비스: trading-backend (uvicorn), trading-frontend (npm run dev) — WSL2 부팅 시 자동 시작
- **코드 수정 후 백엔드 재시작 필수** (`sudo systemctl restart trading-backend`) — 지연 import 때문에 재시작을 빼먹으면 한 프로세스에 신구 코드가 섞여 `AttributeError`로 청산이 조용히 죽는다 (2026-09-08 사례). 프론트는 dev 모드라 자동 반영
- 목표가 도달 시 즉시 TARGET_HIT 청산 (기본, AI thesis 완료 기준)
- `Strategy.use_trailing_stop=true`이면 목표가 후 peak 추적 → peak × (1 - stop_loss_pct%) 이탈 시 청산
- 손절: entry_price × (1 - stop_loss_pct/100) 고정선 (trailing 모드는 peak 기준)
- 전략 검증: pick_count≤4, 일평균≤0.7%/일, R/R≥1.5 (API+프론트 동일 기준). **확률 하한 기준은 없다** — 2026-09-10 제거

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

### 원칙 4: 실무 대비 — 일상 명령은 사용자가 직접 치게 유도
사용자가 실무 투입 대비로 터미널 조작을 손에 익히는 중이다. **일상 운영 명령(git push, systemctl 재시작/상태, journalctl·psql 조회, 패키지 설치 등)은 Claude가 대신 실행하지 말고, 정확한 명령어 + 한 줄 설명을 제시해 사용자가 직접 치게 유도**한다 (세션 안에서는 `! <명령>` 프리픽스 안내).
- 사용자가 친 명령이 실패하면 원인 설명 + 수정 명령 제시 (학습 기회로 활용)
- 예외: 대량 반복 작업, 복잡한 파이프라인/디버깅 루프, 원칙 1의 정리 커밋 흐름, 사용자가 "직접 해줘"라고 명시한 경우는 Claude가 수행

## 파일별 핵심 함수 요약

### app/main.py
- `lifespan()`: 앱 시작 시 루프 저장 → KIS WS 초기화 → 모니터 콜백 등록 → 포지션 로드 → 스케줄러 시작. 종료 시 역순 정리
- `_init_realtime_client()`: broker_accounts에서 REAL 계좌 조회 → KISRealtimeClient 초기화 + H0STCNI0 구독
- `_update_fill_price()`: H0STCNI0 체결통보 콜백 → hts_id로 계좌 조회 → 해당 유저 오늘 포지션 entry_price/peak_price 업데이트

### app/core/
- `config_store.py`: `get_config(db, key)` / `set_config(db, key, value)` — app_config 테이블 key-value 읽기/쓰기
- `loop.py`: `set_loop()` / `get_loop()` — APScheduler 스레드에서 async 함수 호출 시 `run_coroutine_threadsafe`에 넘길 루프 저장
- `security.py`: `hash_password` / `verify_password` (bcrypt, 72바이트 truncate), `create_access_token` / `decode_access_token` (JWT), `encrypt_secret` / `decrypt_secret` (Fernet)

### app/services/kis/client.py
- `KISClient`: httpx 기반 KIS API 래퍼. `_RateLimiter(18/초)` 전역 싱글턴으로 모든 호출 자동 적용
- `_ensure_token()`: `~/.kis_token_cache.json` 파일 캐시 우선, 만료 시 신규 발급. `_token_issue_lock`으로 동시 발급 차단
- `get_current_price(code)`: FHKST01010100, 현재가만 반환
- `get_price_with_change(code)`: 현재가 + 시가 + 등락률 + bid_price (프론트/모니터용)
- `get_intraday_status(code)`: 시가/고가/체결강도/거래량 (09:20 장중 체크용)
- `get_index_change_pct()`: KOSPI(0001)/KOSDAQ(1001) 등락률 — 매수 전 -2% 체크. **휴장일엔 직전 거래일 값을 그대로 반환하므로 시장 조치 전 is_market_open_now() 필수**
- `is_market_open_day(date)` / `is_market_open_now()`: CTCA0903R 휴장일 조회(날짜별 캐시) + KST 평일 09:00~15:30. 조회 실패 시 개장 간주 (보호 조치를 막지 않는 fail-safe)
- `_get_domestic_stock_info(code)`: 현재가+RSI+이평선+외국인/기관 순매수+**per/eps**+**close_high_20d/turnover_ratio**(규칙 전략용, 이미 받은 일봉에서 계산 — 추가 호출 없음) 통합 (runner 종목 데이터 수집용). inquire-price 1회 호출로 price+per+eps 동시 추출 (get_current_price 중복 호출 제거). per/eps는 **trailing(직전 공시 실적) 기준** — forward 추정 서사와 다른 값. 적자기업·데이터없음은 None(0/음수 위장 금지). eps는 KIS가 "6564.00" 문자열 반환 → int(float()) 파싱
- `get_index_daily_series(code, days)`: 지수 일봉 **(날짜, 종가)** 최신순 — 국면 소급 계산용. `get_index_daily_closes`는 종가만 뽑는 얇은 래퍼
- `get_stock_basic_info(code)`: CTPF1002R 섹터 조회 (매수 직전 MAX_PER_SECTOR 체크용)
- `get_fx_daily_closes(symbol='FX@KRW', days)`: USD/KRW 환율 일봉 (FHKST03030100, mrkt div 'X')
- `get_ohlcv_monthly(code, months)`: 월봉 — 1회 호출로 60개월 (PBR 5년 밴드 근사용)
- `get_foreign_holding(code)`: inquire-price 동봉 — 외인 소진율 + per/pbr/eps/bps + 시총(market_cap_eok, 억원)
- `get_today_fill_price(code, side)`: TTTC8001R 당일 체결 조회. side="02"=매수, "01"=매도. 매수/매도 직후 실 체결가 반영에 사용
- `buy_market_order(code, qty)` / `sell_market_order(code, qty)`: TTTC0802U / TTTC0801U 시장가 주문
- `get_quote(code)`: FHKST01010200 호가 — {ask1, bid1} (0이면 None). 가상계좌 체결가 산정용
- `get_kis_client_from_account(account)`: account_id 기준 싱글턴 반환 (`_client_registry`). 동일 계좌는 항상 같은 인스턴스 → 토큰 공유. **VIRTUAL 계좌는 RuntimeError — get_trading_client 사용**

### app/services/kis/realtime.py
- `KISRealtimeClient`: KIS WebSocket(H0STCNT0 가격 + H0STCNI0 체결통보) 클라이언트
- H0STCNT0 필드: [0]코드 [2]현재가 [3]부호 [4]전일대비 [5]등락률 [11]bid_price [13]누적거래량
- H0STCNI0 필드: [0]hts_id [4]매수/매도구분(02=매수) [7]종목코드 [8]수량 [9]체결단가 [12]체결여부(1=체결)
- 재연결: 5초~60초 백오프, 재연결 시 `_subscribed` 전체 자동 재구독
- 하트비트: 30초마다 `ws.ping()` (KIS 서버 idle 끊김 방지)
- `init_realtime_client()` / `get_realtime_client()`: 앱 전역 싱글턴

### app/services/gemini/analyzer.py
- `GeminiAnalyzer`: 4단계 Gemini 파이프라인 + fallback 체인 관리
- `stage1_macro()`: gemini-2.5-flash + google_search → MacroResult (macro_summary, key_factors, market_theme, sector_outlook)
- `stage2_historical()`: gemini-3-flash-preview → HistoricalResult (유사 과거 시기 3개)
- `stage3_industry()`: gemini-3.1-flash-lite → IndustryResult (섹터별 outlook)
- `stage4_picks(stock_data, ..., selection_mode)`: 2단계 — A(flash-preview 자유형식 분석) → B(flash-lite 코드 추출). 그룹 분할은 runner가 담당. selection_mode=earnings_catalyst면 STAGE4A_EARNINGS 프롬프트 사용(아니면 STAGE4A_ANALYSIS)
- `detect_earnings_catalysts(stocks_data)`: 사전필터 종목 대상 최근 실적 카탈리스트(서프라이즈/가이던스 상향/추정치 상향) **1회 그라운딩** 탐지 → {code: note}. 검색 확인 사실만(없으면 빈 목록), 실패 시 빈 dict로 fail-safe(모멘텀 기준 진행). earnings_catalyst 전략 전용
- `_call_with_fallback(prompt, chain)`: 모델 체인 순서대로 시도, 성공 시 (text, model_used) 반환
- `_parse_json(text)`: JSON 파싱 실패 시 gemma-4-31b-it로 재시도

### app/services/trading/runner.py
- `StrategyRunner.run_strategy(strategy)`: 전체 파이프라인 조율 — 종목 샘플링 → KIS 수집 → Gemini 4단계 → DB 저장 → 텔레그램
- `_sample_from_master(strategy)`: candidate_filter/market 기준 stock_master에서 50~200개 샘플링
  - largecap: KOSPI200/KOSDAQ150 시총 내림차순 상위 90% + stride 10%
  - volume: KIS 시총순위 API 실시간
  - mixed: largecap 우선 + stride
- `_collect_stock_data(candidates)`: KIS API로 종목별 현재가/RSI/이평선/수급/per·eps 수집 + stock_name DB 주입
- `_prefilter_stocks(stock_data, n=20)`: 추세정배열·RSI(~60)·수급·거래량 점수로 75개→20개 압축 (모멘텀 리더 보존 + Stage4 컨텍스트 축소)
- `_run_stage4_grouped(...)`: 20개를 10개씩 2그룹 분할 → 각 그룹 Stage4 독립 실행 → 확률순 집계. selection_mode=earnings_catalyst면 사전필터 직후 `detect_earnings_catalysts` 1회 호출 → stock_data에 earnings_catalyst 주입 후 stage4_picks에 mode 전달
- `_is_market_unfavorable(market_theme)`: `_BEAR_KEYWORDS` 감지 → Stage4 스킵 여부 (A-gate, 검증 20건+ 시 활성화)
- `_run_rule_strategy(strategy, run_date, stock_data, selection_mode)`: 규칙 기반 경로. `run_strategy`가 데이터 수집 직후 `is_rule_mode()`로 분기 → Stage1~4 전체 스킵 → `select_by_rule` → 공용 저장
- `_persist_and_notify(strategy, run_date, stock_data, picks_result, *, macro/historical/industry/stage4_skipped)`: **AI·규칙 경로 공용** — 지수 레벨 + 시장 국면 + 랜덤 대조군 기록 → run/추천 저장 → 텔레그램. macro 등이 None이면 규칙 전략(MacroAnalysis 저장 생략)
- `_RANDOM_BASELINE_N = 40`: 랜덤 대조군 표본 크기. pick_count와 무관 (벤치마크는 실매매가 아님)

### app/services/trading/market_regime.py
- `fetch_kospi_history(client, days)`: KOSPI 일봉 (날짜, 종가) 최신순. **소급 계산 시 1회만 호출해 재사용할 것**
- `regime_from_history(history, as_of, ma_period=20)`: as_of 당일 봉 제외(미래 정보 차단) → `{close, ma20, state}`
- `compute_regime(client, as_of)`: 라이브 경로. 실패 시 None 반환 — 국면 기록 실패가 분석을 막지 않는다

### app/services/trading/rule_selector.py
- `is_rule_mode(selection_mode)` / `select_by_rule(mode, stock_data, pick_count)`: 규칙 선정 진입점
- `select_breakout`: 20일 신고가 갱신 + 종가>MA5, 돌파 폭 순 / `select_oversold`: RSI≤35 + 거래대금 전일 1.5배, RSI 낮은 순
- 조건 미충족이면 **빈 목록 반환이 정상** — pick_count 채우려 기준을 낮추지 말 것
- 임계값(`_OVERSOLD_RSI_MAX`, `_OVERSOLD_TURNOVER_MIN`)은 발동 빈도 확보용 완화값. 데이터 쌓이면 조인다

### app/services/trading/executor.py
- `TradeExecutor.execute_pending_buys()`: 09:20 잡 진입점. 플래그 체크(morning_gate/news/cb) → 지수 -2% 체크 → 크로스 시그널 계산 → 전략별 매수
- `execute_buys_for_run(run, user_strategy)`: 추천 목록 정렬(유효확률+크로스보너스) → 섹터/RSI/R:R 필터 → 시장가 매수 → 실 체결가 반영 → Position 저장 → 모니터 등록
- `monitor_positions()`: HOLDING 포지션 순회 → 만료/time-based stop 처리 (손절/익절은 realtime_monitor가 우선)
- `_check_position(pos)`: 목표가/손절가 계산 + trailing 모드 체크 + 타임아웃(+1거래일 신고점 없으면 TARGET_HIT) 처리
- `_close_position(pos, status, price)`: 시장가 매도 → 1초 대기 → `get_today_fill_price(side="01")`로 실 체결가 → exit_price 저장 → 모니터 제거
- `_close_position(pos, ...)`: **매도 전 `monitor.try_claim()`으로 선점** — 폴링 청산과 실시간 청산이 동시에 매도 주문을 내던 레이스 차단. 주문 실패 시 `release()`로 해제 (2026-09-03)
- `_check_circuit_breaker(user_id)`: 직전 4건 청산 전부 손실 시 cb_paused 플래그 설정
- `emergency_close_all_positions(reason)`: 전략 HOLDING 포지션 즉시 청산 (뉴스 CRITICAL+KOSPI -2% 시). 무전략 수동매수는 제외 + 소유자 알림만 (`_notify_manual_positions_excluded`)
- `tighten_stop_losses(reason)`: 수익 중 전략 포지션 현재가 기준 trailing 전환 (뉴스 WARNING+KOSPI -1% 시). 무전략 포지션 제외 (strategy 없인 손절선 계산 불가)
- `_build_cross_signal(db)`: 오늘 전략 추천 집계 → 종목별 다양성 점수 계산
- `_cross_signal_bonus(code, signal)`: 점수 1.0→+7%, 0.5→+3.5%, 상한 +10%

### app/services/trading/realtime_monitor.py
- `RealtimePositionMonitor`: 싱글턴. HOLDING 포지션 인메모리 관리 + KIS 가격 틱 실시간 손절/익절
- `load_all()`: DB에서 HOLDING 전부 → `PositionWatch` 생성 → KIS H0STCNT0 구독
- `on_price(code, price_data)`: 매 틱 bid_price 기준 `_should_close()` → 조건 충족 시 `asyncio.create_task`로 즉시 청산
- `_should_close(watch, price)`: 손절가 이탈 → "stop_loss", 목표가 도달(trailing OFF) → "target_hit", trailing ON → peak 갱신 또는 trailing 손절
- `force_trailing(position_id, peak_price)`: DB + 인메모리 동시 trailing 전환 (뉴스 조치 시 사용)
- `try_claim(position_id)` / `release(position_id)`: 청산 선점/해제. **executor 폴링 청산도 반드시 이걸 거쳐야 한다** — 증권사 잔고 거절이 2차 방어지만 가상계좌엔 그 방어가 없다
- `record_close_failure(position_id, code, error, source)` / `clear_close_failure(position_id)`: 청산 실패 누적 → **3회 연속이면 어드민 🚨 알림**, 지속 시 1시간 간격 재알림. 실시간(`_close_position_sync`)·매도주문 실패·폴링(`monitor_positions`) 3개 경로가 공유. 알림 실패가 청산 경로를 막지 않게 try/except로 감쌈. 카운터는 인메모리(재시작 시 리셋) — 배경은 아래 '청산 실패는 반드시 알림으로 드러낸다'
- `add(watch)` / `remove(position_id, code)`: 매수/청산 시 executor가 호출해 동기화 (remove는 선점 플래그도 정리)

### app/services/trading/market_keywords.py (2026-09-03)
매크로 서술 기반 판단 키워드의 단일 관리 지점. 예전엔 runner와 executor에 따로 박혀 조용히 드리프트했다.
- `BEAR_KEYWORDS` / `is_bearish()`: A-gate — "너무 나빠서 분석조차 안 함" → Stage4 스킵
- `CAUTION_KEYWORDS` / `is_cautious()`: 매수 감액 — "분석은 했지만 조심" → 매수금 50%
- **두 리스트를 같게 만들지 말 것**: A-gate가 먼저 같은 market_theme로 Stage4를 스킵하면 추천이 0개라 감액 로직이 도달 불가능한 죽은 코드가 된다. 감액은 A-gate가 안 잡는 약한 신호를 잡아야 의미가 있다
- executor 구 리스트의 **"위험"·"하락"은 제거** — "위험선호 회복", "위험자산 선호 개선", "하락 압력 완화"는 한국 시장에서 **강세** 표현이라 강세 국면에 매수금을 반토막 내는 오탐. 부분 문자열 매칭이라 더 위험했다

### app/services/news/watcher.py
- `check_news(db)`: gemini-2.5-flash + google_search → severity 판정. 실패 시 20초 후 1회 재시도, 최종 실패 시 `check_failed` 마커 반환 (NORMAL로 위장 금지)
- `morning_gate_check()`: 08:00 실행. 시작부에서 전날 켜진 news_auto_trade_paused stale 자동 해제(news_pause_at ≠ 오늘 KST) → **QQQ 전일 등락률 수치 게이트**(-1.5% WARNING / -3% CRITICAL, `get_us_price_with_change("QQQ","NAS")`, LLM 비의존) + Gemini 미국선물/지정학 체크 → 둘 중 나쁜 쪽 채택 → WARNING/CRITICAL 시 `morning_gate_paused=true`. Gemini 실패해도 수치만으로 차단 가능(fail-safe), `morning_gate_last_check`에 QQQ 실측 vs LLM 수치 기록 (게이트 정확도 사후검증용, date는 KST 거래일 — UTC로 쓰면 08:00 KST 실행 시 전날로 밀림)
- `run_news_check_and_act()`: 스케줄러에서 호출. 장중 120분 간격 체크 (10분 tick 기반). `check_failed`면 이벤트 저장 스킵 + 연속실패 카운트 + 3연속 시 어드민 알림
- `_apply_dual_signal_action(db, result)`: AI 판정 × KOSPI 등락률 교차 검증 → emergency_close / tighten_stop / 알림만. 휴장/장외 무조치 + 당일 재발동 억제(1%p 추가 악화 시만 재청산)
- `check_position_theses(db)`: 10:00/14:00. 2일+ HOLDING 포지션 8개씩 그룹 → gemini-2.5-flash + google_search thesis 재검증 → invalid+손실 시 조기 청산
- `verify_news_events(db)`: 1일/3일 경과 이벤트에 실제 KOSPI/KOSDAQ 변화율 기록
- `verify_run_market_outcomes(db)`: 전날 recommendation_runs의 kospi_change_1d 채움 (A-gate 데이터 축적)
- `_build_history_context(db)`: 최근 15건 이벤트 + 최근 5일 이슈 키워드 → 프롬프트 주입 (중복 감지 억제)

### app/services/trading/scheduler.py
- `start_scheduler()`: APScheduler 설정 + 전체 잡 등록 (KST 기준)
- `run_startup_catchup()`: 서버 재시작 시 누락 분석/검증/stock_master 자동 보완
- `_should_run(db, strategy)`: run_interval_days 경과 여부 체크

### app/services/trading/verifier.py
- `simulate_exit_pnl(bars, entry, target_pct, stop_loss_pct, period_start, period_end)`: **청산 모델 단일 진실 공급원**. 손절-우선 + 만기 종가 + 목표/손절가 정수 반올림 + ±클램프. AI 검증·랜덤 대조군·백테스터가 전부 이 함수를 쓴다 — 청산 로직을 다른 곳에 새로 짜면 예전처럼 한쪽만 드리프트한다
- `_verify_random_baselines(db, client, today)`: 랜덤 대조군 pnl 계산. AI와 **동일 청산 규칙**(위 함수) 적용. 표본은 run 저장 시 기록된 entries(40개)
- `run_verifications(db)`: 검증 대상(verification 없음 + run_date+hold_days ≤ today) 순회 → `_verify_recommendation()`
- `_verify_recommendation(rec, run, strategy, client, today)`: 일봉 날짜순 순회 → 손절/목표가 중 먼저 터치되는 쪽 판정 (같은 날이면 손절 우선). pnl_pct는 실제 exit_price 기준. period_start/end는 반드시 strftime("%Y%m%d") — get_ohlcv() bar.date가 YYYYMMDD 포맷이므로 ISO 포맷과 혼용 금지
- `_update_performance_score(db, version_no)`: 검증 완료 후 prompt_version.performance_score 갱신

### app/services/trading/backtester.py
- `BacktestRunner(db, version_tag)`: 과거 날짜 Stage4 재현 + 즉시 검증. version_tag로 run 라벨링(A/B 비교용)
- `run_backtest(strategy, base_date)`: base_date ±12일/3일간격 최대 9개 날짜 실행 → 집계
- `_run_single_date()`: 과거데이터 수집 → **`StrategyRunner._prefilter_stocks` 적용(라이브 경로 일치)** → `stage4_picks_backtest` → 저장 → 검증. target_price/stop_loss_price는 picks에 없으므로 진입가×전략 파라미터로 산출(확률·목표가 폐기 이후 필수)
- `_verify_pick()`: 일봉 날짜순 손절-우선 청산 모델(verifier.py와 동일 convention). pnl 비현실값(분할/상폐) ±클램프(-100~+200%)
- `_compute_random_baseline(stock_data, target_date, strategy, client)`: 동일 풀 랜덤 표본(`_RANDOM_BASELINE_N`) + `verifier.simulate_exit_pnl` 공용 청산 적용(공정 비교)
- **구조적 한계**: 매크로를 스텁(`market_theme="백테스트"`)함 — Stage1 그라운딩은 현재시점이라 과거날짜 lookahead 방지. 따라서 **매크로 정합 효과는 백테스트로 측정 불가, 기술기준만 평가**됨

### app/services/watchlist/analyzer.py
- 관심종목 분석 서비스 (스펙: docs/watchlist_spec.md). AI = 데이터 집계+구조화, 예측 금지
- `collect_input_snapshot(client, code, name, sector, db=None)`: 6개월 일봉 요약 + 분기 재무(YTD→단일분기 차분) + 컨센서스 추정 + 수급 30거래일 + **환율 3개월 추세 + KOSPI 상대수익률 + PER 4종 병기 + 수급 페이스 판정 + PBR 5년 밴드** + data_flags(결측 명시) → 이 dict가 그대로 프롬프트 입력 + DB 저장 (사후 재구성 보장). db 넘기면 수급 적재 + 60/120일 누적 포함
- `_pace_judgment(avg5, avg30)`: 수급 가속/둔화/전환 판정 문자열 생성 — LLM에 나눗셈 시키지 않기 위해 앱이 확정. 30일 평균 미미하면 중립 취급(비율 폭주 방지), ±20% 밴드 내 "페이스 유사", 부호 전환은 별도 라벨
- `_valuation_scenarios(...)`: **밸류 시나리오 역산 (2026-08-28)** — `현재가 × 목표배수 ÷ 현재배수`로 PER/PBR 밴드 회귀 시 함의주가 산출(주식수 비경유). 상단만 내면 편향이라 하단 대칭 생성. **예측 아닌 산술** — LLM은 표에서 논거와 정합적인 행을 고르고 전제를 밝힐 뿐(목표주가 생성 금지, 프롬프트 규칙 8). 경고 3종을 앱이 결정론 판정: peak_earnings(TTM 이익 > 과거 실적연도 최고) / 영업외 요인 / 장부가 시점차. **행 단위 신뢰도 강등**: 이익 피크면 PER 행 전체("피크 이익 × 바닥 시기 배수 = 이중 계상"), 장부가 괴리 15%+면 PBR 행 전체. 밴드는 min/max 대신 **p20/p80**(사이클 종목의 PER 밴드 상단은 이익 바닥에서 형성 — 하이닉스 실측 77배 → +936%). **`현재 배수 유지 × 연도별 컨센서스` 행은 과거 배수를 안 써서 무플래그** — 사이클 정점에서 유일하게 성립하는 역산이자 "현재 상황 유지 시 얼마까지"의 직답
- `_valuation_bands(client, code, pbr_now, per_now)`: PBR·PER 5년 밴드를 **1회 조회로 동시 산출** (연간 재무 + 월봉 각 1회를 공유). PER 밴드는 적자 연도 제외(음수 PER 오염 방지). `_pbr_band_5y()`는 이 함수의 얇은 래퍼 — 무효화_조건 valuation 체크 전용. **KIS estimate의 per 행이 통째로 비는 종목이 있어**(2026-08-28 SK하이닉스 실측) 자체 밴드가 1차, estimate per는 보조. forward PER도 없으면 `현재가 ÷ 컨센 EPS`로 자체 산출
- `_pbr_band_5y()`: 월별 종가 ÷ 당시 최근 연간 BPS → 현재 PBR의 5년 퍼센타일 (자사주 소각/증자 왜곡 가능 — 근사 명시)
- 프롬프트 규칙 8~11 (2026-08-28): valuation_scenarios는 인용만(warnings 무시한 상단 인용 금지) / 날짜는 dart_disclosures의 rcept_dt에서만 — **정기보고서 법정 제출기한 ≠ 실적 발표일**(16:30 캘린더 기한을 발표일로 착각한 사례) / 단기_촉매는 기준일 이후 이벤트만(발표 완료 건은 논거 배경) / 장기 논거 이벤트가 단기 수급에 반대로 작용하는지(희석·보호예수 해제) 검토 강제
- 프롬프트 규칙: 앱 계산 파생지표(judgment/상대수익률/trend_note/per_ttm/퍼센타일) **재계산 금지, 그대로 인용** / per_trailing 왜곡 시 per_ttm·forward 우선 / 환율=외인 수급 공통 팩터로 종목 고유 요인과 구분
- **뉴스 최신성 가드 (2026-07-03)**: 프롬프트에 14일 창 앵커 + 주가 변동 동인 필수 검색(앱이 1개월/당일 수치 확정 주입) / 파싱 후 14일 내 기사 0건이면 재검색 1회 → 그래도 없으면 `data_flags.news_recency` 명시 후 저장(억지 인용 강제 안 함). 배경: 그라운딩이 앵커 없이는 구 자료로 수렴 (7/2 분석 = 4월 기사 재탕)
- **외부 데이터 어댑터 (2026-07-03)**: 스냅샷에 `dart_disclosures`(DART 공식 API 최근 14일 공시 — 확정 데이터) + `news_recent`(네이버 뉴스 최신순 10건, 제목 중복 제거) 주입. Gemini 검색은 "발견"이 아닌 해석·시장 반응·내용 보강 담당으로 역할 조정. 어댑터 실패 시 분석 안 죽고 data_flags 기록 (공시 "0건"은 실패가 아닌 확정 사실 — status 013은 available=True). DART는 티커가 아닌 8자리 corp_code 사용 — corpCode.xml 매핑을 `~/.dart_corp_code.json` 캐시(30일 TTL, 미매핑 시 강제갱신하되 24h 최소간격)
- `run_analysis(db, user_id, ...)`: 수집 → gemini-2.5-flash 검색 그라운딩 → JSON 파싱 → StockAnalysis 저장. **무효화_조건 비면 1회 강제 재요청** 후 실패 시 ValueError. 조건 파이프라인 순서 = `normalize_conditions`(구조 검증) → `calibrate_conditions`(임계 재계산) → `screen_conditions`(품질 게이트, 결함 시 1회 재요청 — 재요청분도 캘리브레이션 통과) → `_run_falsification_pass`(반증 병합). 완료 시 조건 감시 안내 텔레그램 (best-effort)
- `_run_falsification_pass(db, analyzer, result, snapshot, ...)`: 핵심_주장만 넘긴 반증 전용 호출 → `result["반증_관점"]` 저장 + 무효화_조건 병합. 근거 미인용 항목 제거, `_cond_key`로 중복 제거. result를 제자리에서 갱신
- **무효화_조건 구조화 (2026-07-16)**: `{조건, check_type, params}` 객체 배열 — flow(수급 연속일/누적액)/fx(환율 레벨)/valuation(PBR 5년 퍼센타일)/earnings(대상 분기 실적, 공시 후 판정)/consensus(분석 시점 컨센 대비 하향%)/manual(정성, 확인_방법 명시). params 임계값은 입력 데이터에서 도출 강제, manual에 날짜 지어내기 금지

### app/services/watchlist/invalidation.py
- 무효화_조건 자동 판정 — **판정은 앱이 결정론적으로, LLM 관여 없음** (추가 Gemini 호출 0회)
- `normalize_conditions(raw)`: LLM 출력 검증 — 문자열(구 포맷)/스펙 불완전 조건은 오판 대신 manual 강등 (spec_note 기록)
- `evaluate_condition(...)`: 타입별 체크 → (state, detail). state: ok/triggered/**pending_data**(미공시 분기·수급 커버리지 부족 — 부분 데이터로 단정 금지)/manual/error
- `check_analysis(...)`: 최신 분석 1건의 조건 전체 판정 → condition_status 갱신, **미충족→충족 전이만** 반환 (경계 왕복 노이즈 방지, 해제 후 재충족은 재알림)
- `screen_conditions(db, code, snapshot, conditions)` / `downgrade_rejected(...)`: **조건 품질 게이트 (2026-08-28)** — 구조는 유효하나 감시 가치가 없는 조건을 결정론으로 탈락(추가 API·LLM 0회). ①이미 충족/임계 70% 도달(예정된 사건) ②earnings YoY 임계 |100%| 초과(기저효과 종속) ③valuation `below`(싸지는 건 강세 논거 반증 아님) ④fx 임계가 최근 1개월 진폭 안 ⑤과거 창 발동률 30% 초과/0% ⑥fx 3.5σ 밖 ⑦분기 마진 3σ 밖 ⑧컨센 하향 25% 초과 (⑤~⑧은 "절대 안 켜지는 조건"을 막는 반대편 꼬리, 2026-09-07). 탈락분은 사유와 함께 1회 재요청 → 그래도 남으면 삭제 아닌 manual 강등
- `check_all_watchlist_invalidations()`: 16:20 잡 진입점 — 관심종목 순회(삭제된 종목 자동 제외), 환율은 공통 팩터라 1회만 조회, 전이 시 소유 유저에게만 텔레그램. 수동 트리거: POST /admin/invalidation-check/trigger
- `send_condition_notice(...)`: 분석 완료 시 자동 감시 대상/수동 확인 필요 조건 1회 안내
- consensus 기준값은 input_snapshot.consensus_estimate (분석 시점) — 사후 재구성 데이터 재사용
- **자동 청산 없음** — 감시는 기계, 매매 판단은 사람 (중장기 수동매매 탭 성격 유지)
- analysis_date는 라벨 — KIS 입력은 항상 수집 시점 (snapshot.collected_at 기록)
- 1차 범위: 수동 트리거만. 이벤트 자동 감지(실적/공시/수급·주가 급변)는 후속. 공매도 잔고는 미구현(KIS 일별추이 API 있어 후속 가능), 대차잔고는 KIS 미제공

### app/services/watchlist/calibration.py
- 무효화_조건 임계 캘리브레이션 — **임계는 LLM이 아니라 앱이 정한다** (설계 배경은 위 "무효화_조건 임계 설계 원칙")
- `calibrate_conditions(db, code, snapshot, conditions)`: 타입별 임계 재계산 → params 교체 + `조건` 텍스트 재생성 + `calibration` 근거 기록. 표본 부족·데이터 결측이면 **손대지 않는다**
- `flow_series(db, code, investor)`: 확정 수급만 최신순 반환 — **모든 수급 창 계산의 단일 진입점**. NULL 행이 창을 먹지 않게 하는 것이 요점
- `rolling_sums` / `fire_rate` / `streak_rate`: 발동률 계산 — 캘리브레이션과 품질 게이트가 공유
- `condition_text(check_type, params)`: params에서 조건 서술 결정론 재생성 (임계-텍스트 불일치 방지)
- 상수: `TARGET_FIRE_RATE=0.10` / `FIRE_RATE_MAX=0.30` / `MIN_WINDOWS=30`. 꼬리가 조건 방향과 반대면(순매도 조건인데 p10조차 순매수) 캘리브레이션 포기 — abs()로 뒤집으면 상시 발동 임계가 나온다

### app/services/watchlist/flow_store.py
- `upsert_investor_flows(db, code, rows)`: KIS 일별 수급 → investor_flow_daily upsert (**중복 일자는 최신 응답으로 갱신** — 장중 분석이 미확정 0 행을 먼저 넣으면 16:10 잡 확정값이 영영 못 덮던 동결 버그를 2026-07-20 do_update로 교정)
- `get_extended_flow(db, code)`: 적재분 60/120거래일 누적 — 커버리지 미달이면 부분합 대신 None + 일수 명시
- `collect_all_watchlist_flows()`: 16:10 잡 — 전체 유저 관심종목 distinct 순회 적재

### app/services/watchlist/events.py
- 이벤트 자동 감지 + 트리거급 자동 분석 (16:30 잡 `scan_watchlist_events`, 감지는 전부 결정론 — Gemini 0회, 휴장일 스킵)
- `detect_disclosures`: DART 당일(3일 창) 신규 공시 — rcept_no 중복 방지(app_config `watchlist_seen_disclosures`, 30일 프루닝) + **미확정 공시 후속 추적**(`watchlist_pending_disclosures`, 60일 — "(미확정)" 건을 적재해두고 같은 base 제목의 확정 공시가 뜨면 `후속확정` 이벤트 + 자동 분석. 무효화_조건에서 "수동 확인"으로 방치되던 항목 중 유일하게 자동화 가능한 유형. DART 제목엔 공백이 없어 base는 첫 "(" 앞까지. 도입 이전 건 시드: `scripts/backfill_pending_disclosures.py`), 중요 유형 키워드 분류(실적/자본변동/구조개편/주요사항/대형계약/자사주/지배구조/리스크/조회공시 — 자본변동에 증권신고서·해외증권·예탁증서·신주발행 포함, ADR/해외DR 희석 누락 교정 2026-08-28), 미매칭 잡공시 무시. "영업(잠정)실적"처럼 괄호 낀 실전 표기 매칭 주의
- `detect_flow_spike`: 당일 외인/기관 |순매수| ≥ 30일 평균의 3배 + 10억원 이상 (적재 20일 미만이면 침묵)
- `detect_price_spike`: 당일 등락률 ±5% 이상
- `earnings_calendar_notice`: 정기보고서 법정 제출기한(분기·반기 45일/사업보고서 90일) D-14/D-7 안내 — 한국은 발표일 사전 확정 공표가 드물어 법정 기한만 결정론 계산 가능, 잠정실적 조기 공시는 공시 감지가 담당. 주말 밀림 허용(스테이지 기록으로 중복 방지)
- 트리거급 이벤트(실적/자본변동/구조개편/주요사항/대형계약/리스크/수급·주가 급변) → `run_analysis(trigger_type=...)` 자동 실행(유저·종목·일 1회 상한) → 무효화_조건 즉시 재판정 → 이벤트+분석 요약+조건 판정 통합 텔레그램. 분석 실패 시에도 이벤트 알림은 발송. 자동 매매 개입 없음
- 수동 트리거: POST /admin/watchlist-events/trigger (force=수급/주가 당일 재감지)

### app/api/watchlist.py
- 관심종목 CRUD + `POST /watchlist/analyze` + 이력/상세 조회, 전부 current_user 스코핑
- 삭제 시 분석 일지는 보존 (FK 없음). 이력 집계는 stock_code 기준 별도 쿼리

### app/services/research/analyst.py + app/api/research.py — AI 리서치 탭 (2026-08-10)
- 자유 텍스트 질문 → 종목 리서치. **참고용 프레이밍** — 매매 시그널 아님 (AI 서술형 전망 예측력 미검증은 ai_probability 폐기와 동일 근거, UI 경고 배너 고정), 자동매매 개입 없음
- `identify_stocks(db, question)`: 3단 식별 — ① 6자리 코드 → ② stock_master 이름 부분매칭 (**앞 경계 체크**: "하이닉스" 안의 "이닉스" 오매칭 방지. 뒷 경계는 조사("삼성전자를")가 붙어 미검사. 포함관계 매칭은 긴 이름 우선: 삼성전자우 > 삼성전자) → ③ Gemini 별칭 추출 (gemini-3.1-flash-lite, 삼전→삼성전자, 실패 시 no_match로만 처리). 국내(KOSPI/KOSDAQ)만 — 스냅샷 수집기가 국내 KIS 전용
- `run_research(...)`: 관심종목 `collect_input_snapshot` **그대로 재사용**(수급 적재 기여 포함) → gemini-2.5-flash 검색 그라운딩 → 마크다운 자유 서술 → research_notes 저장. 프롬프트 규칙은 관심종목 탭 이식 (앱 파생지표 재계산 금지·PER 시점 구분·환율 공통팩터·14일 뉴스 창) + "리스크·확인 포인트"/"출처" 섹션 필수. sources는 출처 섹션 링크 정규식 추출 (실패 시 빈 배열, 치명 아님)
- API: `POST /research/query` — 응답 status done/**ambiguous**(후보 복수 → 프론트 선택 후 stock_code 지정 재요청)/no_match. GET /research/history·/{id}, DELETE. 전부 유저 스코핑
- 프론트: `app/research/page.tsx` + `components/Markdown.tsx` (의존성 없는 경량 렌더러 — 프롬프트가 출력 문법을 ##/굵게/목록/파이프 표로 제한)
- GeminiAnalyzer 공용 헬퍼 추가: `grounded_text`(그라운딩 + 원문 텍스트 — 비JSON 출력용) / `plain_json`(비그라운딩 경량 추출)

### app/services/telegram/notifier.py
- `TelegramNotifier`: 멀티유저 텔레그램 알림. chat_id별 개별 전송
- `notify_admins_warning(title, detail)`: `⚠️ [WARNING]` — 정책 경고 (모닝게이트/뉴스차단/손절선 강화 등)
- `notify_admins_error(title, detail)`: `🚨 [ERROR]` — 코드 오류·긴급 조치 (전체 청산/Circuit Breaker/잡 실패)

### app/api/positions.py
- `_enrich(pos)`: Position → PositionOut 변환. target_price(rec 또는 strategy×entry_price), trailing_stop_price(peak×(1-stop_loss_pct/100)) 계산
- `get_stats()`: 확정 포지션 기반 KPI — 승률/손익비/Sharpe/MDD/월별/전략별/종목별/거래목록
- `manual_buy()`: 수동 매수 → 시장가 → 실 체결가 → Position 저장 → `load_all()` (모니터 등록)
- `close_position()` / `close_all_positions()`: 수동 청산 → 실 체결가 반영

### app/api/admin.py
- 수동 트리거: `manual_run_strategy`, `manual_monitor`, `manual_verify`, `trigger_thesis_check`, `trigger_morning_gate`
- 상태 조회: `scheduler_status`, `get_realtime_status` (KIS WS 연결 + 구독 코드 수 + 모니터 종목 수)
- 제어: `resume_auto_trade` (뉴스 차단 해제), `resume_morning_gate`, `resume_circuit_breaker`
