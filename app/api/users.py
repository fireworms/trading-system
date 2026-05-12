import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_access_token, encrypt_secret
from app.models.user import User, Permission, BrokerAccount
from app.schemas.user import (
    UserCreate, UserUpdate, UserOut, PermissionOut,
    BrokerAccountCreate, BrokerAccountUpdate, BrokerAccountOut,
    LoginRequest, TokenOut, TelegramUpdate,
)
from app.api.deps import get_current_user, require_admin

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: UserCreate, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(status_code=400, detail="Username already taken")
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenOut)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    token = create_access_token({"sub": str(user.user_id)})
    return TokenOut(access_token=token)


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.scalars(select(User)).all()


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.user_id != user_id and current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Forbidden")

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(user, field, value)

    db.commit()
    db.refresh(user)
    return user


# --- Permissions ---

@router.get("/{user_id}/permissions", response_model=list[PermissionOut])
def get_permissions(user_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user.permissions


# --- Broker Accounts ---

@router.post("/{user_id}/accounts", response_model=BrokerAccountOut, status_code=201)
def add_broker_account(
    user_id: uuid.UUID,
    body: BrokerAccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.user_id != user_id and current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Forbidden")

    account = BrokerAccount(
        user_id=user_id,
        broker=body.broker,
        account_no=body.account_no,
        api_key_enc=encrypt_secret(body.api_key),
        api_secret_enc=encrypt_secret(body.api_secret),
        account_type=body.account_type,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.get("/{user_id}/accounts", response_model=list[BrokerAccountOut])
def list_broker_accounts(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.user_id != user_id and current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Forbidden")

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user.broker_accounts


@router.patch("/{user_id}/accounts/{account_id}", response_model=BrokerAccountOut)
def update_broker_account(
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    body: BrokerAccountUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.user_id != user_id and current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Forbidden")
    account = db.get(BrokerAccount, account_id)
    if not account or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="Account not found")
    if body.hts_id is not None:
        account.hts_id = body.hts_id or None
    db.commit()
    db.refresh(account)
    return account


@router.patch("/me/telegram", response_model=UserOut)
def update_telegram_chat_id(
    body: TelegramUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """본인 텔레그램 chat_id 등록/수정/해제."""
    current_user.telegram_chat_id = body.telegram_chat_id
    db.commit()
    db.refresh(current_user)
    return current_user
