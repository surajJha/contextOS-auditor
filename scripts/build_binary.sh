#!/usr/bin/env bash
# Builds a standalone, single-file `contextos-auditor` CLI binary using
# PyInstaller -- for teammates who want to *view* a session
# (`watch`/`report`/`doctor`) without touching Python/pip at all.
#
# Important: this binary is for the VIEWER only. The one-line `attach()`
# snippet that hooks into a running agent still has to be `pip install`ed
# into that agent's own Python process -- that's unavoidable since all 4
# supported frameworks (CrewAI/LangGraph/AutoGen/OpenAI Agents SDK) are
# Python-native, and the adapter has to live inside that same interpreter
# to see the framework's real events. This binary just removes the "I want
# to open the live dashboard on my machine" friction for everyone else on
# a team (reviewers, managers, CI dashboards, etc.) who isn't the person
# who wired up the adapter.
#
# Deliberately built from a CLEAN venv with only `contextos-auditor` (zero
# framework extras) installed -- the CLI's `watch`/`report`/`doctor`
# commands never import crewai/langchain_core/agents/autogen_core at
# module level (only inside adapter modules, which the CLI doesn't touch),
# so bundling any of them would only bloat the binary for no reason.
#
# Usage:
#   ./scripts/build_binary.sh [output-dir]
#
# Produces: <output-dir>/contextos-auditor-<os>-<arch>
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-$HERE/dist-binary}"
BUILD_VENV="$(mktemp -d)/build-venv"

OS_NAME="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$OS_NAME" in
  darwin*) OS_TAG="macos" ;;
  linux*)  OS_TAG="linux" ;;
  *)       OS_TAG="$OS_NAME" ;;
esac
ARCH_TAG="$(uname -m)"

echo "==> Building in a clean venv (no framework extras)..."
python3 -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/pip" install --quiet --upgrade pip
"$BUILD_VENV/bin/pip" install --quiet pyinstaller "$HERE"

CLI_ENTRY="$("$BUILD_VENV/bin/python" -c "import contextos_auditor.cli as m; print(m.__file__)")"

echo "==> Running PyInstaller..."
"$BUILD_VENV/bin/pyinstaller" \
  --onefile \
  --name contextos-auditor \
  --collect-submodules contextos_auditor \
  --distpath "$OUT_DIR" \
  --workpath "$(mktemp -d)" \
  --specpath "$(mktemp -d)" \
  "$CLI_ENTRY"

FINAL_NAME="contextos-auditor-${OS_TAG}-${ARCH_TAG}"
mv "$OUT_DIR/contextos-auditor" "$OUT_DIR/$FINAL_NAME"
echo "==> Built: $OUT_DIR/$FINAL_NAME"

echo "==> Smoke test (empty environment, no PATH/PYTHONHOME):"
env -i "$OUT_DIR/$FINAL_NAME" doctor

rm -rf "$BUILD_VENV"
