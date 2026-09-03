from datetime import date
from decimal import Decimal

from sqlalchemy import String, Date, Numeric, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DailyPrice(Base):
    """KRX 오픈API 일별 전종목 시세 적재 (공용 시장 데이터 — 유저 스코핑 없음).

    KIS는 종목당 1회 호출이라 전종목 히스토리 확보가 비현실적인데,
    KRX는 하루치 전종목(KOSPI 943 + KOSDAQ 1822)을 1회 호출로 준다.
    용도: 규칙 전략 파라미터(신고가 기간·거래대금 컷) 백테스트 튜닝.

    - 복합 PK (stock_code, trade_date) — 재적재 시 upsert
    - KRX 제공 시작일: 2010-01-04
    - 금액 단위는 원 (거래대금·시가총액), 명세서에 단위 명시가 없어 실측 대조로 확인
      (SK하이닉스 2026-09-02 시총 1,178조 → 원 단위 확정)
    """
    __tablename__ = "daily_price"
    __table_args__ = (
        Index("ix_daily_price_date", "trade_date"),
        Index("ix_daily_price_date_value", "trade_date", "trade_value"),  # 거래대금 순위 조회용
    )

    stock_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)

    stock_name:  Mapped[str | None] = mapped_column(String(100), nullable=True)
    market:      Mapped[str | None] = mapped_column(String(20), nullable=True)   # KOSPI / KOSDAQ
    sector_type: Mapped[str | None] = mapped_column(String(40), nullable=True)   # 소속부

    open:  Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    high:  Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    low:   Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    close: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)

    change:     Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)  # 전일 대비
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)  # 등락률 %

    volume:        Mapped[Decimal | None] = mapped_column(Numeric(20, 0), nullable=True)
    trade_value:   Mapped[Decimal | None] = mapped_column(Numeric(24, 0), nullable=True)  # 거래대금(원)
    market_cap:    Mapped[Decimal | None] = mapped_column(Numeric(24, 0), nullable=True)  # 시가총액(원)
    listed_shares: Mapped[Decimal | None] = mapped_column(Numeric(20, 0), nullable=True)
