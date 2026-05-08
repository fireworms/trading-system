import uuid
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    hold_days: int = Field(default=10, ge=1, le=365)
    target_pct: Decimal = Field(ge=Decimal("0.1"), le=Decimal("100"))
    stop_loss_pct: Decimal = Field(ge=Decimal("0.1"), le=Decimal("50"))
    min_probability: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    pick_count: int = Field(default=5, ge=1, le=20)
    run_interval_days: int = Field(default=3, ge=1, le=30)


class StrategyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    hold_days: int | None = Field(default=None, ge=1, le=365)
    target_pct: Decimal | None = None
    stop_loss_pct: Decimal | None = None
    min_probability: Decimal | None = None
    pick_count: int | None = None
    run_interval_days: int | None = None
    is_active: bool | None = None


class StrategyOut(BaseModel):
    strategy_id: uuid.UUID
    created_by: uuid.UUID | None
    name: str
    description: str | None
    hold_days: int
    target_pct: Decimal
    stop_loss_pct: Decimal
    min_probability: Decimal
    pick_count: int
    run_interval_days: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserStrategyCreate(BaseModel):
    strategy_id: uuid.UUID
    account_id: uuid.UUID
    invest_amount_per_pick: Decimal = Field(ge=Decimal("10000"))
    is_auto_trade: bool = False


class UserStrategyOut(BaseModel):
    id: int
    user_id: uuid.UUID
    strategy_id: uuid.UUID
    account_id: uuid.UUID
    invest_amount_per_pick: Decimal
    is_auto_trade: bool
    is_active: bool
    subscribed_at: datetime

    model_config = {"from_attributes": True}
