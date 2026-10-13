# This wiki

The documentation lives in `docs/`, as Markdown, beside the code it describes, and changes in the
same pull request as that code. It reads the same on GitHub, where every page is a file, and as a
site that Zensical builds from the same files, with navigation and search.

## How it is organised

| Directory | What goes there |
|---|---|
| `getting-started/` | What a new user needs, in the order they need it |
| `guides/` | One page per feature: what it does, how to use it, what to do when it goes wrong |
| `reference/` | Lists to look things up in, mostly generated |
| `architecture/` | How it is built and why, numbered by section (§5.5 is `architecture/roles.md`, section 5.5) |
| `development/` | Working on the project |

The root `README.md` stays a short getting-started page that links here. `CONTRIBUTING.md` and
`SECURITY.md` stay at the root, where GitHub looks for them. Working documents - plans, reviews and
smoke runbooks - may sit in `docs/` on your machine as `plan-*`, `review-*` and `live-smoke-*`: git
ignores them and the site leaves them out.

## Generated sections

Three kinds of page are generated from the files they describe, so they cannot drift from them.
Each generated section sits between two marker lines; everything outside the markers is written by
hand and kept on every run, which is where context goes.

| Page | Generated from | Markers |
|---|---|---|
| `reference/make-targets.md` | the `## ` comment on each target in the `Makefile`, grouped by its `# --- name ---` banners | `BEGIN_GENERATED: make-targets` |
| `reference/settings.md` | `local.yml.example` and `vault.yml.example`: each comment block and the settings under it, grouped by banner, with the default from `defaults.yml` or the role's `defaults/main.yml`, and the role variable `defaults.yml` wires each setting into | `BEGIN_GENERATED: settings` |
| `reference/roles/<role>.md` | `roles/<role>/meta/argument_specs.yml`, rendered by [aar-doc](https://github.com/telekom-mms/Automated-Ansible-Role-Documentation) with `tools/templates/role.md.j2` | `BEGIN_ANSIBLE_DOCS` |

`tools/docs.py` does all three; `make docs` runs it. To change what a generated section says, change
its source - a role variable's `description:` in `argument_specs.yml`, the comment above a setting in
`local.yml.example`, a target's `## ` text - and run `make docs`. A new role gets its page, with the
markers and a title, the first time `make docs` runs; add it to the `nav` in `zensical.toml`.

`argument_specs.yml` is the role's interface as Ansible itself reads it: Ansible validates every
role's arguments against it when the role runs, so the reference cannot list a variable the role
does not take.

## Building the site

```bash
make docs          # regenerate the generated sections
make docs-serve    # the site at http://127.0.0.1:8000, rebuilt as you save
make docs-build    # the site in site/ (git-ignored), failing on any warning
```

The site is configured in `zensical.toml`: the navigation, the theme, and heading anchors made the way
GitHub makes them, so that `page.md#a-heading` works in both places. Zensical comes from the team
behind Material for MkDocs, which reaches its end of life on 2026-11-05, and reads the same Markdown.
Nothing here depends on a site-generator plugin - the generation is `tools/docs.py`, run before the
build - so the pages would build with another generator unchanged.

## What the tests hold

`tests/unit/test_docs.py`, part of `make unit` and so of CI, fails when:

- a generated section is out of date with its source (run `make docs` and commit the result);
- the site does not build strictly - a link to a page or an anchor that does not exist;
- a page in `docs/` is not in the `nav`, or the `nav` names a page that does not exist;
- a link in `docs/` leaves `docs/` by a relative path, which works on GitHub and breaks on the site
  (name a source file in backticks instead, or link to it on GitHub);
- a relative link in `README.md`, `CONTRIBUTING.md`, `SECURITY.md` or `CLAUDE.md` points at a file or
  heading that does not exist;
- a file in the repository names a `docs/` page that does not exist, as the comments that point a
  reader to the wiki do;
- the Proxmox token commands in `README.md` differ from those in
  [Proxmox access](../getting-started/proxmox-access.md).

## Writing a page

- Write for the reader of that section: a guide says what to do and why, the architecture says how
  and why it is built that way.
- Link to other pages relatively (`../guides/vscode.md#session-notes`); name files of the repository
  in backticks.
- Never an address, a hostname or a token: examples use `alpha`, `192.0.2.0/24` and `you@example.com`.
- Keep the architecture's section numbers: code comments cite them (`docs/architecture/roles.md`
  §5.5).
