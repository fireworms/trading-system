import math
import statistics as _stats
import uuid
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.models.user import User
from app.models.position import Position, PositionStatus
from app.schemas.recommendation import PositionOut
from app.api.deps import get_current_user

router = APIRouter(prefix="/positions", tags=["positions"])

_COMMISSION: dict[str, Decimal] = {
    "KOSPI":  Decimal("0.0027"),
    "KOSDAQ": Decimal("0.0023"),
    "NAS":    Decimal("0.0050"),
}

def _commission_rate(stock_code: str, db: Session) -> Decimal:
    from app.models.stock_master import StockMaster
    row = db.scalar(select(StockMaster).where(StockMaster.stock_code == stock_code))
    market = (row.market if row else None) or "KOSPI"
    return _COMMISSION.get(market, _COMMISSION["KOSPI"])


def _enrich(pos: Position) -> PositionOut:
    """Position 모델 → PositionOut (익절가/손절가 계산 포함)."""
    target_price = None
    trailing_stop_price = None

    if pos.recommendation:
        target_price = pos.recommendation.target_price
    elif pos.strategy and pos.entry_price:
        target_price = (pos.entry_price * (1 + pos.strategy.target_pct / 100)).quantize(Decimal("1"))

    if pos.peak_price and pos.strategy:
        stop_loss_pct = pos.strategy.stop_loss_pct
        trailing_stop_price = (pos.peak_price * (1 - stop_loss_pct / 100)).quantize(Decimal("1"))

    return PositionOut(
        position_id=pos.position_id,
        user_id=pos.user_id,
        strategy_id=pos.strategy_id,
        rec_id=pos.rec_id,
        account_id=pos.account_id,
        stock_code=pos.stock_code,
        entry_price=pos.entry_price,
        entry_date=pos.entry_date,
        quantity=pos.quantity,
        status=pos.status,
        exit_price=pos.exit_price,
        exit_date=pos.exit_date,
        pnl_pct=pos.pnl_pct,
        peak_price=pos.peak_price,
        target_price=target_price,
        trailing_stop_price=trailing_stop_price,
    )


