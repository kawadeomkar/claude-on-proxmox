"""The wiki in docs/ must stay true to the code, whole as a site, and the same on GitHub.

The reference pages are generated from the files they describe (tools/docs.py,
`make docs`), so a role variable, a setting or a make target cannot change
without its page saying so - as long as somebody runs the generator, which is
what the first test is for, and as long as every setting is in the files the
generator reads, which the completeness tests hold.

The rest hold the reading. The site build fails on a page or anchor that does
not exist. GitHub renders the pages as GitHub Flavored Markdown and the site
with Python-Markdown, which differ: a list straight after a paragraph, a nested
list indented two spaces, a `</dev/tty` in a code span (which once dropped half
a page from the site) or a ```mermaid fence. So every page is rendered as GitHub
does and compared, block by block, with the built site. The checks around it
cover what neither build sees: files outside docs/ that link into it or cite it,
and links from docs/ that work on GitHub and break on the site.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest
import tomllib
import yaml
from markdown_it import MarkdownIt

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
ROOT_PAGES = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "CLAUDE.md"]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
CODE_SPAN = re.compile(r"(`+)(.+?)\1", re.S)
# A section cited by page and number: "docs/architecture/roles.md §5.5", the path in backticks or a
# link, or the § on the next comment line.
CITATION = re.compile(r"docs/architecture/([\w-]+\.md)`?\)?(?:\s|#)*§\s?(\d+(?:\.\d+)*)")


def _files(*paths: str) -> list[Path]:
    """Tracked files, and new ones not ignored: a page written but not yet added still counts,
    and a working document git ignores (one at the top of docs/) never does."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *paths],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted({REPO / line for line in out.splitlines() if (REPO / line).is_file()})


def _texts(*paths: str) -> list[tuple[Path, str]]:
    out = []
    for path in _files(*paths):
        try:
            out.append((path, path.read_text()))
        except UnicodeDecodeError:
            continue
    return out


def _pages() -> list[Path]:
    return [p for p in _files("docs") if p.suffix == ".md"]


def _built(site: Path, page: Path) -> Path:
    rel = page.relative_to(DOCS)
    return site / (rel.parent if page.name == "README.md" else rel.with_suffix("")) / "index.html"


