
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    APP_NAME: str = "AtlasAI"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    DATABASE_URL: str = (
        "postgresql+psycopg://postgres:password@localhost:5432/atlasai"
    )

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2:3b"
    OLLAMA_EMBEDDING_MODEL: str = "nomic-embed-text"
    EMBEDDING_DIMENSIONS: int = Field(default=768, gt=0)

    AUTH_JWT_SECRET: str = ""
    AUTH_JWT_ISSUER: str = "atlasai"
    AUTH_JWT_AUDIENCE: str = "atlasai-api"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=15, gt=0, le=1440)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=30, gt=0, le=365)
    REFRESH_COOKIE_NAME: str = "atlasai_refresh"
    REFRESH_COOKIE_SECURE: bool = True
    CORS_ALLOWED_ORIGINS: list[str] = []
    DOCUMENT_STORAGE_DIR: str = "storage/documents"
    MAX_DOCUMENT_UPLOAD_BYTES: int = Field(default=20 * 1024 * 1024, gt=0)
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_DOCUMENT_EVENTS_TOPIC: str = "document-events"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
