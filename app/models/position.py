import uuid
from datetime import datetime, date, timezone
from decimal import Decimal
from sqlalchemy import (
    String, DateTime, Date, ForeignKey, Integer, Numeric, Enum as SAEnum
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.core.database import Base


class PositionStatus(str, enum.Enum):
    HOLDING = "HOLDING"
    TARGET_HIT = "TARGET_HIT"
    STOP_LOSS = "STOP_LOSS"
    EXPIRED = "EXPIRED"
    MANUAL_EXIT = "MANUAL_EXIT"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Position(Base):
    __tablename__ = "positions"

    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategies.strategy_id", ondelete="CASCADE"), nullable=False
    )
    rec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recommendations.rec_id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("broker_accounts.account_id", ondelete="CASCADE"), nullable=False
    )
    stock_code: Mapped[str] = mapped_column(String(20), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PositionStatus] = mapped_column(
        SAEnum(PositionStatus, name="position_status"),
        default=PositionStatus.HOLDING,
        nullable=False,
    )
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 0), nullable=True)
    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)

    user: Mapped["User"] = relationship(back_populates="positions")
    strategy: Mapped["Strategy"] = relationship(back_populates="positions")
    recommendation: Mapped["Recommendation"] = relationship(back_populates="positions")
    account: Mapped["BrokerAccount"] = relationship(back_populates="positions")


from app.models.user import User, BrokerAccount  # noqa: E402
from app.models.strategy import Strategy  # noqa: E402
from app.models.recommendation import Recommendation  # noqa: E402
