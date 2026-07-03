from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = Field(..., alias="DATABASE_URL")
    secret_key: str = Field(..., alias="SECRET_KEY")

    # KIS 키는 DB broker_accounts에서 관리 — .env 불필요 (하위 호환용으로 Optional 유지)
    kis_app_key: str | None = Field(None, alias="KIS_APP_KEY")
    kis_app_secret: str | None = Field(None, alias="KIS_APP_SECRET")
    kis_account_no: str | None = Field(None, alias="KIS_ACCOUNT_NO")

    gemini_api_key: str = Field(..., alias="GEMINI_API_KEY")

    telegram_bot_token: str | None = Field(None, alias="TELEGRAM_BOT_TOKEN")
    # telegram_chat_id는 users.telegram_chat_id(DB)로 관리 — .env 불필요

    # 외부 데이터 어댑터 (관심종목 분석) — 미설정 시 해당 소스만 스킵 (data_flags 폴백)
    dart_api_key: str | None = Field(None, alias="DART_API_KEY")
    naver_client_id: str | None = Field(None, alias="NAVER_CLIENT_ID")
    naver_client_secret: str | None = Field(None, alias="NAVER_CLIENT_SECRET")

    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    class Config:
        env_file = ".env"
        populate_by_name = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
