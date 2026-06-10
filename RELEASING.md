# Releasing

Releases are cut from a developer machine with `scripts/release.sh`. The script computes the next version from the commit history, updates
`manifest.json` and `CHANGELOG.md`, tags `vX.Y.Z`, packages `custom_components/solar_assistant/` into `solar_assistant.zip`, and publishes a
GitHub release with the zip attached. The add-on's `run.sh` downloads that release at install time.

Versioning is **automatic**, driven by [Conventional Commits](https://www.conventionalcommits.org) via
[commitizen](https://commitizen-tools.github.io/commitizen/): the next semver is derived from the commits since the last release, so there
is no manual bump flag.

Versioning is also **coupled**: the integration (`manifest.json`) and the add-on (`../ha_addons/solar_assistant/config.yaml`) share one
version, because the add-on downloads the integration release tagged with its own version. Commitizen bumps and tags this repo; the script
then sets, commits, and pushes the add-on version in lockstep (commitizen can't do it - the add-on lives in a separate repo).

## Prerequisites

- `git`, `zip`, and [`pipx`](https://pipx.pypa.io) (used to run commitizen without polluting your environment).
- The [GitHub CLI](https://cli.github.com) (`gh`) authenticated with rights to create releases on this repo (`gh auth login`).
- The `ha_addons` repo checked out as a sibling directory (`../ha_addons`) - it carries the coupled add-on version.

## How the version is chosen

The increment comes from the Conventional Commit types since the last tag:

| Commit                                                | Bump                                           |
| ----------------------------------------------------- | ---------------------------------------------- |
| `fix:`, `refactor:`, `perf:`                          | patch (`0.1.0` -> `0.1.1`)                     |
| `feat:`                                               | minor (`0.1.0` -> `0.2.0`)                     |
| `!` / `BREAKING CHANGE:`                              | major - capped to a **minor** bump while `0.x` |
| `build:`, `chore:`, `docs:`, `ci:`, `style:`, `test:` | none                                           |

Types that don't bump also don't appear in the changelog. Write good commit messages, and reword non-compliant ones before releasing - a
release is refused if any commit since the last tag fails the Conventional Commits check.

## Cutting a release

Preview first - this changes nothing and prints the next version, a CHANGELOG preview, and the full plan (it still builds the zip to
validate it):

```sh
scripts/release.sh --dry-run
```

Then cut it:

```sh
scripts/release.sh
```

The script is read-only until it prints a summary of exactly what it will commit, push, and publish, then asks for confirmation.

Flags:

- `--dry-run` - run all checks, build the zip, and preview the CHANGELOG section and plan. Always safe.
- `-y`, `--yes` - skip the confirmation prompt (for non-interactive use).

## The py_solar_assistant dependency

[py-solar-assistant](https://pypi.org/project/py-solar-assistant/) is **not** part of this integration. The integration depends on it as an
external package, pinned in `manifest.json` (`requirements`) and pulled from PyPI by Home Assistant at install time. Make sure the pinned
`py-solar-assistant` version is published to PyPI before releasing, or Home Assistant will fail to install the integration.

## Guards

A release is refused if any of these hold:

- **ha_solar_assistant** or **ha_addons** has uncommitted changes (commit or stash first);
- `.cz.toml` and `manifest.json` versions have drifted apart (reconcile them);
- a commit since the last tag is not Conventional Commits compliant;
- the computed tag already exists locally, or a GitHub release for it already exists.

## Recovery

commitizen makes the bump commit + tag in this repo **before** the add-on is
committed, the zip is built, or anything is pushed. If a later step fails, nothing
was pushed. Fix the cause and finish the remaining steps by hand, or undo:

```sh
git tag -d <tag> && git reset --hard HEAD~1      # this repo
git -C ../ha_addons reset --hard HEAD~1          # only if the add-on was committed
```
