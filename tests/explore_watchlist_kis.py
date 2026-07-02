"""
관심종목 분석 탭용 KIS 엔드포인트 실응답 필드 탐사 (1회성).
실행: .venv/bin/python -m tests.explore_watchlist_kis
- 재무비율 FHKST66430300 / 손익계산서 FHKST66430200 / 종목추정실적 HHKST668300C0
- inquire-investor FHKST01010900 일별 raw 행 (날짜/금액 필드 확인)
- inquire-price의 외국인 소진율(hts_frgn_ehrt) 존재 확인
"""
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

CODE = "005930"  # 삼성전자


def dump(title: str, data):
    print(f"\n{'='*70}\n### {title}\n{'='*70}")
    print(json.dumps(data, ensure_ascii=False, indent=1)[:3500])


def main():
    from app.services.kis.client import get_kis_client
    client = get_kis_client()

    # 1) 재무비율 (분기)
    try:
        data = client._get(
            "/uapi/domestic-stock/v1/finance/financial-ratio",
            "FHKST66430300",
            {"FID_DIV_CLS_CODE": "1", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": CODE},
        )
        dump("재무비율 FHKST66430300 (분기)", data.get("output", data)[:4] if isinstance(data.get("output"), list) else data)
    except Exception as e:
        print(f"재무비율 실패: {e}")

    # 2) 손익계산서 (분기)
    try:
        data = client._get(
            "/uapi/domestic-stock/v1/finance/income-statement",
            "FHKST66430200",
            {"FID_DIV_CLS_CODE": "1", "fid_cond_mrkt_div_code": "J", "fid_input_iscd": CODE},
        )
        dump("손익계산서 FHKST66430200 (분기)", data.get("output", data)[:4] if isinstance(data.get("output"), list) else data)
    except Exception as e:
        print(f"손익계산서 실패: {e}")

    # 3) 종목추정실적 (컨센서스)
    try:
        data = client._get(
            "/uapi/domestic-stock/v1/quotations/estimate-perform",
            "HHKST668300C0",
            {"SHT_CD": CODE},
        )
        dump("종목추정실적 HHKST668300C0", data)
    except Exception as e:
        print(f"종목추정실적 실패: {e}")

    # 4) inquire-investor 일별 raw (첫 3행 — 날짜/금액 필드 확인)
    try:
        data = client._get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": CODE},
        )
        rows = data.get("output", [])
        dump(f"inquire-investor FHKST01010900 (총 {len(rows)}행, 첫 3행)", rows[:3])
    except Exception as e:
        print(f"inquire-investor 실패: {e}")

    # 5) inquire-price 외국인 소진율
    try:
        data = client._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": CODE},
        )
        o = data.get("output", {})
        keys = {k: o[k] for k in o if "frgn" in k or "pbr" in k or "per" in k or "eps" in k or "bps" in k}
        dump("inquire-price 외인/밸류 관련 필드", keys)
    except Exception as e:
        print(f"inquire-price 실패: {e}")


if __name__ == "__main__":
    main()