@router.get("/stats")
def get_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """확정손익 통계 — 전체 KPI / 전략별 / 월별 / 종목별."""
    from app.models.strategy import Strategy
    from app.models.stock_master import StockMaster
    from collections import defaultdict

    closed = db.scalars(
        select(Position).where(
            Position.user_id == current_user.user_id,
            Position.status != PositionStatus.HOLDING,
            Position.exit_price.isnot(None),
            Position.pnl_pct.isnot(None),
        ).order_by(Position.exit_date.desc())
    ).all()

    # stock_code → stock_name 캐시 (stock_master 단건 조회)
    codes = {pos.stock_code for pos in closed}
    name_map: dict[str, str] = {}
    if codes:
        masters = db.scalars(
            select(StockMaster).where(StockMaster.stock_code.in_(codes))
        ).all()
        name_map = {m.stock_code: m.stock_name for m in masters}

    def pnl_amount(pos: Position) -> float:
        return float(pos.pnl_pct) / 100 * float(pos.entry_price) * pos.quantity

    def build_kpi(positions):
        if not positions:
            return {"total_trades": 0, "win_count": 0, "loss_count": 0,
                    "win_rate": None, "avg_win_pct": None, "avg_loss_pct": None,
                    "profit_factor": None, "total_pnl_amount": 0, "avg_hold_days": None,
                    "sharpe": None, "max_drawdown_pct": None}
        wins   = [p for p in positions if float(p.pnl_pct) > 0]
        losses = [p for p in positions if float(p.pnl_pct) <= 0]
        avg_win  = sum(float(p.pnl_pct) for p in wins)  / len(wins)  if wins   else None
        avg_loss = sum(float(p.pnl_pct) for p in losses) / len(losses) if losses else None
        pf = abs(avg_win / avg_loss) if avg_win and avg_loss else None
        hold_days = [
            (p.exit_date - p.entry_date).days for p in positions
            if p.exit_date and p.entry_date
        ]

        # 샤프지수 (트레이드 단위: mean(pnl) / std(pnl), 3건 이상 시 계산)
        pnl_list = [float(p.pnl_pct) for p in positions]
        sharpe = None
        if len(pnl_list) >= 3:
            mean_r = sum(pnl_list) / len(pnl_list)
            std_r = _stats.stdev(pnl_list)
            sharpe = round(mean_r / std_r, 2) if std_r > 0 else None

        # MDD (청산일 순 equity curve 기준)
        sorted_pos = sorted(
            [p for p in positions if p.exit_date and p.pnl_pct],
            key=lambda p: p.exit_date,
        )
        max_dd = 0.0
        equity, peak = 1.0, 1.0
        for p in sorted_pos:
            equity *= (1 + float(p.pnl_pct) / 100)
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)

        return {
            "total_trades":     len(positions),
            "win_count":        len(wins),
            "loss_count":       len(losses),
            "win_rate":         round(len(wins) / len(positions), 4) if positions else None,
            "avg_win_pct":      round(avg_win, 4)  if avg_win  is not None else None,
            "avg_loss_pct":     round(avg_loss, 4) if avg_loss is not None else None,
            "profit_factor":    round(pf, 4) if pf is not None else None,
            "total_pnl_amount": round(sum(pnl_amount(p) for p in positions)),
            "avg_hold_days":    round(sum(hold_days) / len(hold_days), 1) if hold_days else None,
            "sharpe":           sharpe,
            "max_drawdown_pct": round(max_dd * 100, 2) if sorted_pos else None,
        }

    # 전략별
    by_strat: dict[str, list] = defaultdict(list)
    for pos in closed:
        key = str(pos.strategy_id) if pos.strategy_id else "__none__"
        by_strat[key].append(pos)

    strategy_stats = []
    for sid, positions in by_strat.items():
        strat = db.get(Strategy, sid) if sid != "__none__" else None
        kpi = build_kpi(positions)
        kpi["strategy_id"]   = sid
        kpi["strategy_name"] = strat.name if strat else "전략 없음"
        strategy_stats.append(kpi)

    # 월별
    by_month: dict[str, list] = defaultdict(list)
    for pos in closed:
        if pos.exit_date:
            by_month[pos.exit_date.strftime("%Y-%m")].append(pos)
    month_stats = [
        {
            "month":            m,
            "total_trades":     len(ps),
            "win_count":        sum(1 for p in ps if float(p.pnl_pct) > 0),
            "total_pnl_amount": round(sum(pnl_amount(p) for p in ps)),
            "avg_pnl_pct":      round(sum(float(p.pnl_pct) for p in ps) / len(ps), 2),
        }
        for m, ps in sorted(by_month.items())
    ]

    # 종목별
    by_stock: dict[str, list] = defaultdict(list)
    for pos in closed:
        by_stock[pos.stock_code].append(pos)
    stock_stats = sorted([
        {
            "stock_code":       code,
            "stock_name":       name_map.get(code, ""),
            "total_trades":     len(ps),
            "win_count":        sum(1 for p in ps if float(p.pnl_pct) > 0),
            "total_pnl_amount": round(sum(pnl_amount(p) for p in ps)),
            "avg_pnl_pct":      round(sum(float(p.pnl_pct) for p in ps) / len(ps), 2),
        }
        for code, ps in by_stock.items()
    ], key=lambda x: x["total_pnl_amount"], reverse=True)

    # 거래 목록 (확정손익 내림차순)
    trade_list = [
        {
            "position_id":  str(pos.position_id),
            "stock_code":   pos.stock_code,
            "stock_name":   name_map.get(pos.stock_code, ""),
            "strategy_name": pos.strategy.name if pos.strategy else "전략 없음",
            "entry_price":  float(pos.entry_price),
            "exit_price":   float(pos.exit_price),
            "quantity":     pos.quantity,
            "pnl_pct":      float(pos.pnl_pct),
            "pnl_amount":   round(pnl_amount(pos)),
            "hold_days":    (pos.exit_date - pos.entry_date).days if pos.exit_date and pos.entry_date else None,
            "exit_date":    pos.exit_date.isoformat() if pos.exit_date else None,
            "status":       pos.status.value,
        }
        for pos in closed
    ]

    return {
        "overall":    build_kpi(closed),
        "by_strategy": strategy_stats,
        "by_month":   month_stats,
        "by_stock":   stock_stats,
        "trades":     trade_list,
    }


