"""Exercise a real MCP subprocess using the official client SDK."""
import asyncio
import argparse
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(read_path: str | None = None, search_keyword: str | None = None, search_path: str = ''):
    params = StdioServerParameters(command=sys.executable, args=['-m', 'nutstore_mcp.server'], env=dict(os.environ))
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            print('Tools:', ', '.join(t.name for t in tools.tools))
            expected = {'check_connection', 'list_directory', 'read_file', 'search_file', 'search_content'}
            if {tool.name for tool in tools.tools} != expected:
                raise RuntimeError('Expected all five read-only tools to be registered.')
            calls = [('check_connection', {}), ('list_directory', {})]
            if search_keyword is not None:
                calls.append(('search_file', {'keyword': search_keyword, 'path': search_path}))
            if read_path is not None:
                calls.append(('read_file', {'path': read_path}))
            for name, arguments in calls:
                result = await session.call_tool(name, arguments)
                print('Call:', name)
                print(json.dumps(result.model_dump(mode='json'), ensure_ascii=False))
                if result.isError:
                    raise RuntimeError(f'{name} failed; review the tool response above.')


def main():
    parser = argparse.ArgumentParser(description='Verify Nutstore MCP tools with an official MCP client.')
    parser.add_argument('--read-path', help='Optional relative text file path. Prints document contents.')
    parser.add_argument('--search-keyword', help='Optional filename substring to search for.')
    parser.add_argument('--search-path', default='', help='Relative directory to search in.')
    args = parser.parse_args()
    if args.search_path and args.search_keyword is None:
        parser.error('--search-path requires --search-keyword')
    asyncio.run(run(args.read_path, args.search_keyword, args.search_path))


if __name__ == '__main__':
    main()
