"""vt.py, the small terminal every live output's tests read the screen back through, against what it must not miss.

A pseudo-terminal's reads can end anywhere - inside a character or an escape sequence: on Linux CI one split a "→"
in two, and the screen read "��" where the log had the arrow - so a stream fed in pieces must read exactly as fed
whole: the screen, the scrollback, the titles, the bells, nothing unknown and nothing left waiting.
"""

from __future__ import annotations

import pytest
from vt import VT

# just status's: a frame, colours, characters of three and four bytes, erase and cursor up.
STATUS = (
    b"\x1b[?2026h\x1b[1;36mVMS\x1b[0m \xe2\x97\x8f alpha\r\n"
    b"\x1b[36m               \xe2\x86\x92 just deploy alpha\x1b[0m\x1b[K\r\n\x1b[2A\r\x1b[J"
    b"\xe2\xa0\x8b reading \xf0\x9f\x99\x82\x1b[?2026l"
)
# just deploy's: a title by BEL and by ST, a frame, pause's ESC [ before its carriage return and its erase, a bell.
DEPLOY = (
    b"\x1b]0;deploy alpha\x07\x1b[?2026h\rone\x1b[K\ntwo\x1b[K\x1b[J\x1b[?2026l\x1b[1A\r\x1b[J"
    b"alpha: paste the code:\r\nabc123^M\x1b[\r\x1b[\x1b[K\x1b]0;t\x1b\\"
    b"\x1b[?2026h\rr\xe2\x9c\x93gion\x1b[K\x1b[J\x1b[?2026l\x07"
)


def _read(*parts):
    screen = VT(60, 10)
    for part in parts:
        screen.feed(part)
    return screen


def _seen(screen):
    return screen.history, screen.lines, screen.titles, screen.bells, screen.unknown, screen.pending


def test_each_stream_reads_whole_as_its_live_output_drew_it():
    status, deploy = _read(STATUS), _read(DEPLOY)
    assert status.transcript() == ["⠋ reading 🙂"] and status.unknown == set() and status.pending == b""
    assert deploy.transcript() == ["alpha: paste the code:", "r✓gion"]
    assert deploy.titles == ["deploy alpha", "t"] and deploy.bells == 1 and deploy.unknown == set()


@pytest.mark.parametrize("stream", [STATUS, DEPLOY], ids=["status", "deploy"])
def test_a_stream_cut_anywhere_reads_as_it_reads_whole(stream):
    whole = _seen(_read(stream))
    assert _seen(_read(*(bytes([byte]) for byte in stream))) == whole
    for first in range(len(stream) + 1):
        for second in range(first, len(stream) + 1):
            assert _seen(_read(stream[:first], stream[first:second], stream[second:])) == whole, (first, second)


def test_an_osc_ended_by_st_swallows_nothing_after_it():
    screen = _read(b"\x1b]0;title\x1b\\after")
    assert screen.titles == ["title"] and screen.transcript() == ["after"] and screen.pending == b""
