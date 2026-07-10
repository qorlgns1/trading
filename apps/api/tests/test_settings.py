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
