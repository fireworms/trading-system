from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AppConfig(Base):
    """시스템 설정 키-값 저장소. 민감한 값은 Fernet 암호화."""
    __tablename__ = "app_config"

    key:       Mapped[str]       = mapped_column(String(100), primary_key=True)
    value_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_encrypted: Mapped[bool]   = mapped_column(default=True)
