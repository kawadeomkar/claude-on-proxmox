# 12. CI

`.github/workflows/ci.yml`, on push to `main`/`master`, on every pull request, and weekly
(`17 6 * * 1`).

| Job | Contents |
|---|---|
| `lint` | tracked-file guard → `just lint` (the linters, `--syntax-check`, then the pre-commit hooks they do not cover: private-key detection, the large-file check and the whitespace hooks) → gitleaks in `dir` mode → gitleaks in `git` mode over the commits the push or pull request brings in (full clone, `fetch-depth: 0`) |
| `unit` | `Unit tests (1/3)` … `(3/3)`: `just test unit --shard <n>/3`, four workers each (`PYTEST_XDIST_AUTO_NUM_WORKERS`, so it never depends on psutil). Each module that shares a fixture stays whole in one shard; the shards are balanced by `tests/unit/durations.json` (`tools/unit_durations.py` writes it from a run's JUnit report), a test it lacks counting as the mean. 7.9–8.4 minutes for the slowest shard on hosted runners, against 44 for the whole suite one test at a time |
| `dependency-review` | PRs only, fails on moderate severity |
| `molecule` | matrix over all eight roles, `just test --role <role>`, from the start of the run: behind `lint` a run took the unit suite's time plus the slowest scenario's |
| `playbooks` | matrix over the `configure` and `provision` scenarios, `just test integration` and `provision`, from the start of the run too |
| `result` | `CI result`: `needs` every job but the canary, `if: always()`, and fails unless each one succeeded or was skipped. The one check branch protection requires: a job added to the gate, or a matrix entry, is held to it without a settings change, and jobs can be split or renamed under it |
| `canary` | schedule/dispatch only: checks that the cloud image URL in `roles/proxmox_template/defaults/main.yml` still answers and that Ubuntu's `SHA256SUMS` still lists it, then runs `configure` against the **unpinned** Ubuntu container image (`MOLECULE_IMAGE=...:latest`); allowed to fail — that failure is the notification |

`.github/actions/setup` is a composite action installing Python 3.12 and uv (at the version
`uv.lock` pins), making a virtualenv first on `PATH` (`activate-environment`), restoring the uv and Galaxy caches, and
running `just install`. The Galaxy cache key hashes
`requirements.yml`, which is only correct because those versions are exact. Third-party actions are
pinned to commit SHAs, and Dependabot updates them along with the uv lock every week. Each of its pull
requests asks the repository's owner for a review through `.github/CODEOWNERS`, which names the files
those updates change; `dependabot.yml` has no `reviewers` option any more. Locally, `pre-commit` runs the same linters from the
virtualenv.

The weekly run is a canary. Most of what this project installs lives outside the repository - the
Ubuntu cloud image, the Docker, NodeSource and GitHub CLI apt repositories, the Claude Code installer,
the uv release and, where it is turned on, the Rust toolchain - and any of it can break without a commit here. (The Galaxy
collections are pinned to exact versions, so they cannot.) The Molecule scenarios never fetch the real
cloud image, which is why the canary checks its URL and checksum list. The unpinned-image job never runs
on a push or a pull request: its failure is how you learn the current Ubuntu image no longer works before
you bump the digest.

The gitleaks scans, and what they cannot see, are in [Secret scanning](security-model.md#secret-scanning).
A secret they find has already been pushed and is public: revoke it rather than only rewriting history.

**No job uses a secret**, so the full suite runs on pull requests from forks. Keep it that way.
`just test` runs everything the CI gate runs locally, the pre-commit hooks included, but it is not a strict
superset: CI also scans the working tree and the commits a push brings in with gitleaks, which a
local run cannot reproduce from the git index alone.

Pushing a `v*` tag runs `.github/workflows/release.yml`, which publishes a GitHub release with notes
generated from the commits since the previous tag.

A push to `main` that changes the wiki runs `.github/workflows/docs.yml`, which builds the site
strictly and deploys it to GitHub Pages; [This wiki](../development/docs.md#publishing) says how.
