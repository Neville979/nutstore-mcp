"""Real HTTP boundaries: encoding, bounded reads and bounded directory traversal."""
import base64
import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote
from xml.sax.saxutils import escape

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from nutstore_mcp.config import Settings
from nutstore_mcp.webdav import NutstoreWebDAV


@pytest.fixture
def text_dav(monkeypatch, request):
    files = {
        'notes.txt': '你好\ncontrol'.encode('utf-8'),
        '资料/Control.MD': b'\xef\xbb\xbf# Motion\n',
        '资料/deep/control.txt': b'learning',
        'binary.txt': b'\x00\x01\x02',
        'invalid.txt': b'\xff\xfe',
        'large.txt': b'x' * 10000,
        'stream.txt': b'x' * 10000,
        'empty.txt': b'',
        'paper.pdf': b'%PDF-1.7',
    }
    directories = {
        '': ['notes.txt', '资料/', 'binary.txt', 'invalid.txt', 'large.txt',
             'stream.txt', 'empty.txt', 'paper.pdf'],
        '资料/': ['Control.MD', 'deep/'],
        '资料/deep/': ['control.txt'],
    }
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def authorize(self):
            if self.headers.get('Authorization') == 'Basic ' + base64.b64encode(b'test:app-password').decode():
                return True
            self.send_response(401)
            self.end_headers()
            return False

        def resource(self):
            calls.append((self.command, self.path))
            return unquote(self.path).removeprefix('/dav/')

        def do_HEAD(self):
            if not self.authorize():
                return
            path = self.resource()
            if getattr(request, 'param', '') == 'head_forbidden':
                self.send_response(403)
            else:
                self.send_response(200 if path in files or path in directories else 404)
            self.end_headers()

        def do_PROPFIND(self):
            if not self.authorize():
                return
            path = self.resource()
            mode = getattr(request, 'param', '')
            if mode == 'redirect':
                self.send_response(302)
                self.send_header('Location', '/outside/')
                self.end_headers()
                return
            if mode in ('html200', 'html207', 'broken207'):
                self.send_response(200 if mode == 'html200' else 207)
                self.end_headers()
                self.wfile.write(b'<broken' if mode == 'broken207' else b'<html>Unavailable</html>')
                return
            if path not in directories:
                self.send_response(404)
                self.end_headers()
                return
            assert self.headers.get('Depth') == '1'
            hrefs = [path] + [path + name for name in directories[path]]
            responses = ''.join('<d:response><d:href>' + escape('/dav/' + quote(href)) +
                                '</d:href></d:response>' for href in hrefs)
            body = ('<d:multistatus xmlns:d="DAV:">' + responses + '</d:multistatus>').encode()
            self.send_response(207)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.authorize():
                return
            path = self.resource()
            if path not in files:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            if path != 'stream.txt':
                self.send_header('Content-Length', str(len(files[path])))
            self.end_headers()
            self.wfile.write(files[path])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('NUTSTORE_URL', f'http://127.0.0.1:{server.server_port}/dav/')
    monkeypatch.setenv('NUTSTORE_USERNAME', 'test')
    monkeypatch.setenv('NUTSTORE_PASSWORD', 'app-password')
    try:
        yield NutstoreWebDAV(Settings.from_env()), calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_read_chinese_utf8(text_dav):
    dav, _ = text_dav
    assert dav.read_file('notes.txt') == {
        'path': 'notes.txt', 'content': '你好\ncontrol', 'encoding': 'utf-8', 'byte_count': 14,
    }


def test_read_bom_chinese_path(text_dav):
    dav, _ = text_dav
    assert dav.read_file('资料/Control.MD')['content'] == '# Motion\n'


def test_read_empty_file(text_dav):
    assert text_dav[0].read_file('empty.txt')['content'] == ''


def test_read_exact_byte_limit(text_dav):
    assert text_dav[0].read_file('notes.txt', max_bytes=14)['content'] == '你好\ncontrol'


@pytest.mark.parametrize('path', ['large.txt', 'stream.txt'])
def test_read_enforces_actual_byte_limit(text_dav, path):
    with pytest.raises(ValueError, match='limit'):
        text_dav[0].read_file(path, max_bytes=100)


