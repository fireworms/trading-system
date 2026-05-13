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

PICK_COUNT_MAX       = 4
DAILY_RETURN_MAX_PCT = 0.7   # target_pct / hold_days 상한
RR_RATIO_MIN         = 1.5   # target_pct / stop_loss_pct 하한
MIN_PROBABILITY_MIN  = 55.0  # AI 확률 최소값


def _validate_strategy(body) -> None:
    """전략 파라미터 상호 검증."""
    pick_count     = getattr(body, "pick_count", None)
    target_pct     = float(getattr(body, "target_pct", 0) or 0)
    hold_days      = int(getattr(body, "hold_days", 1) or 1)
    stop_loss_pct  = float(getattr(body, "stop_loss_pct", 0) or 0)
    min_probability = float(getattr(body, "min_probability", 0) or 0)

    if pick_count is not None and pick_count > PICK_COUNT_MAX:
        raise HTTPException(status_code=422, detail=f"pick_count는 최대 {PICK_COUNT_MAX}개입니다.")

    if target_pct and hold_days:
        daily = target_pct / hold_days
        if daily > DAILY_RETURN_MAX_PCT:
            raise HTTPException(
                status_code=422,
                detail=f"일평균 기대수익률 {daily:.2f}%는 현실적이지 않습니다 (상한 {DAILY_RETURN_MAX_PCT}%/일)."
            )

    if target_pct and stop_loss_pct:
        rr = target_pct / stop_loss_pct
        if rr < RR_RATIO_MIN:
            raise HTTPException(
                status_code=422,
                detail=f"R/R 비율 {rr:.2f}가 너무 낮습니다 (최소 {RR_RATIO_MIN})."
            )

    if min_probability and min_probability < MIN_PROBABILITY_MIN:
        raise HTTPException(
            status_code=422,
            detail=f"min_probability는 최소 {MIN_PROBABILITY_MIN}% 이상이어야 합니다."
        )


@router.post("", response_model=StrategyOut, status_code=201)
def create_strategy(
    body: StrategyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _validate_strategy(body)
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

    # 수정 후 전략의 최종 값으로 검증
    merged = strategy.__dict__.copy()
    merged.update(body.model_dump(exclude_none=True))

    class _Merged:
        def __init__(self, d):
            for k, v in d.items():
                setattr(self, k, v)

    _validate_strategy(_Merged(merged))

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
