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

    def __init__(self, db: Session):
        self.db = db
        self.analyzer = GeminiAnalyzer()

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
            prompt_version="backtest-v1",
            is_backtest=True,
            raw_response={"picks": picks_result.raw},
        )
        self.db.add(run)
        self.db.flush()

        price_map = {s["stock_code"]: Decimal(str(s["current_price"])) for s in stock_data if s.get("current_price")}
        recs = []
        for pick in picks_result.picks:
            code = pick.get("stock_code", "")
            raw_price = pick.get("current_price") or price_map.get(code)
            rec = Recommendation(
                run_id=run.run_id,
                stock_code=code,
                stock_name=pick.get("stock_name", ""),
                current_price_at_rec=Decimal(str(raw_price)) if raw_price else None,
                target_price=Decimal(str(pick.get("target_price", 0))) if pick.get("target_price") else None,
                stop_loss_price=Decimal(str(pick.get("stop_loss_price", 0))) if pick.get("stop_loss_price") else None,
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

        self.db.commit()

        success = sum(1 for p in verified_picks if p["result"] == "SUCCESS")
        total = len(verified_picks)
        return {
            "date": str(target_date),
            "run_id": str(run.run_id),
            "picks": verified_picks,
            "win_rate": success / total if total else None,
            "avg_pnl": sum(p["pnl_pct"] for p in verified_picks if p["pnl_pct"] is not None) / total if total else None,
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

    def _verify_pick(self, rec: Recommendation, run_date: date, hold_days: int, client) -> Verification | None:
        """단일 픽 즉시 검증. hold_days 기간 OHLCV로 성공/실패 판정."""
        try:
            bars = client.get_ohlcv(rec.stock_code, days=100)
            period_start = str(run_date)
            period_end = str(run_date + timedelta(days=hold_days))
            relevant = [b for b in bars if period_start <= b.date <= period_end]

            entry = rec.current_price_at_rec
            if not entry or entry == 0 or not relevant:
                return None

            max_high = max((b.high for b in relevant), default=entry)
            max_low = min((b.low for b in relevant), default=entry)
            end_price = Decimal(str(relevant[-1].close)) if relevant else entry

            verdict = VerificationResult.FAIL
            if rec.target_price and max_high >= rec.target_price:
                verdict = VerificationResult.SUCCESS

            pnl = (end_price - entry) / entry * 100

            return Verification(
                rec_id=rec.rec_id,
                verified_at=datetime.now(timezone.utc),
                price_at_verify=end_price,
                max_high=Decimal(str(max_high)),
                max_low=Decimal(str(max_low)),
                result=verdict,
                pnl_pct=Decimal(str(round(float(pnl), 4))),
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
        pnls = [p["pnl_pct"] for p in all_picks if p.get("pnl_pct") is not None]

        return {
            "win_rate": round(success / total, 4) if total else None,
            "avg_pnl": round(sum(pnls) / len(pnls), 4) if pnls else None,
            "total_picks": total,
            "success_count": success,
            "fail_count": total - success,
        }
