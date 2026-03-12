#!/usr/bin/env bash
# install.sh — Install claude-code-sidecar for Claude Code
# Usage: ./install.sh [--link]
#   --link  Use symbolic links instead of copies (for development)
# Idempotent: safe to run multiple times.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIDECAR_DIR="$HOME/.claude/claude-code-sidecar"
SETTINGS="$HOME/.claude/settings.json"

# Config files to install
CONFIG_FILES=("settings.toml" "commands-risks.toml" "permissions.toml" "delete-policy.toml")

# Parse args
USE_LINKS=false
for arg in "$@"; do
    case "$arg" in
        --link) USE_LINKS=true ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "Installing claude-code-sidecar..."

# 1. Create sidecar directory
mkdir -p "$SIDECAR_DIR"

# 2. Install files (copy or symlink)
if $USE_LINKS; then
    ln -sf "$SCRIPT_DIR/filter.py" "$SIDECAR_DIR/filter.py"
    ln -sf "$SCRIPT_DIR/delete_policy_engine.py" "$SIDECAR_DIR/delete_policy_engine.py"
    for cfg in "${CONFIG_FILES[@]}"; do
        if [ -f "$SCRIPT_DIR/$cfg" ]; then
            ln -sf "$SCRIPT_DIR/$cfg" "$SIDECAR_DIR/$cfg"
        fi
    done
    echo -e "${GREEN}✓${NC} Symlinked filter.py, delete_policy_engine.py, and config files to $SIDECAR_DIR/ (dev mode)"
else
    cp "$SCRIPT_DIR/filter.py" "$SIDECAR_DIR/filter.py"
    cp "$SCRIPT_DIR/delete_policy_engine.py" "$SIDECAR_DIR/delete_policy_engine.py"
    for cfg in "${CONFIG_FILES[@]}"; do
        if [ -f "$SCRIPT_DIR/$cfg" ]; then
            cp "$SCRIPT_DIR/$cfg" "$SIDECAR_DIR/$cfg"
        fi
    done
    echo -e "${GREEN}✓${NC} Copied filter.py, delete_policy_engine.py, and config files to $SIDECAR_DIR/"
fi
chmod +x "$SIDECAR_DIR/filter.py"
chmod +x "$SIDECAR_DIR/delete_policy_engine.py"

# 3. Register hook in settings.json (idempotent)
python3 - "$SETTINGS" << 'PYEOF'
import json, sys

settings_path = sys.argv[1]

# Read existing settings or start fresh
try:
    with open(settings_path) as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    settings = {}

# Ensure hooks.PreToolUse exists
hooks = settings.setdefault("hooks", {})
pre_tool_use = hooks.setdefault("PreToolUse", [])

# Check if filter.py hook already registered
hook_command = "python3 ~/.claude/claude-code-sidecar/filter.py"
already_exists = any(
    hook_command in h.get("command", "")
    for group in pre_tool_use
    for h in group.get("hooks", [])
)

if not already_exists:
    pre_tool_use.append({
        "matcher": "Bash",
        "hooks": [
            {
                "type": "command",
                "command": hook_command
            }
        ]
    })
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print("ADDED")
else:
    print("EXISTS")
PYEOF

RESULT=$(python3 - "$SETTINGS" << 'PYEOF'
import json, sys
settings_path = sys.argv[1]
try:
    with open(settings_path) as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    print("MISSING")
    sys.exit(0)

hook_command = "python3 ~/.claude/claude-code-sidecar/filter.py"
exists = any(
    hook_command in h.get("command", "")
    for group in settings.get("hooks", {}).get("PreToolUse", [])
    for h in group.get("hooks", [])
)
print("EXISTS" if exists else "MISSING")
PYEOF
)

if [ "$RESULT" = "EXISTS" ]; then
    echo -e "${GREEN}✓${NC} Hook registered in $SETTINGS"
else
    echo -e "${YELLOW}⚠${NC} Could not verify hook in $SETTINGS — check manually"
fi

# 4. Advisory
echo ""
echo -e "${GREEN}Installation complete!${NC}"
if $USE_LINKS; then
    echo -e "${YELLOW}Dev mode:${NC} Files are symlinked — edits to the project take effect immediately."
fi
echo ""
echo "Config files:"
echo "  settings.toml       — mode selection and risk thresholds"
echo "  commands-risks.toml — command-to-risk-level mappings"
echo "  permissions.toml    — block/allow/ask/alter lists"
echo "  delete-policy.toml  — deletion policy rules"
echo ""
echo "Modes (set in settings.toml):"
echo "  lists — list-based engine only (block/allow/ask/alter)"
echo "  risk  — risk-level engine only (0=safe to 4=critical)"
echo "  both  — both engines, most restrictive wins"
echo ""
echo "Edit config at: $SIDECAR_DIR/"
echo ""
echo -e "${YELLOW}Note:${NC} If you have rules in ~/.claude/settings.json under"
echo "permissions.allow or permissions.ask, they still apply alongside this hook."
echo "Consider consolidating them into permissions.toml for a single source of truth."
