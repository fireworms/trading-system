import uuid
import enum
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, DateTime, Text, Numeric, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class NewsSeverity(str, enum.Enum):
    NORMAL   = "NORMAL"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"


class NewsEvent(Base):
    __tablename__ = "news_events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    severity: Mapped[NewsSeverity] = mapped_column(
        SAEnum(NewsSeverity, name="news_severity", create_type=False), nullable=False
    )
    event_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    keywords: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    ai_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)

    # 감지 시점 지수 레벨 (사후 검증 기준점)
    kospi_at_detection: Mapped[Decimal | None]  = mapped_column(Numeric(12, 2), nullable=True)
    kosdaq_at_detection: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # 사후 검증 (1일 / 3일 후 스케줄러가 채움)
    kospi_change_1d:  Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    kospi_change_3d:  Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    kosdaq_change_1d: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    kosdaq_change_3d: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    verified_1d_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_3d_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
