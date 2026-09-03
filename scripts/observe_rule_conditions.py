"""
규칙 전략 조건의 **발동 빈도 관측** (daily_price 기반).

⚠️ 이건 파라미터 튜닝 도구가 아니다. 의도적으로 성과(수익률)를 계산하지 않는다.

배경: `[TEST] 규칙 모멘텀 (AI 대조군)`의 존재 이유는 "AI가 단순 규칙보다 나은가"를
재는 것이다. 규칙 쪽만 과거 데이터로 최적화하면 AI는 튜닝되지 않은 채로 비교당해
대조군 설계("변수는 AI 사용 여부 하나")가 깨진다. 그래서 지금 단계에서는
**어떤 파라미터가 더 벌었는지 보지 않고**, 조건이 얼마나 자주·몇 종목이나
발동하는지만 본다.

여기서 얻으려는 것:
- 20일 신고가 조건이 현실적인 빈도로 발동하는가 (매번 0개거나 매번 100개면 무의미)
- 기간(20/40/60일)에 따라 발동 종목 수가 어떻게 변하는가
- 거래대금 컷을 넣으면 얼마나 걸러지는가 — 대형주 유니버스에서 무효 필터라는
  판단이 실제 데이터로도 맞는지
- 국면(상승/하락일)에 따라 발동 빈도가 어떻게 쏠리는가

나중에 실제로 튜닝하게 되면 "몇 개 후보 중에 골랐는지"를 알아야 과최적화 정도를
가늠할 수 있는데, 그 분모를 미리 확보해두는 의미도 있다.

구현 메모: 전체 구간을 메모리에 올리면 6년치가 460만 행이라 미니PC에서 터진다.
날짜순 스트리밍 + 종목별 최근 N개 종가만 유지하는 롤링 윈도로 처리한다
(메모리 = 종목수 × 윈도크기).

사용법:
  .venv/bin/python -m scripts.observe_rule_conditions
  .venv/bin/python -m scripts.observe_rule_conditions --market KOSDAQ --top-n 150
"""
import argparse
import logging
import sys
from collections import defaultdict, deque

from sqlalchemy import select, func

from app.core.database import SessionLocal
from app.models.daily_price import DailyPrice

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

BREAKOUT_PERIODS = (20, 40, 60)
_MA_SHORT = 5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KOSPI", help="KOSPI / KOSDAQ / 전체는 ALL")
    ap.add_argument("--top-n", type=int, default=200, help="날짜별 시총 상위 N (기본 200)")
    args = ap.parse_args()
    market = None if args.market == "ALL" else args.market

    db = SessionLocal()
    try:
        if not db.scalar(select(func.count()).select_from(DailyPrice)):
            logger.error("daily_price가 비어 있다. scripts.load_krx_daily 먼저 실행할 것.")
            return 1

        q = select(
            DailyPrice.trade_date, DailyPrice.stock_code,
            DailyPrice.close, DailyPrice.trade_value,
            DailyPrice.market_cap, DailyPrice.change_pct,
        ).where(DailyPrice.close.isnot(None)).order_by(DailyPrice.trade_date)
        if market:
            q = q.where(DailyPrice.market == market)

        window = max(BREAKOUT_PERIODS)
        history: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))

        counts = {p: [] for p in BREAKOUT_PERIODS}       # 날짜별 발동 종목 수
        rank_dist: list[int] = []                        # 20일 발동 건의 거래대금 순위
        up_hits: list[int] = []
        down_hits: list[int] = []
        n_days, first_date, last_date = 0, None, None

        cur_date, buf = None, []

        def flush(day, rows):
            """하루치 처리 — 유니버스 확정 → 조건 판정 → 히스토리 갱신."""
            nonlocal n_days, first_date, last_date
            if not rows:
                return
            n_days += 1
            if first_date is None:
                first_date = day
            last_date = day

            universe = {
                r.stock_code for r in
                sorted([r for r in rows if r.market_cap], key=lambda r: -r.market_cap)[:args.top_n]
            }
            value_rank = {
                r.stock_code: i for i, r in
                enumerate(sorted(rows, key=lambda r: -(r.trade_value or 0)), start=1)
            }

            hit20 = 0
            for period in BREAKOUT_PERIODS:
                cnt = 0
                for r in rows:
                    if r.stock_code not in universe:
                        continue
                    prior = history[r.stock_code]          # 오늘 이전 종가들
                    if len(prior) < period:
                        continue
                    close = float(r.close)
                    prior_high = max(list(prior)[-period:])
                    recent = list(prior)[-(_MA_SHORT - 1):] + [close]
                    ma5 = sum(recent) / len(recent)
                    if close > prior_high and close > ma5:
                        cnt += 1
                        if period == 20:
                            rk = value_rank.get(r.stock_code)
                            if rk:
                                rank_dist.append(rk)
                counts[period].append(cnt)
                if period == 20:
                    hit20 = cnt

            # 시장 방향 대용: 유니버스 등락률 중앙값 (지수를 따로 안 긁어도 되고 정의가 일치)
            chg = sorted(float(r.change_pct) for r in rows
                         if r.stock_code in universe and r.change_pct is not None)
            if chg:
                (up_hits if chg[len(chg) // 2] >= 0 else down_hits).append(hit20)

            for r in rows:
                history[r.stock_code].append(float(r.close))

        for row in db.execute(q).yield_per(20000):
            if row.trade_date != cur_date:
                flush(cur_date, buf)
                cur_date, buf = row.trade_date, []
            buf.append(row)
        flush(cur_date, buf)

        logger.info("관측 구간: %s ~ %s (%d거래일, 유니버스=%s 시총상위 %d)",
                    first_date, last_date, n_days, args.market, args.top_n)

        logger.info("\n%-12s %10s %10s %8s", "조건", "평균종목", "0건일", "최대")
        logger.info("-" * 44)
        for p in BREAKOUT_PERIODS:
            # 워밍업 구간 제외 — 앞 p거래일은 비교할 과거 종가가 없어 무조건 0건이다
            warm = counts[p][p:]
            if not warm:
                logger.info("%-12s %s", f"{p}일 신고가", "구간 부족 (워밍업 미달)")
                continue
            logger.info("%-12s %10.1f %8d일 %8d",
                        f"{p}일 신고가", sum(warm) / len(warm),
                        sum(1 for x in warm if x == 0), max(warm))

        if rank_dist:
            rank_dist.sort()
            n = len(rank_dist)
            logger.info("\n[거래대금 컷 실효성] 20일 신고가 발동 %s건의 시장 내 거래대금 순위", f"{n:,}")
            logger.info("  중앙값 %d위 | 상위100 이내 %.1f%% | 상위300 이내 %.1f%%",
                        rank_dist[n // 2],
                        100 * sum(1 for r in rank_dist if r <= 100) / n,
                        100 * sum(1 for r in rank_dist if r <= 300) / n)
            logger.info("  → '거래대금 상위 100위' 컷이 실제로 걸러내는 비율: %.1f%%",
                        100 * sum(1 for r in rank_dist if r > 100) / n)

        if up_hits or down_hits:
            logger.info("\n[국면별 발동 쏠림] 20일 신고가")
            if up_hits:
                logger.info("  상승일(%4d일) 평균 %.1f종목", len(up_hits), sum(up_hits) / len(up_hits))
            if down_hits:
                logger.info("  하락일(%4d일) 평균 %.1f종목", len(down_hits), sum(down_hits) / len(down_hits))

        logger.info("\n※ 수익률은 의도적으로 계산하지 않는다 — 대조군 설계(변수=AI 사용 여부)를 지키기 위함")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
