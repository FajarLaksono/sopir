from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    db_user: str = Field(
        default="postgres", 
        env="DB_USER"
    )

    db_password: str = Field(
        default="postgres", 
        env="DB_PASSWORD"
    )

    db_host: str = Field(
        default="localhost", 
        env="DB_HOST"
    )

    db_port: int = Field(
        default=5432, 
        env="DB_PORT"
    )

    db_name: str = Field(
        default="sopirlabdb", 
        env="DB_NAME"
    )
    database_url_override: str | None = Field(
        default=None, 
        env="DATABASE_URL"
    )

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

Settings = Settings()