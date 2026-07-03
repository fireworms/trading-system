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
│   │   └── investor_flow.py     # InvestorFlowDaily (관심종목 일별 수급 적재, 공용)
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
│   │   └── ws.py                # WebSocket /ws/prices (실시간 가격)
│   ├── services/
│   │   ├── kis/
│   │   │   ├── client.py        # KISClient (httpx 네이티브)
│   │   │   └── realtime.py      # KIS WebSocket 클라이언트 (H0STCNT0 + H0STCNI0)
│   │   ├── gemini/
│   │   │   ├── analyzer.py      # GeminiAnalyzer (4단계)
│   │   │   └── prompts.py       # 프롬프트 템플릿 (STAGE1~4, BUY_CONFIRM 미사용)
│   │   ├── news/
│   │   │   └── watcher.py       # 뉴스 감시, news_events 저장, 사후 검증
│   │   ├── stock_master/
│   │   │   ├── updater.py       # KIS MST 파일 파싱 → stock_master 갱신
│   │   │   └── index_constituents.py  # KOSPI200/KOSDAQ150 구성종목
│   │   ├── watchlist/
│   │   │   ├── analyzer.py      # 관심종목 분석 (수집→스냅샷→Gemini 구조화→저장)
│   │   │   └── flow_store.py    # 일별 수급 적재/60·120일 누적 (KIS 30거래일 한계 보완)
│   │   ├── telegram/
│   │   │   └── notifier.py      # TelegramNotifier (멀티유저, chat_id별 전송)
│   │   │                        # notify_admins_warning: 정책 경고 (⚠️ [WARNING])
│   │   │                        # notify_admins_error: 코드 오류·긴급 조치 (🚨 [ERROR])
│   │   └── trading/
│   │       ├── realtime_monitor.py  # 실시간 포지션 모니터 (서버사이드 상시 구독, 즉시 손절/익절)
│   │       ├── scheduler.py     # APScheduler 잡 정의
│   │       ├── runner.py        # StrategyRunner (AI 파이프라인, 분석만)
│   │       ├── executor.py      # TradeExecutor (매수/매도/모니터링)
│   │       └── verifier.py      # 추천 결과 사후 검증
│   └── schemas/
│       ├── user.py
│       ├── strategy.py
│       ├── recommendation.py    # PositionOut (target_price, trailing_stop_price 포함)
│       └── watchlist.py
├── docs/
│   └── watchlist_spec.md        # 관심종목 분석 탭 스펙 (관련 작업 시 필독)
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
- **selection_mode**: momentum(기본) / earnings_catalyst — Stage4 선정 프롬프트 변형 분기 (전략 단위)
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
  - **watchlist_stocks에 FK 없음** — 관심종목 삭제해도 일지 영구 보존
- 상세 설계·KIS 필드 디코딩 근거는 docs/watchlist_spec.md + 메모리 watchlist_tab.md 참조
- **스냅샷 v2 (2026-07-02, 실검증 2026-07-03 완료)**: fx_usdkrw(USD/KRW 3개월 추세) / market(KOSPI 레벨·1/3개월 + 종목 상대수익률) / PER 4종 병기(trailing·TTM·최근분기 연환산·컨센서스 forward — trailing 왜곡 대응) / 수급 페이스 판정 문자열(5일 vs 30일 일평균, 앱이 확정 — LLM 재계산 금지) / 개인 순매수 5/20/30일 / PBR 5년 밴드 근사(월봉÷당시 연간 BPS, 근사 명시)

### investor_flow_daily ← 관심종목 수급 적재 (2026-07-02)
- flow_id (PK), stock_code (idx), trade_date, frgn/orgn/prsn_ntby_amt (백만원), close
- UNIQUE(stock_code, trade_date), 유저 스코핑 없음 (공용 시장 데이터)
- KIS FHKST01010900이 최근 30거래일만 반환 → 16:10 잡 + 분석 실행이 매일 upsert해 60/120일 누적 구축
- **백필 불가** — 2026-07-02부터 축적, 커버리지 미달 구간은 부분합으로 위장하지 않고 None + 일수 명시

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
- `CRITICAL + KOSPI ≤ -2%` → 전 포지션 즉시 청산 (MANUAL_EXIT) + 텔레그램
- `WARNING/CRITICAL + KOSPI ≤ -1%` → 수익 포지션 현재가 기준 trailing 전환 + 텔레그램
- AI 단독 신호 (KOSPI 멀쩡) → 알림만 (오탐 방지)

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
| run_strategies | 08:30 Mon/Wed/Fri | AI 분석 (매수 없음, morning_gate와 무관하게 실행) |
| execute_pending_buys | 09:20 평일 | 크로스 시그널 보너스 적용 후 매수 (morning_gate/news 차단 시 스킵) |
| monitor_positions | 09:05~15:55 매 10분 평일 | 포지션 손절/익절 모니터링 |
| thesis_check | 10:00, 14:00 평일 | 보유 포지션 thesis 재검증 (8개씩 그룹 grounding) |
| verify_recommendations | 00:10 매일 | 추천 결과 사후 검증 |
| verify_news_events | 16:00 평일 | 뉴스 이벤트 + recommendation_runs 실제 시장 영향 검증 |
| collect_watchlist_flows | 16:10 평일 | 관심종목 일별 수급 적재 (60/120일 누적, 2026-07-02 시작) |
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
  - min_probability 필터 제거 (DB 컬럼은 유지, executor에서 미사용)
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

