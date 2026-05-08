"""
Gemini 4단계 AI 파이프라인 테스트.
실행: python -m tests.test_gemini_pipeline [--stage 1]
  --stage 1  : 1단계만 실행 (빠른 연결 확인)
  --stage all: 전체 4단계 실행 (RPD 소모 주의)
"""
import sys
import json
import logging
import argparse
from datetime import date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def test_stage1(analyzer) -> tuple[bool, object]:
    logger.info("[Stage 1] 매크로 분석 시작...")
    try:
        result = analyzer.stage1_macro(date.today())
        logger.info("  theme    : %s", result.market_theme)
        logger.info("  summary  : %s", result.macro_summary[:120])
        logger.info("  key_factors (%d): %s", len(result.key_factors), result.key_factors[:3])
        logger.info("  risk_factors (%d): %s", len(result.risk_factors), result.risk_factors[:2])
        logger.info("  [OK] Stage 1 완료")
        return True, result
    except Exception as e:
        logger.error("  [FAIL] Stage 1: %s", e)
        return False, None


def test_stage2(analyzer, macro) -> tuple[bool, object]:
    logger.info("[Stage 2] 역사적 유사 시기 탐색...")
    try:
        result = analyzer.stage2_historical(macro)
        logger.info("  matches: %d건", len(result.historical_matches))
        if result.historical_matches:
            m = result.historical_matches[0]
            logger.info("  첫 번째: %s (유사도=%s)", m.get("period"), m.get("similarity_score"))
        logger.info("  [OK] Stage 2 완료")
        return True, result
    except Exception as e:
        logger.error("  [FAIL] Stage 2: %s", e)
        return False, None


def test_stage3(analyzer, macro, historical) -> tuple[bool, object]:
    logger.info("[Stage 3] 산업 흐름 분석...")
    try:
        result = analyzer.stage3_industry(macro, historical)
        logger.info("  expected_beneficiary: %s", result.expected_beneficiary[:80])
        logger.info("  past_winners (%d): %s",
                    len(result.past_winners),
                    [w.get("industry") for w in result.past_winners[:3]])
        logger.info("  [OK] Stage 3 완료")
        return True, result
    except Exception as e:
        logger.error("  [FAIL] Stage 3: %s", e)
        return False, None


def test_stage4(analyzer, macro, industry) -> tuple[bool, object]:
    logger.info("[Stage 4] 종목 선정...")
    # 간단한 mock 종목 데이터 (KIS API 없이 테스트)
    mock_stocks = [
        {
            "stock_code": "005930", "stock_name": "삼성전자",
            "current_price": 72000, "rsi_14": 52.3,
            "ma5": 71500, "ma20": 70000, "ma60": 68000,
            "avg_volume_20d": 15000000,
            "recent_ohlcv": [
                {"date": "2026-05-07", "open": 71500, "high": 72500,
                 "low": 71000, "close": 72000, "volume": 14000000},
            ],
        },
        {
            "stock_code": "000660", "stock_name": "SK하이닉스",
            "current_price": 195000, "rsi_14": 61.8,
            "ma5": 193000, "ma20": 185000, "ma60": 175000,
            "avg_volume_20d": 5000000,
            "recent_ohlcv": [
                {"date": "2026-05-07", "open": 193000, "high": 196000,
                 "low": 192000, "close": 195000, "volume": 4800000},
            ],
        },
    ]
    from decimal import Decimal

    class MockStrategy:
        hold_days = 10
        target_pct = Decimal("10")
        stop_loss_pct = Decimal("5")
        min_probability = Decimal("60")
        pick_count = 3

    try:
        result = analyzer.stage4_picks(
            macro=macro,
            industry=industry,
            stocks_data=mock_stocks,
            hold_days=MockStrategy.hold_days,
            target_pct=MockStrategy.target_pct,
            stop_loss_pct=MockStrategy.stop_loss_pct,
            min_probability=MockStrategy.min_probability,
            pick_count=MockStrategy.pick_count,
        )
        logger.info("  picks: %d개", len(result.picks))
        for p in result.picks:
            logger.info(
                "    %s %s 목표=%s 손절=%s 확률=%s%%",
                p.get("stock_code"), p.get("stock_name"),
                p.get("target_price"), p.get("stop_loss_price"),
                p.get("ai_probability"),
            )
        logger.info("  [OK] Stage 4 완료")
        return True, result
    except Exception as e:
        logger.error("  [FAIL] Stage 4: %s", e)
        return False, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", default="1",
                        help="실행할 단계: 1, 2, 3, 4, all (기본: 1)")
    args = parser.parse_args()
    run_all = args.stage == "all"
    max_stage = int(args.stage) if not run_all else 4

    from app.services.gemini.analyzer import GeminiAnalyzer
    from app.core.config import get_settings

    settings = get_settings()
    logger.info("=== Gemini 파이프라인 테스트 (stage=%s) ===", args.stage)
    logger.info("GEMINI_API_KEY: %s***", settings.gemini_api_key[:8])

    try:
        analyzer = GeminiAnalyzer()
    except Exception as e:
        logger.error("GeminiAnalyzer 초기화 실패: %s", e)
        sys.exit(1)

    results = []
    macro = historical = industry = None

    ok, macro = test_stage1(analyzer)
    results.append(ok)
    if not ok or max_stage < 2:
        _print_summary(results)
        return

    ok, historical = test_stage2(analyzer, macro)
    results.append(ok)
    if not ok or max_stage < 3:
        _print_summary(results)
        return

    ok, industry = test_stage3(analyzer, macro, historical)
    results.append(ok)
    if not ok or max_stage < 4:
        _print_summary(results)
        return

    ok, _ = test_stage4(analyzer, macro, industry)
    results.append(ok)
    _print_summary(results)


def _print_summary(results: list[bool]):
    passed = sum(results)
    total = len(results)
    logger.info("\n=== 결과: %d/%d 단계 통과 ===", passed, total)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
