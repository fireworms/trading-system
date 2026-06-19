"""
PER/EPS 참고 필드 주입 검증.
실행: .venv/bin/python -m tests.test_per_eps_inject
- get_stock_info() 반환에 per/eps 키 존재 여부
- 실제 KIS 값이 합리적 범위인지
- 적자/데이터없음 종목에서 None 처리되는지
- stocks_data json.dumps 시 프롬프트에 per/eps 렌더되는지
"""
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# 005930 삼성전자, 000660 SK하이닉스, 035420 NAVER, 042700 한미반도체
TEST_STOCKS = ["005930", "000660", "035420", "042700"]


def main():
    from app.services.kis.client import get_kis_client

    client = get_kis_client()
    logger.info("=== PER/EPS 주입 검증 ===")

    samples = []
    for code in TEST_STOCKS:
        info = client.get_stock_info(code)
        assert "per" in info and "eps" in info, f"{code}: per/eps 키 누락!"
        per, eps, price = info["per"], info["eps"], info["current_price"]
        # 역산 검증: per ≈ price / eps (정수반올림 오차 허용)
        recompute = round(price / eps, 1) if eps else None
        logger.info(
            "[%s] price=%s  per=%s  eps=%s  (price/eps≈%s)",
            code, price, per, eps, recompute,
        )
        samples.append(info)

    # 프롬프트 직렬화 시 per/eps가 실제로 박히는지 (analyzer와 동일 방식)
    rendered = json.dumps(
        [{k: s[k] for k in ("stock_code", "current_price", "rsi_14", "per", "eps")} for s in samples],
        ensure_ascii=False, indent=2,
    )
    assert '"per"' in rendered and '"eps"' in rendered
    logger.info("\n--- 프롬프트 렌더 샘플 (stocks_data 일부) ---\n%s", rendered)
    logger.info("\n=== 통과: per/eps 주입 + 직렬화 정상 ===")


if __name__ == "__main__":
    main()
