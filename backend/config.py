from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql://postgres:postgres@db:5432/opendrivelab",
        validation_alias="DATABASE_URL",
    )
    poll_interval: int = Field(default=5, validation_alias="POLL_INTERVAL")
    telemetry_batch_size: int = Field(default=100, validation_alias="TELEMETRY_BATCH_SIZE")
    sumo_home: str = Field(default="/usr/share/sumo", validation_alias="SUMO_HOME")
    scenario_data_dir: str = Field(
        default="/data/scenarios",
        validation_alias="SCENARIO_DATA_DIR",
    )
    scenario_templates_dir: str = Field(
        default="/app/scenario_gen/templates",
        validation_alias="SCENARIO_TEMPLATES_DIR",
    )


settings = Settings()
