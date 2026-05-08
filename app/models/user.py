import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (
    String, Boolean, DateTime, ForeignKey, Enum as SAEnum, Text
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.core.database import Base


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    TRADER = "TRADER"
    VIEWER = "VIEWER"


class BrokerType(str, enum.Enum):
    KIS = "KIS"


class AccountType(str, enum.Enum):
    REAL = "REAL"
    PAPER = "PAPER"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role"), default=UserRole.TRADER, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    permissions: Mapped[list["Permission"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    broker_accounts: Mapped[list["BrokerAccount"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    user_strategies: Mapped[list["UserStrategy"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    positions: Mapped[list["Position"]] = relationship(back_populates="user")


class Permission(Base):
    __tablename__ = "permissions"

    permission_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    menu_key: Mapped[str] = mapped_column(String(100), nullable=False)
    is_allowed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped["User"] = relationship(back_populates="permissions")


class BrokerAccount(Base):
    __tablename__ = "broker_accounts"

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    broker: Mapped[BrokerType] = mapped_column(
        SAEnum(BrokerType, name="broker_type"), default=BrokerType.KIS, nullable=False
    )
    account_no: Mapped[str] = mapped_column(String(50), nullable=False)
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    api_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    account_type: Mapped[AccountType] = mapped_column(
        SAEnum(AccountType, name="account_type"), default=AccountType.REAL, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped["User"] = relationship(back_populates="broker_accounts")
    user_strategies: Mapped[list["UserStrategy"]] = relationship(back_populates="account")
    positions: Mapped[list["Position"]] = relationship(back_populates="account")


# circular import 방지용 지연 import
from app.models.strategy import UserStrategy  # noqa: E402
from app.models.position import Position  # noqa: E402
