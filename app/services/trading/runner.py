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

    def run_strategy(self, strategy: Strategy, today: date | None = None) -> RecommendationRun:
        """
        전략 1회 실행:
        1. 후보 종목 시세 수집
        2. AI 4단계 파이프라인
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

        # 2. AI 파이프라인 실행
        macro, historical, industry, picks_result = self.analyzer.run_full_pipeline(
            strategy=strategy,
            candidate_stocks=stock_data,
            today=run_date,
        )

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
            raw_response={
                "macro": macro.raw,
                "historical": historical.raw,
                "industry": industry.raw,
                "picks": picks_result.raw,
                "random_baseline": {"entries": random_entries},
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

        # Recommendations 저장 (current_price_at_rec: 검증 pnl 기준가)
        # stock_data에서 종목코드 → 현재가 맵 구성
        price_map: dict[str, Decimal] = {
            s["stock_code"]: Decimal(str(s["current_price"]))
            for s in stock_data if s.get("stock_code") and s.get("current_price")
        }
        for pick in picks_result.picks:
            code = pick.get("stock_code", "")
            # AI가 반환한 current_price 우선, 없으면 수집 데이터에서 조회
            raw_price = pick.get("current_price") or price_map.get(code)
            rec = Recommendation(
                run_id=run.run_id,
                stock_code=code,
                stock_name=pick.get("stock_name", ""),
                current_price_at_rec=Decimal(str(raw_price)) if raw_price else None,
                target_price=Decimal(str(pick.get("target_price", 0))) if pick.get("target_price") else None,
                stop_loss_price=Decimal(str(pick.get("stop_loss_price", 0))) if pick.get("stop_loss_price") else None,
                ai_probability=Decimal(str(pick.get("ai_probability", 0))) if pick.get("ai_probability") else None,
                ai_reason=pick.get("ai_reason"),
                historical_basis=pick.get("historical_basis"),
                risk_factors=pick.get("risk_factors"),
                rank=pick.get("rank"),
            )
            self.db.add(rec)

        self.db.commit()
        self.db.refresh(run)
        logger.info("Strategy run saved: run_id=%s, picks=%d", run.run_id, len(picks_result.picks))

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
