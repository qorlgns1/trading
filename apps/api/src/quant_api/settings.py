from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    app_mode: Literal["public_demo", "local_research"] = "public_demo"
    app_env: Literal["development", "test", "production"] = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str = "sqlite+aiosqlite:///./data/quant.db"
    valkey_url: str | None = None
    artifact_backend: Literal["local", "oci"] = "local"
    artifact_root: Path = Path("artifacts")
    rate_limit_secret: str = "development-only-secret-change-me"
    backtest_eager: bool = True
    auto_create_schema: bool = True
    trust_proxy: bool = False
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    oci_bucket_name: str | None = None
    oci_namespace: str | None = None
    oci_region: str = "ap-seoul-1"
    research_root: Path = Path("data/research")
    research_history_years: int = Field(default=10, ge=1, le=30)
    research_score_lookback_sessions: int = Field(default=400, ge=253, le=1000)
    research_batch_size: int = Field(default=20, ge=1, le=100)
    research_max_retries: int = Field(default=3, ge=1, le=10)
    research_minimum_group_assets: int = Field(default=30, ge=1, le=10_000)
    research_auto_sync: bool = True
    research_poll_seconds: int = Field(default=900, ge=60, le=86_400)
    krx_id: SecretStr | None = None
    krx_pw: SecretStr | None = None
    research_krx_stock_csv: Path | None = None
    research_krx_etf_csv: Path | None = None
    tossinvest_enabled: bool = False
    tossinvest_base_url: str = "https://openapi.tossinvest.com"
    tossinvest_client_id: SecretStr | None = None
    tossinvest_client_secret: SecretStr | None = None

    @field_validator("research_krx_stock_csv", "research_krx_etf_csv", mode="before")
    @classmethod
    def empty_path_is_none(cls, value: object) -> object:
        return None if value is None or str(value).strip() == "" else value

    @model_validator(mode="after")
    def validate_production(self) -> "Settings":
        if self.app_env == "production" and len(self.rate_limit_secret) < 32:
            raise ValueError("운영 RATE_LIMIT_SECRET은 32자 이상이어야 합니다.")
        if self.artifact_backend == "oci" and not (
            self.oci_bucket_name and self.oci_namespace
        ):
            raise ValueError("OCI 저장소에는 버킷 이름과 네임스페이스가 필요합니다.")
        if self.app_mode == "local_research" and self.artifact_backend != "local":
            raise ValueError("local_research 모드의 산출물은 로컬 저장소만 사용할 수 있습니다.")
        has_krx_id = self.krx_id is not None and bool(self.krx_id.get_secret_value())
        has_krx_pw = self.krx_pw is not None and bool(self.krx_pw.get_secret_value())
        if has_krx_id != has_krx_pw:
            raise ValueError("KRX_ID와 KRX_PW는 함께 설정해야 합니다.")
        has_toss_id = self.tossinvest_client_id is not None and bool(
            self.tossinvest_client_id.get_secret_value()
        )
        has_toss_secret = self.tossinvest_client_secret is not None and bool(
            self.tossinvest_client_secret.get_secret_value()
        )
        if has_toss_id != has_toss_secret:
            raise ValueError(
                "TOSSINVEST_CLIENT_ID와 TOSSINVEST_CLIENT_SECRET은 함께 설정해야 합니다."
            )
        if self.tossinvest_enabled and not (has_toss_id and has_toss_secret):
            raise ValueError("토스 Open API를 활성화하려면 자격증명이 필요합니다.")
        self.tossinvest_base_url = self.tossinvest_base_url.rstrip("/")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
