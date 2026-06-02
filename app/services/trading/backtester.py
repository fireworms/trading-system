"""
백테스트 서비스.
기준 날짜 ±12일, 3일 간격으로 최대 9회 AI Stage4를 과거 데이터로 실행하고 즉시 검증.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.strategy import Strategy
from app.models.recommendation import RecommendationRun, Recommendation, Verification, VerificationResult
from app.models.stock_master import StockMaster
from app.services.gemini.analyzer import GeminiAnalyzer
from app.services.kis.client import get_kis_client

logger = logging.getLogger(__name__)


def _next_weekday(d: date) -> date:
    """주말이면 다음 월요일로 이동."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def generate_backtest_dates(base_date: date, hold_days: int) -> tuple[list[date], list[str]]:
    """
    ±12일, 3일 간격으로 날짜 생성. 유효하지 않은 날짜는 경고와 함께 제외.
    반환: (유효한 날짜 리스트, 스킵 사유 목록)
    """
    today = date.today()
    candidates = []
    seen = set()
    for offset in range(-12, 13, 3):
        d = _next_weekday(base_date + timedelta(days=offset))
        if d not in seen:
            seen.add(d)
            candidates.append(d)
    candidates.sort()

    valid, skipped = [], []
    for d in candidates:
        if d >= today:
            skipped.append(f"{d}: 미래 날짜")
        elif (today - d).days < hold_days:
            skipped.append(f"{d}: hold_days({hold_days}일) 미경과")
        elif (today - d).days > 95:
            skipped.append(f"{d}: KIS OHLCV 범위 초과(최대 95일)")
        else:
            valid.append(d)

    return valid, skipped