@pytest.mark.parametrize('path', ['binary.txt', 'invalid.txt', 'paper.pdf', '', '资料/', '../secret.txt'])
def test_read_rejects_nontext_and_unsafe_paths(text_dav, path):
    with pytest.raises(ValueError):
        text_dav[0].read_file(path)


@pytest.mark.parametrize('size', [0, -1, 1048577])
def test_read_rejects_invalid_budget_without_request(text_dav, size):
    with pytest.raises(ValueError):
        text_dav[0].read_file('notes.txt', max_bytes=size)
    assert text_dav[1] == []


def test_read_missing_file(text_dav):
    with pytest.raises(RuntimeError, match='WebDAV'):
        text_dav[0].read_file('missing.txt')


def test_read_auth_failure_hides_password(text_dav, monkeypatch):
    monkeypatch.setenv('NUTSTORE_PASSWORD', 'incorrect-secret')
    with pytest.raises(RuntimeError) as exc:
        NutstoreWebDAV(Settings.from_env()).read_file('notes.txt')
    assert 'incorrect-secret' not in str(exc.value)


def test_search_recurses_case_insensitively_without_reading_body(text_dav):
    dav, calls = text_dav
    result = dav.search_file('CONTROL')
    assert result['matches'] == ['资料/Control.MD', '资料/deep/control.txt']
    assert not result['truncated']
    assert result['scanned_directories'] == 3
    assert result['scanned_entries'] == 11
    assert all(method != 'GET' for method, _ in calls)


def test_search_does_not_match_body(text_dav):
    assert text_dav[0].search_file('learning')['matches'] == []


def test_search_nonrecursive_and_scoped(text_dav):
    result = text_dav[0].search_file('control', path='资料', recursive=False)
    assert result['matches'] == ['资料/Control.MD']
    assert not result['truncated']
    assert result['scanned_directories'] == 1


@pytest.mark.parametrize('text_dav', ['head_forbidden'], indirect=True)
def test_directory_search_works_when_head_is_forbidden(text_dav):
    dav, calls = text_dav
    result = dav.search_file('control', path='资料/')
    assert result['matches'] == ['资料/Control.MD', '资料/deep/control.txt']
    assert all(method != 'HEAD' for method, _ in calls)


@pytest.mark.parametrize('text_dav', ['html200', 'html207', 'broken207'], indirect=True)
def test_directory_rejects_non_webdav_responses(text_dav):
    with pytest.raises(RuntimeError, match='WebDAV'):
        text_dav[0].check_connection()


@pytest.mark.parametrize('text_dav', ['redirect'], indirect=True)
def test_directory_rejects_redirect_without_following(text_dav):
    with pytest.raises(RuntimeError, match='WebDAV'):
        text_dav[0].list_directory()
    assert text_dav[1] == [('PROPFIND', '/dav/')]


@pytest.mark.parametrize('budget, reason', [
    ({'max_depth': 0}, 'max_depth'),
    ({'max_directories': 1}, 'max_directories'),
    ({'max_entries': 1}, 'max_entries'),
    ({'max_results': 1}, 'max_results'),
])
def test_search_reports_incomplete_scan(text_dav, budget, reason):
    result = text_dav[0].search_file('control', **budget)
    assert result['truncated']
    assert reason in result['limit_reasons']
    if reason == 'max_results':
        assert len(result['matches']) == 1


def test_search_entry_budget_stops_before_next_directory_request(text_dav):
    dav, calls = text_dav
    result = dav.search_file('control', max_entries=8)
    assert result['truncated']
    assert result['limit_reasons'] == ['max_entries']
    assert result['scanned_directories'] == 1
    assert calls == [('PROPFIND', '/dav/')]


@pytest.mark.parametrize('arguments', [
    {'keyword': ''}, {'keyword': '  '}, {'keyword': 'x', 'path': '../secret'},
    {'keyword': 'x', 'max_depth': -1}, {'keyword': 'x', 'max_depth': 11},
    {'keyword': 'x', 'max_results': 0}, {'keyword': 'x', 'max_results': 201},
    {'keyword': 'x', 'max_directories': 501}, {'keyword': 'x', 'max_entries': 20001},
])
def test_search_rejects_invalid_arguments_without_request(text_dav, arguments):
    with pytest.raises(ValueError):
        text_dav[0].search_file(**arguments)
    assert text_dav[1] == []


