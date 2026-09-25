#!/usr/bin/env bash
# Install OAgents into the running Omarchy shell.
#
#   ./install.sh          copy this checkout into ~/.config/omarchy/plugins
#   ./install.sh --link   symlink it instead (for active development)
#   ./install.sh --uninstall
#
# Copy is the default because the shell's file watcher reloads plugin code
# it can see change on disk. Re-run this script after editing and changes
# take effect immediately.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ID="ttt.agents"
PLUGIN_DIR="$HOME/.config/omarchy/plugins/$PLUGIN_ID"
MODE="copy"
ENABLE_BAR=true

for arg in "$@"; do
  case "$arg" in
    --copy) MODE="copy" ;;
    --link) MODE="link" ;;
    --enable) ENABLE_BAR=true ;;
    --no-enable) ENABLE_BAR=false ;;
    --uninstall) MODE="uninstall" ;;
    -h|--help)
      cat <<EOF
Usage: ./install.sh [OPTIONS]

Install OAgents into the Omarchy shell.

Options:
  --copy         Copy plugin files into ~/.config/omarchy/plugins (default)
  --link         Symlink checkout into ~/.config/omarchy/plugins (for development)
  --no-enable    Do not automatically enable the widget in the Omarchy bar
  --uninstall    Remove the OAgents plugin from Omarchy
  -h, --help     Show this help message
EOF
      exit 0
      ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

reload_shell() {
  if command -v omarchy-shell >/dev/null 2>&1; then
    omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
  fi
}

if [[ "$MODE" == "uninstall" ]]; then
  if command -v omarchy >/dev/null 2>&1; then
    omarchy plugin disable "$PLUGIN_ID" >/dev/null 2>&1 || true
  fi
  rm -rf "$PLUGIN_DIR"
  rm -rf "${XDG_STATE_HOME:-$HOME/.local/state}/ttt/agents" "${XDG_CACHE_HOME:-$HOME/.cache}/ttt/agent-usage"
  reload_shell
  echo "OAgents removed. API keys in ~/.config/ttt/agents.json were left alone."
  exit 0
fi

# Check core dependencies
for dep in python3 jq; do
  if ! command -v "$dep" >/dev/null 2>&1; then
    echo "Note: '$dep' is not installed yet. Install it with: sudo pacman -S ${dep/python3/python}"
  fi
done
if ! command -v gh >/dev/null 2>&1; then
  echo "Note: the Copilot tab needs the GitHub CLI: sudo pacman -S github-cli && gh auth login"
fi

# Pre-validate before installing
if command -v omarchy-plugin-validate >/dev/null 2>&1; then
  omarchy-plugin-validate "$SRC" >/dev/null 2>&1 || {
    echo "Error: omarchy-plugin-validate failed." >&2
    exit 1
  }
fi

mkdir -p "$(dirname "$PLUGIN_DIR")"
rm -rf "$PLUGIN_DIR"

if [[ "$MODE" == "link" ]]; then
  ln -sfn "$SRC" "$PLUGIN_DIR"
  echo "Linked $SRC -> $PLUGIN_DIR"
else
  mkdir -p "$PLUGIN_DIR"
  cp -r "$SRC/manifest.json" "$SRC/Panel.qml" "$SRC/Main.qml" "$SRC/Agent.qml" \
    "$SRC/bin" "$SRC/collectors" "$SRC/assets" "$PLUGIN_DIR/"
  rm -rf "$PLUGIN_DIR/collectors/__pycache__"
  echo "Copied OAgents into $PLUGIN_DIR"
fi
chmod +x "$PLUGIN_DIR/bin/"* "$PLUGIN_DIR"/collectors/[!_]*

reload_shell

if [[ "$ENABLE_BAR" == true ]] && command -v omarchy >/dev/null 2>&1; then
  if omarchy plugin list --json 2>/dev/null | python3 -c '
import json, sys
plugins = json.load(sys.stdin)
sys.exit(0 if any(p.get("id") == sys.argv[1] and p.get("enabled") for p in plugins) else 1)
' "$PLUGIN_ID" 2>/dev/null; then
    echo "OAgents is already enabled in the bar."
  else
    omarchy plugin enable "$PLUGIN_ID" center >/dev/null 2>&1 \
      && echo "Added OAgents to the bar (center section)." \
      || echo "Could not add widget automatically. Run: omarchy plugin enable $PLUGIN_ID center"
  fi
fi

if command -v omarchy >/dev/null 2>&1 && omarchy plugin list 2>/dev/null | grep -qE '^omarchy\.agents +enabled'; then
  echo "Note: Omarchy's own Agents widget is also enabled. Hide it with: omarchy plugin disable omarchy.agents"
fi

echo
echo "🎉 OAgents ready!"
echo "  • Open the panel:  Click the 󱚣 icon in your Omarchy bar"
echo "  • Refresh:         omarchy-shell $PLUGIN_ID refresh"
echo "  • API keys:        ~/.config/ttt/agents.json (see README)"