class BacktestRunner:
    """전략 백테스트 실행기."""

    def __init__(self, db: Session, version_tag: str = "backtest-v2-momentum"):
        self.db = db
        self.analyzer = GeminiAnalyzer()
        self.version_tag = version_tag

    def run_backtest(self, strategy: Strategy, base_date: date) -> dict:
        """
        기준 날짜 주변 최대 9개 날짜로 백테스트 실행.
        각 날짜별 결과를 DB에 저장(is_backtest=True)하고 집계 반환.
        """
        valid_dates, skipped = generate_backtest_dates(base_date, strategy.hold_days)
        if not valid_dates:
            return {
                "status": "error",
                "message": "유효한 백테스트 날짜가 없습니다.",
                "skipped": skipped,
                "results": [],
            }

        logger.info("Backtest: strategy=%s, dates=%s", strategy.name, valid_dates)
        results = []
        for d in valid_dates:
            try:
                result = self._run_single_date(strategy, d)
                if result:
                    results.append(result)
            except Exception as e:
                logger.error("Backtest failed for date %s: %s", d, e)
                results.append({"date": str(d), "error": str(e), "picks": []})

        return {
            "status": "ok",
            "strategy_name": strategy.name,
            "base_date": str(base_date),
            "dates_attempted": len(valid_dates),
            "dates_succeeded": sum(1 for r in results if "error" not in r),
            "skipped": skipped,
            "summary": self._aggregate_results(results),
            "results": results,
        }

    def _run_single_date(self, strategy: Strategy, target_date: date) -> dict | None:
        """단일 날짜 백테스트: 과거 데이터 수집 → AI 픽 → DB 저장 → 즉시 검증."""
        logger.info("Backtest single: strategy=%s date=%s", strategy.name, target_date)

        # 1. 과거 주식 데이터 수집
        stock_data = self._collect_historical_data(strategy, target_date)
        if not stock_data:
            logger.warning("No historical stock data for %s", target_date)
            return None

        # 1.5 라이브 경로와 동일하게 모멘텀 prefilter 적용 (추세·RSI·거래량 기준 20개 압축)
        #     백테스트는 과거 수급(net_buy) 복원 불가 → buy_score 중립, 추세/RSI가 선별 주도
        from app.services.trading.runner import StrategyRunner
        stock_data = StrategyRunner._prefilter_stocks(
            stock_data, getattr(strategy, "candidate_filter", "mixed"), target=20
        )

        # 2. AI Stage4 (Lite 모델)
        picks_result = self.analyzer.stage4_picks_backtest(
            stocks_data=stock_data,
            hold_days=strategy.hold_days,
            target_pct=strategy.target_pct,
            stop_loss_pct=strategy.stop_loss_pct,
            min_probability=strategy.min_probability,
            pick_count=strategy.pick_count,
            candidate_filter=getattr(strategy, "candidate_filter", "mixed"),
            backtest_date=target_date,
        )

        if not picks_result.picks:
            return {"date": str(target_date), "picks": [], "win_rate": None}

        # 3. DB 저장 (is_backtest=True)
        run = RecommendationRun(
            strategy_id=strategy.strategy_id,
            run_date=target_date,
            ai_model_used=picks_result.model_used,
            stage4_model=picks_result.model_used,
            prompt_version=self.version_tag,
            is_backtest=True,
            raw_response={"picks": picks_result.raw},
        )
        self.db.add(run)
        self.db.flush()

        price_map = {s["stock_code"]: Decimal(str(s["current_price"])) for s in stock_data if s.get("current_price")}
        tgt_mult  = Decimal("1") + strategy.target_pct / Decimal("100")
        stop_mult = Decimal("1") - strategy.stop_loss_pct / Decimal("100")
        recs = []
        for pick in picks_result.picks:
            code = pick.get("stock_code", "")
            raw_price = pick.get("current_price") or price_map.get(code)
            entry = Decimal(str(raw_price)) if raw_price else None
            # picks엔 target/stop이 없으므로(확률·목표가 폐기) 진입가×전략 파라미터로 산출
            rec = Recommendation(
                run_id=run.run_id,
                stock_code=code,
                stock_name=pick.get("stock_name", ""),
                current_price_at_rec=entry,
                target_price=(entry * tgt_mult) if entry else None,
                stop_loss_price=(entry * stop_mult) if entry else None,
                ai_probability=Decimal(str(pick.get("ai_probability", 0))) if pick.get("ai_probability") else None,
                ai_reason=pick.get("ai_reason"),
                historical_basis=pick.get("historical_basis"),
                risk_factors=pick.get("risk_factors"),
                rank=pick.get("rank"),
            )
            self.db.add(rec)
            recs.append(rec)
        self.db.flush()

        # 4. 즉시 검증
        client = get_kis_client(self.db)
        verified_picks = []
        for rec in recs:
            v = self._verify_pick(rec, target_date, strategy.hold_days, client)
            if v:
                self.db.add(v)
                verified_picks.append({
                    "stock_code": rec.stock_code,
                    "stock_name": rec.stock_name,
                    "result": v.result.value if v.result else None,
                    "pnl_pct": float(v.pnl_pct) if v.pnl_pct else None,
                })

        # 5. 랜덤 대조군 (AI와 동일한 종목 풀에서 pick_count개 무작위 선택)
        random_avg_pnl = self._compute_random_baseline(
            stock_data, target_date, strategy, client
        )

        # raw_response에 랜덤 결과 포함 저장
        run.raw_response = {"picks": picks_result.raw, "random_avg_pnl": random_avg_pnl}
        self.db.commit()

        success = sum(1 for p in verified_picks if p["result"] == "SUCCESS")
        total = len(verified_picks)
        pnls = [p["pnl_pct"] for p in verified_picks if p["pnl_pct"] is not None]
        success_pnls = [p["pnl_pct"] for p in verified_picks if p["result"] == "SUCCESS" and p["pnl_pct"] is not None]
        fail_pnls    = [p["pnl_pct"] for p in verified_picks if p["result"] == "FAIL"    and p["pnl_pct"] is not None]
        return {
            "date": str(target_date),
            "run_id": str(run.run_id),
            "picks": verified_picks,
            "win_rate":        success / total if total else None,
            "avg_pnl":         sum(pnls) / len(pnls) if pnls else None,
            "success_avg_pnl": sum(success_pnls) / len(success_pnls) if success_pnls else None,
            "fail_avg_pnl":    sum(fail_pnls) / len(fail_pnls) if fail_pnls else None,
            "random_avg_pnl":  random_avg_pnl,
        }

    def _collect_historical_data(self, strategy: Strategy, target_date: date) -> list[dict]:
        """stock_master 샘플링 후 과거 OHLCV 데이터 수집."""
        mkt = getattr(strategy, "candidate_market", "ALL")
        sample_size = min(max(strategy.pick_count * 15, 50), 200)

        q = self.db.query(StockMaster).filter(
            StockMaster.is_active == True,
            StockMaster.country == "KR",  # 백테스트는 국내만
        )
        if mkt not in ("ALL", "NAS"):
            q = q.filter(StockMaster.market == mkt)

        all_stocks = q.all()
        if not all_stocks:
            return []

        # 단순 stride 샘플링
        step = max(1, len(all_stocks) // sample_size)
        candidates = all_stocks[::step][:sample_size]

        client = get_kis_client(self.db)
        result = []
        for stock in candidates:
            info = client.get_historical_stock_info(stock.stock_code, target_date)
            if info and info.get("current_price", 0) > 0:
                info["stock_name"] = stock.stock_name
                result.append(info)

        logger.info("Historical data collected: %d/%d stocks for %s", len(result), len(candidates), target_date)
        return result

    def _compute_random_baseline(
        self, stock_data: list[dict], target_date: date, strategy: Strategy, client
    ) -> float | None:
        """AI와 동일한 종목 풀에서 랜덤 pick_count개 선택 후 평균 pnl 계산.
        AI와 동일한 목표/손절 청산 규칙·클램프를 적용해 공정 비교."""
        import random as _random
        pick_count = strategy.pick_count
        hold_days = strategy.hold_days
        tgt_mult  = 1.0 + float(strategy.target_pct) / 100.0
        stop_mult = 1.0 - float(strategy.stop_loss_pct) / 100.0
        period_start = target_date.strftime("%Y%m%d")
        period_end = (target_date + timedelta(days=hold_days)).strftime("%Y%m%d")

        candidates = [s for s in stock_data if s.get("current_price", 0) > 0]
        if len(candidates) < pick_count:
            return None

        sampled = _random.sample(candidates, pick_count)
        pnls = []
        for s in sampled:
            try:
                bars = client.get_ohlcv(s["stock_code"], days=100)
                future = sorted([b for b in bars if period_start < b.date <= period_end], key=lambda b: b.date)
                if not future:
                    continue
                entry = float(s["current_price"])
                if entry <= 0:
                    continue
                target, stop = entry * tgt_mult, entry * stop_mult
                exit_price = float(future[-1].close)
                for b in future:                       # 손절-우선 청산
                    if b.low <= stop:
                        exit_price = stop
                        break
                    if b.high >= target:
                        exit_price = target
                        break
                pnl = (exit_price - entry) / entry * 100
                pnls.append(max(-100.0, min(200.0, pnl)))
            except Exception:
                continue

        return round(sum(pnls) / len(pnls), 4) if pnls else None

    def _verify_pick(self, rec: Recommendation, run_date: date, hold_days: int, client) -> Verification | None:
        """단일 픽 즉시 검증. 일봉 날짜순 순회 → 손절/목표가 중 먼저 터치되는 쪽 판정.
        verifier.py와 동일 convention: 같은 날 둘 다 터치 시 손절 우선, pnl은 실제 청산가 기준."""
        try:
            bars = client.get_ohlcv(rec.stock_code, days=100)
            period_start = run_date.strftime("%Y%m%d")
            period_end = (run_date + timedelta(days=hold_days)).strftime("%Y%m%d")
            relevant = sorted([b for b in bars if period_start <= b.date <= period_end], key=lambda b: b.date)

            entry = rec.current_price_at_rec
            if not entry or entry == 0 or not relevant:
                return None

            max_high = max((b.high for b in relevant), default=entry)
            max_low = min((b.low for b in relevant), default=entry)
            target = rec.target_price
            stop = rec.stop_loss_price

            # 날짜순 순회: 손절 먼저 터치 → FAIL(손절가 청산), 목표 먼저 → SUCCESS(목표가 청산)
            verdict, exit_price = VerificationResult.FAIL, Decimal(str(relevant[-1].close))
            for b in relevant:
                low, high = Decimal(str(b.low)), Decimal(str(b.high))
                if stop and low <= stop:          # 같은 날 동시 터치 시 손절 우선
                    verdict, exit_price = VerificationResult.FAIL, stop
                    break
                if target and high >= target:
                    verdict, exit_price = VerificationResult.SUCCESS, target
                    break

            pnl = float((exit_price - entry) / entry * 100)
            # 분할/상폐 등 데이터 오류로 인한 비현실적 수익률 클램프 (KR 일일 등락 ±30%)
            if pnl > 200 or pnl < -100:
                logger.warning("Clamp implausible pnl %.1f%% for %s (split/bad data?)", pnl, rec.stock_code)
                pnl = max(-100.0, min(200.0, pnl))

            return Verification(
                rec_id=rec.rec_id,
                verified_at=datetime.now(timezone.utc),
                price_at_verify=exit_price,
                max_high=Decimal(str(max_high)),
                max_low=Decimal(str(max_low)),
                result=verdict,
                pnl_pct=Decimal(str(round(pnl, 4))),
            )
        except Exception as e:
            logger.warning("Verify failed for %s: %s", rec.stock_code, e)
            return None

    def _aggregate_results(self, results: list[dict]) -> dict:
        """여러 날짜 결과 집계."""
        valid = [r for r in results if "error" not in r and r.get("win_rate") is not None]
        if not valid:
            return {"win_rate": None, "avg_pnl": None, "total_picks": 0}

        all_picks = [p for r in valid for p in r.get("picks", [])]
        success = sum(1 for p in all_picks if p.get("result") == "SUCCESS")
        total = len(all_picks)
        pnls         = [p["pnl_pct"] for p in all_picks if p.get("pnl_pct") is not None]
        success_pnls = [p["pnl_pct"] for p in all_picks if p.get("result") == "SUCCESS" and p.get("pnl_pct") is not None]
        fail_pnls    = [p["pnl_pct"] for p in all_picks if p.get("result") == "FAIL"    and p.get("pnl_pct") is not None]
        random_pnls  = [r["random_avg_pnl"] for r in valid if r.get("random_avg_pnl") is not None]

        return {
            "win_rate":        round(success / total, 4) if total else None,
            "avg_pnl":         round(sum(pnls) / len(pnls), 4) if pnls else None,
            "success_avg_pnl": round(sum(success_pnls) / len(success_pnls), 4) if success_pnls else None,
            "fail_avg_pnl":    round(sum(fail_pnls) / len(fail_pnls), 4) if fail_pnls else None,
            "random_avg_pnl":  round(sum(random_pnls) / len(random_pnls), 4) if random_pnls else None,
            "total_picks":     total,
            "success_count":   success,
            "fail_count":      total - success,
        }
