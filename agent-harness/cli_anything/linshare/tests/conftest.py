import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Every test gets its own config directory and no env overrides."""
    config_dir = tmp_path / "config"
    monkeypatch.setenv("LINSHARE_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("LINSHARE_SERVER", raising=False)
    monkeypatch.delenv("LINSHARE_TOKEN", raising=False)
    return config_dir


@pytest.fixture(autouse=True)
def utc_timezone(monkeypatch):
    """Deterministic local timezone; tests that need another one override TZ."""
    import time

    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()
