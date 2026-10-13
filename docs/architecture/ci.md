# 12. CI

`.github/workflows/ci.yml`, on push to `main`/`master`, on every pull request, and weekly
(`17 6 * * 1`).

| Job | Contents |
|---|---|
| `lint` | tracked-file guard → `make lint` → `make syntax` → `make unit` → pre-commit (minus the linters already run) → gitleaks in `dir` mode → gitleaks in `git` mode over the commits the push or pull request brings in (full clone, `fetch-depth: 0`) |
| `dependency-review` | PRs only, fails on moderate severity |
| `molecule` | matrix over all seven roles, `needs: lint` |
| `playbooks` | matrix over the `configure` and `provision` scenarios, `needs: lint` |
| `canary` | schedule/dispatch only, runs `configure` against the **unpinned** Ubuntu image; allowed to fail — that failure is the notification |

`.github/actions/setup` is a composite action installing Python 3.12 and uv (at the version
`uv.lock` pins), restoring the uv and Galaxy caches, and running `make deps`. The Galaxy cache key hashes
`requirements.yml`, which is only correct because those versions are exact.

**No job uses a secret**, so the full suite runs on pull requests from forks. Keep it that way.
`make test` runs everything the CI gate runs locally, pre-commit included, but it is not a strict
superset: CI also scans the working tree and the commits a push brings in with gitleaks, which a
local run cannot reproduce from the git index alone.
