"""
전략 실행 서비스.
AI 파이프라인 실행 → DB 저장 → 자동매매 실행을 조율한다.
"""
import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.strategy import Strategy, UserStrategy
from app.models.recommendation import (
    RecommendationRun, MacroAnalysis, Recommendation
)
from app.models.candidate_stock import CandidateStock
from app.services.gemini.analyzer import GeminiAnalyzer
from app.services.kis.client import get_kis_client, get_kis_client_from_account

logger = logging.getLogger(__name__)


class StrategyRunner:
    """전략별 AI 분석 실행 및 DB 저장."""

    def __init__(self, db: Session):
        self.db = db
        self.analyzer = GeminiAnalyzer()

    def _get_candidate_codes(self) -> list[str]:
        """DB에서 활성 후보 종목 코드 목록 반환."""
        rows = (
            self.db.query(CandidateStock.stock_code)
            .filter(CandidateStock.is_active == True)
            .order_by(CandidateStock.stock_id)
            .all()
        )
        codes = [r[0] for r in rows]
        if not codes:
            logger.warning("No active candidate stocks in DB")
        return codes

    def _collect_stock_data(self, stock_codes: list[str]) -> list[dict]:
        """KIS API로 후보 종목 기술 데이터 수집."""
        client = get_kis_client(self.db)
        result = []
        failed = []
        for code in stock_codes:
            try:
                info = client.get_stock_info(code)
                result.append(info)
            except Exception as e:
                logger.warning("Failed to fetch %s: %s", code, e)
                failed.append(f"{code}: {e}")

        # 절반 이상 실패하면 KIS API 장애 알림
        if failed and len(failed) >= len(stock_codes) // 2:
            from app.services.telegram.notifier import get_notifier
            notifier = get_notifier()
            if notifier:
                notifier.notify_error(
                    "KIS API 시세 수집 실패",
                    f"{len(failed)}/{len(stock_codes)}개 종목 수집 실패\n"
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

        # 1. 종목 데이터 수집 (DB 기반 후보 풀)
        candidate_codes = self._get_candidate_codes()
        stock_data = self._collect_stock_data(candidate_codes)
        if not stock_data:
            raise RuntimeError("No stock data collected")

        # 2. AI 파이프라인 실행
        macro, historical, industry, picks_result = self.analyzer.run_full_pipeline(
            strategy=strategy,
            candidate_stocks=stock_data,
            today=run_date,
        )

        # 3. DB 저장
        run = RecommendationRun(
            strategy_id=strategy.strategy_id,
            run_date=run_date,
            ai_model_used=picks_result.model_used or "gemini-3-flash-preview",
            prompt_version="v1.0",
            raw_response={
                "macro": macro.raw,
                "historical": historical.raw,
                "industry": industry.raw,
                "picks": picks_result.raw,
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
        for pick in picks_result.picks:
            rec = Recommendation(
                run_id=run.run_id,
                stock_code=pick.get("stock_code", ""),
                stock_name=pick.get("stock_name", ""),
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

        # 5. 자동매매 실행 (auto_trade가 켜진 구독자)
        self._execute_auto_trades(strategy, run)

        return run

    def _execute_auto_trades(self, strategy: Strategy, run: RecommendationRun) -> None:
        """자동매매 구독자에 대해 매수 주문 실행."""
        from app.services.trading.executor import TradeExecutor

        subscriptions = [
            s for s in strategy.user_strategies
            if s.is_active and s.is_auto_trade
        ]

        if not subscriptions:
            return

        executor = TradeExecutor(self.db)
        for sub in subscriptions:
            try:
                executor.execute_buys_for_run(sub, run)
            except Exception as e:
                logger.error(
                    "Auto trade failed for user=%s strategy=%s: %s",
                    sub.user_id, strategy.strategy_id, e,
                )
