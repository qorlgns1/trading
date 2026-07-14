import pytest
from pydantic import ValidationError
from quant_api.settings import Settings


def test_production_requires_long_rate_limit_secret() -> None:
    with pytest.raises(ValidationError, match="32자"):
        Settings(app_env="production", rate_limit_secret="short")


def test_oci_backend_requires_bucket_identity() -> None:
    with pytest.raises(ValidationError, match="버킷"):
        Settings(artifact_backend="oci")


def test_empty_optional_research_paths_are_none() -> None:
    settings = Settings(  # type: ignore[arg-type]
        research_krx_stock_csv="",
        research_krx_etf_csv="",
    )
    assert settings.research_krx_stock_csv is None
    assert settings.research_krx_etf_csv is None


def test_research_download_defaults_are_bounded() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.research_batch_size == 40
    assert settings.research_download_workers == 3


@pytest.mark.parametrize("workers", [0, 5])
def test_research_download_workers_must_stay_between_one_and_four(workers: int) -> None:
    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            research_download_workers=workers,
        )


def test_krx_credentials_must_be_configured_as_a_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    with pytest.raises(ValidationError, match="함께 설정"):
        Settings(_env_file=None, krx_id="research-user")  # type: ignore[call-arg]


def test_krx_credentials_are_secret_values() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        krx_id="research-user",
        krx_pw="research-password",
    )

    assert settings.krx_id is not None
    assert settings.krx_pw is not None
    assert settings.krx_id.get_secret_value() == "research-user"
    assert "research-user" not in repr(settings.krx_id)
    assert "research-password" not in repr(settings.krx_pw)


def test_toss_credentials_must_be_configured_as_a_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TOSSINVEST_CLIENT_ID", raising=False)
    monkeypatch.delenv("TOSSINVEST_CLIENT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="함께 설정"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            tossinvest_client_id="client-id",
        )


def test_enabled_toss_provider_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOSSINVEST_CLIENT_ID", raising=False)
    monkeypatch.delenv("TOSSINVEST_CLIENT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="자격증명"):
        Settings(_env_file=None, tossinvest_enabled=True)  # type: ignore[call-arg]


def test_toss_credentials_are_secret_and_base_url_is_normalized() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        tossinvest_enabled=True,
        tossinvest_base_url="https://openapi.example.test/",
        tossinvest_client_id="client-id",
        tossinvest_client_secret="client-secret",
    )

    assert settings.tossinvest_client_id is not None
    assert settings.tossinvest_client_secret is not None
    assert settings.tossinvest_client_id.get_secret_value() == "client-id"
    assert settings.tossinvest_base_url == "https://openapi.example.test"
    assert "client-id" not in repr(settings.tossinvest_client_id)
    assert "client-secret" not in repr(settings.tossinvest_client_secret)
