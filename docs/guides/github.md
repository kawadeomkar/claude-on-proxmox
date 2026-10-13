# GitHub repositories and sign-in

## Choosing repositories

`github_projects` (in `local.yml`) is a list of repository names. Leave it empty and every repository
owned by `github_username` is cloned; list names to clone only those. Forks and archived repositories are
skipped unless `github_projects_include_forks` / `github_projects_include_archived` are true, and
`github_projects_exclude` always wins.

## Private repositories, and pushing from the VM

A private repository takes two credentials: a token to *list* it and one for `git` to *clone* it, and a
push or a `gh pr create` later needs the second again. `github_projects` covers all of them through `gh`
on the VM, one of two ways:

- **With `vault_github_token` set**, the token lists your repositories, private ones included, and signs
  `gh` in on the VM where gh is signed out or signed in by this project, so one `make deploy` ends with
  everything cloned and a VM that pushes. A login you made on the VM yourself stays, and the token only
  lists there; each run says so, and `github_auth_method: token` puts the token over it. As a classic
  token it needs `repo`, `workflow` and `read:org` (with `repo` alone it only lists, and the run says which
  scopes to add); as a fine-grained one, Metadata (read), Contents,
  Pull requests and Workflows (read and write) on all repositories - a token limited to selected
  repositories lists and clones only those. The same token serves every VM.
- **Without one**, `make deploy` signs the VM in itself, with no question first (`github_login_auto_start`;
  `make github-login VM_NAME=alpha` does the same for a VM that exists). It runs GitHub's device flow for
  you, as far as GitHub allows: gh
  starts on the VM in the background, the page github.com/login/device opens in your browser, the
  one-time code is on your clipboard and printed, and the run waits up to fifteen minutes while you paste
  the code and authorise GitHub CLI; then it makes gh git's credential helper, lists with the token gh just
  stored and clones the private repositories, all in that one run. Nothing is ever in the vault. The
  paste and the click are the floor: GitHub's device flow never pre-fills the code, and gh has no other
  browser flow for github.com.

```bash
make deploy VM_NAME=alpha        # ... the GitHub page opens, the code is on your clipboard: paste,
                                 # authorise, and the private repositories are cloned in the same run
make github-login VM_NAME=alpha  # the same, later, for a VM make configure left signed out
```

  Without a name, `make github-login` only reports each VM's state; given several, it signs them in one
  after another, since the browser takes one sign-in at a time. A code left unapproved expires after
  fifteen minutes and that VM's sign-in fails saying so, after the others have had their turn; run it
  again for a fresh code. `make deploy` starts it for every VM but one whose Claude sign-in nobody
  answered, and a run with no terminal skips it, as nobody is there to approve it. Once the code is shown
  the run waits until it is approved or expires, and Ctrl-C there ends the whole run; with
  `github_login_auto_start: false` it asks first, and `n` leaves a VM for later. `make configure` never
  runs it. On a controller with no browser or clipboard the run prints the page and the
  code and waits all the same, and `login_desktop: false` makes it never try. The terminal that `make
  code` opens runs the same flow after the Claude sign-in, with the page opening by itself and the code
  printed to copy (a Remote-SSH terminal cannot fill your clipboard); Ctrl-C there skips it and starts
  Claude. The private repositories then take one `make configure`, or `make github-login VM_NAME=alpha`,
  which on a VM already signed in runs the clone straight away.

`github_auth_method` picks between the two: `auto` (the default) is the token when there is one and the
device flow otherwise, `web` is the device flow always, with a token used for the listing only, and
`token` is the token only, where an empty vault fails by name instead of falling back. `github_login_scopes`
is what the device flow asks for on top of gh's own, `workflow` by default.

Either way `gh` becomes git's credential helper for github.com (`gh auth setup-git`), so the checkouts
keep their HTTPS remotes and `git push` and `gh pr create` work, in a terminal, in the `make code` window
and in a Remote Control session alike. The token lives in `~/.config/gh/hosts.yml` on the VM, readable by
the VM user and so by Claude Code (see [What the VM trusts](what-the-vm-trusts.md)). The role marks the login
it made - a digest of the token, in `~/.config/claude-on-proxmox/` on the VM - and removes only that one
when you clear `vault_github_token`; a login you made yourself is never replaced by the vault token nor
removed, a token of your own pasted into `gh` included. `gh auth status --json`, which the role reads, needs gh 2.81 or newer: GitHub's apt repository,
which `dev_tools` installs from, provides it, and an older gh is a named failure rather than a VM reported
as signed out on every run. `github_auth: false` in `local.yml` leaves `gh` and `~/.gitconfig` alone: a
token then only lists. A private repository is cloned over HTTPS only where git can ask gh for it - gh
signed in and made git's credential helper, by this role or by you (`gh auth setup-git`) - and is
otherwise skipped and named rather than failed; over SSH your key clones it either way.

Commits need an identity. `git_user_name` and `git_user_email` (in `local.yml`) are written to
`~/.gitconfig` on the VM when set; left empty, each is derived only where `~/.gitconfig` has none, so an
identity you set on the VM stays: the display name (or login) and GitHub's noreply address,
`<id>+<login>@users.noreply.github.com`, of the account the token belongs to, or of `github_username`
without one, so no private email lands in commits by default. An organisation gives none, which the run
says: set the two, or sign gh in as yourself.

`github_projects_clone_protocol: ssh` clones over SSH instead, with a key of your own on the VM; the role
pre-seeds GitHub's published host keys, so there is no trust-on-first-use prompt. That covers `git` only:
`gh pr create` still needs the login above.