def test_mcp_text_tools_roundtrip(text_dav):
    async def run():
        params = StdioServerParameters(command=sys.executable, args=['-m', 'nutstore_mcp.server'], env=dict(os.environ))
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    'check_connection', 'list_directory', 'read_file', 'search_file', 'search_content',
                }
                reading = await session.call_tool('read_file', {'path': '资料/Control.MD'})
                assert not reading.isError
                assert json.loads(reading.content[0].text)['content'] == '# Motion\n'
                search = await session.call_tool('search_file', {'keyword': 'control', 'path': '资料/'})
                assert not search.isError
                assert json.loads(search.content[0].text)['matches'] == [
                    '资料/Control.MD', '资料/deep/control.txt',
                ]
                content = await session.call_tool('search_content', {'keyword': 'learning'})
                assert not content.isError
                body = json.loads(content.content[0].text)
                assert body['matches'][0]['path'] == '资料/deep/control.txt'
                assert body['matches'][0]['snippet'] == 'learning'
                assert body['incomplete'] is True
                assert any(error['reason'] in {'not_utf8', 'binary'} for error in body['file_errors'])
                invalid = await session.call_tool('search_content', {'keyword': ' '})
                assert invalid.isError
                missing = await session.call_tool('search_content', {'keyword': 'x', 'path': 'missing/'})
                assert missing.isError
                for name, arguments in (
                    ('read_file', {'path': '../secret.txt'}),
                    ('read_file', {'path': 'stream.txt', 'max_bytes': 100}),
                    ('read_file', {'path': 'missing.txt'}),
                    ('search_file', {'keyword': ''}),
                    ('search_file', {'keyword': 'x', 'path': 'missing/'}),
                ):
                    assert (await session.call_tool(name, arguments)).isError
    asyncio.run(run())


def test_smoke_can_exercise_text_tools(text_dav):
    result = subprocess.run(
        [sys.executable, '-m', 'nutstore_mcp.smoke', '--read-path', '资料/Control.MD',
         '--search-keyword', 'control', '--search-path', '资料/'],
        capture_output=True, text=True, encoding='utf-8', timeout=30,
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
    )
    assert result.returncode == 0, result.stderr
    assert 'read_file' in result.stdout
    assert 'search_file' in result.stdout
    assert '# Motion' in result.stdout


