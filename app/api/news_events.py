from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.news_event import NewsEvent

router = APIRouter(prefix="/news-events", tags=["news-events"])


@router.get("")
def list_news_events(
    limit: int = Query(default=50, le=200),
    severity: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(NewsEvent).order_by(NewsEvent.detected_at.desc())
    if severity:
        q = q.where(NewsEvent.severity == severity.upper())
    events = db.scalars(q.limit(limit)).all()

    return [
        {
            "event_id":           str(e.event_id),
            "detected_at":        e.detected_at.isoformat(),
            "severity":           e.severity.value,
            "event_description":  e.event_description,
            "keywords":           e.keywords or [],
            "ai_confidence":      float(e.ai_confidence) if e.ai_confidence else None,
            "kospi_at_detection": float(e.kospi_at_detection) if e.kospi_at_detection else None,
            "kosdaq_at_detection":float(e.kosdaq_at_detection) if e.kosdaq_at_detection else None,
            "kospi_change_1d":    float(e.kospi_change_1d)  if e.kospi_change_1d  else None,
            "kospi_change_3d":    float(e.kospi_change_3d)  if e.kospi_change_3d  else None,
            "kosdaq_change_1d":   float(e.kosdaq_change_1d) if e.kosdaq_change_1d else None,
            "kosdaq_change_3d":   float(e.kosdaq_change_3d) if e.kosdaq_change_3d else None,
            "verified_1d":        e.verified_1d_at is not None,
            "verified_3d":        e.verified_3d_at is not None,
        }
        for e in events
    ]
