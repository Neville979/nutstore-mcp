"""Explicit configuration; environment variables take priority over .env."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


@dataclass(frozen=True)
class Settings:
    url: str
    username: str
    password: str = field(repr=False)
    timeout: int = 30

    @classmethod
    def from_env(cls):
        # Editable installation works regardless of Cursor's working directory.
        env_file = Path(os.environ.get('NUTSTORE_ENV_FILE', Path(__file__).resolve().parents[2] / '.env'))
        values = {**dotenv_values(env_file), **os.environ}
        for key in ('NUTSTORE_USERNAME', 'NUTSTORE_PASSWORD'):
            if not values.get(key, '').strip():
                raise ValueError(f'Missing {key}; fill in the local .env file.')
        url = values.get('NUTSTORE_URL', 'https://dav.jianguoyun.com/dav/').rstrip('/') + '/'
        parsed = urlsplit(url)
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')):
            raise ValueError('NUTSTORE_URL requires HTTPS (HTTP only for loopback tests).')
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Invalid NUTSTORE_URL.')
        timeout = int(values.get('NUTSTORE_TIMEOUT', '30'))
        if not 1 <= timeout <= 120:
            raise ValueError('NUTSTORE_TIMEOUT must be between 1 and 120 seconds.')
        return cls(url, values['NUTSTORE_USERNAME'].strip(), values['NUTSTORE_PASSWORD'], timeout)
