import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (
    String, Boolean, DateTime, ForeignKey, Integer, Numeric, Text
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Strategy(Base):
    __tablename__ = "strategies"

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    hold_days: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    target_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    stop_loss_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    min_probability: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    pick_count: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    run_interval_days: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    candidate_filter: Mapped[str] = mapped_column(String(20), default="mixed", nullable=False)
    candidate_market: Mapped[str] = mapped_column(String(20), default="ALL", nullable=False)
    # 선정 로직 변형: momentum(기본 STAGE4A) / earnings_catalyst(실적 카탈리스트 우선 변형)
    selection_mode: Mapped[str] = mapped_column(String(20), server_default="momentum", nullable=False)
    use_trailing_stop: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user_strategies: Mapped[list["UserStrategy"]] = relationship(back_populates="strategy", cascade="all, delete-orphan")
    recommendation_runs: Mapped[list["RecommendationRun"]] = relationship(back_populates="strategy")
    positions: Mapped[list["Position"]] = relationship(back_populates="strategy")


class UserStrategy(Base):
    __tablename__ = "user_strategies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategies.strategy_id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("broker_accounts.account_id", ondelete="CASCADE"), nullable=False
    )
    invest_amount_per_pick: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    is_auto_trade: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    subscribed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="user_strategies")
    strategy: Mapped["Strategy"] = relationship(back_populates="user_strategies")
    account: Mapped["BrokerAccount"] = relationship(back_populates="user_strategies")


from app.models.user import User, BrokerAccount  # noqa: E402
from app.models.recommendation import RecommendationRun  # noqa: E402
from app.models.position import Position  # noqa: E402