@pytest.fixture
def content_dav(monkeypatch):
    files = {}
    for suffix in ('.md', '.markdown', '.txt', '.json', '.csv', '.yaml', '.yml', '.rst'):
        files['kinds/sample' + suffix] = '控制\n'.encode()
    files.update({
        'clean/body.txt': '前言\n这里有控制\n'.encode(),
        'clean/NameHits.txt': b'nothing inside\n',
        'clean/repeat.txt': b'alpha one\nalpha two\n',
        'clean/empty.txt': b'',
        'clean/paper.pdf': b'%PDF-1.7',
        'clean/notes/deep.md': b'deepword\n',
        'problems/huge.txt': b'PREFIX-SECRET' + b'y' * 200,
        'problems/later.txt': '结尾有控制\n'.encode(),
        'problems/bad.txt': b'\xff\xfe',
        'problems/bin.txt': b'ok\x00',
        'problems/denied.txt': b'secret\n',
        'problems/gone.txt': b'control\n',
        'problems/redir.txt': b'control\n',
        'problems/unauth.txt': b'control\n',
        'problems/paper.pdf': b'%PDF-1.7',
    })
    directories = {
        '': ['clean/', 'kinds/', 'problems/'],
        'clean/': ['body.txt', 'NameHits.txt', 'repeat.txt', 'empty.txt', 'paper.pdf', 'notes/'],
        'clean/notes/': ['deep.md'],
        'kinds/': ['sample' + suffix for suffix in
                   ('.md', '.markdown', '.txt', '.json', '.csv', '.yaml', '.yml', '.rst')],
        'problems/': [
            'huge.txt', 'later.txt', 'bad.txt', 'bin.txt', 'denied.txt', 'gone.txt',
            'redir.txt', 'unauth.txt', 'paper.pdf',
        ],
    }
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def authorize(self):
            if self.headers.get('Authorization') == 'Basic ' + base64.b64encode(b'test:app-password').decode():
                return True
            self.send_response(401)
            self.end_headers()
            return False

        def resource(self):
            calls.append((self.command, self.path))
            return unquote(self.path).removeprefix('/dav/')

        def do_PROPFIND(self):
            if not self.authorize():
                return
            path = self.resource()
            if path not in directories:
                self.send_response(404)
                self.end_headers()
                return
            hrefs = [path] + [path + name for name in directories[path]]
            responses = ''.join(
                '<d:response><d:href>' + escape('/dav/' + quote(href)) + '</d:href></d:response>'
                for href in hrefs
            )
            body = ('<d:multistatus xmlns:d="DAV:">' + responses + '</d:multistatus>').encode()
            self.send_response(207)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.authorize():
                return
            path = self.resource()
            if path == 'problems/denied.txt':
                self.send_response(403)
                self.end_headers()
                return
            if path == 'problems/unauth.txt':
                self.send_response(401)
                self.end_headers()
                return
            if path == 'problems/gone.txt':
                self.send_response(404)
                self.end_headers()
                return
            if path == 'problems/redir.txt':
                self.send_response(302)
                self.send_header('Location', 'http://example.invalid/secret')
                self.end_headers()
                return
            if path not in files:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Length', str(len(files[path])))
            self.end_headers()
            self.wfile.write(files[path])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('NUTSTORE_URL', f'http://127.0.0.1:{server.server_port}/dav/')
    monkeypatch.setenv('NUTSTORE_USERNAME', 'test')
    monkeypatch.setenv('NUTSTORE_PASSWORD', 'app-password')
    try:
        yield NutstoreWebDAV(Settings.from_env()), calls
    finally:
        server.shutdown()
        server.server_close()


def test_content_finds_body_when_filename_lacks_keyword(content_dav):
    result = content_dav[0].search_content('控制', path='clean')
    assert result['matches'] == [{
        'path': 'clean/body.txt', 'line': 2, 'snippet': '这里有控制', 'snippet_truncated': False,
    }]
    assert result['incomplete'] is False
    assert result['truncated'] is False
    assert result['file_errors'] == []


def test_content_ignores_filename_only_hit(content_dav):
    result = content_dav[0].search_content('NameHits', path='clean')
    assert result['matches'] == []
    assert result['incomplete'] is False


def test_content_casefold_and_all_text_suffixes(content_dav):
    result = content_dav[0].search_content('控制', path='kinds')
    assert sorted(match['path'] for match in result['matches']) == sorted(
        'kinds/sample' + suffix for suffix in
        ('.md', '.markdown', '.txt', '.json', '.csv', '.yaml', '.yml', '.rst')
    )
    assert result['incomplete'] is False


def test_content_repeated_hits_stop_at_max_results(content_dav):
    result = content_dav[0].search_content('alpha', path='clean', max_results=1)
    assert len(result['matches']) == 1
    assert result['matches'][0]['path'] == 'clean/repeat.txt'
    assert result['truncated'] is True
    assert result['limit_reasons'] == ['max_results']
    assert result['incomplete'] is True


def test_content_complete_miss_is_not_incomplete(content_dav):
    result = content_dav[0].search_content('不存在的词', path='clean')
    assert result['matches'] == []
    assert result['incomplete'] is False
    assert result['truncated'] is False
    assert result['file_errors'] == []
    assert result['scanned_files'] == 5


def test_content_nonrecursive_does_not_add_max_depth(content_dav):
    result = content_dav[0].search_content('deepword', path='clean', recursive=False)
    assert result['matches'] == []
    assert result['limit_reasons'] == []
    assert result['incomplete'] is False


def test_content_does_not_download_pdf(content_dav):
    content_dav[0].search_content('控制', path='clean')
    assert all(not path.endswith('paper.pdf') for _, path in content_dav[1] if _ == 'GET')


