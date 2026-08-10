import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class ResearchNote(Base):
    """AI 리서치 탭 — 자유 질문 기반 종목 리서치 결과 (참고용, 매매 시그널 아님).

    관심종목 분석(stock_analyses)과 분리한 이유:
    - stock_analyses는 무효화_조건 구조화가 필수이고 16:20 자동 판정 잡이
      종목별 최신 분석을 읽는다 — 자유 서술 결과가 섞이면 조건 감시가 깨짐.
    - 리서치는 마크다운 자유 서술이라 스키마 자체가 다름.
    """
    __tablename__ = "research_notes"

    research_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    stock_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    stock_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Gemini 마크다운 답변 원문 (자유 서술 + 필수 섹션: 리스크·확인 포인트 / 출처)
    answer_md: Mapped[str] = mapped_column(Text, nullable=False)
    gemini_model: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    # 답변의 "출처" 섹션에서 추출한 링크 목록 [{title, url}] (파싱 실패 시 빈 배열)
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 분석 시점 KIS 입력 스냅샷 (관심종목 탭과 동일 수집기 — 사후 재구성용)
    input_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
