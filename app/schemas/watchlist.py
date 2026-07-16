import uuid
from datetime import datetime, date
from typing import Literal
from pydantic import BaseModel, Field

TriggerType = Literal["manual", "earnings", "disclosure", "flow_spike", "price_spike"]


class WatchlistCreate(BaseModel):
    stock_code: str = Field(min_length=1, max_length=20)
    memo: str = ""


class WatchlistUpdate(BaseModel):
    memo: str | None = None


class WatchlistOut(BaseModel):
    watch_id: uuid.UUID
    stock_code: str
    stock_name: str
    sector: str | None
    memo: str
    added_at: datetime
    analysis_count: int = 0
    last_analysis_date: date | None = None

    model_config = {"from_attributes": True}


class AnalyzeRequest(BaseModel):
    stock_code: str = Field(min_length=1, max_length=20)
    analysis_date: date | None = None   # 미지정 시 오늘 (KIS 데이터는 항상 수집 시점 기준)
    trigger_type: TriggerType = "manual"


class AnalysisSummaryOut(BaseModel):
    analysis_id: uuid.UUID
    stock_code: str
    stock_name: str
    analysis_date: date
    trigger_type: str
    gemini_model: str
    result: dict | None          # 5개 섹션 + 뉴스_출처 (스냅샷 제외 — 상세에서 제공)
    # 무효화_조건 자동 체크 상태 — {checked_at, items: [{state, detail, ...}]} (16:20 잡 갱신)
    condition_status: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AnalysisDetailOut(AnalysisSummaryOut):
    input_snapshot: dict | None  # 그날 쓴 지표/수급/뉴스 출처 원본 (사후 검증용)
