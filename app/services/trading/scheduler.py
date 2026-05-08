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






# ------------------------------------------------------------------ #
# 서버 재시작 시 누락 작업 catch-up
# ------------------------------------------------------------------ #

def run_startup_catchup() -> None:
    """
    서버 재시작 시 호출. 스케줄러 다운 중 누락된 작업을 보완한다.

    - 전략 실행: _should_run 체크 → 필요한 전략만 실행 (중복 방지)
    - 검증: 멱등성 보장 → 항상 실행 (이미 검증된 건 스킵됨)
    - 포지션 모니터링: 장중에만 의미 있으므로 조건부 실행
    - stock_master: 7일 이상 미갱신 시 갱신
    """
    import threading
    from datetime import datetime, timezone as _tz

    def _run() -> None:
        import time as _time
        _time.sleep(5)  # 서버 완전 기동 대기
        logger.info("=== Startup catch-up start ===")

        # 1. 검증 (멱등 — 항상 안전)
        try:
            job_verify_recommendations()
        except Exception as e:
            logger.error("Catchup verify failed: %s", e)

        # 2. 전략 실행 (_should_run 내부에서 체크)
        try:
            job_run_strategies()
        except Exception as e:
            logger.error("Catchup strategy run failed: %s", e)

        # 3. stock_master: 마지막 갱신 7일 초과 시
        try:
            from app.models.stock_master import StockMaster
            with SessionLocal() as db:
                last = db.query(StockMaster.updated_at).order_by(
                    StockMaster.updated_at.desc()
                ).first()
                if last is None or (
                    datetime.now(_tz.utc) - last[0]
                ).days >= 7:
                    logger.info("stock_master outdated, running update")
                    job_update_stock_master()
        except Exception as e:
            logger.error("Catchup stock_master check failed: %s", e)

        logger.info("=== Startup catch-up done ===")

    threading.Thread(target=_run, daemon=True).start()


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

    _scheduler.start()
    logger.info("Scheduler started")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
