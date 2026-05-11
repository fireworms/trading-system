import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.models.user import User
from app.models.strategy import Strategy, UserStrategy
from app.schemas.strategy import (
    StrategyCreate, StrategyUpdate, StrategyOut,
    UserStrategyCreate, UserStrategyUpdate, UserStrategyOut,
)
from app.api.deps import get_current_user

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.post("", response_model=StrategyOut, status_code=201)
def create_strategy(
    body: StrategyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    strategy = Strategy(**body.model_dump(), created_by=current_user.user_id)
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return strategy


@router.get("", response_model=list[StrategyOut])
def list_strategies(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.scalars(select(Strategy).where(Strategy.is_active == True)).all()


@router.get("/{strategy_id}", response_model=StrategyOut)
def get_strategy(strategy_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    strategy = db.get(Strategy, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategy


@router.patch("/{strategy_id}", response_model=StrategyOut)
def update_strategy(
    strategy_id: uuid.UUID,
    body: StrategyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    strategy = db.get(Strategy, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if str(strategy.created_by) != str(current_user.user_id) and current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Forbidden")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(strategy, field, value)

    db.commit()
    db.refresh(strategy)
    return strategy


@router.delete("/{strategy_id}", status_code=204)
def delete_strategy(
    strategy_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    strategy = db.get(Strategy, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if str(strategy.created_by) != str(current_user.user_id) and current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Forbidden")

    strategy.is_active = False
    db.commit()


# --- User Strategy Subscriptions ---

@router.post("/subscribe", response_model=UserStrategyOut, status_code=201)
def subscribe_strategy(
    body: UserStrategyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    strategy = db.get(Strategy, body.strategy_id)
    if not strategy or not strategy.is_active:
        raise HTTPException(status_code=404, detail="Strategy not found")

    existing = db.scalar(
        select(UserStrategy).where(
            UserStrategy.user_id == current_user.user_id,
            UserStrategy.strategy_id == body.strategy_id,
            UserStrategy.is_active == True,
        )
    )
    if existing:
        raise HTTPException(status_code=400, detail="Already subscribed")

    sub = UserStrategy(
        user_id=current_user.user_id,
        strategy_id=body.strategy_id,
        account_id=body.account_id,
        invest_amount_per_pick=body.invest_amount_per_pick,
        is_auto_trade=body.is_auto_trade,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


@router.get("/my/subscriptions", response_model=list[UserStrategyOut])
def my_subscriptions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.scalars(
        select(UserStrategy).where(
            UserStrategy.user_id == current_user.user_id,
            UserStrategy.is_active == True,
        )
    ).all()


@router.patch("/subscriptions/{sub_id}", response_model=UserStrategyOut)
def update_subscription(
    sub_id: int,
    body: UserStrategyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sub = db.get(UserStrategy, sub_id)
    if not sub or sub.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if not sub.is_active:
        raise HTTPException(status_code=400, detail="Subscription is not active")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(sub, field, value)

    db.commit()
    db.refresh(sub)
    return sub


@router.delete("/subscriptions/{sub_id}", status_code=204)
def unsubscribe_strategy(
    sub_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    구독 해지. 기존 HOLDING 포지션은 원래 전략 조건대로 계속 모니터링됨.
    """
    sub = db.get(UserStrategy, sub_id)
    if not sub or sub.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if not sub.is_active:
        raise HTTPException(status_code=400, detail="Already unsubscribed")

    sub.is_active = False
    db.commit()


@router.patch("/subscriptions/{sub_id}/auto-trade", response_model=UserStrategyOut)
def toggle_auto_trade(
    sub_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sub = db.get(UserStrategy, sub_id)
    if not sub or sub.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="Subscription not found")

    sub.is_auto_trade = not sub.is_auto_trade
    db.commit()
    db.refresh(sub)
    return sub
