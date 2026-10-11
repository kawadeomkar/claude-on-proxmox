"""Unit tests for filter_plugins/desktop.py: the argv that opens a sign-in page on this machine."""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

SPEC = importlib.util.spec_from_file_location(
    "desktop_filters",
    pathlib.Path(__file__).resolve().parents[2] / "filter_plugins" / "desktop.py",
)
desktop_filters = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(desktop_filters)

page_opener = desktop_filters.page_opener

URL = "https://github.com/login/device"


@pytest.mark.parametrize(
    ("browser", "argv"),
    [
        ("firefox", ["firefox", URL]),
        # The documented form: a list of commands. Only the first can be an argv.
        ("firefox:chromium", ["firefox", URL]),
        ("firefox --new-tab %s:chromium", ["firefox", "--new-tab", URL]),
        # %s is where the URL goes, inside an argument too, and then not appended.
        ("firefox --new-tab %s", ["firefox", "--new-tab", URL]),
        ("browser --url=%s --fresh", ["browser", "--url=" + URL, "--fresh"]),
        # Split as a shell would: no empty argument for a double space, and a
        # quoted path with a space stays one argument.
        ("firefox  --new-tab", ["firefox", "--new-tab", URL]),
        ("'/Applications/My Browser.app/run' %s", ["/Applications/My Browser.app/run", URL]),
        # An empty first entry is no command: the next one is the first.
        (":chromium", ["chromium", URL]),
        ("  chromium  ", ["chromium", URL]),
    ],
)
def test_browser_names_the_command(browser, argv):
    assert page_opener(URL, browser, ["/usr/bin/open", "/usr/bin/xdg-open"]) == argv


@pytest.mark.parametrize(
    ("found", "argv"),
    [
        (["/usr/bin/open"], ["open", URL]),
        (["/usr/bin/xdg-open"], ["xdg-open", URL]),
        # macOS's opener first when both are there.
        (["/usr/bin/xdg-open", "/usr/bin/open"], ["open", URL]),
        # Names as well as paths, and which's line endings.
        (["xdg-open\n"], ["xdg-open", URL]),
        # pbcopy and the like are no openers.
        (["/usr/bin/pbcopy", "/usr/bin/xclip"], []),
        ([], []),
        (None, []),
    ],
)
def test_without_browser_an_opener_that_was_found_is_used(found, argv):
    assert page_opener(URL, "", found) == argv


@pytest.mark.parametrize("browser", [None, "", " ", ":", "::"])
def test_a_browser_that_names_no_command_falls_back(browser):
    assert page_opener(URL, browser, ["/usr/bin/open"]) == ["open", URL]


def test_a_browser_that_cannot_be_split_falls_back():
    # Unbalanced quotes read no command; the openers are tried instead.
    assert page_opener(URL, "'firefox", ["/usr/bin/xdg-open"]) == ["xdg-open", URL]
    assert page_opener(URL, "'firefox", []) == []


def test_the_url_is_one_argument_whatever_it_holds():
    # Never handed to a shell: a URL with spaces or quotes is still one argument.
    url = "https://example.com/a b?c='d'"
    assert page_opener(url, "firefox", []) == ["firefox", url]
    assert page_opener(url, "", ["/usr/bin/open"]) == ["open", url]


def test_the_filter_is_registered():
    assert desktop_filters.FilterModule().filters()["page_opener"] is page_opener
