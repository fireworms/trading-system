"""
APScheduler 기반 스케줄러.
- 3일마다: 활성 전략 AI 분석 실행
- 매일 장중(09:05): 보유 포지션 모니터링
- 10일마다(00:00): 추천 종목 검증
"""
import logging
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.strategy import Strategy
from app.models.recommendation import RecommendationRun

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


# ------------------------------------------------------------------ #
# 잡 함수
# ------------------------------------------------------------------ #

def _notify_error(title: str, detail: str) -> None:
    try:
        from app.services.telegram.notifier import notify_admins_error
        notify_admins_error(title, detail)
    except Exception as e:
        logger.error("Failed to send error notification: %s", e)


def _should_run(db, strategy: Strategy) -> bool:
    """run_interval_days 기준으로 실행 여부 판단."""
    last_run = db.scalar(
        select(RecommendationRun.run_date)
        .where(RecommendationRun.strategy_id == strategy.strategy_id)
        .order_by(RecommendationRun.run_date.desc())
        .limit(1)
    )
    if last_run is None:
        return True  # 한 번도 실행 안 됨
    return date.today() >= last_run + timedelta(days=strategy.run_interval_days)


def job_run_strategies() -> None:
    """활성 전략 전체 실행 — run_interval_days 경과한 전략만."""
    from app.services.trading.runner import StrategyRunner

    with SessionLocal() as db:
        strategies = db.scalars(
            select(Strategy).where(Strategy.is_active == True)
        ).all()

        due = [s for s in strategies if _should_run(db, s)]
        logger.info("Scheduled: %d/%d strategies due", len(due), len(strategies))

        runner = StrategyRunner(db)
        for strategy in due:
            try:
                runner.run_strategy(strategy)
            except Exception as e:
                logger.error("Strategy run failed: %s - %s", strategy.name, e)
                _notify_error(
                    f"전략 실행 실패: {strategy.name}",
                    str(e),
                )


def job_monitor_positions() -> None:
    """보유 포지션 모니터링 (매일 장중)."""
    from app.services.trading.executor import TradeExecutor

    try:
        with SessionLocal() as db:
            executor = TradeExecutor(db)
            executor.monitor_positions()
    except Exception as e:
        logger.error("Position monitor failed: %s", e)
        _notify_error("포지션 모니터링 실패", str(e))


def job_verify_recommendations() -> None:
    """추천 종목 결과 검증 (10일 경과 종목)."""
    from app.services.trading.verifier import run_verifications

    try:
        with SessionLocal() as db:
            run_verifications(db)
    except Exception as e:
        logger.error("Verification job failed: %s", e)
        _notify_error("추천 검증 작업 실패", str(e))


def job_update_stock_master() -> None:
    """주 1회 KIS MST 파일로 stock_master 갱신 + 지수 구성종목 캐시 갱신."""
    from app.services.stock_master.updater import update_stock_master
    from app.services.stock_master.index_constituents import refresh_index_cache

    try:
        with SessionLocal() as db:
            result = update_stock_master(db)
            logger.info("stock_master update done: %s", result)

        idx = refresh_index_cache()
        logger.info("index cache refreshed: %s", idx)
    except Exception as e:
        logger.error("stock_master update failed: %s", e)
        _notify_error("stock_master 업데이트 실패", str(e))


def job_refresh_candidates() -> None:
    """3일마다 장 마감 후 candidate_stocks 자동 선별."""
    from app.services.stock_master.updater import refresh_candidate_stocks

    try:
        with SessionLocal() as db:
            result = refresh_candidate_stocks(db)
            logger.info("candidate refresh done: %s", result)
    except Exception as e:
        logger.error("candidate refresh failed: %s", e)
        _notify_error("후보 종목 갱신 실패", str(e))


# ------------------------------------------------------------------ #
# 스케줄러 시작/종료
# ------------------------------------------------------------------ #

def start_scheduler() -> None:
    global _scheduler

    if _scheduler and _scheduler.running:
        logger.warning("Scheduler already running")
        return

    _scheduler = BackgroundScheduler(timezone="Asia/Seoul")

    # 3일마다 전략 실행 (평일 08:30)
    _scheduler.add_job(
        job_run_strategies,
        trigger=CronTrigger(day_of_week="mon,wed,fri", hour=8, minute=30),
        id="run_strategies",
        replace_existing=True,
    )

    # 매일 장중 포지션 모니터링 (09:05, 12:00, 15:10)
    for hour, minute in [(9, 5), (12, 0), (15, 10)]:
        _scheduler.add_job(
            job_monitor_positions,
            trigger=CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
            id=f"monitor_{hour}{minute:02d}",
            replace_existing=True,
        )

    # 매일 자정: 10일 경과 종목 검증
    _scheduler.add_job(
        job_verify_recommendations,
        trigger=CronTrigger(hour=0, minute=10),
        id="verify_recommendations",
        replace_existing=True,
    )

    # 매주 일요일 03:00: stock_master 전체 갱신
    _scheduler.add_job(
        job_update_stock_master,
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=0),
        id="update_stock_master",
        replace_existing=True,
    )

    # 월/수/금 15:30 (장 마감 후): 후보 종목 풀 갱신
    _scheduler.add_job(
        job_refresh_candidates,
        trigger=CronTrigger(day_of_week="mon,wed,fri", hour=15, minute=30),
        id="refresh_candidates",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
