#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/release_local.sh --host <host> [options]

Stage ../py_solar_assistant (as the version pinned in manifest.json) and this
repo's integration onto a running Home Assistant box, then restart Core. For
local testing only - no git tag, no PyPI upload, no GitHub release.

Options:
  --host <host>   Target box address (required), e.g. 10.0.0.10
  --port <port>   SSH port (default 22)
  --user <user>   SSH user (default root)
  --dry-run       Print the plan and target paths, then stop. Changes nothing.
  -y, --yes       Skip the confirmation prompt (for non-interactive use).
  -h, --help      Show this help.

  CORE_PY (env)   Override Core's Python version (e.g. 3.14) if auto-detect fails.

Example:
  scripts/release_local.sh --host 10.0.0.10 --port 2222
EOF
}

die()  { echo "error: $*" >&2; exit 1; }
info() { echo "==> $*"; }
warn() { echo "warning: $*" >&2; }

# --- Parse args ------------------------------------------------------------
DRY_RUN=false
ASSUME_YES=false
HA_SSH_HOST=""
HA_SSH_PORT=22
HA_SSH_USER=root
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HA_SSH_HOST="${2:-}"; [ -n "$HA_SSH_HOST" ] || die "--host needs a value"; shift 2 ;;
    --port) HA_SSH_PORT="${2:-}"; [ -n "$HA_SSH_PORT" ] || die "--port needs a value"; shift 2 ;;
    --user) HA_SSH_USER="${2:-}"; [ -n "$HA_SSH_USER" ] || die "--user needs a value"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -y|--yes)  ASSUME_YES=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *)         usage >&2; die "unknown argument: $1" ;;
  esac
done
[ -n "$HA_SSH_HOST" ] || { usage >&2; die "missing --host"; }
SSH=(ssh -p "$HA_SSH_PORT" -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new
     "${HA_SSH_USER}@${HA_SSH_HOST}")

# --- Resolve repo + sibling layout -----------------------------------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PSA="$(cd -- "$ROOT/.." && pwd)/py_solar_assistant"
CC_DIR="$ROOT/custom_components"
MANIFEST="$CC_DIR/solar_assistant/manifest.json"

# --- Preflight -------------------------------------------------------------
command -v ssh >/dev/null || die "ssh not found"
command -v tar >/dev/null || die "tar not found"
command -v python3 >/dev/null || die "python3 not found (needed to read pyproject.toml)"
python3 -c 'import tomllib' 2>/dev/null || die "python3 3.11+ required (needs tomllib)"
[ -f "$MANIFEST" ] || die "manifest not found at $MANIFEST (run from the integration repo)"
[ -d "$PSA/src/py_solar_assistant" ] \
  || die "py_solar_assistant not found at $PSA (expected a sibling checkout)"

# --- Which version does the integration require? ---------------------------
PIN_VER="$(grep -oE 'py-solar-assistant==[0-9]+\.[0-9]+\.[0-9]+' "$MANIFEST" | head -1 | sed -E 's/.*==//')"
[ -n "$PIN_VER" ] \
  || die "no 'py-solar-assistant==X.Y.Z' pin found in $MANIFEST requirements"

# --- Reach the box + find Core's Python version ----------------------------
info "Checking SSH to $HA_SSH_USER@$HA_SSH_HOST:$HA_SSH_PORT"
"${SSH[@]}" true || die "cannot ssh to the box"

if [ -n "${CORE_PY:-}" ]; then
  PYVER="$CORE_PY"
  info "Using Core Python $PYVER (from CORE_PY)"
else
  # Compiled bytecode is tagged with the exact interpreter that ran it
  # (e.g. coordinator.cpython-314.pyc -> 3.14). Most reliable signal we have
  # from outside the Core container.
  TAG="$("${SSH[@]}" 'ls /config/custom_components/*/__pycache__/*.pyc 2>/dev/null | head -1' || true)"
  PYVER="$(printf '%s' "$TAG" | sed -nE 's/.*cpython-3([0-9]+).*/3.\1/p')"
  [ -n "$PYVER" ] \
    || die "could not detect Core Python version from __pycache__ - set CORE_PY (e.g. 3.14)"
  info "Detected Core Python $PYVER"
