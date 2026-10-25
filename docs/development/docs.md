# This wiki

The documentation lives in `docs/`, as Markdown, beside the code it describes, and changes in the
same pull request as that code. It reads the same on GitHub, where every page is a file, and as a
site that Zensical builds from the same files, with navigation and search, published at
<https://kawadeomkar.github.io/claude-on-proxmox/> from `main` (see [Publishing](#publishing)).

## How it is organised

| Directory | What goes there |
|---|---|
| `getting-started/` | What a new user needs, in the order they need it |
| `guides/` | One page per feature: what it does, how to use it, what to do when it goes wrong |
| `reference/` | Lists to look things up in, generated from the files they describe |
| `architecture/` | How it is built and why, in numbered sections: [§5.5](../architecture/roles.md#55-claude_code--install-claude-code) is section 5.5, on `architecture/roles.md` |
| `development/` | Working on the project |

The root `README.md` stays a short getting-started page that links here. `CONTRIBUTING.md` and
`SECURITY.md` stay at the root, where GitHub looks for them.

The wiki is `docs/README.md` and those folders, and `docs/stylesheets/`. Anything else at the top of
`docs/` is a working document - a plan, a review, a smoke runbook that names your hosts - and stays on
your machine whatever it is called: `.gitignore` ignores it, `tests/check_no_local_files.sh` refuses it
even with `git add -f`, and `zensical.toml`'s `exclude_docs` leaves it out of the site. A new wiki folder
is added to all three.

## Generated sections

Three kinds of page are generated from the files they describe, so they cannot drift from them.
Each generated section sits between two marker lines; everything outside the markers is written by
hand and kept on every run, which is where context goes.

| Page | Generated from | Markers |
|---|---|---|
| `reference/commands.md` | the `justfile`'s recipes as `just --json` gives them, grouped by their `[group]`: each one's `[doc]`, and the `help` of each of its options and words | `BEGIN_GENERATED: commands` |
| `reference/settings.md` | `local.yml.example` and `vault.yml.example`: each comment block and the settings under it, grouped by banner; the default from `defaults.yml` or the role's `defaults/main.yml`; the role variable each setting ends up in, followed through `defaults.yml`'s wiring, with the first sentence of its `argument_specs` description | `BEGIN_GENERATED: settings` |
| `reference/roles/<role>.md` | `roles/<role>/`: the variables of each entry point from `meta/argument_specs.yml` (a default it leaves out comes from `defaults/main.yml`), the internal values in `vars/main.yml`, and every task in `tasks/` with a flowchart of each task file, rendered by [docsible](https://github.com/docsible/docsible) with `tools/templates/role.md.j2` | `BEGIN_GENERATED: role` |

`tools/docs.py` does all three; `just docs` runs it, and so does a pre-commit hook whenever a commit
touches one of the sources. To change what a generated section says, change its source - a role
variable's `description:` in `argument_specs.yml`, the comment above a setting in `local.yml.example`
or `vault.yml.example`, a default in `defaults.yml` or a role's `defaults/main.yml`, a task file, a recipe's
`[doc()]` or `[arg(help=...)]` - and run `just docs`. A new role gets its page, with the markers and a title, the first time
`just docs` runs; add it to the `nav` in `zensical.toml`. Text taken from a source is escaped outside
code spans, so a placeholder such as `<name>` reads as text rather than vanishing as an HTML tag.

The example files are parsed strictly: in `local.yml.example` a setting is a commented `# name: value`
line under a `# --- Section ---` banner, in `vault.yml.example` an uncommented one, and the generator
stops on a name listed twice, a setting with no default anywhere, or an example whose type differs from
its default's - each a comment line read as a setting.

`argument_specs.yml` is the role's interface as Ansible itself reads it: Ansible checks the type and the
choices of every variable it lists when the role runs. It does not check that the list is complete, so a
test holds every variable in a role's `defaults/main.yml` to an entry there, and every setting that ends
up in a role to an entry in one of the example files.

docsible renders the role pages because it reads the task files as well as `argument_specs.yml`: each
page lists every task and draws a flowchart of each task file, so a task added, renamed or removed changes
its page, and the drift test fails until `just docs` runs. The pages use this project's template rather
than docsible's own, which turns every `|` into `¦`, leaves the descriptions out on entry points other
than `main`, drops the tasks under `rescue` and `always`, and shows a multi-line value as a placeholder -
for that last reason `tools/docs.py` writes the internal-values table itself. The flowcharts are
docsible's, with their labels lowercased and their punctuation removed. Task comments stay off: in
docsible 0.8.0, the release pinned, they break its tables. docsible is pre-1.0 and that release is from
November 2025; should it stop being maintained, `ansible-doc -t role -j`, which ships with ansible-core,
gives the variables as JSON. The role pages come to about 2,250 lines, most of them tasks and internal
values; should they grow too large to read, the internal values are the part to leave out.

## Building the site

```bash
just docs              # regenerate the generated sections
just docs serve        # the site at http://127.0.0.1:8000, rebuilt as you save
just docs build        # the site in site/ (git-ignored), failing on any warning
```

The site is configured in `zensical.toml`: the navigation, the theme, Mermaid diagrams, and heading
anchors made the way GitHub makes them, so that `page.md#a-heading` works in both places. Zensical is the
successor to Material for MkDocs, from the team that made it; Material for MkDocs is maintained until
May 5, 2027. Zensical is pre-1.0 - its 0.1.0, on 2026-11-05, starts a release line its authors call
dependable - and 0.0.70 is the first with `exclude_docs`, which the configuration uses. The docs tools
are the `docs` group in `pyproject.toml`, and Dependabot proposes them as one pull request of their own.
Nothing here depends on a site-generator plugin - the generation is `tools/docs.py`, run before the
build - so the pages would build with another generator unchanged.

## Publishing

`.github/workflows/docs.yml` builds the site strictly and deploys it to GitHub Pages whenever a push
to `main` changes `docs/`, `zensical.toml`, `pyproject.toml` or `uv.lock`, and on demand from the Actions
tab. It installs only the `docs` group, at the versions `uv.lock` locks, so the published site is the
one the tests build. Pull requests need no job of their own: `just test unit` builds the site and compares it
with GitHub's rendering already. The repository's Pages source must be set to GitHub Actions (Settings →
Pages) for the deploy to run.

## What the tests hold

`tests/unit/test_docs.py`, part of `just test unit` and so of CI, fails when:

- a generated section is out of date with its source (run `just docs` and commit the result);
- a setting that ends up in a role is missing from both example files, or a role default from its
  `argument_specs.yml`;
- the site does not build strictly - a link to a page or an anchor that does not exist;
- a heading does not reach its built page, a page renders differently on the site from how GitHub
  renders it (compared block by block: a list straight after a paragraph, a nested list indented two
  spaces, text read as an HTML tag), or a ```` ```mermaid ```` fence does not become a diagram;
- a code span holds a closing tag such as `< /dev/tty` written without its space, which drops the rest
  of a page from the site;
- a page repeats a heading, whose anchor GitHub and the site would number differently;
- a page in `docs/` is not in the `nav`, or the `nav` names a page that does not exist;
- a link in `docs/` leaves `docs/` by a relative path, which works on GitHub and breaks on the site
  (name a source file in backticks instead, or link to it on GitHub);
- a relative link in `README.md`, `CONTRIBUTING.md`, `SECURITY.md` or `CLAUDE.md` points at a file or
  heading that does not exist;
- a file in the repository names a `docs/` page that does not exist, as the comments that point a
  reader to the wiki do, or cites an architecture section (`docs/architecture/roles.md §5.5`) that is
  not on that page;
- a page refers to an architecture section on another page by its § without linking it;
- the README's set-up commands, files table or Proxmox token commands differ from the copies in
  [Your first deploy](../getting-started/first-deploy.md) and [Proxmox access](../getting-started/proxmox-access.md).

## Writing a page

- Write for the reader of that section: a guide says what to do and why, the architecture says how
  and why it is built that way.
- Link to other pages relatively (`../guides/vscode.md#session-notes`); name files of the repository
  in backticks.
- Never an address, a hostname or a token: examples use `alpha`, `192.0.2.0/24` and `you@example.com`.
- Keep the architecture's section numbers: code comments cite them
  (`docs/architecture/roles.md §5.5`), and link a § that is on another page.
- Leave a blank line before a list, indent a nested list by four spaces, and never start a wrapped line
  with a dash and a space: the site's Markdown needs the first two, and GitHub reads the third as a
  list.
