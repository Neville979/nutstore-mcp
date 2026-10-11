"""Pure text matching. This module performs no I/O."""


def validate_keyword(keyword: str) -> None:
    if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 256 or not keyword.casefold():
        raise ValueError('keyword must contain 1 to 256 characters and cannot be blank.')


def _line_parts(text: str) -> list[tuple[int, str]]:
    parts = []
    offset = 0
    for raw in text.splitlines(keepends=True):
        if raw.endswith('\r\n'):
            content = raw[:-2]
        elif raw.endswith('\n') or raw.endswith('\r'):
            content = raw[:-1]
        else:
            content = raw
        parts.append((offset, content))
        offset += len(raw)
    return parts


def _snippet(content: str, start: int, length: int, limit: int) -> tuple[str, bool]:
    if len(content) <= limit:
        return content, False
    if length >= limit:
        return content[start:start + limit], True
    window = max(0, min(start - (limit - length) // 2, len(content) - limit))
    return content[window:window + limit], True


def _locate(lines: list[tuple[int, str]], text: str, start: int, end: int) -> tuple[int, str, int, int]:
    for number, (line_start, content) in enumerate(lines, start=1):
        next_start = lines[number][0] if number < len(lines) else len(text)
        if line_start <= start < next_start:
            content_end = line_start + len(content)
            overlap_start = min(max(start, line_start), content_end) - line_start
            overlap_end = min(max(end, line_start), content_end) - line_start
            return number, content, overlap_start, max(0, overlap_end - overlap_start)
    return 1, '', 0, 0


def find_snippets(text: str, keyword: str, *, max_snippet_chars: int = 200) -> list[dict]:
    validate_keyword(keyword)
    if isinstance(max_snippet_chars, bool) or not isinstance(max_snippet_chars, int) or max_snippet_chars < 1:
        raise ValueError('max_snippet_chars must be a positive integer.')
    needle = keyword.casefold()
    origin = []
    folded_parts = []
    for index, char in enumerate(text):
        piece = char.casefold()
        folded_parts.append(piece)
        origin.extend([index] * len(piece))
    haystack = ''.join(folded_parts)
    lines = _line_parts(text)
    hits = []
    cursor = 0
    while True:
        found = haystack.find(needle, cursor)
        if found < 0:
            break
        start = origin[found]
        end = origin[found + len(needle) - 1] + 1
        line_number, content, overlap_start, overlap_length = _locate(lines, text, start, end)
        snippet, truncated = _snippet(content, overlap_start, overlap_length, max_snippet_chars)
        hits.append({'line': line_number, 'snippet': snippet, 'snippet_truncated': truncated})
        next_origin = max(end, start + 1)
        cursor = next((index for index, original in enumerate(origin) if original >= next_origin), len(haystack))
    return hits
