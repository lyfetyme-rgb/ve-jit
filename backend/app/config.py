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

    SECRET_KEY: str = "change-me-in-production-use-long-random-string"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30


settings = Settings()
