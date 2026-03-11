#!/usr/bin/env bash
# uninstall.sh — Remove claude-code-sidecar from Claude Code
# Usage: ./uninstall.sh [--keep-config]
#   --keep-config  Preserve config files (keeps your custom rules)
# Idempotent: safe to run multiple times.
set -euo pipefail

SIDECAR_DIR="$HOME/.claude/claude-code-sidecar"
SETTINGS="$HOME/.claude/settings.json"
KEEP_CONFIG=false

# Config files
CONFIG_FILES=("settings.toml" "commands-risks.toml" "permissions.toml")

for arg in "$@"; do
    case "$arg" in
        --keep-config) KEEP_CONFIG=true ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "Uninstalling claude-code-sidecar..."

# 1. Remove hook from settings.json
if [ -f "$SETTINGS" ]; then
    python3 - "$SETTINGS" << 'PYEOF'
import json, sys

settings_path = sys.argv[1]

try:
    with open(settings_path) as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    sys.exit(0)

hooks = settings.get("hooks", {})
pre_tool_use = hooks.get("PreToolUse", [])

# Filter out groups containing bash_filter.py
hook_command = "python3 ~/.claude/claude-code-sidecar/bash_filter.py"
filtered = [
    group for group in pre_tool_use
    if not any(
        hook_command in h.get("command", "")
        for h in group.get("hooks", [])
    )
]

if len(filtered) != len(pre_tool_use):
    if filtered:
        hooks["PreToolUse"] = filtered
    else:
        del hooks["PreToolUse"]

    if not hooks:
        del settings["hooks"]

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print("REMOVED")
else:
    print("NOT_FOUND")
PYEOF
    echo -e "${GREEN}✓${NC} Hook entry removed from $SETTINGS"
else
    echo -e "${YELLOW}⚠${NC} Settings file not found at $SETTINGS"
fi

# 2. Remove files
if [ -e "$SIDECAR_DIR/bash_filter.py" ]; then
    rm "$SIDECAR_DIR/bash_filter.py"
    echo -e "${GREEN}✓${NC} Removed $SIDECAR_DIR/bash_filter.py"
fi

if [ "$KEEP_CONFIG" = true ]; then
    echo -e "${YELLOW}⚠${NC} Kept config files in $SIDECAR_DIR/ (--keep-config)"
else
    for cfg in "${CONFIG_FILES[@]}"; do
        if [ -e "$SIDECAR_DIR/$cfg" ]; then
            rm "$SIDECAR_DIR/$cfg"
            echo -e "${GREEN}✓${NC} Removed $SIDECAR_DIR/$cfg"
        fi
    done
fi

# 3. Remove sidecar directory if empty
if [ -d "$SIDECAR_DIR" ] && [ -z "$(ls -A "$SIDECAR_DIR")" ]; then
    rmdir "$SIDECAR_DIR"
    echo -e "${GREEN}✓${NC} Removed empty directory $SIDECAR_DIR/"
fi

echo ""
echo -e "${GREEN}Uninstall complete.${NC}"
echo ""
echo "Claude Code will use its default permission flow for Bash commands."
if [ "$KEEP_CONFIG" = false ]; then
    echo "Your config files have been removed. A copy exists in the project repo."
fi
