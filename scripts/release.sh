#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/release.sh [options]

Compute the next version from Conventional Commits, update manifest.json and
CHANGELOG.md, tag vX.Y.Z, bump the coupled add-on (../ha_addons), build
solar_assistant.zip, push both repos, and publish a GitHub release.

Options:
  --dry-run     Run all read-only checks, build the zip, preview the changelog,
                and print the plan. Commits/tags/pushes/publishes nothing.
  -y, --yes     Skip the confirmation prompt (for non-interactive use).
  -h, --help    Show this help.

Versioning is automatic: feat -> minor, fix/refactor/perf -> patch, breaking ->
major (capped to a minor bump while 0.x). Reword non-compliant commits first.

Requirements: git, zip, gh (authenticated), and pipx. ../ha_addons must be
checked out as a sibling (it carries the coupled add-on version).
EOF
}

die()  { echo "error: $*" >&2; exit 1; }
info() { echo "==> $*"; }

# Run commitizen via pipx so it need not be installed globally.
CZ=(pipx run --spec commitizen cz)

# Refuse to release if the given repo has uncommitted changes.
require_clean() {
  local label="$1" dir="$2"
  [ -z "$(git -C "$dir" status --porcelain)" ] \
    || die "$label has uncommitted changes - commit or stash them first"
}

# Read the version out of manifest.json / config.yaml.
read_json_version() {
  grep -o '"version"[[:space:]]*:[[:space:]]*"[^"]*"' "$1" \
    | head -1 | sed -E 's/.*:[[:space:]]*"([^"]*)".*/\1/'
}
read_yaml_version() {
  grep -E '^version:' "$1" | head -1 \
    | sed -E 's/^version:[[:space:]]*"?([^"]*)"?[[:space:]]*$/\1/'
}

# Rewrite the add-on version in place (commitizen handles manifest.json itself).
set_yaml_version() {
  local file="$1" new="$2" tmp
  tmp="$(mktemp)"
  sed -E "s/^version:.*/version: \"${new}\"/" "$file" >"$tmp"
  mv "$tmp" "$file"
}

# Print just the changelog section the next release would add (writes no file).
# --start-rev scopes generation to commits after the last tag; --unreleased-version
# labels them as the next version. With no prior tag, generate the full history.
changelog_preview() {
  if [ -n "$LAST_TAG" ]; then
    "${CZ[@]}" changelog --dry-run --start-rev "$LAST_TAG" --unreleased-version "$TAG" 2>/dev/null
  else
    "${CZ[@]}" changelog --dry-run --unreleased-version "$TAG" 2>/dev/null
  fi
}

# --- Parse args ------------------------------------------------------------
DRY_RUN=false
ASSUME_YES=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    -y|--yes)  ASSUME_YES=true ;;
    -h|--help) usage; exit 0 ;;
    *)         usage >&2; die "unknown argument: $arg" ;;
  esac
done

if $DRY_RUN; then
  info "Dry run - nothing edited, committed, tagged, pushed, or published."
fi

# Resolve repo root from this script's location (scripts/ -> repo root).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

COMPONENT="custom_components/solar_assistant"
MANIFEST="$COMPONENT/manifest.json"
ZIP_NAME="solar_assistant.zip"
ZIP_PATH="$ROOT/dist/$ZIP_NAME"
ADDONS_ROOT="$ROOT/../ha_addons"
ADDON_CONFIG="$ADDONS_ROOT/solar_assistant/config.yaml"
ADDON_CHANGELOG="$ADDONS_ROOT/solar_assistant/CHANGELOG.md"

build_zip() {
  info "Building $ZIP_NAME"
  rm -f "$ZIP_PATH"
  mkdir -p "$(dirname "$ZIP_PATH")"
  # Zip from the repo root so entries are rooted at custom_components/solar_assistant/.
  ( cd "$ROOT" && zip -r -q "$ZIP_PATH" "$COMPONENT" \
      -x '*/__pycache__/*' -x '*.pyc' -x '*.pyo' -x '*/.DS_Store' )
  info "Wrote $ZIP_PATH"
}

# --- Preflight -------------------------------------------------------------
command -v git  >/dev/null || die "git not found"
command -v zip  >/dev/null || die "zip not found"
command -v gh   >/dev/null || die "GitHub CLI (gh) not found - https://cli.github.com"
command -v pipx >/dev/null || die "pipx not found - https://pipx.pypa.io"
[ -f "$MANIFEST" ]      || die "manifest not found at $MANIFEST (run from the integration repo)"
[ -f "$ROOT/.cz.toml" ] || die ".cz.toml not found (run from the integration repo)"

if ! $DRY_RUN; then
  gh auth status >/dev/null 2>&1 || die "gh is not authenticated - run: gh auth login"
fi

# --- Coupled add-on repo must be present -----------------------------------
[ -f "$ADDON_CONFIG" ]     || die "../ha_addons must be checked out (no config.yaml at $ADDON_CONFIG)"
[ -d "$ADDONS_ROOT/.git" ] || die "../ha_addons is not a git repo"

# --- Refuse dirty trees: a release must come from a clean, committed state -
# Both repos this release touches must be clean: ha_solar_assistant ships the
# zipped integration, ha_addons carries the coupled add-on version.
require_clean "ha_solar_assistant" "$ROOT"
require_clean "ha_addons"          "$ADDONS_ROOT"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

