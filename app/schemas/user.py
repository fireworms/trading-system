import uuid
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, EmailStr, Field
from app.models.user import UserRole, BrokerType, AccountType


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(min_length=8)
    role: UserRole = UserRole.TRADER


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=50)
    email: EmailStr | None = None
    role: UserRole | None = None
    is_active: bool | None = None


class UserOut(BaseModel):
    user_id: uuid.UUID
    username: str
    email: str
    role: UserRole
    is_active: bool
    telegram_chat_id: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TelegramUpdate(BaseModel):
    telegram_chat_id: str | None = None


class PermissionOut(BaseModel):
    permission_id: int
    menu_key: str
    is_allowed: bool

    model_config = {"from_attributes": True}


class BrokerAccountCreate(BaseModel):
    broker: BrokerType = BrokerType.KIS
    account_no: str
    api_key: str
    api_secret: str
    hts_id: str | None = None
    account_type: AccountType = AccountType.REAL


class BrokerAccountUpdate(BaseModel):
    hts_id: str | None = None


class VirtualAccountCreate(BaseModel):
    initial_cash: Decimal = Field(default=Decimal("10000000"), gt=0)
    label: str | None = Field(default=None, max_length=50)


class BrokerAccountOut(BaseModel):
    account_id: uuid.UUID
    broker: BrokerType
    account_no: str
    account_type: AccountType
    hts_id: str | None
    is_active: bool
    virtual_cash: Decimal | None = None
    virtual_cash_initial: Decimal | None = None

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
