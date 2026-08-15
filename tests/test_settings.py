from repomesh.settings import Settings


def test_shared_model_connection_configures_repomesh_planning(monkeypatch) -> None:
    monkeypatch.setenv("REPOMESH_MODEL_API_KEY", "shared-key")
    monkeypatch.setenv("REPOMESH_MODEL_BASE_URL", "https://models.example/v1")
    monkeypatch.setenv("REPOMESH_MODEL", "shared-model")

    settings = Settings(_env_file=None)

    assert settings.deepseek_api_key == "shared-key"
    assert settings.deepseek_base_url == "https://models.example/v1"
    assert settings.deepseek_model == "shared-model"


def test_planning_specific_model_connection_overrides_shared_default(monkeypatch) -> None:
    monkeypatch.setenv("REPOMESH_MODEL_API_KEY", "shared-key")
    monkeypatch.setenv("REPOMESH_MODEL_BASE_URL", "https://shared.example/v1")
    monkeypatch.setenv("REPOMESH_MODEL", "shared-model")
    monkeypatch.setenv("REPOMESH_DEEPSEEK_API_KEY", "planning-key")
    monkeypatch.setenv("REPOMESH_DEEPSEEK_BASE_URL", "https://planning.example/v1")
    monkeypatch.setenv("REPOMESH_DEEPSEEK_MODEL", "planning-model")

    settings = Settings(_env_file=None)

    assert settings.deepseek_api_key == "planning-key"
    assert settings.deepseek_base_url == "https://planning.example/v1"
    assert settings.deepseek_model == "planning-model"
