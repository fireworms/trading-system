"""
전략 실행 서비스.
AI 파이프라인 실행 → DB 저장 → 자동매매 실행을 조율한다.
"""
import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.strategy import Strategy, UserStrategy
from app.models.recommendation import (
    RecommendationRun, MacroAnalysis, Recommendation
)
from app.models.stock_master import StockMaster
from app.services.gemini.analyzer import GeminiAnalyzer
from app.services.kis.client import get_kis_client, get_kis_client_from_account

logger = logging.getLogger(__name__)

# Stage4 A-gate: 이 키워드가 market_theme에 있으면 하락장 신호
_BEAR_KEYWORDS = ["하락장", "폭락", "급락", "약세", "하락세", "조정장", "침체", "위기", "crash", "bear", "매도세"]
# 데이터가 이 건수 이상 쌓여야 A-gate 활성화 (충분한 calibration 전 오작동 방지)
_GATE_MIN_DATA = 20


def _is_market_unfavorable(market_theme: str) -> bool:
    t = market_theme.lower()
    return any(kw in t for kw in _BEAR_KEYWORDS)


class StrategyRunner:
    """전략별 AI 분석 실행 및 DB 저장."""

    def __init__(self, db: Session):
        self.db = db
        self.analyzer = GeminiAnalyzer()

    # ------------------------------------------------------------------ #
    # stock_master 직접 샘플링
    # ------------------------------------------------------------------ #

    def _sample_from_master(self, strategy: Strategy) -> list[dict]:
        """
        stock_master에서 직접 필터링 + 샘플링.

        샘플 크기: pick_count × 15, 최소 50 최대 200
          (Stage4 프롬프트 크기와 KIS API 호출 수의 균형점)

        candidate_market: ALL / KOSPI / KOSDAQ / NAS
        candidate_filter:
          volume   → KIS 거래량 순위 상위 우선 + 나머지 stride 채우기
          largecap → KOSPI200 / KOSDAQ150 구성종목만
          mixed    → largecap 우선 + stride 채우기
        """
        from app.services.stock_master.index_constituents import (
            get_kospi200, get_kosdaq150,
            _fetch_cap_rank_sector, _KOSPI_SECTOR_CODES, _KOSDAQ_SECTOR_CODES,
        )

        mkt    = getattr(strategy, "candidate_market", "ALL")
        flt    = getattr(strategy, "candidate_filter", "mixed")
        sample = min(max(strategy.pick_count * 15, 50), 200)

        # 1) stock_master 로드 (시장 필터)
        q = self.db.query(StockMaster).filter(StockMaster.is_active == True)
        if mkt != "ALL":
            q = q.filter(StockMaster.market == mkt)
        all_rows: dict[str, StockMaster] = {r.stock_code: r for r in q.all()}

        if not all_rows:
            logger.warning("No stocks in stock_master for market=%s", mkt)
            return []

        # 2) 필터별 정렬 / 선별
        if flt == "largecap":
            # 상위 90%: 시총 내림차순 보장, 하위 10%: stride 다양성
            k200_ordered  = get_kospi200()   # list, 시총 내림차순
            kq150_ordered = get_kosdaq150()
            index_rank = {code: i for i, code in enumerate(k200_ordered + kq150_ordered)}
            cap_slots  = int(sample * 0.9)
            div_slots  = sample - cap_slots
            ordered = sorted(
                [r for code, r in all_rows.items() if code in index_rank],
                key=lambda r: index_rank[r.stock_code],
            )[:cap_slots]
            rest    = [r for code, r in all_rows.items() if code not in index_rank]
            ordered += self._stride(rest, div_slots)

        elif flt == "volume":
            # KIS 시총 순위 API (FHPST01740000) 섹터별 호출로 실시간 랭킹
            client = get_kis_client(self.db)
            cap_map: dict[str, int] = {}
            sectors = (
                _KOSPI_SECTOR_CODES  if mkt == "KOSPI"  else
                _KOSDAQ_SECTOR_CODES if mkt == "KOSDAQ" else
                _KOSPI_SECTOR_CODES + _KOSDAQ_SECTOR_CODES
            )
            for iscd in sectors[:12]:  # NAS 없고, 속도 위해 12개로 제한
                for code, cap in _fetch_cap_rank_sector(client, iscd):
                    if code in all_rows:
                        cap_map[code] = max(cap_map.get(code, 0), cap)
            ranked  = sorted(cap_map.keys(), key=lambda c: cap_map[c], reverse=True)
            ordered = [all_rows[c] for c in ranked if c in all_rows]
            rest    = [r for code, r in all_rows.items() if code not in set(ranked)]
            ordered += self._stride(rest, max(0, sample - len(ordered)))

        else:  # mixed
            # largecap 먼저, 나머지 stride
            kospi200  = set(get_kospi200())
            kosdaq150 = set(get_kosdaq150())
            index_set = kospi200 | kosdaq150
            largecap  = [r for code, r in all_rows.items() if code in index_set]
            rest      = [r for code, r in all_rows.items() if code not in index_set]
            ordered   = largecap + self._stride(rest, max(0, sample - len(largecap)))

        selected = ordered[:sample]
        logger.info("Sampled %d stocks from master (filter=%s market=%s pick_count=%d)",
                    len(selected), flt, mkt, strategy.pick_count)
        return [
            {"code": r.stock_code, "country": r.country or "KR", "market": r.market}
            for r in selected
        ]

    @staticmethod
    def _stride(rows: list, limit: int) -> list:
        if limit <= 0 or not rows:
            return []
        if len(rows) <= limit:
            return rows
        step = len(rows) / limit
        return [rows[int(i * step)] for i in range(limit)]

    def _collect_stock_data(self, candidates: list[dict]) -> list[dict]:
        """KIS API로 후보 종목 기술 데이터 수집."""
        client = get_kis_client(self.db)
        result = []
        failed = []
        for c in candidates:
            code, country, market = c["code"], c["country"], c["market"]
            try:
                info = client.get_stock_info(code, country=country, market=market)
                result.append(info)
            except Exception as e:
                logger.warning("Failed to fetch %s: %s", code, e)
                failed.append(f"{code}: {e}")

        if failed and len(failed) >= len(candidates) // 2:
            from app.services.telegram.notifier import get_notifier
            notifier = get_notifier()
            if notifier:
                notifier.notify_error(
                    "KIS API 시세 수집 실패",
                    f"{len(failed)}/{len(candidates)}개 종목 수집 실패\n"
                    + "\n".join(failed[:5]),
                )
        return result

    def _prefilter_stocks(
        self, stocks_data: list[dict], candidate_filter: str, target: int = 20
    ) -> list[dict]:
        """RSI·수급·거래량 기준으로 target개로 압축. 환각 억제용."""
        def _score(s: dict) -> float:
            rsi = s.get("rsi_14") or 50.0
            net_buy_5d = (s.get("frgn_net_buy_5d") or 0) + (s.get("orgn_net_buy_5d") or 0)
            avg_vol = s.get("avg_volume_20d") or 1
            recent = s.get("recent_ohlcv", [])
            vol_ratio = (recent[0]["volume"] / avg_vol) if recent else 0.0

            rsi_score = max(0.0, 1.0 - abs(rsi - 55) / 30)   # 55 근처 최고
            buy_score = 1.0 if net_buy_5d > 0 else 0.0
            vol_score = min(vol_ratio / 2.0, 1.0) if candidate_filter == "volume" else 0.5

            return rsi_score * 0.4 + buy_score * 0.3 + vol_score * 0.3

        # 1차: RSI 극단값 제거 (30 미만 / 72 초과)
        strict = [s for s in stocks_data if s.get("rsi_14") is not None and 30 <= s["rsi_14"] <= 72]
        pool = strict if len(strict) >= target else (
            [s for s in stocks_data if s.get("rsi_14") is not None and 25 <= s["rsi_14"] <= 78]
        )
        if len(pool) < target:
            pool = stocks_data  # 그래도 부족하면 전체

        result = sorted(pool, key=_score, reverse=True)[:target]
        logger.info("Pre-filter: %d → %d stocks (filter=%s)", len(stocks_data), len(result), candidate_filter)
        return result

    def _run_stage4_grouped(self, macro, industry, stock_data: list[dict], strategy: Strategy):
        """사전필터(20개) → 10개씩 그룹 → Stage4 반복 실행 → 집계."""
        from app.services.gemini.analyzer import PickResult

        filtered = self._prefilter_stocks(stock_data, strategy.candidate_filter, target=20)
        groups = [filtered[i:i + 10] for i in range(0, len(filtered), 10)]

        all_picks: list[dict] = []
        model_used = ""
        raw_groups = []

        for i, group in enumerate(groups):
            logger.info("Stage4 group %d/%d (%d stocks)", i + 1, len(groups), len(group))
            result = self.analyzer.stage4_picks(
                macro=macro,
                industry=industry,
                stocks_data=group,
                hold_days=strategy.hold_days,
                target_pct=strategy.target_pct,
                stop_loss_pct=strategy.stop_loss_pct,
                min_probability=strategy.min_probability,
                pick_count=strategy.pick_count,
                candidate_filter=strategy.candidate_filter,
            )
            all_picks.extend(result.picks)
            model_used = result.model_used or model_used
            raw_groups.append({"group": i + 1, "stock_count": len(group), "raw": result.raw})

        # 중복 제거 + 확률 내림차순 → pick_count개 + rank 재부여
        seen: set[str] = set()
        unique: list[dict] = []
        for p in sorted(all_picks, key=lambda x: x.get("ai_probability") or 0, reverse=True):
            code = p.get("stock_code", "")
            if code and code not in seen:
                seen.add(code)
                unique.append(p)
            if len(unique) >= strategy.pick_count:
                break
        for idx, p in enumerate(unique, 1):
            p["rank"] = idx

        logger.info(
            "Stage4 grouped done: %d groups → %d candidates → %d picks [%s]",
            len(groups), len(all_picks), len(unique), model_used,
        )
        return PickResult(
            picks=unique,
            excluded_reason=f"{len(groups)} groups processed",
            model_used=model_used,
            raw={"groups": raw_groups},
        )

    def run_strategy(self, strategy: Strategy, today: date | None = None) -> RecommendationRun:
        """
        전략 1회 실행:
        1. 후보 종목 시세 수집
        2. AI 4단계 파이프라인 (Stage4는 그룹 분할 실행)
        3. 결과 DB 저장
        4. 자동매매 구독자에게 주문 실행
        """
        run_date = today or date.today()
        logger.info("Running strategy: %s (%s)", strategy.name, run_date)

        # 1. stock_master에서 직접 샘플링 → KIS 데이터 수집
        candidates = self._sample_from_master(strategy)
        stock_data = self._collect_stock_data(candidates)
        if not stock_data:
            raise RuntimeError("No stock data collected")

        # stock_name 주입: AI가 훈련 기억 대신 stock_master의 정확한 이름 사용
        from sqlalchemy import select as _select
        codes_in_data = [s["stock_code"] for s in stock_data if s.get("stock_code")]
        sm_rows = self.db.scalars(
            _select(StockMaster).where(StockMaster.stock_code.in_(codes_in_data))
        ).all()
        name_map_pre = {r.stock_code: r.stock_name for r in sm_rows}
        for s in stock_data:
            s["stock_name"] = name_map_pre.get(s.get("stock_code", ""), "")

        # 2. AI 파이프라인: Stage 1-3 순차 실행
        macro = self.analyzer.stage1_macro(run_date)
        logger.info("Stage1 done: theme=%s [%s]", macro.market_theme, macro.model_used)
        historical = self.analyzer.stage2_historical(macro)
        logger.info("Stage2 done: %d matches [%s]", len(historical.historical_matches), historical.model_used)
        industry = self.analyzer.stage3_industry(macro, historical)
        logger.info("Stage3 done: beneficiary=%s [%s]", industry.expected_beneficiary[:40], industry.model_used)

        # Stage4: A-gate 체크 후 실행
        # - 데이터 >= _GATE_MIN_DATA 이고 market_theme이 하락 키워드 포함 시 Stage4 스킵
        from sqlalchemy import func as _func
        verified_count = self.db.scalar(
            select(RecommendationRun.run_id).where(
                RecommendationRun.kospi_change_1d.isnot(None)
            ).with_only_columns(_func.count())
        ) or 0

        stage4_skipped = False
        if verified_count >= _GATE_MIN_DATA and _is_market_unfavorable(macro.market_theme):
            logger.info(
                "Stage4 SKIPPED by A-gate: theme=%s, verified_data=%d",
                macro.market_theme, verified_count,
            )
            stage4_skipped = True
            picks_result = PickResult(
                picks=[],
                excluded_reason=f"A-gate: market unfavorable ({macro.market_theme})",
                model_used="none",
                raw={"skipped": True, "theme": macro.market_theme},
            )
        else:
            picks_result = self._run_stage4_grouped(macro, industry, stock_data, strategy)

        # 실행 시점 지수 레벨 수집 (Stage1 정확도 검증용)
        kospi_at_run = kosdaq_at_run = None
        try:
            _kis = get_kis_client(self.db)
            kospi_at_run  = _kis._get_index_level("0001")
            kosdaq_at_run = _kis._get_index_level("1001")
        except Exception as _e:
            logger.warning("Index level fetch failed at run time: %s", _e)

        # 3. 랜덤 대조군 진입가 기록 (같은 종목 풀에서 pick_count개 무작위)
        import random as _random
        random_entries: dict[str, float] = {}
        eligible = [s for s in stock_data if s.get("stock_code") and s.get("current_price", 0) > 0]
        for s in _random.sample(eligible, min(strategy.pick_count, len(eligible))):
            random_entries[s["stock_code"]] = float(s["current_price"])

        # 4. DB 저장
        run = RecommendationRun(
            strategy_id=strategy.strategy_id,
            run_date=run_date,
            ai_model_used=picks_result.model_used or "gemini-3-flash-preview",
            stage1_model=macro.model_used or None,
            stage2_model=historical.model_used or None,
            stage3_model=industry.model_used or None,
            stage4_model=picks_result.model_used or None,
            prompt_version="v1.0",
            kospi_at_run=kospi_at_run,
            kosdaq_at_run=kosdaq_at_run,
            stage4_skipped=stage4_skipped,
            raw_response={
                "macro": macro.raw,
                "historical": historical.raw,
                "industry": industry.raw,
                "picks": picks_result.raw,
                "random_baseline": {"entries": random_entries},
                # KIS 수집 시점 가격 감사 로그 (환각 검증용)
                "price_snapshot": {
                    s["stock_code"]: float(s["current_price"])
                    for s in stock_data if s.get("stock_code") and s.get("current_price")
                },
            },
        )
        self.db.add(run)
        self.db.flush()  # run_id 확보

        # MacroAnalysis 저장
        analysis = MacroAnalysis(
            run_id=run.run_id,
            current_situation=macro.macro_summary,
            historical_matches=historical.raw,
            industry_mapping=industry.raw,
            expected_beneficiary=industry.expected_beneficiary,
        )
        self.db.add(analysis)

        # Recommendations 저장
        # KIS 실측 가격맵 (AI 반환값보다 항상 우선)
        price_map: dict[str, Decimal] = {
            s["stock_code"]: Decimal(str(s["current_price"]))
            for s in stock_data if s.get("stock_code") and s.get("current_price")
        }

        # stock_master에서 종목명 맵 구성 (AI 이름 환각 차단)
        from app.models.stock_master import StockMaster
        from sqlalchemy import select as _select
        name_map: dict[str, str] = {}
        if price_map:
            masters = self.db.scalars(
                _select(StockMaster).where(StockMaster.stock_code.in_(price_map.keys()))
            ).all()
            name_map = {m.stock_code: m.stock_name for m in masters}

        saved_count = 0
        for pick in picks_result.picks:
            code = pick.get("stock_code", "")

            # 샘플에 없는 코드 = AI가 코드를 환각한 것 → 저장 거부
            if code not in price_map:
                logger.warning(
                    "Pick rejected — code not in sample (hallucinated?): code=%s ai_name=%s",
                    code, pick.get("stock_name"),
                )
                continue

            # 가격: KIS 실측값 (AI 반환 current_price 무시)
            kis_price = price_map[code]
            # 종목명: stock_master 우선 (AI 반환 stock_name 무시)
            stock_name = name_map.get(code) or pick.get("stock_name", "")
            # target/stop: 전략 파라미터로 서버에서 직접 계산 (AI 계산값 무시)
            target_price = (kis_price * (1 + strategy.target_pct / 100)).quantize(Decimal("1"))
            stop_price   = (kis_price * (1 - strategy.stop_loss_pct / 100)).quantize(Decimal("1"))

            rec = Recommendation(
                run_id=run.run_id,
                stock_code=code,
                stock_name=stock_name,
                current_price_at_rec=kis_price,
                target_price=target_price,
                stop_loss_price=stop_price,
                ai_probability=Decimal(str(pick.get("ai_probability", 0))) if pick.get("ai_probability") else None,
                ai_reason=pick.get("ai_reason"),
                historical_basis=pick.get("historical_basis"),
                risk_factors=pick.get("risk_factors"),
                rank=pick.get("rank"),
            )
            self.db.add(rec)
            saved_count += 1

        self.db.commit()
        self.db.refresh(run)
        logger.info(
            "Strategy run saved: run_id=%s, picks=%d (hallucinated/rejected=%d)",
            run.run_id, saved_count, len(picks_result.picks) - saved_count,
        )

        # 4. 텔레그램 알림 — 구독자 각각 전송
        from app.services.telegram.notifier import get_notifier
        from app.models.user import User
        notifier = get_notifier()
        if notifier:
            subscriber_ids = [s.user_id for s in strategy.user_strategies if s.is_active]
            # 구독자가 없으면 전략 생성자에게 전송
            if not subscriber_ids and strategy.created_by:
                subscriber_ids = [strategy.created_by]
            for uid in subscriber_ids:
                user = self.db.get(User, uid)
                if user and user.telegram_chat_id:
                    notifier.notify_recommendations(
                        chat_id=user.telegram_chat_id,
                        strategy_name=strategy.name,
                        run_date=run_date,
                        market_theme=macro.market_theme,
                        picks=picks_result.picks,
                    )

        return run
