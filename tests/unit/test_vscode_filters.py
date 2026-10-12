"""Unit tests for filter_plugins/vscode.py."""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

SPEC = importlib.util.spec_from_file_location(
    "vscode_filters",
    pathlib.Path(__file__).resolve().parents[2] / "filter_plugins" / "vscode.py",
)
vscode_filters = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vscode_filters)

jsonc_loads = vscode_filters.jsonc_loads
vscode_setting = vscode_filters.vscode_setting

KEY = "task.allowAutomaticTasks"


class TestJsoncLoads:
    def test_plain_json(self):
        assert jsonc_loads('{"a": 1, "b": [1, 2]}') == {"a": 1, "b": [1, 2]}

    def test_line_and_block_comments(self):
        text = """{
          // a line comment
          "a": 1, /* a block
          comment */ "b": 2 // trailing
        }"""
        assert jsonc_loads(text) == {"a": 1, "b": 2}

    def test_trailing_commas_in_objects_and_arrays(self):
        assert jsonc_loads('{"a": [1, 2,], "b": {"c": 3,},}') == {"a": [1, 2], "b": {"c": 3}}

    def test_trailing_comma_before_a_comment(self):
        assert jsonc_loads('{"a": 1, // last\n}') == {"a": 1}
        assert jsonc_loads('{"a": 1, /* last */ }') == {"a": 1}

    def test_comment_markers_inside_strings_are_text(self):
        text = '{"url": "https://example.com/a//b", "glob": "/*.js", "odd": ",}"}'
        assert jsonc_loads(text) == {"url": "https://example.com/a//b", "glob": "/*.js", "odd": ",}"}

    def test_quotes_inside_comments_do_not_start_strings(self):
        text = '{\n  // set "x" to "y", or leave it\n  "a": 1 /* "b": 2 */\n}'
        assert jsonc_loads(text) == {"a": 1}

    def test_escaped_quotes_stay_inside_their_string(self):
        # One escaped quote before the "//", so a scanner that took it for the
        # end of the string would read a comment from there.
        assert jsonc_loads(r'{"a": "say \"hi // not a comment\""}') == {"a": 'say "hi // not a comment"'}

    def test_an_escaped_quote_does_not_end_the_string(self):
        assert jsonc_loads(r'{"a": "x\"", "url": "http://x//y"}') == {"a": 'x"', "url": "http://x//y"}

    def test_an_escaped_backslash_before_the_closing_quote(self):
        assert jsonc_loads(r'{"a": "x\\", "url": "http://x//y"}') == {"a": "x\\", "url": "http://x//y"}

    def test_a_comma_before_a_string_is_kept(self):
        assert jsonc_loads('["a", "b"]') == ["a", "b"]
        text = '{"cSpell.words": ["ansible", "proxmox"], "task.allowAutomaticTasks": "off"}'
        assert vscode_setting(text, KEY) == "off"

    def test_escapes_and_string_arrays_in_one_file(self):
        text = r'{"a": "x\"", "b": "y\\", "c": ["p", "q"], "url": "http://x//y",}'
        assert jsonc_loads(text) == {"a": 'x"', "b": "y\\", "c": ["p", "q"], "url": "http://x//y"}

    def test_a_byte_order_mark_is_ignored(self):
        assert jsonc_loads('\ufeff{"a": 1}') == {"a": 1}

    def test_broken_text_raises(self):
        with pytest.raises(ValueError):
            jsonc_loads('{"a": 1')
        with pytest.raises(ValueError):
            jsonc_loads('{"a": "unterminated}')


class TestVscodeSetting:
    @pytest.mark.parametrize("value", ["on", "off"])
    def test_reads_the_flat_key(self, value):
        assert vscode_setting(f'{{"{KEY}": "{value}"}}', KEY) == value

    def test_absent_key_gives_the_default(self):
        assert vscode_setting('{"editor.fontSize": 14}', KEY) is None
        assert vscode_setting('{"editor.fontSize": 14}', KEY, "") == ""

    def test_a_commented_out_key_does_not_count(self):
        text = f'{{\n  // "{KEY}": "on",\n  "editor.fontSize": 14,\n}}'
        assert vscode_setting(text, KEY, "") == ""

    def test_a_block_commented_key_does_not_count(self):
        assert vscode_setting(f'{{ /* "{KEY}": "off" */ }}', KEY, "") == ""

    def test_odd_spacing_and_a_trailing_comma(self):
        text = f'{{\n\t"{KEY}"\n  :\t"on"  ,\n}}'
        assert vscode_setting(text, KEY) == "on"

    def test_the_last_of_duplicate_keys_wins(self):
        assert vscode_setting(f'{{"{KEY}": "on", "{KEY}": "off"}}', KEY) == "off"

    def test_the_nested_spelling(self):
        assert vscode_setting('{"task": {"allowAutomaticTasks": "on"}}', KEY) == "on"
        assert vscode_setting('{"task": {"quickOpen.skip": true}}', KEY, "") == ""
        assert vscode_setting('{"task": "on"}', KEY, "") == ""

    def test_the_later_spelling_wins_as_vs_code_reads_it(self):
        # Keys apply in file order: a nested object replaces what the flat
        # key built, and a flat key reaches into a nested object.
        assert vscode_setting(f'{{"task": {{"allowAutomaticTasks": "on"}}, "{KEY}": "off"}}', KEY) == "off"
        assert vscode_setting(f'{{"{KEY}": "on", "task": {{"allowAutomaticTasks": "off"}}}}', KEY) == "off"
        assert vscode_setting(f'{{"task": {{"quickOpen.skip": true}}, "{KEY}": "on"}}', KEY) == "on"

    def test_a_key_through_a_value_that_is_not_an_object_is_dropped(self):
        # VS Code logs a conflict and drops the key.
        assert vscode_setting(f'{{"task": null, "{KEY}": "off"}}', KEY, "") == ""
        assert vscode_setting(f'{{"task": "x", "{KEY}": "off"}}', KEY, "") == ""

    @pytest.mark.parametrize("text", ["", None, "   ", "not json", '{"a": ', "[1, 2]", '"on"'])
    def test_empty_unreadable_or_not_an_object_gives_the_default(self, text):
        assert vscode_setting(text, KEY, "") == ""

    @pytest.mark.parametrize("text", ["not json", '{"a": ', "[1, 2]", '"on"', '{"a": 1\n "b": 2}'])
    def test_text_that_does_not_parse_is_told_apart_when_asked(self, text):
        assert vscode_setting(text, KEY, "", unparsable="broken") == "broken"

    @pytest.mark.parametrize("text", ["", None, "   ", "{}", f'{{"{KEY}": "on"}}'])
    def test_empty_text_and_an_absent_key_are_not_unparsable(self, text):
        assert vscode_setting(text, KEY, "", unparsable="broken") != "broken"

    def test_a_real_looking_settings_file(self):
        text = """// Place your settings in this file to overwrite the default settings
{
    "workbench.colorTheme": "Default Dark Modern",
    "files.exclude": {
        "**/.git": true, // keep "these" out
    },
    /* Ask once, then remember: */
    "task.allowAutomaticTasks": "on",
    "remote.SSH.defaultExtensions": ["anthropic.claude-code",],
}
"""
        assert vscode_setting(text, KEY) == "on"
        assert vscode_setting(text, "remote.SSH.defaultExtensions") == ["anthropic.claude-code"]
