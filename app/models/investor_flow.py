import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import String, Date, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class InvestorFlowDaily(Base):
    """종목별 일별 투자자 순매수 적재 (공용 시장 데이터 — 유저 스코핑 없음).

    KIS FHKST01010900이 최근 30거래일만 반환하므로 매일 적재해
    60/120거래일 누적 수급 히스토리를 만든다 (백필 불가 — 적재 시작일부터).
    금액 단위: 백만원.
    """
    __tablename__ = "investor_flow_daily"
    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uq_invflow_stock_date"),
    )

    flow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    stock_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    frgn_ntby_amt: Mapped[Decimal | None] = mapped_column(Numeric(18, 0), nullable=True)
    orgn_ntby_amt: Mapped[Decimal | None] = mapped_column(Numeric(18, 0), nullable=True)
    prsn_ntby_amt: Mapped[Decimal | None] = mapped_column(Numeric(18, 0), nullable=True)
    close: Mapped[Decimal | None] = mapped_column(Numeric(12, 0), nullable=True)
