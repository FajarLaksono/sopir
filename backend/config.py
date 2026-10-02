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

    # Streaming / Kafka
    kafka_bootstrap_servers: str = Field(
        default="redpanda:9092",
        validation_alias="KAFKA_BOOTSTRAP_SERVERS",
    )
    schema_registry_url: str = Field(
        default="http://redpanda:8081",
        validation_alias="SCHEMA_REGISTRY_URL",
    )

    # S3-compatible object store (LocalStack for local dev)
    s3_endpoint: str = Field(
        default="http://localstack:4566",
        validation_alias="S3_ENDPOINT",
    )
    s3_access_key: str = Field(
        default="test",
        validation_alias="S3_ACCESS_KEY",
    )
    s3_secret_key: str = Field(
        default="test",
        validation_alias="S3_SECRET_KEY",
    )
    s3_bucket: str = Field(
        default="sopir-raw",
        validation_alias="S3_BUCKET",
    )

    # Telemetry sink selection: "postgres" or "kafka"
    telemetry_sink: str = Field(
        default="postgres",
        validation_alias="TELEMETRY_SINK",
    )


settings = Settings()
