import pytest

from nutstore_mcp.content import find_snippets, validate_keyword


def test_chinese_substring_uses_the_starting_line():
    hits = find_snippets('前言\n这里有控制\n', '控制')
    assert hits == [{'line': 2, 'snippet': '这里有控制', 'snippet_truncated': False}]


def test_casefold_matches_ascii_and_keeps_original_text():
    hits = find_snippets('Control\n', 'control')
    assert hits == [{'line': 1, 'snippet': 'Control', 'snippet_truncated': False}]


def test_spaces_are_literal():
    assert find_snippets('ab', 'a b') == []
    assert find_snippets('a b', 'ab') == []
    assert find_snippets(' a ', ' a ')[0]['snippet'] == ' a '


def test_eszett_maps_back_to_original_letters():
    hits = find_snippets('SS', 'ß')
    assert hits == [{'line': 1, 'snippet': 'SS', 'snippet_truncated': False}]


def test_hits_do_not_overlap_and_same_line_hits_are_separate():
    assert [hit['line'] for hit in find_snippets('alpha one\nalpha two', 'alpha')] == [1, 2]
    same_line = find_snippets('alpha alpha', 'alpha')
    assert [hit['line'] for hit in same_line] == [1, 1]
    assert same_line[0]['snippet'] == same_line[1]['snippet'] == 'alpha alpha'
    assert len(find_snippets('aaa', 'aa')) == 1


def test_long_line_snippet_is_200_characters_around_the_hit():
    text = ('a' * 180) + 'TARGET' + ('b' * 180)
    hit = find_snippets(text, 'TARGET')[0]
    assert hit['line'] == 1
    assert len(hit['snippet']) == 200
    assert hit['snippet_truncated'] is True
    assert 'TARGET' in hit['snippet']
    assert hit['snippet'].startswith('a')
    assert '…' not in hit['snippet'] and '...' not in hit['snippet']


def test_hit_longer_than_200_keeps_its_first_200_characters():
    keyword = 'K' * 220
    hit = find_snippets(keyword + 'tail', keyword)[0]
    assert hit['snippet'] == 'K' * 200
    assert hit['snippet_truncated'] is True


def test_empty_text_and_miss_are_empty_lists():
    assert find_snippets('', '控制') == []
    assert find_snippets('没有', '控制') == []


def test_cross_line_hit_reports_the_start_line_only():
    hits = find_snippets('alpha\nbeta', 'a\nb')
    assert hits == [{'line': 1, 'snippet': 'alpha', 'snippet_truncated': False}]


def test_blank_or_long_keyword_raises():
    with pytest.raises(ValueError):
        validate_keyword('   ')
    with pytest.raises(ValueError):
        find_snippets('text', '')
    with pytest.raises(ValueError):
        find_snippets('text', 'x' * 257)
