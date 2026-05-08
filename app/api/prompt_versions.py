"""
프롬프트 버전 관리 API.
- 버전 목록/상세/성과 조회 (인증 유저)
- 버전 생성/수정 (관리자)
- 성과 점수 재계산 (관리자)
"""
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.api.deps import get_db, get_current_user, require_admin
from app.models.recommendation import (
    PromptVersion, RecommendationRun, Recommendation, Verification, VerificationResult
)
from app.models.user import User

router = APIRouter(prefix="/prompt-versions", tags=["prompt-versions"])


# ------------------------------------------------------------------ #
# Schemas
# ------------------------------------------------------------------ #

class PromptVersionOut(BaseModel):
    version_id: int
    stage: int
    version_no: str
    prompt_text: str
    created_at: datetime
    performance_score: Optional[float]

    class Config:
        from_attributes = True


class PromptVersionCreate(BaseModel):
    stage: int
    version_no: str
    prompt_text: str


class PromptVersionStats(BaseModel):
    version_no: str
    stage: int
    performance_score: Optional[float]
    total_verified: int
    success_count: int
    fail_count: int
    run_count: int


# ------------------------------------------------------------------ #
# 내부 헬퍼
# ------------------------------------------------------------------ #

def _get_stats(db: Session, version_no: str, stage: int) -> dict:
    """버전/단계별 성과 통계 계산."""
    run_count = db.scalar(
        select(func.count(RecommendationRun.run_id))
        .where(RecommendationRun.prompt_version == version_no)
    ) or 0

    rows = db.execute(
        select(Verification.result, func.count().label("cnt"))
        .join(Recommendation, Recommendation.rec_id == Verification.rec_id)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(RecommendationRun.prompt_version == version_no)
        .group_by(Verification.result)
    ).all()

    total   = sum(r.cnt for r in rows)
    success = next((r.cnt for r in rows if r.result == VerificationResult.SUCCESS), 0)
    fail    = next((r.cnt for r in rows if r.result == VerificationResult.FAIL), 0)

    return {
        "total_verified": total,
        "success_count":  success,
        "fail_count":     fail,
        "run_count":      run_count,
    }


# ------------------------------------------------------------------ #
# Endpoints
# ------------------------------------------------------------------ #

@router.get("", response_model=list[PromptVersionOut])
def list_prompt_versions(
    stage: Optional[int] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(PromptVersion).order_by(PromptVersion.stage, PromptVersion.version_id)
    if stage:
        q = q.where(PromptVersion.stage == stage)
    return db.scalars(q).all()


@router.get("/stats", response_model=list[PromptVersionStats])
def get_all_stats(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """모든 버전의 성과 통계 비교."""
    pvs = db.scalars(select(PromptVersion).order_by(PromptVersion.stage, PromptVersion.version_id)).all()
    results = []
    for pv in pvs:
        stats = _get_stats(db, pv.version_no, pv.stage)
        results.append(PromptVersionStats(
            version_no=pv.version_no,
            stage=pv.stage,
            performance_score=float(pv.performance_score) if pv.performance_score else None,
            **stats,
        ))
    return results


@router.get("/{version_id}", response_model=PromptVersionOut)
def get_prompt_version(
    version_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    pv = db.get(PromptVersion, version_id)
    if not pv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return pv


@router.get("/{version_id}/stats", response_model=PromptVersionStats)
def get_version_stats(
    version_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    pv = db.get(PromptVersion, version_id)
    if not pv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    stats = _get_stats(db, pv.version_no, pv.stage)
    return PromptVersionStats(
        version_no=pv.version_no,
        stage=pv.stage,
        performance_score=float(pv.performance_score) if pv.performance_score else None,
        **stats,
    )


@router.post("", response_model=PromptVersionOut, status_code=status.HTTP_201_CREATED)
def create_prompt_version(
    body: PromptVersionCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    pv = PromptVersion(**body.model_dump())
    db.add(pv)
    db.commit()
    db.refresh(pv)
    return pv


@router.post("/{version_id}/recalculate", response_model=dict)
def recalculate_score(
    version_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """해당 버전의 성과 점수를 즉시 재계산."""
    pv = db.get(PromptVersion, version_id)
    if not pv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    from app.services.trading.verifier import _update_performance_score
    _update_performance_score(db, pv.version_no)
    db.refresh(pv)

    return {
        "version_no": pv.version_no,
        "performance_score": float(pv.performance_score) if pv.performance_score else None,
    }
