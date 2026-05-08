"""
.env의 KIS 키를 admin 유저의 broker_accounts에 등록.
최초 1회 실행 후 .env에서 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO 제거 가능.
실행: python -m scripts.seed_broker_account
"""
import sys
sys.path.insert(0, ".")

import os
from dotenv import load_dotenv
load_dotenv()

from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.models.user import User, BrokerAccount, BrokerType, AccountType

def main():
    app_key    = os.getenv("KIS_APP_KEY")
    app_secret = os.getenv("KIS_APP_SECRET")
    account_no = os.getenv("KIS_ACCOUNT_NO")

    if not all([app_key, app_secret, account_no]):
        print("ERROR: .env에 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO 필요")
        sys.exit(1)

    db = SessionLocal()
    admin = db.query(User).first()
    if not admin:
        print("ERROR: 유저가 없습니다")
        db.close()
        sys.exit(1)

    existing = db.query(BrokerAccount).filter(
        BrokerAccount.user_id == admin.user_id,
        BrokerAccount.account_no == account_no,
    ).first()

    if existing:
        print(f"이미 등록됨: {existing.account_id} ({account_no})")
        db.close()
        return

    account = BrokerAccount(
        user_id=admin.user_id,
        broker=BrokerType.KIS,
        account_no=account_no,
        api_key_enc=encrypt_secret(app_key),
        api_secret_enc=encrypt_secret(app_secret),
        account_type=AccountType.REAL,
        is_active=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    print(f"✅ broker_account 등록 완료")
    print(f"   user     : {admin.username}")
    print(f"   account_id: {account.account_id}")
    print(f"   account_no: {account_no}")
    print(f"   type     : {account.account_type.value}")
    print(f"   api_key  : {app_key[:8]}*** (암호화 저장)")

    db.close()

if __name__ == "__main__":
    main()
