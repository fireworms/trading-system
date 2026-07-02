import uuid
from datetime import datetime, timezone, date
from sqlalchemy import String, DateTime, Date, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class WatchlistStock(Base):
    """중장기 수동매매용 관심종목 (유저별). 스펙: docs/watchlist_spec.md"""
    __tablename__ = "watchlist_stocks"
    __table_args__ = (
        UniqueConstraint("user_id", "stock_code", name="uq_watchlist_user_stock"),
    )

    watch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    stock_code: Mapped[str] = mapped_column(String(20), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    memo: Mapped[str] = mapped_column(Text, nullable=False, default="")
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class StockAnalysis(Base):
    """관심종목 분석 일지 (일자별 스냅샷 + AI 구조화 결과).

    watchlist_stocks에 FK를 걸지 않음 — 관심종목에서 제거해도 일지는 남아야 함
    (매매 일지의 영속성이 이 탭의 핵심 가치).
    """
    __tablename__ = "stock_analyses"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    stock_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    stock_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    analysis_date: Mapped[date] = mapped_column(Date, nullable=False)
    trigger_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="manual"
    )  # manual / earnings / disclosure / flow_spike / price_spike
    gemini_model: Mapped[str] = mapped_column(String(50), nullable=False, default="")

    # AI 구조화 출력 (논거/단기_촉매/장기_논거/무효화_조건/밸류_코멘트 + 뉴스 출처)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 분석에 사용한 입력 데이터 원본 (KIS 지표/재무/수급/추정실적 + 가용성 플래그)
    input_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Gemini 원문 (디버깅용)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
