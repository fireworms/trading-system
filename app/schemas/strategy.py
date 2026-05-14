import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field

CandidateFilter = Literal["volume", "largecap", "mixed"]
CandidateMarket = Literal["KOSPI", "KOSDAQ", "NAS", "ALL"]


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    hold_days: int = Field(default=10, ge=1, le=365)
    target_pct: Decimal = Field(ge=Decimal("0.1"), le=Decimal("100"))
    stop_loss_pct: Decimal = Field(ge=Decimal("0.1"), le=Decimal("50"))
    min_probability: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    pick_count: int = Field(default=5, ge=1, le=20)
    run_interval_days: int = Field(default=3, ge=1, le=30)
    candidate_filter: CandidateFilter = "mixed"
    candidate_market: CandidateMarket = "ALL"
    use_trailing_stop: bool = False


class StrategyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    hold_days: int | None = Field(default=None, ge=1, le=365)
    target_pct: Decimal | None = None
    stop_loss_pct: Decimal | None = None
    min_probability: Decimal | None = None
    pick_count: int | None = None
    run_interval_days: int | None = None
    candidate_filter: CandidateFilter | None = None
    candidate_market: CandidateMarket | None = None
    use_trailing_stop: bool | None = None
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
    candidate_filter: str
    candidate_market: str
    use_trailing_stop: bool
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserStrategyUpdate(BaseModel):
    invest_amount_per_pick: Decimal | None = Field(default=None, ge=Decimal("10000"))
    is_auto_trade: bool | None = None
    account_id: uuid.UUID | None = None


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