## Circuit Breaker
- 직전 4건 청산이 전부 손실이면 해당 유저 매수 자동 차단 (4건 미만은 체크 안 함)
- app_config: `cb_paused_{user_id}`, `cb_reason_{user_id}`
- 트리거 시 어드민 텔레그램 알림, 수동 해제만 가능
- GET /admin/circuit-breaker/status, POST /admin/circuit-breaker/resume/{user_id}
- GET /admin/realtime/status — KIS WS 연결 여부 + realtime_monitor 감시 종목 수

## KIS API 주요 엔드포인트
- `FHKST01010100` inquire-price: 현재가 + 시가/고가/체결강도(cttr)/거래량 + **per/eps/pbr/bps**(밸류, 응답에 동봉 — 추가 호출 불필요)
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
  - NEXT_PUBLIC_WS_URL=ws://192.168.0.10:8000 (.env.local 필수)
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
- 목표가 도달 시 즉시 TARGET_HIT 청산 (기본, AI thesis 완료 기준)
- `Strategy.use_trailing_stop=true`이면 목표가 후 peak 추적 → peak × (1 - stop_loss_pct%) 이탈 시 청산
- 손절: entry_price × (1 - stop_loss_pct/100) 고정선 (trailing 모드는 peak 기준)
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
- `get_index_change_pct()`: KOSPI(0001)/KOSDAQ(1001) 등락률 — 매수 전 -2% 체크
- `_get_domestic_stock_info(code)`: 현재가+RSI+이평선+외국인/기관 순매수+**per/eps** 통합 (runner 종목 데이터 수집용). inquire-price 1회 호출로 price+per+eps 동시 추출 (get_current_price 중복 호출 제거). per/eps는 **trailing(직전 공시 실적) 기준** — forward 추정 서사와 다른 값. 적자기업·데이터없음은 None(0/음수 위장 금지). eps는 KIS가 "6564.00" 문자열 반환 → int(float()) 파싱
- `get_stock_basic_info(code)`: CTPF1002R 섹터 조회 (매수 직전 MAX_PER_SECTOR 체크용)
- `get_fx_daily_closes(symbol='FX@KRW', days)`: USD/KRW 환율 일봉 (FHKST03030100, mrkt div 'X')
- `get_ohlcv_monthly(code, months)`: 월봉 — 1회 호출로 60개월 (PBR 5년 밴드 근사용)
- `get_foreign_holding(code)`: inquire-price 동봉 — 외인 소진율 + per/pbr/eps/bps + 시총(market_cap_eok, 억원)
- `get_today_fill_price(code, side)`: TTTC8001R 당일 체결 조회. side="02"=매수, "01"=매도. 매수/매도 직후 실 체결가 반영에 사용
- `buy_market_order(code, qty)` / `sell_market_order(code, qty)`: TTTC0802U / TTTC0801U 시장가 주문
- `get_kis_client_from_account(account)`: account_id 기준 싱글턴 반환 (`_client_registry`). 동일 계좌는 항상 같은 인스턴스 → 토큰 공유

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

### app/services/trading/executor.py
- `TradeExecutor.execute_pending_buys()`: 09:20 잡 진입점. 플래그 체크(morning_gate/news/cb) → 지수 -2% 체크 → 크로스 시그널 계산 → 전략별 매수
- `execute_buys_for_run(run, user_strategy)`: 추천 목록 정렬(유효확률+크로스보너스) → 섹터/RSI/R:R 필터 → 시장가 매수 → 실 체결가 반영 → Position 저장 → 모니터 등록
- `monitor_positions()`: HOLDING 포지션 순회 → 만료/time-based stop 처리 (손절/익절은 realtime_monitor가 우선)
- `_check_position(pos)`: 목표가/손절가 계산 + trailing 모드 체크 + 타임아웃(+1거래일 신고점 없으면 TARGET_HIT) 처리
- `_close_position(pos, status, price)`: 시장가 매도 → 1초 대기 → `get_today_fill_price(side="01")`로 실 체결가 → exit_price 저장 → 모니터 제거
- `_check_circuit_breaker(user_id)`: 직전 4건 청산 전부 손실 시 cb_paused 플래그 설정
- `emergency_close_all_positions(reason)`: 전 포지션 즉시 청산 (뉴스 CRITICAL+KOSPI -2% 시)
- `tighten_stop_losses(reason)`: 수익 포지션 현재가 기준 trailing 전환 (뉴스 WARNING+KOSPI -1% 시)
- `_build_cross_signal(db)`: 오늘 전략 추천 집계 → 종목별 다양성 점수 계산
- `_cross_signal_bonus(code, signal)`: 점수 1.0→+7%, 0.5→+3.5%, 상한 +10%

