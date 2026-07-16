import uuid
from datetime import datetime, date
from decimal import Decimal
from pydantic import BaseModel
from app.models.recommendation import VerificationResult
from app.models.position import PositionStatus


class RecommendationOut(BaseModel):
    rec_id: uuid.UUID
    run_id: uuid.UUID
    stock_code: str
    stock_name: str
    target_price: Decimal | None
    stop_loss_price: Decimal | None
    ai_probability: Decimal | None
    ai_reason: str | None
    historical_basis: str | None
    risk_factors: str | None
    rank: int | None
    verification: "VerificationOut | None" = None

    model_config = {"from_attributes": True}


class MacroAnalysisOut(BaseModel):
    analysis_id: uuid.UUID
    run_id: uuid.UUID
    current_situation: str | None
    historical_matches: dict | None
    industry_mapping: dict | None
    expected_beneficiary: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RecommendationRunOut(BaseModel):
    run_id: uuid.UUID
    strategy_id: uuid.UUID
    run_date: date
    ai_model_used: str | None
    stage1_model: str | None
    stage2_model: str | None
    stage3_model: str | None
    stage4_model: str | None
    prompt_version: str | None
    is_backtest: bool = False
    recommendations: list[RecommendationOut] = []

    model_config = {"from_attributes": True}


class VerificationOut(BaseModel):
    verify_id: uuid.UUID
    rec_id: uuid.UUID
    verified_at: datetime
    price_at_verify: Decimal | None
    max_high: Decimal | None
    max_low: Decimal | None
    result: VerificationResult | None
    pnl_pct: Decimal | None

    model_config = {"from_attributes": True}


class PositionOut(BaseModel):
    position_id: uuid.UUID
    user_id: uuid.UUID
    strategy_id: uuid.UUID | None
    rec_id: uuid.UUID | None
    account_id: uuid.UUID
    stock_code: str
    stock_name: str = ""
    entry_price: Decimal
    entry_date: date
    quantity: int
    status: PositionStatus
    exit_price: Decimal | None
    exit_date: date | None
    pnl_pct: Decimal | None
    peak_price: Decimal | None
    target_price: Decimal | None
    trailing_stop_price: Decimal | None
    target_hit_at: datetime | None = None
    trailing_stop_override: bool | None = None  # None=전략에 따름, True=ON, False=OFF
    account_type: str | None = None  # REAL / PAPER / VIRTUAL (가상계좌 배지용)

    model_config = {"from_attributes": True}