@router.get("", response_model=list[PositionOut])
def list_positions(
    status: PositionStatus | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = select(Position).where(Position.user_id == current_user.user_id)
    if status:
        q = q.where(Position.status == status)
    return [_enrich(p) for p in db.scalars(q.order_by(Position.entry_date.desc())).all()]


@router.get("/{position_id}", response_model=PositionOut)
def get_position(
    position_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    pos = db.get(Position, position_id)
    if not pos or pos.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="Position not found")
    return _enrich(pos)


@router.post("/{position_id}/close", response_model=PositionOut)
def close_position(
    position_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """보유 포지션 수동 청산 (시장가 매도)."""
    from datetime import date
    pos = db.get(Position, position_id)
    if not pos or pos.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="Position not found")
    if pos.status != PositionStatus.HOLDING:
        raise HTTPException(status_code=400, detail="Already closed")

    from app.services.kis.client import get_kis_client_from_account
    import time as _time
    client = get_kis_client_from_account(pos.account)

    try:
        client.sell_market_order(pos.stock_code, pos.quantity)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"주문 실패: {e}")

    _time.sleep(1)
    fill_price = client.get_today_fill_price(pos.stock_code, side="01") \
                 or client.get_current_price(pos.stock_code)

    commission = _commission_rate(pos.stock_code, db)
    pnl = (fill_price - pos.entry_price) / pos.entry_price * 100 - commission * 100
    pos.exit_price = fill_price
    pos.exit_date   = date.today()
    pos.status      = PositionStatus.MANUAL_EXIT
    pos.pnl_pct     = Decimal(str(round(float(pnl), 4)))
    db.commit()
    db.refresh(pos)
    return _enrich(pos)


@router.post("/close-all")
def close_all_positions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """보유 중인 전체 포지션 수동 청산."""
    from datetime import date
    from app.services.kis.client import get_kis_client_from_account

    positions = db.scalars(
        select(Position).where(
            Position.user_id == current_user.user_id,
            Position.status == PositionStatus.HOLDING,
        )
    ).all()

    import time as _time
    results = []
    for pos in positions:
        try:
            client = get_kis_client_from_account(pos.account)
            client.sell_market_order(pos.stock_code, pos.quantity)
            _time.sleep(1)
            fill_price = client.get_today_fill_price(pos.stock_code, side="01") \
                         or client.get_current_price(pos.stock_code)
            commission = _commission_rate(pos.stock_code, db)
            pnl = (fill_price - pos.entry_price) / pos.entry_price * 100 - commission * 100
            pos.exit_price = fill_price
            pos.exit_date   = date.today()
            pos.status      = PositionStatus.MANUAL_EXIT
            pos.pnl_pct     = Decimal(str(round(float(pnl), 4)))
            results.append({"stock_code": pos.stock_code, "status": "closed", "pnl_pct": float(pnl)})
        except Exception as e:
            results.append({"stock_code": pos.stock_code, "status": "failed", "error": str(e)})

    db.commit()
    return {"closed": len([r for r in results if r["status"] == "closed"]), "results": results}


@router.post("/manual-buy", response_model=PositionOut)
def manual_buy(
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    수동 매수. body: {stock_code, account_id, amount (투자금액), strategy_id}
    기존 포지션과 별개로 새 Position 레코드 생성.
    """
    from datetime import date
    from app.services.kis.client import get_kis_client_from_account
    from app.models.user import BrokerAccount

    stock_code  = body.get("stock_code", "").strip()
    account_id  = body.get("account_id")
    amount      = Decimal(str(body.get("amount", 0)))
    strategy_id = body.get("strategy_id")

    if not stock_code or not account_id or amount <= 0:
        raise HTTPException(status_code=400, detail="stock_code, account_id, amount 필수")

    account = db.get(BrokerAccount, uuid.UUID(account_id))
    if not account or account.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="Account not found")

    client = get_kis_client_from_account(account)
    current_price = client.get_current_price(stock_code)
    quantity = int(amount // current_price)
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="투자금액이 현재가보다 작습니다")

    try:
        client.buy_market_order(stock_code, quantity)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"주문 실패: {e}")

    import time
    time.sleep(1)
    fill_price = client.get_today_fill_price(stock_code) or current_price

    pos = Position(
        user_id     = current_user.user_id,
        strategy_id = uuid.UUID(strategy_id) if strategy_id else None,
        account_id  = account.account_id,
        stock_code  = stock_code,
        entry_price = fill_price,
        peak_price  = fill_price,
        entry_date  = date.today(),
        quantity    = quantity,
        status      = PositionStatus.HOLDING,
    )
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return _enrich(pos)
