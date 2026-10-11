"""Read-only WebDAV adapter and direct connectivity command."""
import argparse
import json
import sys
import time
from collections import deque
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from xml.etree import ElementTree

from webdav3.client import Client, WebDavXmlUtils
from webdav3.urn import Urn
from requests.exceptions import RequestException

from .config import Settings
from .content import find_snippets, validate_keyword

TEXT_SUFFIXES = {'.md', '.markdown', '.txt', '.json', '.csv', '.yaml', '.yml', '.rst'}
REQUEST_ERROR = 'WebDAV request failed. Check app password, directory, network and service limits.'


def _outcome(downloaded_bytes, text=None, file_error=None, stop_reason=None):
    return {
        'text': text, 'downloaded_bytes': downloaded_bytes,
        'file_error': file_error, 'stop_reason': stop_reason,
    }


def validate_budget(name: str, value: int, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer between {minimum} and {maximum}.')


def validate_path(path: str) -> str:
    if path.startswith('/') or '\\' in path or ':' in path or any(p in ('.', '..') for p in path.split('/')) or '%' in path or any(ord(c) < 32 for c in path):
        raise ValueError('Use a relative WebDAV path; traversal and encoded paths are unsupported.')
    return path


class NutstoreWebDAV:
    def __init__(self, settings: Settings):
        url = urlsplit(settings.url)
        self._auth = (settings.username, settings.password)
        self._timeout = settings.timeout
        self.client = Client({
            'webdav_hostname': f'{url.scheme}://{url.netloc}',
            'webdav_root': url.path,
            'webdav_login': settings.username,
            'webdav_password': settings.password,
            'webdav_timeout': settings.timeout,
        })

    def list_directory(self, path: str = '') -> list[str]:
        path = validate_path(path)
        directory_urn = Urn(path, directory=True)
        full_path = Urn.normalize_path(self.client.get_full_path(directory_urn))
        try:
            # Direct PROPFIND avoids Nutstore's unsupported HEAD preflight and
            # upstream's permissive parsing/redirect policy. Keep its URL and XML helpers.
            with self.client.session.request(
                'PROPFIND', self.client.get_url(directory_urn.quote()),
                auth=self._auth, headers=self.client.get_headers('list'),
                timeout=self._timeout, stream=True, allow_redirects=False,
            ) as response:
                if response.status_code != 207:
                    raise RuntimeError(REQUEST_ERROR)
                body = response.content
                tree = ElementTree.fromstring(body)
                if tree.tag != '{DAV:}multistatus' or not tree.findall('{DAV:}response'):
                    raise RuntimeError(REQUEST_ERROR)
                urns = WebDavXmlUtils.parse_get_list_response(body)
                if not any(Urn.compare_path(full_path, urn.path()) for urn in urns):
                    raise RuntimeError(REQUEST_ERROR)
                return [urn.filename() for urn in urns if not Urn.compare_path(full_path, urn.path())]
        except (RequestException, ElementTree.ParseError):
            # Upstream exceptions can include URLs; never emit credentials.
            raise RuntimeError(REQUEST_ERROR) from None

    def read_file(self, path: str, max_bytes: int = 262144) -> dict:
        """Read bounded UTF-8 text. Never silently truncate a document."""
        path = validate_path(path)
        validate_budget('max_bytes', max_bytes, 1, 1048576)
        if not path or path.endswith('/') or PurePosixPath(path).suffix.lower() not in TEXT_SUFFIXES:
            raise ValueError('Use a supported text file: md, markdown, txt, json, csv, yaml, yml or rst.')
        data = bytearray()
        try:
            # Direct streamed GET avoids download_from's extra metadata requests and
            # unbounded buffer. Do not follow redirects outside the configured root.
            with self.client.session.get(
                self.client.get_url(Urn(path).quote()), auth=self._auth,
                timeout=self._timeout, stream=True, allow_redirects=False,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise RuntimeError(REQUEST_ERROR)
                for chunk in response.iter_content(chunk_size=min(8192, max_bytes + 1)):
                    if len(data) + len(chunk) > max_bytes:
                        raise ValueError('File exceeds max_bytes limit; request a smaller file or increase the limit.')
                    data.extend(chunk)
        except RequestException:
            raise RuntimeError(REQUEST_ERROR) from None
        try:
            content = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('File is not UTF-8 text; binary and other encodings are unsupported.') from None
        if any(ord(char) < 32 and char not in '\t\n\r\f' for char in content):
            raise ValueError('File contains binary control characters; only text files are supported.')
        return {'path': path, 'content': content, 'encoding': 'utf-8', 'byte_count': len(data)}

    class WalkState:
        def __init__(self):
            self.reasons = set()
            self.scanned_directories = 0
            self.scanned_entries = 0

    def _iter_entries(self, path, *, recursive, max_depth, max_directories, max_entries, before_list=None):
        state = self.WalkState()

        def generate():
            root = path.rstrip('/') + '/' if path else ''
            pending = deque([(root, 0)])
            while pending:
                reason = before_list() if before_list is not None else None
                if reason:
                    state.reasons.add(reason)
                    return
                if state.scanned_entries >= max_entries:
                    state.reasons.add('max_entries')
                    return
                if state.scanned_directories >= max_directories:
                    state.reasons.add('max_directories')
                    return
                directory, depth = pending.popleft()
                entries = self.list_directory(directory)
                state.scanned_directories += 1
                for name in entries:
                    if state.scanned_entries >= max_entries:
                        state.reasons.add('max_entries')
                        return
                    state.scanned_entries += 1
                    child_name = name.rstrip('/')
                    if not child_name or '/' in child_name:
                        raise ValueError('WebDAV returned an invalid child name.')
                    child = validate_path(directory + name)
                    if name.endswith('/') and recursive:
                        if depth < max_depth:
                            pending.append((child, depth + 1))
                        else:
                            state.reasons.add('max_depth')
                    yield {'name': name, 'child': child, 'is_dir': name.endswith('/')}

        return state, generate()

    def search_file(
        self, keyword: str, path: str = '', recursive: bool = True,
        max_depth: int = 3, max_results: int = 50,
        max_directories: int = 100, max_entries: int = 5000,
    ) -> dict:
        """Search file names by case-insensitive substring, never file contents."""
        path = validate_path(path)
        if not keyword.strip() or len(keyword) > 256:
            raise ValueError('keyword must contain 1 to 256 characters and cannot be blank.')
        for name, value, minimum, maximum in (
            ('max_depth', max_depth, 0, 10), ('max_results', max_results, 1, 200),
            ('max_directories', max_directories, 1, 500), ('max_entries', max_entries, 1, 20000),
        ):
            validate_budget(name, value, minimum, maximum)
        needle = keyword.casefold()
        state, entries = self._iter_entries(
            path, recursive=recursive, max_depth=max_depth,
            max_directories=max_directories, max_entries=max_entries,
        )
        matches = []
        for entry in entries:
            if entry['is_dir']:
                continue
            if needle in entry['name'].casefold():
                matches.append(entry['child'])
                if len(matches) >= max_results:
                    state.reasons.add('max_results')
                    break
        return {
            'matches': matches, 'truncated': bool(state.reasons),
            'limit_reasons': sorted(state.reasons),
            'scanned_directories': state.scanned_directories,
            'scanned_entries': state.scanned_entries,
        }

    def search_content(
        self, keyword: str, path: str = '', recursive: bool = True,
        max_depth: int = 3, max_results: int = 20,
        max_directories: int = 50, max_entries: int = 2000,
        max_files: int = 30, max_file_bytes: int = 262144,
        max_total_bytes: int = 1048576, max_seconds: int = 15,
        clock=None,
    ) -> dict:
        path = validate_path(path)
        validate_keyword(keyword)
        for name, value, minimum, maximum in (
            ('max_depth', max_depth, 0, 10), ('max_results', max_results, 1, 100),
            ('max_directories', max_directories, 1, 200), ('max_entries', max_entries, 1, 10000),
            ('max_files', max_files, 1, 100), ('max_file_bytes', max_file_bytes, 1, 1048576),
            ('max_total_bytes', max_total_bytes, 1, 4194304), ('max_seconds', max_seconds, 1, 60),
        ):
            validate_budget(name, value, minimum, maximum)
        clock = time.monotonic if clock is None else clock
        started = clock()
        matches = []
        file_errors = []
        downloaded_bytes = 0
        opened_files = 0
        scanned_files = 0

        def before_list():
            if clock() - started >= max_seconds:
                return 'max_seconds'
            return None

        state, entries = self._iter_entries(
            path, recursive=recursive, max_depth=max_depth,
            max_directories=max_directories, max_entries=max_entries, before_list=before_list,
        )
        for entry in entries:
            if entry['is_dir'] or PurePosixPath(entry['child']).suffix.lower() not in TEXT_SUFFIXES:
                continue
            if opened_files >= max_files:
                state.reasons.add('max_files')
                break
            if downloaded_bytes >= max_total_bytes:
                state.reasons.add('max_total_bytes')
                break
            if clock() - started >= max_seconds:
                state.reasons.add('max_seconds')
                break
            opened_files += 1
            outcome = self._read_text_bounded(
                entry['child'], max_file_bytes, downloaded_bytes, max_total_bytes,
            )
            downloaded_bytes = outcome['downloaded_bytes']
            if outcome['stop_reason']:
                state.reasons.add(outcome['stop_reason'])
            if outcome['file_error']:
                file_errors.append({'path': entry['child'], 'reason': outcome['file_error']})
            if outcome['stop_reason']:
                break
            if outcome['file_error']:
                continue
            scanned_files += 1
            for hit in find_snippets(outcome['text'], keyword):
                matches.append({'path': entry['child'], **hit})
                if len(matches) >= max_results:
                    state.reasons.add('max_results')
                    break
            if 'max_results' in state.reasons:
                break
        reasons = state.reasons
        return {
            'matches': matches,
            'incomplete': bool(reasons or file_errors),
            'truncated': bool(reasons),
            'limit_reasons': sorted(reasons),
            'file_errors': file_errors,
            'scanned_directories': state.scanned_directories,
            'scanned_entries': state.scanned_entries,
            'scanned_files': scanned_files,
            'downloaded_bytes': downloaded_bytes,
        }

    def _read_text_bounded(self, path, max_file_bytes, downloaded_bytes, max_total_bytes):
        data = bytearray()
        try:
            with self.client.session.get(
                self.client.get_url(Urn(path).quote()), auth=self._auth,
                timeout=self._timeout, stream=True, allow_redirects=False,
            ) as response:
                status = response.status_code
                if status in (401, 403):
                    return _outcome(downloaded_bytes, file_error='forbidden')
                if status == 404:
                    return _outcome(downloaded_bytes, file_error='not_found')
                if not 200 <= status < 300:
                    return _outcome(downloaded_bytes, file_error='read_failed')
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    over_file = len(data) + len(chunk) > max_file_bytes
                    over_total = downloaded_bytes + len(chunk) > max_total_bytes
                    downloaded_bytes += len(chunk)
                    if over_file and over_total:
                        return _outcome(downloaded_bytes, file_error='too_large', stop_reason='max_total_bytes')
                    if over_file:
                        return _outcome(downloaded_bytes, file_error='too_large')
                    if over_total:
                        return _outcome(downloaded_bytes, stop_reason='max_total_bytes')
                    data.extend(chunk)
        except RequestException:
            return _outcome(downloaded_bytes, file_error='read_failed')
        try:
            text = bytes(data).decode('utf-8-sig')
        except UnicodeDecodeError:
            return _outcome(downloaded_bytes, file_error='not_utf8')
        if any(ord(char) < 32 and char not in '\t\n\r\f' for char in text):
            return _outcome(downloaded_bytes, file_error='binary')
        return _outcome(downloaded_bytes, text=text)

    def check_connection(self) -> dict:
        entries = self.list_directory()
        return {'ok': True, 'entry_count': len(entries)}


def main():
    parser = argparse.ArgumentParser(description='List a Nutstore WebDAV directory.')
    parser.add_argument('--path', default='')
    args = parser.parse_args()
    try:
        print(json.dumps(NutstoreWebDAV(Settings.from_env()).list_directory(args.path), ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
