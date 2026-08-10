import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class ResearchQueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    # 지정 시 종목 식별 생략 (검색으로 직접 지정 / 후보 선택 재요청)
    stock_code: str | None = Field(default=None, max_length=20)


class ResearchCandidate(BaseModel):
    stock_code: str
    stock_name: str
    market: str
    sector: str | None = None


class ResearchNoteOut(BaseModel):
    research_id: uuid.UUID
    stock_code: str
    stock_name: str
    question: str
    answer_md: str
    gemini_model: str
    sources: list | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ResearchNoteDetailOut(ResearchNoteOut):
    input_snapshot: dict | None = None


class ResearchSummaryOut(BaseModel):
    """이력 목록용 — 답변 본문 제외."""
    research_id: uuid.UUID
    stock_code: str
    stock_name: str
    question: str
    gemini_model: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ResearchQueryResponse(BaseModel):
    # done: 분석 완료 / ambiguous: 종목 후보 여러 개 — 선택 필요 / no_match: 종목 인식 실패
    status: Literal["done", "ambiguous", "no_match"]
    message: str | None = None
    candidates: list[ResearchCandidate] | None = None
    note: ResearchNoteDetailOut | None = None