def _slug(heading: str) -> str:
    """The anchor GitHub gives a heading, which zensical.toml has the site give it too."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def _headings(page: Path) -> list[str]:
    out, fence = [], False
    for line in page.read_text().splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"^#{1,6} (.+?)\s*#*$", line)
        if m:
            out.append(m.group(1))
    return out


def _anchors(page: Path) -> set[str]:
    anchors, seen = set(), {}
    for heading in _headings(page):
        slug = _slug(heading)
        n = seen.get(slug, 0)
        anchors.add(slug if n == 0 else f"{slug}-{n}")
        seen[slug] = n + 1
    return anchors


def _prose(text: str) -> str:
    """The text with code blocks removed: their brackets and section signs are not links or citations."""
    return re.sub(r"```.*?```", "", text, flags=re.S)


def _links(page: Path) -> list[str]:
    return [t for t in LINK.findall(_prose(page.read_text())) if not re.match(r"^[a-z][a-z0-9+.-]*:", t)]


def _generator():
    spec = importlib.util.spec_from_file_location("docs_generator", REPO / "tools" / "docs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> tuple[Path, subprocess.CompletedProcess]:
    """The site built strictly from a copy: the config and the pages git knows of. The site must land
    under the config's directory, and a developer's own working documents are no part of the wiki."""
    zensical = Path(sys.executable).parent / "zensical"
    if not zensical.exists():
        pytest.fail(f"{zensical} is missing: run make init")
    root = tmp_path_factory.mktemp("wiki")
    shutil.copy(REPO / "zensical.toml", root)
    for page in _files("docs"):
        target = root / page.relative_to(REPO)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(page, target)
    result = subprocess.run(
        [str(zensical), "build", "--strict", "--clean", "-f", str(root / "zensical.toml")],
        cwd=root,  # its .cache/ lands here, not in the repository
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    return root / "site", result


# ---------------------------------------------------------------- generated ----


def test_generated_sections_are_up_to_date():
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "docs.py"), "--check"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_every_setting_that_reaches_a_role_is_in_an_example_file():
    """The settings page is generated from local.yml.example and vault.yml.example, so a setting missing
    from both is missing from the page, which calls everything it leaves out internal."""
    gen = _generator()
    _, feeds = gen.wiring()
    role_specs = gen.specs()
    documented = set()
    for name, secret in (("local.yml.example", False), ("vault.yml.example", True)):
        for _, groups in gen.parse_example(gen.GROUP_VARS / name, secret):
            documented |= {setting for _, found in groups for setting, _ in found}
    # Set from a vault_* secret, which the page lists in its place.
    from_vault = {var for source, targets in feeds.items() if source.startswith("vault_") for var in targets}
    not_settings = {
        "inventory_hostname": "Ansible's own: the VM's name",
        "proxmox_api_host": "taken from inventory/hosts.yml",
        "proxmox_api_ca_path": "REQUESTS_CA_BUNDLE, while proxmox_validate_certs is on",
    }
    missing = [
        source
        for source in sorted(feeds)
        if gen.set_by(source, feeds, role_specs)
        and source not in documented | from_vault | set(not_settings)
        and not gen.role_of(source, role_specs)  # a role variable is on its role's page
    ]
    assert missing == [], "add these to inventory/group_vars/all/local.yml.example (or the vault example)"


def test_every_role_default_is_in_its_argument_specs():
    """The role pages list what argument_specs.yml lists; a default missing there is missing from the page."""
    gen = _generator()
    role_specs = gen.specs()
    missing = []
    for role in gen.roles():
        defaults = yaml.safe_load((REPO / "roles" / role / "defaults" / "main.yml").read_text()) or {}
        missing += [f"{role}: {var}" for var in sorted(set(defaults) - set(role_specs[role]))]
    assert missing == []


# ---------------------------------------------------------------- the site ----


def test_the_site_builds_strictly(site):
    """Zensical fails a strict build on a link to a page or an anchor that does not exist."""
    built, result = site
    output = ANSI.sub("", result.stdout + result.stderr)
    assert result.returncode == 0, output
    assert (built / "index.html").exists(), output


def test_every_heading_reaches_its_built_page(site):
    built, result = site
    assert result.returncode == 0
    lost = []
    for page in _pages():
        ids = set(re.findall(r'\bid="([^"]+)"', _built(built, page).read_text()))
        lost += [f"{page.relative_to(DOCS)}#{a}" for a in sorted(_anchors(page) - ids)]
    assert lost == [], "headings missing from the built pages: a page lost its content there"


class _Blocks(HTMLParser):
    """(tag, text) per block element, leaving out the site's own chrome: permalinks, the edit
    button, code line anchors."""

    BLOCK = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "th", "td", "blockquote"}
    CHROME = re.compile(r"headerlink|md-content__button|__codelineno")
    KNOWN = set(
        "a abbr article blockquote br code del details div em h1 h2 h3 h4 h5 h6 hr img input kbd li ol p "
        "path pre span strong sub summary sup svg table tbody td th thead tr ul".split()
    )

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self.unknown: set[str] = set()
        self._stack: list[list[str]] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self._skip or self.CHROME.search((a.get("class") or "") + (a.get("id") or "")):
            self._skip += 1
            return
        if tag not in self.KNOWN:
            self.unknown.add(tag)
        if tag in self.BLOCK:
            self._stack.append([tag, ""])

    def handle_endtag(self, tag):
        if self._skip:
            self._skip -= 1
            return
        if tag in self.BLOCK and self._stack and self._stack[-1][0] == tag:
            name, text = self._stack.pop()
            self.blocks.append((name, re.sub(r"\s+", " ", text).strip()))

    def handle_data(self, data):
        if not self._skip and self._stack:
            self._stack[-1][1] += data


def _blocks(markup: str) -> _Blocks:
    parser = _Blocks()
    parser.feed(markup)
    return parser


def test_every_page_renders_the_same_on_github_and_on_the_site(site):
    """Block by block, the page as GitHub renders it (CommonMark and tables, here by markdown-it-py)
    against the built site's article. A list after a paragraph, a two-space nested list, a `</tag`
    in a code span each make the two differ; text read as an HTML tag vanishes from both."""
    built, result = site
    assert result.returncode == 0
    md = MarkdownIt("commonmark").enable("table").enable("strikethrough")
    differ = []
    for page in _pages():
        rel = page.relative_to(DOCS)
        html = _built(built, page).read_text()
        article = html[html.index("<article") : html.index("</article>")]
        github, ours = _blocks(md.render(page.read_text())), _blocks(article)
        if github.blocks != ours.blocks:
            pairs = zip(github.blocks + [None], ours.blocks + [None], strict=False)
            i = next(i for i, (a, b) in enumerate(pairs) if a != b)
            differ.append(f"{rel}: GitHub {github.blocks[i : i + 1]} / site {ours.blocks[i : i + 1]}")
        if github.unknown:
            differ.append(f"{rel}: text read as an HTML tag, hidden on both: {sorted(github.unknown)}")
    assert differ == []


def test_every_mermaid_fence_becomes_a_diagram(site):
    built, result = site
    assert result.returncode == 0
    plain = []
    for page in _pages():
        fences = len(re.findall(r"^```mermaid\s*$", page.read_text(), re.M))
        diagrams = _built(built, page).read_text().count('class="mermaid"')
        if fences != diagrams:
            plain.append(f"{page.relative_to(DOCS)}: {fences} mermaid fences, {diagrams} diagrams")
    assert plain == []


def test_no_code_span_holds_a_closing_tag():
    """`</dev/tty` in a code span is read as a closing tag on the site, which drops what follows it
    without a warning. Write it `< /dev/tty`."""
    found = []
    for page in _pages():  # the site's pages; GitHub alone renders the root files
        for m in CODE_SPAN.finditer(_prose(page.read_text())):
            if re.search(r"</[A-Za-z]", m.group(2)):
                found.append(f"{page.relative_to(REPO)}: {m.group(0)}")
    assert found == []


def test_no_page_shows_a_character_reference_as_text():
    """A reference escaped twice, as `&amp;emsp;`, shows as `&emsp;` on GitHub and on the site alike, so
    the comparison above passes it: the role pages once put one before every task inside a block. Code
    is left out, where a page writes one to show it."""
    md = MarkdownIt("commonmark").enable("table").enable("strikethrough")
    shown = []
    for page in _pages():
        prose = re.sub(r"<code>.*?</code>", "", md.render(page.read_text()), flags=re.S)
        for _, text in _blocks(prose).blocks:
            shown += [f"{page.relative_to(DOCS)}: {ref}" for ref in re.findall(r"&#?\w+;", text)]
    assert shown == []


def test_no_page_repeats_a_heading():
    """GitHub suffixes a repeated heading's anchor -1, the site _1: a link to it works in one only."""
    repeated = []
    for page in _pages():
        slugs = [_slug(h) for h in _headings(page)]
        repeated += [f"{page.relative_to(DOCS)}: {s}" for s in sorted({s for s in slugs if slugs.count(s) > 1})]
    assert repeated == []


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


