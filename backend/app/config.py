from typing import List, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    APP_NAME: str = "VE-JIT Platform"
    APP_VERSION: str = "4.1.0"
    DEBUG: bool = False

    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 2

    ALLOWED_ORIGINS: List[str] = ["*"]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def split_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    STORE_ID: str = "store_001"
    STORE_NAME: str = "Demo Store"
    STORE_TIMEZONE: str = "America/New_York"
    TENANT_ID: str = "tenant_001"

    # Policy bundle freshness: newest active rule older than this => hard-safety gate closed. 0 disables.
    POLICY_BUNDLE_MAX_AGE_SECONDS: int = 86400

    # Edge -> cloud synchronization. Empty means dry-run (outbox drains report, send nothing).
    CLOUD_SYNC_URL: str = ""
    # Cloud signer: disabled until both are set (never ship a default signing key)
    CLOUD_SIGNER_TOKEN: str = ""
    POLICY_SIGNING_KEY: str = ""  # 32-byte Ed25519 seed, base64url
    POLICY_BUNDLE_TTL_SECONDS: int = 86400

    QUEUE_TARGET_WAIT_SECONDS: int = 180
    QUEUE_MAX_WAIT_SECONDS: int = 300
    QUEUE_ALERT_COOLDOWN_SECONDS: int = 300
    QUEUE_AVG_ORDER_VALUE: float = 45.0
    QUEUE_SAFETY_BUFFER: float = 1.15
    QUEUE_POLL_INTERVAL_SECONDS: int = 30

    DATABASE_URL: str = "sqlite:///./vejit.db"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    REDIS_URL: str = "redis://redis:6379/0"
    REDIS_MAX_CONNECTIONS: int = 50

    KAFKA_BOOTSTRAP_SERVERS: str = "kafka:9092"
    KAFKA_TOPIC_QUEUE_METRICS: str = "queue-metrics"
    KAFKA_TOPIC_QUEUE_ALERTS: str = "queue-alerts"

    SENSOR_API_URL: str = "http://localhost:8001"
    SENSOR_API_TIMEOUT: int = 5
    SENSOR_API_RETRY_COUNT: int = 3

    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    PROMETHEUS_ENABLED: bool = True
    PROMETHEUS_PORT: int = 9090

    # Atlassian integration (CENTRAL BACKEND ONLY — never on edge nodes).
    # Credentials are read from files, never from the repository or edge config.
    ATLASSIAN_ENABLED: bool = False
    ATLASSIAN_SITE_URL: str = ""          # https://example.atlassian.net
    ATLASSIAN_CLOUD_ID: str = ""
    ATLASSIAN_EMAIL: str = ""
    ATLASSIAN_API_TOKEN_FILE: str = ""    # path to a secret file with the API token
    ATLASSIAN_WEBHOOK_SECRET_FILE: str = ""  # path to Jira webhook HMAC secret
    JIRA_PROJECT_KEY: str = "KAN"          # change-request project
    JSM_SERVICE_DESK_ID: str = ""          # JSM service desk for store incidents
    CONFLUENCE_SPACE_KEY: str = "SD"
    CONFLUENCE_ROOT_PAGE_ID: str = ""      # parent for runbooks/release pages
    ATLASSIAN_RATE_LIMIT_PER_MIN: int = 60  # central backend cap (GitHub Actions + stores share it)
    ATLASSIAN_BURST: int = 10
    ATLASSIAN_MAX_RETRIES: int = 4
    ATLASSIAN_STORM_WINDOW_SECONDS: int = 300
    ATLASSIAN_STORM_KEY_PREFIX: str = "vejit-store-health"

    # ---------------------------------------------------------------- identity & access (Phase 1)
    # local = signed HS256 tokens issued by /api/v1/auth/login from env-configured users.
    # oidc  = RS256 tokens issued by the corporate IdP and verified against its JWKS.
    AUTH_MODE: str = "local"
    JWT_ISSUER: str = "ve-jit"
    JWT_AUDIENCE: str = "ve-jit-api"
    # JSON array of local users. Never hardcode credentials in source.
    # Each entry: {"username","password_hash","role","tenants":[...],"stores":[...]}
    LOCAL_USERS_JSON: str = ""
    OIDC_ISSUER: str = ""
    OIDC_AUDIENCE: str = ""
    OIDC_JWKS_URL: str = ""
    OIDC_JWKS_CACHE_SECONDS: int = 300
    OIDC_ROLE_CLAIM: str = "role"
    OIDC_TENANT_CLAIM: str = "vejit_tenants"
    OIDC_STORE_CLAIM: str = "vejit_stores"

    # Request-scoped defences. Rate limiting is per-process; use a shared store
    # (Redis) when running more than one replica.
    RATE_LIMIT_PER_MINUTE: int = 120
    RATE_LIMIT_BURST: int = 20
    CSRF_ENFORCE: bool = True

    SECRET_KEY: str = "change-me-in-production-use-long-random-string"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30


settings = Settings()
