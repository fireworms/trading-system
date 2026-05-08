"""
전체 플로우 테스트: 전략 생성 → KIS 시세 수집 → Gemini AI 분석 → 종목 추천 → DB 저장

실행: python -m tests.test_full_flow
옵션:
  --stocks N   : 후보 종목 수 제한 (기본 5, 전체는 20)
  --picks N    : AI가 추천할 종목 수 (기본 3)
  --cleanup    : 테스트 후 생성된 전략/추천 데이터 삭제
"""
import sys
import time
import logging
import argparse
from decimal import Decimal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

STRATEGY_NAME = "[TEST] AI 성장주 전략"


def create_strategy(db, user_id) -> "Strategy":
    from app.models.strategy import Strategy

    existing = db.query(Strategy).filter(Strategy.name == STRATEGY_NAME).first()
    if existing:
        logger.info("기존 전략 사용: %s (%s)", existing.name, existing.strategy_id)
        return existing

    strategy = Strategy(
        created_by=user_id,
        name=STRATEGY_NAME,
        description="테스트용 AI 분석 전략 - 매크로 + 역사적 패턴 기반 성장주 발굴",
        hold_days=10,
        target_pct=Decimal("10.00"),
        stop_loss_pct=Decimal("5.00"),
        min_probability=Decimal("60.00"),
        pick_count=3,
        run_interval_days=3,
        is_active=True,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    logger.info("전략 생성: %s (%s)", strategy.name, strategy.strategy_id)
    return strategy


def collect_stock_data(codes: list[str]) -> list[dict]:
    from app.services.kis.client import get_kis_client

    client = get_kis_client()
    logger.info("KIS API 시세 수집 시작 (%d개 종목)...", len(codes))
    results = []
    for i, code in enumerate(codes, 1):
        try:
            t0 = time.time()
            info = client.get_stock_info(code)
            elapsed = time.time() - t0
            logger.info(
                "  [%d/%d] %s | 현재가=%s | RSI=%s | MA5=%s (%.1fs)",
                i, len(codes), code,
                info["current_price"], info["rsi_14"], info["ma5"], elapsed,
            )
            results.append(info)
        except Exception as e:
            logger.warning("  [%d/%d] %s 수집 실패: %s", i, len(codes), code, e)
    logger.info("시세 수집 완료: %d/%d개", len(results), len(codes))
    return results


def run_ai_pipeline(strategy, stock_data: list[dict]) -> tuple:
    from app.services.gemini.analyzer import GeminiAnalyzer

    analyzer = GeminiAnalyzer()

    logger.info("\n[Stage 1] 매크로 분석...")
    t0 = time.time()
    macro = analyzer.stage1_macro()
    logger.info("  theme: %s (%.1fs)", macro.market_theme, time.time() - t0)
    logger.info("  summary: %s", macro.macro_summary[:100])

    logger.info("\n[Stage 2] 역사적 유사 시기...")
    t0 = time.time()
    historical = analyzer.stage2_historical(macro)
    logger.info("  %d건 (%.1fs)", len(historical.historical_matches), time.time() - t0)
    for m in historical.historical_matches[:2]:
        logger.info("    - %s (유사도 %s)", m.get("period"), m.get("similarity_score"))

    logger.info("\n[Stage 3] 산업 흐름 분석...")
    t0 = time.time()
    industry = analyzer.stage3_industry(macro, historical)
    logger.info("  수혜 섹터: %s (%.1fs)", industry.expected_beneficiary[:60], time.time() - t0)

    logger.info("\n[Stage 4] 종목 선정 (%d개 후보 → %d개 추천)...",
                len(stock_data), strategy.pick_count)
    t0 = time.time()
    picks = analyzer.stage4_picks(
        macro=macro,
        industry=industry,
        stocks_data=stock_data,
        hold_days=strategy.hold_days,
        target_pct=strategy.target_pct,
        stop_loss_pct=strategy.stop_loss_pct,
        min_probability=strategy.min_probability,
        pick_count=strategy.pick_count,
    )
    logger.info("  %d개 선정 (%.1fs)", len(picks.picks), time.time() - t0)

    return macro, historical, industry, picks


def save_results(db, strategy, macro, historical, industry, picks) -> "RecommendationRun":
    from datetime import date
    from app.models.recommendation import RecommendationRun, MacroAnalysis, Recommendation
    import json

    run = RecommendationRun(
        strategy_id=strategy.strategy_id,
        run_date=date.today(),
        ai_model_used="gemini-3-flash-preview",
        prompt_version="v1.0",
        raw_response={
            "macro": macro.raw,
            "historical": historical.raw,
            "industry": industry.raw,
            "picks": picks.raw,
        },
    )
    db.add(run)
    db.flush()

    db.add(MacroAnalysis(
        run_id=run.run_id,
        current_situation=macro.macro_summary,
        historical_matches=historical.raw,
        industry_mapping=industry.raw,
        expected_beneficiary=industry.expected_beneficiary,
    ))

    for pick in picks.picks:
        db.add(Recommendation(
            run_id=run.run_id,
            stock_code=pick.get("stock_code", ""),
            stock_name=pick.get("stock_name", ""),
            target_price=Decimal(str(pick["target_price"])) if pick.get("target_price") else None,
            stop_loss_price=Decimal(str(pick["stop_loss_price"])) if pick.get("stop_loss_price") else None,
            ai_probability=Decimal(str(pick["ai_probability"])) if pick.get("ai_probability") else None,
            ai_reason=pick.get("ai_reason"),
            historical_basis=pick.get("historical_basis"),
            risk_factors=pick.get("risk_factors"),
            rank=pick.get("rank"),
        ))

    db.commit()
    db.refresh(run)
    return run


def print_summary(run, picks):
    logger.info("\n" + "=" * 60)
    logger.info("AI 종목 추천 결과")
    logger.info("=" * 60)
    logger.info("run_id : %s", run.run_id)
    logger.info("run_date: %s", run.run_date)
    logger.info("")
    for i, pick in enumerate(picks.picks, 1):
        code = pick.get("stock_code", "")
        name = pick.get("stock_name", "")
        prob = pick.get("ai_probability", "")
        target = pick.get("target_price", "")
        stop = pick.get("stop_loss_price", "")
        reason = (pick.get("ai_reason") or "")[:80]
        logger.info("[%d] %s %s", i, code, name)
        logger.info("     확률=%s%% | 목표가=%s | 손절가=%s", prob, target, stop)
        logger.info("     근거: %s", reason)
    logger.info("=" * 60)


def cleanup(db, strategy):
    from app.models.recommendation import RecommendationRun, MacroAnalysis, Recommendation
    from app.models.strategy import Strategy

    runs = db.query(RecommendationRun).filter(
        RecommendationRun.strategy_id == strategy.strategy_id
    ).all()
    for run in runs:
        db.delete(run)
    db.delete(strategy)
    db.commit()
    logger.info("테스트 데이터 삭제 완료 (run %d건)", len(runs))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stocks", type=int, default=5,
                        help="후보 종목 수 (기본 5, 최대 20)")
    parser.add_argument("--picks", type=int, default=3,
                        help="AI 추천 종목 수 (기본 3)")
    parser.add_argument("--cleanup", action="store_true",
                        help="테스트 완료 후 데이터 삭제")
    args = parser.parse_args()

    from app.core.database import SessionLocal
    from app.models.user import User
    from app.models.candidate_stock import CandidateStock

    db = SessionLocal()
    total_start = time.time()

    try:
        # 유저 확인
        user = db.query(User).first()
        if not user:
            logger.error("유저가 없습니다. 먼저 유저를 생성하세요.")
            sys.exit(1)
        logger.info("유저: %s (%s)", user.username, user.role)

        # 전략 생성
        strategy = create_strategy(db, user.user_id)
        if args.picks != 3:
            strategy.pick_count = args.picks

        # 후보 종목 로드 (DB, 최대 args.stocks개)
        rows = (
            db.query(CandidateStock)
            .filter(CandidateStock.is_active == True)
            .order_by(CandidateStock.stock_id)
            .limit(args.stocks)
            .all()
        )
        candidate_codes = [r.stock_code for r in rows]
        logger.info("\n후보 종목 풀 (%d개): %s", len(candidate_codes),
                    [f"{r.stock_code}({r.stock_name})" for r in rows])

        # KIS 시세 수집
        logger.info("")
        stock_data = collect_stock_data(candidate_codes)
        if not stock_data:
            logger.error("시세 수집 실패")
            sys.exit(1)

        # AI 파이프라인
        logger.info("")
        macro, historical, industry, picks = run_ai_pipeline(strategy, stock_data)

        if not picks.picks:
            logger.warning("AI가 추천 종목을 선정하지 못했습니다.")
            logger.warning("excluded_reason: %s", picks.excluded_reason)
            sys.exit(1)

        # DB 저장
        logger.info("\nDB 저장 중...")
        run = save_results(db, strategy, macro, historical, industry, picks)
        logger.info("저장 완료: run_id=%s", run.run_id)

        # 텔레그램 알림 (전략 생성자에게 전송)
        from app.services.telegram.notifier import get_notifier
        from app.models.user import User
        notifier = get_notifier()
        if notifier and strategy.created_by:
            creator = db.get(User, strategy.created_by)
            if creator and creator.telegram_chat_id:
                notifier.notify_recommendations(
                    chat_id=creator.telegram_chat_id,
                    strategy_name=strategy.name,
                    run_date=run.run_date,
                    market_theme=macro.market_theme,
                    picks=picks.picks,
                )
                logger.info("텔레그램 알림 전송 완료")

        # 결과 출력
        print_summary(run, picks)

        total = time.time() - total_start
        logger.info("\n전체 소요시간: %.1f초", total)

        # 정리
        if args.cleanup:
            cleanup(db, strategy)

    finally:
        db.close()


if __name__ == "__main__":
    main()
