"""Local WebDAV write contract: scoped paths, conditional updates, and readback."""
import asyncio
import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from nutstore_mcp.config import Settings
from nutstore_mcp.webdav import NutstoreWebDAV


@pytest.fixture
def writable_dav(monkeypatch):
    state = {'directories': {''}, 'files': {}, 'versions': {}, 'requests': []}

    class Handler(BaseHTTPRequestHandler):
        def resource(self):
            state['requests'].append(self.command)
            expected = 'Basic ' + base64.b64encode(b'test:app-password').decode()
            if self.headers.get('Authorization') != expected:
                self.send_response(401)
                self.end_headers()
                return None
            return unquote(self.path).removeprefix('/dav/')

        def reply(self, code, data=b'', *, etag=None):
            self.send_response(code)
            if etag is not None:
                self.send_header('ETag', etag)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_MKCOL(self):
            path = self.resource()
            if path is None:
                return
            path = path.rstrip('/') + '/'
            parent = path.rstrip('/').rpartition('/')[0]
            parent = parent + '/' if parent else ''
            if path in state['directories']:
                self.reply(405)
            elif parent not in state['directories']:
                self.reply(409)
            else:
                state['directories'].add(path)
                self.reply(201)

        def do_PROPFIND(self):
            path = self.resource()
            if path is None:
                return
            path = path.rstrip('/') + '/' if path else ''
            if path not in state['directories']:
                self.reply(404)
                return
            body = (
                '<d:multistatus xmlns:d="DAV:"><d:response><d:href>/dav/'
                + path + '</d:href><d:propstat><d:prop><d:resourcetype>'
                + '<d:collection/></d:resourcetype></d:prop></d:propstat>'
                + '</d:response></d:multistatus>'
            ).encode()
            self.reply(207, body)

        def do_PUT(self):
            path = self.resource()
            if path is None:
                return
            parent = path.rpartition('/')[0] + '/'
            if parent not in state['directories']:
                self.reply(409)
                return
            current = state['versions'].get(path)
            if self.headers.get('If-None-Match') == '*' and current is not None:
                self.reply(412)
                return
            if 'If-Match' in self.headers and self.headers['If-Match'] != current:
                self.reply(412)
                return
            data = self.rfile.read(int(self.headers['Content-Length']))
            state['files'][path] = data
            version = int(current.strip('"v')) + 1 if current else 1
            state['versions'][path] = f'"v{version}"'
            self.reply(204 if current else 201, etag=state['versions'][path])

        def do_GET(self):
            path = self.resource()
            if path is None:
                return
            if path not in state['files']:
                self.reply(404)
            else:
                self.reply(200, state['files'][path], etag=state['versions'][path])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('NUTSTORE_URL', f'http://127.0.0.1:{server.server_port}/dav/')
    monkeypatch.setenv('NUTSTORE_USERNAME', 'test')
    monkeypatch.setenv('NUTSTORE_PASSWORD', 'app-password')
    monkeypatch.setenv('NUTSTORE_WRITE_ROOT', 'Growth-Journal')
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_write_scope_disabled_without_explicit_root(writable_dav, monkeypatch):
    monkeypatch.delenv('NUTSTORE_WRITE_ROOT')
    with pytest.raises(PermissionError, match='disabled'):
        NutstoreWebDAV(Settings.from_env()).write_file('Growth-Journal/daily/2026-11-03.md', 'hi')
    assert writable_dav['requests'] == []


@pytest.mark.parametrize('path', [
    'other/notes.md', 'Growth-Journal-evil/notes.md',
    'Growth-Journal/../secrets.md', '/Growth-Journal/notes.md',
    'Growth-Journal/notes.txt',
])
def test_write_rejects_out_of_scope_and_non_markdown(writable_dav, path):
    with pytest.raises((PermissionError, ValueError)):
        NutstoreWebDAV(Settings.from_env()).write_file(path, 'no')
    assert writable_dav['requests'] == []


def test_create_update_and_reject_stale_version(writable_dav):
    dav = NutstoreWebDAV(Settings.from_env())
    path = 'Growth-Journal/daily/2026/11/2026-11-03.md'
    first = dav.write_file(path, '# 记录\n中文')
    assert first == {
        'path': path, 'byte_count': len('# 记录\n中文'.encode()),
        'created': True, 'etag': '"v1"', 'verified': True,
    }
    assert dav.read_file(path, include_etag=True)['etag'] == '"v1"'
    with pytest.raises(FileExistsError, match='version changed'):
        dav.write_file(path, 'blind overwrite')
    second = dav.write_file(path, '# 记录\n新的内容', expected_etag='"v1"')
    assert not second['created'] and second['etag'] == '"v2"'
    with pytest.raises(FileExistsError):
        dav.write_file(path, 'stale update', expected_etag='"v1"')
    assert dav.read_file(path)['content'] == '# 记录\n新的内容'


def test_existing_directories_are_verified(writable_dav):
    dav = NutstoreWebDAV(Settings.from_env())
    assert dav.create_directory('Growth-Journal/daily')['created'] == [
        'Growth-Journal', 'Growth-Journal/daily',
    ]
    assert dav.create_directory('Growth-Journal/daily')['created'] == []


@pytest.mark.parametrize('content, etag', [
    ('a' * 1048577, None), ('\x00', None), ('ok', '*'), ('ok', 'bad\nvalue'),
])
def test_invalid_write_rejected_before_requests(writable_dav, content, etag):
    with pytest.raises(ValueError):
        NutstoreWebDAV(Settings.from_env()).write_file('Growth-Journal/notes.md', content, etag)
    assert writable_dav['requests'] == []


def test_auth_errors_do_not_leak_credentials(writable_dav, monkeypatch):
    monkeypatch.setenv('NUTSTORE_PASSWORD', 'incorrect-secret')
    with pytest.raises(RuntimeError) as exc:
        NutstoreWebDAV(Settings.from_env()).write_file('Growth-Journal/notes.md', 'hello')
    assert 'incorrect-secret' not in str(exc.value)


def test_mcp_write_roundtrip_and_conflict(writable_dav):
    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=['-m', 'nutstore_mcp.server'], env=dict(os.environ),
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                path = 'Growth-Journal/daily/2026/11/2026-11-03.md'
                created = await session.call_tool('write_file', {'path': path, 'content': '# 第一条'})
                assert not created.isError
                assert json.loads(created.content[0].text)['verified'] is True
                read = await session.call_tool('read_file', {'path': path, 'include_etag': True})
                assert not read.isError
                assert json.loads(read.content[0].text)['etag'] == '"v1"'
                conflict = await session.call_tool('write_file', {'path': path, 'content': 'unsafe'})
                assert conflict.isError
    asyncio.run(run())