# ---------------------------------------------------------------- links and citations ----


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
    fixtures = {REPO / "tests" / "unit" / "test_tracked_files.py"}  # paths the guard must refuse
    missing = []
    for path, text in _texts("."):
        if path in fixtures:
            continue
        for match in sorted(set(named.findall(text))):
            if not (REPO / match).exists():
                missing.append(f"{path.relative_to(REPO)}: {match}")
    assert missing == []


def _sections() -> dict[str, Path]:
    """Every numbered architecture section, by number, and the page that holds it."""
    out = {}
    for page in sorted((DOCS / "architecture").glob("*.md")):
        for heading in _headings(page):
            m = re.match(r"(\d+(?:\.\d+)*)\.?\s", heading)
            if m:
                out[m.group(1)] = page
    return out


def test_every_architecture_section_cited_exists():
    """Code cites the design by page and section: docs/architecture/roles.md §5.5."""
    sections = _sections()
    missing = []
    for path, text in _texts("."):
        for page, section in CITATION.findall(text):
            if sections.get(section) != DOCS / "architecture" / page:
                missing.append(f"{path.relative_to(REPO)}: {page} §{section}")
    assert missing == []


def test_every_cross_page_section_reference_is_a_link():
    """In one file a § was a search away; across pages it is a link, or the reader has to find it."""
    sections = _sections()
    unlinked = []
    for page in _pages():
        for line in _prose(page.read_text()).splitlines():
            if line.startswith("#"):
                continue
            plain = CODE_SPAN.sub("", re.sub(r"\[[^\]]*\]\([^)]*\)", "", line))  # links and code out
            for section in re.findall(r"§\s?(\d+(?:\.\d+)*)", plain):
                if sections.get(section) not in (None, page):
                    unlinked.append(f"{page.relative_to(DOCS)}: §{section}")
    assert unlinked == []


def test_the_quick_start_matches_the_wiki():
    """The README repeats what a first deploy needs so it needs no second page: the token commands
    from Proxmox access, and the set-up commands and the files table from Your first deploy."""

    def fragment(path: Path, marker: str) -> str:
        text = path.read_text()
        blocks = re.findall(r"```bash\n.*?```", text, flags=re.S)
        blocks += re.findall(r"^\| File \| Contents \|\n(?:\|.*\|\n)+", text, flags=re.M)
        found = [b for b in blocks if marker in b]
        assert len(found) == 1, f"{path.relative_to(REPO)}: expected one block holding {marker!r}"
        return found[0]

    readme = REPO / "README.md"
    started = DOCS / "getting-started"
    assert fragment(readme, "pveum role add") == fragment(started / "proxmox-access.md", "pveum role add")
    for marker in ("make init", "make vault", "| File | Contents |"):
        assert fragment(readme, marker) == fragment(started / "first-deploy.md", marker), marker
