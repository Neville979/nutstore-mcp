import asyncio
import base64
import os
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from nutstore_mcp.config import Settings
from nutstore_mcp.webdav import NutstoreWebDAV


@pytest.fixture
def dav(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_PROPFIND(self):
            expected = 'Basic ' + base64.b64encode(b'test:app-password').decode()
            if self.headers.get('Authorization') != expected:
                self.send_response(401)
                self.end_headers()
                return
            assert self.path == '/dav/'
            assert self.headers.get('Depth') == '1'
            body = b'''<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">
            <d:response><d:href>/dav/</d:href></d:response>
            <d:response><d:href>/dav/notes.txt</d:href></d:response>
            <d:response><d:href>/dav/%E8%B5%84%E6%96%99/</d:href></d:response>
            </d:multistatus>'''
            self.send_response(207)
            self.send_header('Content-Type', 'application/xml')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('NUTSTORE_URL', f'http://127.0.0.1:{server.server_port}/dav/')
    monkeypatch.setenv('NUTSTORE_USERNAME', 'test')
    monkeypatch.setenv('NUTSTORE_PASSWORD', 'app-password')
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_webdav_listing(dav):
    assert NutstoreWebDAV(Settings.from_env()).list_directory() == ['notes.txt', '资料/']


def test_auth_failure_hides_password(dav, monkeypatch):
    monkeypatch.setenv('NUTSTORE_PASSWORD', 'incorrect-secret')
    with pytest.raises(RuntimeError) as exc:
        NutstoreWebDAV(Settings.from_env()).list_directory()
    assert 'incorrect-secret' not in str(exc.value)


def test_mcp_subprocess_roundtrip(dav):
    async def run():
        params = StdioServerParameters(command=sys.executable, args=['-m', 'nutstore_mcp.server'], env=dict(os.environ))
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {
                    'check_connection', 'list_directory', 'read_file', 'search_file', 'search_content',
                }
                connection = await session.call_tool('check_connection', {})
                assert not connection.isError
                assert json.loads(connection.content[0].text) == {'ok': True, 'entry_count': 2}
                listing = await session.call_tool('list_directory', {})
                assert not listing.isError
                assert listing.structuredContent == {'result': ['notes.txt', '资料/']}
                invalid = await session.call_tool('list_directory', {'path': '../secret'})
                assert invalid.isError
    asyncio.run(run())
