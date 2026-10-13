"""The wiki in docs/ must stay true to the code and whole as a site.

The reference pages are generated from the files they describe (tools/docs.py,
`make docs`), so a role variable, a setting or a make target cannot change
without its page saying so - as long as somebody runs the generator, which is
what the first test is for. The rest hold the links: the site build fails on a
page or anchor that does not exist, and the checks around it cover what the
build cannot see - the files outside docs/ that link into it or cite it, and
links from docs/ that would work on GitHub and break on the site.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
ROOT_PAGES = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "CLAUDE.md"]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _files(*paths: str) -> list[Path]:
    """Tracked files, and new ones not ignored: a page written but not yet added still counts,
    and a working document git ignores (docs/plan-*.md) never does."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *paths],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted({REPO / line for line in out.splitlines() if (REPO / line).is_file()})


def _pages() -> list[Path]:
    return [p for p in _files("docs") if p.suffix == ".md"]


def _slug(heading: str) -> str:
    """The anchor GitHub gives a heading, which zensical.toml has the site give it too."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def _anchors(page: Path) -> set[str]:
    anchors, seen, fence = set(), {}, False
    for line in page.read_text().splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"^#{1,6} (.+?)\s*#*$", line)
        if m:
            slug = _slug(m.group(1))
            n = seen.get(slug, 0)
            anchors.add(slug if n == 0 else f"{slug}-{n}")
            seen[slug] = n + 1
    return anchors


def _links(page: Path) -> list[str]:
    text = re.sub(r"```.*?```", "", page.read_text(), flags=re.S)  # a code block's brackets are not links
    return [t for t in LINK.findall(text) if not re.match(r"^[a-z][a-z0-9+.-]*:", t)]


def test_generated_sections_are_up_to_date():
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "docs.py"), "--check"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_the_site_builds_strictly(tmp_path):
    """Zensical fails a strict build on a link to a page or an anchor that does not exist.

    It builds a copy - the config and the pages git knows of - since the site must land under
    the config's directory and a developer's own working documents are no part of the wiki.
    And every heading must reach its page: Python-Markdown reads `</dev/tty` in a code span as a
    closing tag and drops the rest of the page without a warning, where GitHub shows it all.
    """
    zensical = Path(sys.executable).parent / "zensical"
    if not zensical.exists():
        pytest.fail(f"{zensical} is missing: run make venv")
    shutil.copy(REPO / "zensical.toml", tmp_path)
    for page in _files("docs"):
        target = tmp_path / page.relative_to(REPO)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(page, target)
    result = subprocess.run(
        [str(zensical), "build", "--strict", "--clean", "-f", str(tmp_path / "zensical.toml")],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    output = ANSI.sub("", result.stdout + result.stderr)
    assert result.returncode == 0, output
    assert (tmp_path / "site" / "index.html").exists(), output
    lost = []
    for page in _pages():
        rel = page.relative_to(DOCS)
        built = rel.parent if page.name == "README.md" else rel.with_suffix("")
        html = (tmp_path / "site" / built / "index.html").read_text()
        ids = set(re.findall(r'\bid="([^"]+)"', html))
        lost += [f"{rel}#{anchor}" for anchor in sorted(_anchors(page) - ids)]
    assert lost == [], "headings missing from the built pages: a page lost its content there"


def test_every_page_is_in_the_nav_and_every_nav_entry_is_a_page():
    def entries(nav):
        for item in nav:
            if isinstance(item, str):
                yield item
            else:
                for value in item.values():
                    yield from entries(value) if isinstance(value, list) else [value]

    nav = list(entries(tomllib.loads((REPO / "zensical.toml").read_text())["project"]["nav"]))
    pages = {str(p.relative_to(DOCS)) for p in _pages()}
    assert sorted(set(nav) - pages) == [], "the nav names pages that do not exist"
    assert sorted(pages - set(nav)) == [], "pages missing from the nav in zensical.toml"
    assert len(nav) == len(set(nav)), "a page is in the nav twice"


def test_no_relative_link_leaves_docs():
    """A link out of docs/ works on GitHub and is a dead link on the site."""
    leaving = []
    for page in _pages():
        for target in _links(page):
            path = target.split("#")[0]
            if path and not (page.parent / path).resolve().is_relative_to(DOCS):
                leaving.append(f"{page.relative_to(REPO)}: {target}")
    assert leaving == [], "name the file in backticks, or link to it on GitHub"


@pytest.mark.parametrize("name", ROOT_PAGES)
def test_links_from_the_root_files_resolve(name):
    page = REPO / name
    broken = []
    for target in _links(page):
        path, _, anchor = target.partition("#")
        dest = (page.parent / path).resolve() if path else page
        if not dest.exists():
            broken.append(f"{target}: no such file")
        elif anchor and dest.suffix == ".md" and anchor not in _anchors(dest):
            broken.append(f"{target}: no such heading")
    assert broken == []


def test_every_docs_page_the_repository_names_exists():
    """Comments and messages point readers at the wiki by path (docs/guides/vscode.md)."""
    named = re.compile(r"\bdocs/[\w./-]+\.md\b")
    missing = []
    for path in _files("."):
        if path.suffix not in {".yml", ".py", ".md", ".j2", ".sh", ".cfg", ".toml", ".example", ""}:
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for match in sorted(set(named.findall(text))):
            if not (REPO / match).exists():
                missing.append(f"{path.relative_to(REPO)}: {match}")
    assert missing == []


def test_every_architecture_section_cited_exists():
    """Code cites the design by page and section number: docs/architecture/roles.md §5.5."""
    cite = re.compile(r"docs/architecture/([\w-]+\.md)\)?\s+§\s?(\d+(?:\.\d+)*)")
    missing = []
    for path in _files("."):
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for page, section in cite.findall(text):
            target = DOCS / "architecture" / page
            number = re.escape(section)
            heading = re.compile(rf"^#{{1,6}} {number}(\.|\s)", re.M)
            if not target.exists() or not heading.search(target.read_text()):
                missing.append(f"{path.relative_to(REPO)}: {page} §{section}")
    assert missing == []


def test_the_quick_start_token_commands_match_the_wiki():
    """The README repeats the pveum commands so a first deploy needs no second page; they must not drift."""

    def block(path: Path) -> str:
        blocks = re.findall(r"```bash\n(.*?)```", path.read_text(), flags=re.S)
        found = [b for b in blocks if "pveum role add" in b]
        assert len(found) == 1, f"{path.relative_to(REPO)}: expected one block of pveum commands"
        return found[0]

    assert block(REPO / "README.md") == block(DOCS / "getting-started" / "proxmox-access.md")