### app/services/trading/realtime_monitor.py
- `RealtimePositionMonitor`: 싱글턴. HOLDING 포지션 인메모리 관리 + KIS 가격 틱 실시간 손절/익절
- `load_all()`: DB에서 HOLDING 전부 → `PositionWatch` 생성 → KIS H0STCNT0 구독
- `on_price(code, price_data)`: 매 틱 bid_price 기준 `_should_close()` → 조건 충족 시 `asyncio.create_task`로 즉시 청산
- `_should_close(watch, price)`: 손절가 이탈 → "stop_loss", 목표가 도달(trailing OFF) → "target_hit", trailing ON → peak 갱신 또는 trailing 손절
- `force_trailing(position_id, peak_price)`: DB + 인메모리 동시 trailing 전환 (뉴스 조치 시 사용)
- `add(watch)` / `remove(position_id, code)`: 매수/청산 시 executor가 호출해 동기화

### app/services/news/watcher.py
- `check_news(db)`: gemini-2.5-flash + google_search → severity 판정. 실패 시 20초 후 1회 재시도, 최종 실패 시 `check_failed` 마커 반환 (NORMAL로 위장 금지)
- `morning_gate_check()`: 08:00 실행. 시작부에서 전날 켜진 news_auto_trade_paused stale 자동 해제(news_pause_at ≠ 오늘 KST) → **QQQ 전일 등락률 수치 게이트**(-1.5% WARNING / -3% CRITICAL, `get_us_price_with_change("QQQ","NAS")`, LLM 비의존) + Gemini 미국선물/지정학 체크 → 둘 중 나쁜 쪽 채택 → WARNING/CRITICAL 시 `morning_gate_paused=true`. Gemini 실패해도 수치만으로 차단 가능(fail-safe), `morning_gate_last_check`에 QQQ 실측 vs LLM 수치 기록 (게이트 정확도 사후검증용, date는 KST 거래일 — UTC로 쓰면 08:00 KST 실행 시 전날로 밀림)
- `run_news_check_and_act()`: 스케줄러에서 호출. 장중 120분 간격 체크 (10분 tick 기반). `check_failed`면 이벤트 저장 스킵 + 연속실패 카운트 + 3연속 시 어드민 알림
- `_apply_dual_signal_action(db, result)`: AI 판정 × KOSPI 등락률 교차 검증 → emergency_close / tighten_stop / 알림만
- `check_position_theses(db)`: 10:00/14:00. 2일+ HOLDING 포지션 8개씩 그룹 → gemini-2.5-flash + google_search thesis 재검증 → invalid+손실 시 조기 청산
- `verify_news_events(db)`: 1일/3일 경과 이벤트에 실제 KOSPI/KOSDAQ 변화율 기록
- `verify_run_market_outcomes(db)`: 전날 recommendation_runs의 kospi_change_1d 채움 (A-gate 데이터 축적)
- `_build_history_context(db)`: 최근 15건 이벤트 + 최근 5일 이슈 키워드 → 프롬프트 주입 (중복 감지 억제)

### app/services/trading/scheduler.py
- `start_scheduler()`: APScheduler 설정 + 전체 잡 등록 (KST 기준)
- `run_startup_catchup()`: 서버 재시작 시 누락 분석/검증/stock_master 자동 보완
- `_should_run(db, strategy)`: run_interval_days 경과 여부 체크

### app/services/trading/verifier.py
- `run_verifications(db)`: 검증 대상(verification 없음 + run_date+hold_days ≤ today) 순회 → `_verify_recommendation()`
- `_verify_recommendation(rec, run, strategy, client, today)`: 일봉 날짜순 순회 → 손절/목표가 중 먼저 터치되는 쪽 판정 (같은 날이면 손절 우선). pnl_pct는 실제 exit_price 기준. period_start/end는 반드시 strftime("%Y%m%d") — get_ohlcv() bar.date가 YYYYMMDD 포맷이므로 ISO 포맷과 혼용 금지
- `_update_performance_score(db, version_no)`: 검증 완료 후 prompt_version.performance_score 갱신

