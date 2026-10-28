from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://bats:change_me@localhost:5432/bats"
    cors_origins: list[str] = []  # the frontend is served same-origin, so no CORS by default
    jwt_secret: str  # required: the app refuses to start without it
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    audit_anchor_path: str = "audit_anchor.log"  # off-database record of the chain head
    audit_verify_interval_seconds: int = 300
    hms_retry_interval_seconds: int = 10
    hms_api_key: str  # shared secret the HMS presents when calling BATS; required
    enable_mock_hms: bool = False  # unauthenticated simulation: enable only for demos
    enable_docs: bool = False  # /docs and /openapi.json describe the whole API: enable only in development
    enable_hsts: bool = False  # set true when served over HTTPS
    trusted_proxy_count: int = 0  # reverse proxies in front of the app; 0 = ignore X-Forwarded-For
    timezone: str = "Asia/Kolkata"  # the calendar "today" for expiry decisions
    quarantine_days_before_expiry: int = 2
    expiry_sweep_interval_seconds: int = 3600
    issuance_request_ttl_minutes: int = 15
    bats_inbound_url: str = "http://localhost:8000/api/v1/hms/inbound"
    hms_webhook_url: str = "http://localhost:8000/mock-hms/webhook"
    hms_timeout_seconds: float = 3.0


settings = Settings()
