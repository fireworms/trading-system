import uuid
from datetime import datetime, date, timezone
from decimal import Decimal
from sqlalchemy import (
    String, DateTime, Date, ForeignKey, Integer, Numeric, Text, Enum as SAEnum
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VerificationResult(str, enum.Enum):
    SUCCESS = "SUCCESS"
    FAIL = "FAIL"


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategies.strategy_id", ondelete="CASCADE"), nullable=False
    )
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    ai_model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    strategy: Mapped["Strategy"] = relationship(back_populates="recommendation_runs")
    macro_analysis: Mapped["MacroAnalysis | None"] = relationship(back_populates="run", uselist=False, cascade="all, delete-orphan")
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class MacroAnalysis(Base):
    __tablename__ = "macro_analysis"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recommendation_runs.run_id", ondelete="CASCADE"), unique=True, nullable=False
    )
    current_situation: Mapped[str | None] = mapped_column(Text, nullable=True)
    historical_matches: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    industry_mapping: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    expected_beneficiary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    run: Mapped["RecommendationRun"] = relationship(back_populates="macro_analysis")


class Recommendation(Base):
    __tablename__ = "recommendations"

    rec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recommendation_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    stock_code: Mapped[str] = mapped_column(String(20), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 0), nullable=True)
    stop_loss_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 0), nullable=True)
    ai_probability: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    historical_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_factors: Mapped[str | None] = mapped_column(Text, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    run: Mapped["RecommendationRun"] = relationship(back_populates="recommendations")
    verification: Mapped["Verification | None"] = relationship(back_populates="recommendation", uselist=False, cascade="all, delete-orphan")
    positions: Mapped[list["Position"]] = relationship(back_populates="recommendation")


class Verification(Base):
    __tablename__ = "verifications"

    verify_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recommendations.rec_id", ondelete="CASCADE"), unique=True, nullable=False
    )
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price_at_verify: Mapped[Decimal | None] = mapped_column(Numeric(18, 0), nullable=True)
    max_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 0), nullable=True)
    max_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 0), nullable=True)
    result: Mapped[VerificationResult | None] = mapped_column(
        SAEnum(VerificationResult, name="verification_result"), nullable=True
    )
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)

    recommendation: Mapped["Recommendation"] = relationship(back_populates="verification")


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    version_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stage: Mapped[int] = mapped_column(Integer, nullable=False)
    version_no: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    performance_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)


from app.models.strategy import Strategy  # noqa: E402
from app.models.position import Position  # noqa: E402