fi
DEPS_SP="/config/deps/lib/python$PYVER/site-packages"

# --- Plan ------------------------------------------------------------------
echo
info "Local deploy plan:"
echo "    client:      $PSA"
echo "                 -> staged as py-solar-assistant==$PIN_VER in $DEPS_SP"
echo "    integration: $CC_DIR/solar_assistant"
echo "                 -> /config/custom_components/solar_assistant"
echo "    then:        ha core restart"
echo

if $DRY_RUN; then
  info "Dry run - nothing staged, copied, or restarted."
  exit 0
fi

if ! $ASSUME_YES; then
  printf 'Proceed? [y/N] '
  read -r reply || reply=""
  [[ "$reply" =~ ^[yY]([eE][sS])?$ ]] || die "aborted"
fi

# --- Build the client payload (no PyPI, no build step) ---------------------
# py_solar_assistant is pure Python with no entry points, so a copy of the
# package plus dist metadata is enough for Home Assistant to consider the
# requirement satisfied (it checks importlib.metadata.version against the pin)
# and to import it. aiohttp - its only runtime dep - already ships in Core.
info "Staging client working tree as version $PIN_VER"
PAYLOAD="$(mktemp -d)"
trap 'rm -rf "$PAYLOAD"' EXIT
cp -r "$PSA/src/py_solar_assistant" "$PAYLOAD/py_solar_assistant"
find "$PAYLOAD/py_solar_assistant" -name __pycache__ -type d -prune -exec rm -rf {} +

# Derive the dist metadata from py_solar_assistant's pyproject.toml (name, python
# requirement, deps) so it never drifts from the real package; only the version
# is overridden to the manifest pin. Writes the .dist-info dir into the payload.
python3 - "$PSA/pyproject.toml" "$PIN_VER" "$PAYLOAD" <<'PY'
import os, re, sys, tomllib

pyproject, version, payload = sys.argv[1], sys.argv[2], sys.argv[3]
with open(pyproject, "rb") as f:
    proj = tomllib.load(f)["project"]

name = proj["name"]
norm = re.sub(r"[-_.]+", "_", name).lower()  # PEP 427 dist-info dir naming
dist_info = os.path.join(payload, f"{norm}-{version}.dist-info")
os.makedirs(dist_info, exist_ok=True)

lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
if proj.get("requires-python"):
    lines.append(f"Requires-Python: {proj['requires-python']}")
for dep in proj.get("dependencies", []):
    lines.append(f"Requires-Dist: {dep}")

with open(os.path.join(dist_info, "METADATA"), "w") as f:
    f.write("\n".join(lines) + "\n")
PY

# --- Copy client into Core's import path -----------------------------------
info "Copying client into $DEPS_SP"
tar -C "$PAYLOAD" -cf - . | "${SSH[@]}" "
  set -e
  rm -rf '$DEPS_SP'/py_solar_assistant '$DEPS_SP'/py_solar_assistant-*.dist-info
  mkdir -p '$DEPS_SP'
  tar -C '$DEPS_SP' -xf -
"

# --- Copy the integration (replaces the whole folder) ----------------------
info "Copying integration into /config/custom_components/solar_assistant"
tar -C "$CC_DIR" --exclude __pycache__ -cf - solar_assistant | "${SSH[@]}" "
  set -e
  rm -rf /config/custom_components/solar_assistant
  mkdir -p /config/custom_components
  tar -C /config/custom_components -xf -
"

# --- Restart + smoke-check -------------------------------------------------
info "Restarting Home Assistant Core"
"${SSH[@]}" "ha core restart"

echo
info "Staged client metadata on the box:"
"${SSH[@]}" "grep -E '^(Name|Version):' '$DEPS_SP'/py_solar_assistant-*.dist-info/METADATA" \
  || warn "could not read staged METADATA"

echo
info "Recent Core log lines mentioning the integration / client:"
"${SSH[@]}" "ha core logs 2>/dev/null | grep -iE 'solar_assistant|py_solar_assistant' | tail -n 25" \
  || warn "no matching log lines yet (Core may still be starting)"

echo
info "Done. Staged py-solar-assistant==$PIN_VER and the integration; Core restarted."
