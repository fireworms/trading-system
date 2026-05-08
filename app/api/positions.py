import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.models.user import User
from app.models.position import Position, PositionStatus
from app.schemas.recommendation import PositionOut
from app.api.deps import get_current_user

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("", response_model=list[PositionOut])
def list_positions(
    status: PositionStatus | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = select(Position).where(Position.user_id == current_user.user_id)
    if status:
        q = q.where(Position.status == status)
    return db.scalars(q.order_by(Position.entry_date.desc())).all()


@router.get("/{position_id}", response_model=PositionOut)
def get_position(
    position_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    pos = db.get(Position, position_id)
    if not pos or pos.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="Position not found")
    return pos
