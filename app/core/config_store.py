"""AppConfig 키-값 읽기/쓰기 헬퍼."""
from sqlalchemy.orm import Session
from app.models.app_config import AppConfig


def get_config(db: Session, key: str, default: str = "") -> str:
    row = db.get(AppConfig, key)
    if not row or row.value_enc is None:
        return default
    return row.value_enc


def set_config(db: Session, key: str, value: str) -> None:
    row = db.get(AppConfig, key)
    if row:
        row.value_enc = value
        row.is_encrypted = False
    else:
        db.add(AppConfig(key=key, value_enc=value, is_encrypted=False))
    db.commit()
