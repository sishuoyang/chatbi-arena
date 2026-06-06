import os
from arena.config import load_config


def test_env_expansion(monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_CLOUD_HOST", "example.clickhouse.cloud")
    monkeypatch.setenv("CLICKHOUSE_CLOUD_DATABASE", "arena_house")
    monkeypatch.setenv("CLICKHOUSE_CLOUD_USER", "default")
    monkeypatch.setenv("CLICKHOUSE_CLOUD_PASSWORD", "pw")
    monkeypatch.setenv("ARENA_RO_PASSWORD", "ropw")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    cfg = load_config("config.yaml")
    assert cfg.clickhouse.host == "example.clickhouse.cloud"
    assert cfg.clickhouse.database == "arena_house"
    assert cfg.clickhouse.ro_user == "arena_ro"
    assert cfg.bedrock.region == "ap-southeast-1"
    assert len(cfg.models) == 1
    assert cfg.models[0].name == "nova-lite"
    assert {p.name for p in cfg.prompts} == {"P1_zeroshot", "P3_dialect"}


def test_resolve_grid():
    cfg = load_config("config.yaml")
    model_names, prompt_names = cfg.resolved_grid()
    assert model_names == ["nova-lite"]
    assert prompt_names == ["P1_zeroshot", "P3_dialect"]
