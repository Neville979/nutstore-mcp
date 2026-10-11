import pytest


def test_missing_credentials(monkeypatch):
    from nutstore_mcp.config import Settings
    from pathlib import Path
    monkeypatch.delenv('NUTSTORE_USERNAME', raising=False)
    monkeypatch.delenv('NUTSTORE_PASSWORD', raising=False)
    monkeypatch.setenv('NUTSTORE_ENV_FILE', str(Path(__file__).parent / 'absent.env'))
    with pytest.raises(ValueError, match='NUTSTORE_USERNAME'):
        Settings.from_env()


@pytest.mark.parametrize('path', ['../secret', '/etc', 'https://evil.test', r'a\b'])
def test_reject_unsafe_paths(path):
    from nutstore_mcp.webdav import validate_path
    with pytest.raises(ValueError):
        validate_path(path)