### app/services/trading/backtester.py
- `BacktestRunner(db, version_tag)`: 과거 날짜 Stage4 재현 + 즉시 검증. version_tag로 run 라벨링(A/B 비교용)
- `run_backtest(strategy, base_date)`: base_date ±12일/3일간격 최대 9개 날짜 실행 → 집계
- `_run_single_date()`: 과거데이터 수집 → **`StrategyRunner._prefilter_stocks` 적용(라이브 경로 일치)** → `stage4_picks_backtest` → 저장 → 검증. target_price/stop_loss_price는 picks에 없으므로 진입가×전략 파라미터로 산출(확률·목표가 폐기 이후 필수)
- `_verify_pick()`: 일봉 날짜순 손절-우선 청산 모델(verifier.py와 동일 convention). pnl 비현실값(분할/상폐) ±클램프(-100~+200%)
- `_compute_random_baseline(stock_data, target_date, strategy, client)`: 동일 풀 랜덤 픽 + **AI와 동일 목표/손절 청산·클램프** 적용(공정 비교)
- **구조적 한계**: 매크로를 스텁(`market_theme="백테스트"`)함 — Stage1 그라운딩은 현재시점이라 과거날짜 lookahead 방지. 따라서 **매크로 정합 효과는 백테스트로 측정 불가, 기술기준만 평가**됨

### app/services/watchlist/analyzer.py
- 관심종목 분석 서비스 (스펙: docs/watchlist_spec.md). AI = 데이터 집계+구조화, 예측 금지
- `collect_input_snapshot(client, code, name, sector, db=None)`: 6개월 일봉 요약 + 분기 재무(YTD→단일분기 차분) + 컨센서스 추정 + 수급 30거래일 + **환율 3개월 추세 + KOSPI 상대수익률 + PER 4종 병기 + 수급 페이스 판정 + PBR 5년 밴드** + data_flags(결측 명시) → 이 dict가 그대로 프롬프트 입력 + DB 저장 (사후 재구성 보장). db 넘기면 수급 적재 + 60/120일 누적 포함
- `_pace_judgment(avg5, avg30)`: 수급 가속/둔화/전환 판정 문자열 생성 — LLM에 나눗셈 시키지 않기 위해 앱이 확정. 30일 평균 미미하면 중립 취급(비율 폭주 방지), ±20% 밴드 내 "페이스 유사", 부호 전환은 별도 라벨
- `_pbr_band_5y()`: 월별 종가 ÷ 당시 최근 연간 BPS → 현재 PBR의 5년 퍼센타일 (자사주 소각/증자 왜곡 가능 — 근사 명시)
- 프롬프트 규칙: 앱 계산 파생지표(judgment/상대수익률/trend_note/per_ttm/퍼센타일) **재계산 금지, 그대로 인용** / per_trailing 왜곡 시 per_ttm·forward 우선 / 환율=외인 수급 공통 팩터로 종목 고유 요인과 구분
- **뉴스 최신성 가드 (2026-07-03)**: 프롬프트에 14일 창 앵커 + 주가 변동 동인 필수 검색(앱이 1개월/당일 수치 확정 주입) / 파싱 후 14일 내 기사 0건이면 재검색 1회 → 그래도 없으면 `data_flags.news_recency` 명시 후 저장(억지 인용 강제 안 함). 배경: 그라운딩이 앵커 없이는 구 자료로 수렴 (7/2 분석 = 4월 기사 재탕)
- `run_analysis(db, user_id, ...)`: 수집 → gemini-2.5-flash 검색 그라운딩 → JSON 파싱 → StockAnalysis 저장. **무효화_조건 비면 1회 강제 재요청** 후 실패 시 ValueError
- analysis_date는 라벨 — KIS 입력은 항상 수집 시점 (snapshot.collected_at 기록)
- 1차 범위: 수동 트리거만. 이벤트 자동 감지(실적/공시/수급·주가 급변)는 후속. 공매도 잔고는 미구현(KIS 일별추이 API 있어 후속 가능), 대차잔고는 KIS 미제공

### app/services/watchlist/flow_store.py
- `upsert_investor_flows(db, code, rows)`: KIS 일별 수급 → investor_flow_daily upsert (중복 일자 스킵)
- `get_extended_flow(db, code)`: 적재분 60/120거래일 누적 — 커버리지 미달이면 부분합 대신 None + 일수 명시
- `collect_all_watchlist_flows()`: 16:10 잡 — 전체 유저 관심종목 distinct 순회 적재

### app/api/watchlist.py
- 관심종목 CRUD + `POST /watchlist/analyze` + 이력/상세 조회, 전부 current_user 스코핑
- 삭제 시 분석 일지는 보존 (FK 없음). 이력 집계는 stock_code 기준 별도 쿼리

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
