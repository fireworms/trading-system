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

    # KRX 오픈API (일별 전종목 시세 벌크 적재) — 미설정 시 어댑터가 available=False 반환
    krx_api_key: str | None = Field(None, alias="KRX_API_KEY")

    # 로깅 — 기본 INFO (앱 로거가 루트 기본값 WARNING에 막혀 정상 기록이 안 남던 문제 교정)
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_access: bool = Field(True, alias="LOG_ACCESS")  # uvicorn 액세스 로그 (프론트 폴링 소음)

    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    class Config:
        env_file = ".env"
        populate_by_name = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
