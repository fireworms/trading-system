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
