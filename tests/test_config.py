import importlib
import os
import sys

import pytest


def reload_config(monkeypatch, **env):
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    module_name = "litreel.config"
    if module_name in sys.modules:
        del sys.modules[module_name]
    config = importlib.import_module(module_name)
    importlib.reload(config)
    return config


def test_supabase_pooler_uses_nullpool(monkeypatch):
    config = reload_config(
        monkeypatch,
        DATABASE_URL="postgresql://user:pass@aws-1-us-east-2.pooler.supabase.com:5432/postgres",
        SQLALCHEMY_POOL_SIZE="5",
        SQLALCHEMY_MAX_OVERFLOW="2",
        DATABASE_PROFILE="",
    )

    engine_opts = config.Config.SQLALCHEMY_ENGINE_OPTIONS
    poolclass = engine_opts.get("poolclass")
    assert poolclass is not None
    assert poolclass.__name__ == "NullPool"
    assert "pool_size" not in engine_opts
    assert "max_overflow" not in engine_opts
    assert "pool_timeout" not in engine_opts or engine_opts["pool_timeout"] == 0


def test_regular_database_respects_pool_settings(monkeypatch):
    config = reload_config(
        monkeypatch,
        DATABASE_URL="postgresql://user:pass@localhost:5432/postgres",
        SQLALCHEMY_POOL_SIZE="3",
        SQLALCHEMY_MAX_OVERFLOW="0",
        DATABASE_PROFILE="",
    )
    engine_opts = config.Config.SQLALCHEMY_ENGINE_OPTIONS
    assert engine_opts["pool_size"] == 3
    assert engine_opts["max_overflow"] == 0
    assert "poolclass" not in engine_opts
