"""
KIS API 실제 연동 테스트 (네이티브 httpx 클라이언트).
실행: python -m tests.test_kis_connection
"""
import sys
import logging
from decimal import Decimal

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
# httpx 요청 로그 숨기기
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

TEST_STOCKS = ["005930", "000660", "035420"]


def test_current_price(client, code: str) -> bool:
    try:
        price = client.get_current_price(code)
        assert isinstance(price, Decimal) and price > 0
        logger.info("  [OK] get_current_price(%s) = %s", code, price)
        return True
    except Exception as e:
        logger.error("  [FAIL] get_current_price(%s): %s", code, e)
        return False


def test_ohlcv(client, code: str) -> bool:
    try:
        bars = client.get_ohlcv(code)
        assert len(bars) > 0
        b0, b_last = bars[0], bars[-1]
        logger.info(
            "  [OK] get_ohlcv(%s): %d bars | 최신=%s(%s) | 최오래=%s(%s)",
            code, len(bars), b0.close, b0.date, b_last.close, b_last.date,
        )
        # 최신 봉이 [0]이어야 함
        assert b0.date >= b_last.date, f"bars 순서 오류: [0]={b0.date} < [-1]={b_last.date}"
        return True
    except Exception as e:
        logger.error("  [FAIL] get_ohlcv(%s): %s", code, e)
        return False


def test_investor_trend(client, code: str) -> bool:
    try:
        inv = client.get_investor_trend(code)
        assert all(k in inv for k in ["frgn_net_buy_1d", "frgn_net_buy_5d",
                                       "orgn_net_buy_1d", "orgn_net_buy_5d"])
        logger.info(
            "  [OK] get_investor_trend(%s): 외국인1d=%+d 5d=%+d | 기관1d=%+d 5d=%+d",
            code,
            inv["frgn_net_buy_1d"], inv["frgn_net_buy_5d"],
            inv["orgn_net_buy_1d"], inv["orgn_net_buy_5d"],
        )
        return True
    except Exception as e:
        logger.error("  [FAIL] get_investor_trend(%s): %s", code, e)
        return False


def test_stock_info(client, code: str) -> bool:
    try:
        info = client.get_stock_info(code)
        assert info["stock_code"] == code
        logger.info(
            "  [OK] get_stock_info(%s): price=%s rsi=%s ma5=%s ma20=%s "
            "frgn1d=%+d orgn1d=%+d",
            code,
            info["current_price"], info["rsi_14"],
            info["ma5"], info["ma20"],
            info["frgn_net_buy_1d"], info["orgn_net_buy_1d"],
        )
        return True
    except Exception as e:
        logger.error("  [FAIL] get_stock_info(%s): %s", code, e)
        return False


def test_balance(client) -> bool:
    try:
        items = client.get_balance()
        logger.info("  [OK] get_balance(): %d positions", len(items))
        for item in items[:3]:
            logger.info("    %s %s qty=%d avg=%.0f pnl=%.2f%%",
                        item.stock_code, item.stock_name,
                        item.quantity, item.avg_price, item.pnl_pct)
        return True
    except Exception as e:
        logger.error("  [FAIL] get_balance(): %s", e)
        return False


def test_buyable_cash(client) -> bool:
    try:
        cash = client.get_buyable_cash()
        logger.info("  [OK] get_buyable_cash(): %s 원", cash)
        return True
    except Exception as e:
        logger.error("  [FAIL] get_buyable_cash(): %s", e)
        return False


def main():
    from app.services.kis.client import get_kis_client
    from app.core.config import get_settings

    settings = get_settings()
    logger.info("=== KIS API 연동 테스트 (네이티브) ===")
    logger.info("Account: %s", settings.kis_account_no)

    try:
        client = get_kis_client()
        logger.info("KISClient 생성 성공")
    except Exception as e:
        logger.error("KISClient 생성 실패: %s", e)
        sys.exit(1)

    results = []

    logger.info("\n--- 시세 조회 테스트 ---")
    for code in TEST_STOCKS:
        logger.info("[%s]", code)
        results.append(test_current_price(client, code))
        results.append(test_ohlcv(client, code))
        results.append(test_investor_trend(client, code))

    logger.info("\n--- 통합 종목 정보 ---")
    results.append(test_stock_info(client, TEST_STOCKS[0]))

    logger.info("\n--- 계좌 조회 테스트 ---")
    results.append(test_balance(client))
    results.append(test_buyable_cash(client))

    passed = sum(results)
    total  = len(results)
    logger.info("\n=== 결과: %d/%d 통과 ===", passed, total)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
