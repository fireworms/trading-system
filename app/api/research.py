"""AI 리서치 탭 API — 자유 질문 종목 리서치 (참고용, 매매 시그널 아님)."""
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.stock_master import StockMaster
from app.models.research import ResearchNote
from app.schemas.research import (
    ResearchQueryRequest, ResearchQueryResponse, ResearchCandidate,
    ResearchNoteDetailOut, ResearchSummaryOut,
)

router = APIRouter(prefix="/research", tags=["research"])

_MAX_CANDIDATES = 8


@router.post("/query", response_model=ResearchQueryResponse)
def query_research(
    body: ResearchQueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """자유 질문 → 종목 식별 → 스냅샷 수집 + Gemini 그라운딩 리서치.

    종목 후보가 여러 개면 ambiguous로 반환 — 프론트가 후보 선택 후
    stock_code를 지정해 재요청한다. (분석은 1~2분 걸릴 수 있음)
    """
    from app.services.research.analyst import identify_stocks, run_research

    question = body.question.strip()

    if body.stock_code:
        target = db.scalar(select(StockMaster).where(
            StockMaster.stock_code == body.stock_code,
            StockMaster.is_active == True,  # noqa: E712
        ).limit(1))
        if not target:
            raise HTTPException(status_code=404, detail=f"종목 {body.stock_code}를 찾을 수 없습니다.")
        if target.market not in ("KOSPI", "KOSDAQ"):
            raise HTTPException(status_code=400, detail="국내(KOSPI/KOSDAQ) 종목만 지원합니다.")
    else:
        candidates = identify_stocks(db, question)
        if not candidates:
            return ResearchQueryResponse(
                status="no_match",
                message="질문에서 종목을 인식하지 못했습니다. 정확한 종목명(또는 6자리 코드)을 "
                        "포함하거나, 종목 검색으로 직접 지정해주세요. (국내 종목만 지원)",
            )
        if len(candidates) > 1:
            return ResearchQueryResponse(
                status="ambiguous",
                message="여러 종목이 인식됐습니다. 분석할 종목을 선택해주세요.",
                candidates=[
                    ResearchCandidate(stock_code=c.stock_code, stock_name=c.stock_name,
                                      market=c.market, sector=c.sector)
                    for c in candidates[:_MAX_CANDIDATES]
                ],
            )
        target = candidates[0]

    try:
        note = run_research(
            db,
            user_id=current_user.user_id,
            question=question,
            stock_code=target.stock_code,
            stock_name=target.stock_name,
            sector=target.sector,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"AI 리서치 실패: {e}")
    return ResearchQueryResponse(status="done", note=ResearchNoteDetailOut.model_validate(note))


@router.get("/history", response_model=list[ResearchSummaryOut])
def list_history(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.scalars(
        select(ResearchNote)
        .where(ResearchNote.user_id == current_user.user_id)
        .order_by(ResearchNote.created_at.desc())
        .limit(limit)
    ).all()


@router.get("/{research_id}", response_model=ResearchNoteDetailOut)
def get_note(
    research_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    note = db.get(ResearchNote, research_id)
    if not note or note.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="리서치를 찾을 수 없습니다.")
    return note


@router.delete("/{research_id}", status_code=204)
def delete_note(
    research_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    note = db.get(ResearchNote, research_id)
    if not note or note.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="리서치를 찾을 수 없습니다.")
    db.delete(note)
    db.commit()