# --- commitizen version must match the manifest (guard against drift) ------
CURRENT="$("${CZ[@]}" version -p)"
MANIFEST_VERSION="$(read_json_version "$MANIFEST" || true)"
[ "$CURRENT" = "$MANIFEST_VERSION" ] \
  || die ".cz.toml version ($CURRENT) != manifest.json ($MANIFEST_VERSION) - reconcile them first"

# --- Lint commit messages since the last release ---------------------------
LAST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
if [ -n "$LAST_TAG" ]; then
  info "Checking Conventional Commits compliance since $LAST_TAG"
  "${CZ[@]}" check --rev-range "$LAST_TAG..HEAD" \
    || die "non-compliant commit messages in $LAST_TAG..HEAD - reword them first"
fi

# --- Compute the next version ----------------------------------------------
# cz bump --dry-run exits non-zero by design; --get-next prints just the next
# version. Tolerate its non-zero exit for the "nothing to bump" case.
NEXT="$("${CZ[@]}" bump --get-next --yes 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)" || true
[ -n "$NEXT" ] || die "no version-bumping commits since ${LAST_TAG:-the start} - nothing to release"
TAG="v$NEXT"
info "Current version $CURRENT -> next version $NEXT (tag $TAG)"

# --- Warn if the add-on has drifted from the version we are bumping FROM ----
ADDON_VERSION="$(read_yaml_version "$ADDON_CONFIG" || true)"
if [ "$ADDON_VERSION" != "$CURRENT" ]; then
  echo "warning: ha_addons config.yaml version ($ADDON_VERSION) != $CURRENT;" >&2
  echo "         this release will set it to $NEXT regardless." >&2
fi

# --- Guard against re-releasing --------------------------------------------
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null 2>&1; then
  die "tag $TAG already exists locally"
fi
if gh release view "$TAG" >/dev/null 2>&1; then
  die "release $TAG already exists - delete it first (gh release delete $TAG --cleanup-tag) or bump"
fi

# --- Dry run: build the zip, preview the changelog, print the plan, stop ----
if $DRY_RUN; then
  build_zip
  echo
  info "CHANGELOG.md preview - the $TAG section that would be added:"
  echo
  changelog_preview | sed 's/^/    /' || true
  echo
  info "Plan (not executed):"
  echo "    - bump $CURRENT -> $NEXT in manifest.json + .cz.toml, update CHANGELOG.md, commit, tag $TAG"
  echo "    - set ../ha_addons/solar_assistant/config.yaml to $NEXT, mirror CHANGELOG.md, and commit both"
  echo "    - build $ZIP_NAME from the bumped tree"
  echo "    - push $BRANCH + $TAG (this repo) and $BRANCH (../ha_addons)"
  echo "    - create GitHub release $TAG with $ZIP_NAME attached"
  exit 0
fi

# --- Confirm before mutating anything --------------------------------------
echo
info "About to release $TAG. This will:"
echo "    - bump $CURRENT -> $NEXT, update CHANGELOG.md, commit, and tag $TAG (this repo)"
echo "    - set ../ha_addons/solar_assistant/config.yaml to $NEXT, mirror CHANGELOG.md, and commit both"
echo "    - build $ZIP_NAME and attach it to GitHub release $TAG"
echo "    - push $BRANCH + $TAG (this repo) and $BRANCH (../ha_addons)"
echo
if ! $ASSUME_YES; then
  printf 'Proceed? [y/N] '
  read -r reply || reply=""
  [[ "$reply" =~ ^[yY]([eE][sS])?$ ]] || die "aborted"
fi

# --- Bump version + changelog + tag (commitizen, this repo) ----------------
info "Bumping to $NEXT and updating CHANGELOG.md"
"${CZ[@]}" bump --yes --changelog   # bumps manifest.json + .cz.toml, commits, tags $TAG

# --- Bump the coupled add-on (separate repo) -------------------------------
info "Setting ../ha_addons/solar_assistant/config.yaml -> $NEXT"
set_yaml_version "$ADDON_CONFIG" "$NEXT"
[ "$(read_yaml_version "$ADDON_CONFIG" || true)" = "$NEXT" ] || die "failed to update $ADDON_CONFIG"
# Mirror the integration changelog into the add-on folder so the Supervisor
# update dialog shows release notes (Supervisor reads CHANGELOG.md from there,
# not from the GitHub release). cz bump --changelog refreshed it just above.
cp "$ROOT/CHANGELOG.md" "$ADDON_CHANGELOG"
git -C "$ADDONS_ROOT" commit -m "chore: release $TAG" -- \
  "solar_assistant/config.yaml" "solar_assistant/CHANGELOG.md"

# --- Build the zip (after the bump so it carries $NEXT) --------------------
build_zip

# --- Push both repos -------------------------------------------------------
info "Pushing $BRANCH + $TAG (this repo)"
git push origin "$BRANCH" "$TAG"
info "Pushing $BRANCH (../ha_addons)"
git -C "$ADDONS_ROOT" push origin HEAD

# --- Publish the GitHub release --------------------------------------------
info "Creating GitHub release $TAG"
NOTES="$(mktemp)"
awk '/^## /{n++} n==1' "$ROOT/CHANGELOG.md" > "$NOTES"   # the just-added top section
gh release create "$TAG" "$ZIP_PATH" --title "$TAG" --notes-file "$NOTES"
rm -f "$NOTES"

info "Done. Release $TAG published with $ZIP_NAME attached; ../ha_addons bumped to $NEXT."