def test_content_rejects_bad_arguments_before_request(content_dav):
    dav, calls = content_dav
    for arguments in (
        {'keyword': '  '}, {'keyword': 'x' * 257}, {'keyword': 'x', 'path': '../secret'},
        {'keyword': 'x', 'max_results': 0}, {'keyword': 'x', 'max_results': 101},
        {'keyword': 'x', 'max_files': 0}, {'keyword': 'x', 'max_seconds': True},
        {'keyword': 'x', 'max_total_bytes': 4194305},
    ):
        with pytest.raises(ValueError):
            dav.search_content(**arguments)
    assert calls == []


def test_content_reports_file_errors_and_keeps_later_hit(content_dav):
    dav, calls = content_dav
    result = dav.search_content(
        '控制', path='problems', max_file_bytes=64, max_total_bytes=1000000,
    )
    assert result['matches'] == [{
        'path': 'problems/later.txt', 'line': 1, 'snippet': '结尾有控制', 'snippet_truncated': False,
    }]
    assert {'path': 'problems/huge.txt', 'reason': 'too_large'} in result['file_errors']
    assert {'path': 'problems/bad.txt', 'reason': 'not_utf8'} in result['file_errors']
    assert {'path': 'problems/bin.txt', 'reason': 'binary'} in result['file_errors']
    assert {'path': 'problems/denied.txt', 'reason': 'forbidden'} in result['file_errors']
    assert {'path': 'problems/unauth.txt', 'reason': 'forbidden'} in result['file_errors']
    assert {'path': 'problems/gone.txt', 'reason': 'not_found'} in result['file_errors']
    assert {'path': 'problems/redir.txt', 'reason': 'read_failed'} in result['file_errors']
    assert result['incomplete'] is True
    assert all('PREFIX-SECRET' not in match['snippet'] for match in result['matches'])
    assert all('example.invalid' not in path for _, path in calls)
    assert 'app-password' not in str(result)


def test_content_total_byte_cap_stops_without_a_file_error(content_dav):
    dav, calls = content_dav
    result = dav.search_content(
        '控制', path='problems', max_file_bytes=100000, max_total_bytes=50,
    )
    assert result['matches'] == []
    assert result['file_errors'] == []
    assert result['limit_reasons'] == ['max_total_bytes']
    assert result['truncated'] is True
    assert result['downloaded_bytes'] >= 50
    assert not any(path.endswith('later.txt') for _, path in calls if _ == 'GET')


def test_content_file_and_total_caps_together(content_dav):
    dav, calls = content_dav
    result = dav.search_content(
        '控制', path='problems', max_file_bytes=8, max_total_bytes=50,
    )
    assert {'path': 'problems/huge.txt', 'reason': 'too_large'} in result['file_errors']
    assert 'max_total_bytes' in result['limit_reasons']
    assert not any(path.endswith('later.txt') for _, path in calls if _ == 'GET')


def test_content_budget_reasons(content_dav):
    dav = content_dav[0]
    cases = (
        ({'max_depth': 0, 'path': 'clean', 'keyword': 'deepword'}, 'max_depth'),
        ({'max_directories': 1, 'keyword': '控制'}, 'max_directories'),
        ({'max_entries': 1, 'path': 'clean', 'keyword': '控制'}, 'max_entries'),
        ({'max_files': 1, 'path': 'clean', 'keyword': '控制'}, 'max_files'),
    )
    for arguments, reason in cases:
        result = dav.search_content(**arguments)
        assert reason in result['limit_reasons']
        assert result['truncated'] is True
        assert result['incomplete'] is True


def test_content_time_budget_uses_injected_clock(content_dav):
    ticks = iter([0, 0, 50])
    result = content_dav[0].search_content('控制', path='clean', clock=lambda: next(ticks))
    assert result['limit_reasons'] == ['max_seconds']
    assert all(method != 'GET' for method, _ in content_dav[1])


def test_content_directory_failure_raises_without_a_partial_dict(content_dav):
    with pytest.raises(RuntimeError, match='WebDAV'):
        content_dav[0].search_content('控制', path='missing')
