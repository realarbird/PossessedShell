import pytest

from possessed_shell import config


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Settings isolated from the real environment and home directory."""
    for var in ("SENTRY_DSN", "BACKBOARD_API_KEY", "POSSESSED_DISABLED", "POSSESSED_LLM_API", "POSSESSED_LLM_URL",
                "OLLAMA_HOST", "POSSESSED_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("POSSESSED_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("POSSESSED_NO_ANIMATION", "1")
    s = config.load()
    s.ensure_home()
    return s
