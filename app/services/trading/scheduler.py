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


def job_execute_pending_buys() -> None:
    """분석 완료된 run 중 미체결 매수 실행 (매수 전용 스케줄러)."""
    from app.services.trading.executor import TradeExecutor

    try:
        with SessionLocal() as db:
            executor = TradeExecutor(db)
            executor.execute_pending_buys()
    except Exception as e:
        logger.error("Pending buys execution failed: %s", e)
        _notify_error("자동매수 실패", str(e))


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


def job_news_watch_tick() -> None:
    """
    10분마다 호출. 설정된 주기(news_check_interval_min)가 지났으면 뉴스 체크 실행.
    장중(09:00~15:30 평일)에만 동작.
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now()
    # 장외 시간 제외
    if now.weekday() >= 5:
        return
    if not (9 <= now.hour < 15 or (now.hour == 15 and now.minute <= 30)):
        return

    from app.core.database import SessionLocal
    from app.core.config_store import get_config

    with SessionLocal() as db:
        interval_min = int(get_config(db, "news_check_interval_min", "120"))
        last_check = get_config(db, "news_last_check_at", "")

    if last_check:
        try:
            last_dt = datetime.fromisoformat(last_check)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last_dt < timedelta(minutes=interval_min):
                return
        except Exception:
            pass

    try:
        from app.services.news.watcher import run_news_check_and_act
        run_news_check_and_act()
    except Exception as e:
        logger.error("News watch tick failed: %s", e)
        _notify_error("뉴스 감시 tick 실패", str(e))


def job_morning_gate() -> None:
    """08:00 개장 전 리스크 체크 — 미국 야간/선물 급락 or 지정학 이슈 감지 시 당일 매수 차단."""
    try:
        from app.services.news.watcher import morning_gate_check
        morning_gate_check()
    except Exception as e:
        logger.error("Morning gate check failed: %s", e)
        _notify_error("모닝 게이트 잡 실패", str(e))


def job_thesis_check() -> None:
    """보유 포지션 thesis 재검증 (하루 2회: 10:00, 14:00)."""
    try:
        from app.services.news.watcher import check_position_theses
        check_position_theses()
    except Exception as e:
        logger.error("Thesis check failed: %s", e)
        _notify_error("Thesis 재검증 잡 실패", str(e))


def job_verify_news_events() -> None:
    """1일/3일 경과 뉴스 이벤트 + recommendation_runs 실제 시장 영향 검증."""
    try:
        from app.services.news.watcher import verify_news_events, verify_run_market_outcomes
        verify_news_events()
        verify_run_market_outcomes()
    except Exception as e:
        logger.error("News event verification failed: %s", e)
        _notify_error("뉴스 이벤트 검증 잡 실패", str(e))


def job_backup_db() -> None:
    """매일 03:30 pg_dump로 DB 백업, 최근 7개 유지."""
    import subprocess
    from pathlib import Path
    from datetime import datetime

    backup_dir = Path(__file__).resolve().parents[3] / "backups"
    backup_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = backup_dir / f"trading_db_{ts}.sql.gz"

    try:
        from app.core.config import get_settings
        db_url = get_settings().database_url
        # postgresql+asyncpg://user:pass@host/db → user:pass@host/db
        dsn = db_url.replace("postgresql+asyncpg://", "").replace("postgresql://", "")
        # user:pass@host/db 파싱
        at = dsn.rfind("@")
        userpass = dsn[:at]
        hostdb   = dsn[at+1:]
        user, password = (userpass.split(":", 1) + [""])[:2]
        host_part, dbname = hostdb.rsplit("/", 1)
        host = host_part.split(":")[0]
        port = host_part.split(":")[1] if ":" in host_part else "5432"

        env = {"PGPASSWORD": password}
        cmd = [
            "pg_dump",
            "-h", host, "-p", port, "-U", user, dbname,
            "--format=custom", "--compress=9",
        ]
        with open(out_path, "wb") as f:
            subprocess.run(cmd, stdout=f, env={**__import__("os").environ, **env}, check=True)

        size_kb = out_path.stat().st_size // 1024
        logger.info("DB backup done: %s (%d KB)", out_path.name, size_kb)

        # 최근 7개만 유지
        backups = sorted(backup_dir.glob("trading_db_*.sql.gz"))
        for old in backups[:-7]:
            old.unlink()
            logger.info("Removed old backup: %s", old.name)

    except Exception as e:
        logger.error("DB backup failed: %s", e)
        _notify_error("DB 백업 실패", str(e))


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

    # 모닝 게이트: 평일 08:00 — 개장 전 야간 리스크 체크, 이상 시 당일 매수 차단
    _scheduler.add_job(
        job_morning_gate,
        trigger=CronTrigger(day_of_week="mon-fri", hour=8, minute=0),
        id="morning_gate",
        replace_existing=True,
    )

    # 분석 잡: 3일마다 (평일 08:30) — AI 분석 + recommendations 저장만
    _scheduler.add_job(
        job_run_strategies,
        trigger=CronTrigger(day_of_week="mon,wed,fri", hour=8, minute=30),
        id="run_strategies",
        replace_existing=True,
    )

    # 매수 잡: 평일 09:20 — AI 장중 확인 후 매수 실행
    _scheduler.add_job(
        job_execute_pending_buys,
        trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute=20),
        id="execute_pending_buys",
        replace_existing=True,
    )

    # 장중 포지션 모니터링: 09:05~15:20 10분 간격
    _scheduler.add_job(
        job_monitor_positions,
        trigger=CronTrigger(day_of_week="mon-fri", hour="9-15", minute="5,15,25,35,45,55"),
        id="monitor_positions",
        replace_existing=True,
    )

    # 매일 자정: 10일 경과 종목 검증
    _scheduler.add_job(
        job_verify_recommendations,
        trigger=CronTrigger(hour=0, minute=10),
        id="verify_recommendations",
        replace_existing=True,
    )

    # 10분마다 뉴스 감시 tick (장중에만 실제 실행)
    _scheduler.add_job(
        job_news_watch_tick,
        trigger=CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/10"),
        id="news_watch_tick",
        replace_existing=True,
    )

    # thesis 재검증: 평일 10:00, 14:00 (8개씩 그룹 분할 그라운딩)
    _scheduler.add_job(
        job_thesis_check,
        trigger=CronTrigger(day_of_week="mon-fri", hour="10,14", minute=0),
        id="thesis_check",
        replace_existing=True,
    )

    # 매일 장 마감 후: 뉴스 이벤트 시장 영향 검증 (1일/3일 경과분)
    _scheduler.add_job(
        job_verify_news_events,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=0),
        id="verify_news_events",
        replace_existing=True,
    )

    # 매일 03:30: DB 백업 (최근 7개 유지)
    _scheduler.add_job(
        job_backup_db,
        trigger=CronTrigger(hour=3, minute=30),
        id="backup_db",
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
