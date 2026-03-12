#!/usr/bin/env bash
# uninstall.sh — Remove claude-code-sidecar from Claude Code
# Usage: ./uninstall.sh [--keep-config] [--project [path]]
#   --keep-config     Preserve config files (keeps your custom rules)
#   --project [path]  Uninstall from project-level .claude/ (default: current directory)
# Idempotent: safe to run multiple times.
set -euo pipefail

# Config files
CONFIG_FILES=("settings.toml" "commands-risks.toml" "permissions.toml" "delete-policy.toml")

# Parse args
KEEP_CONFIG=false
PROJECT_MODE=false
PROJECT_ROOT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-config) KEEP_CONFIG=true; shift ;;
        --project)
            PROJECT_MODE=true
            if [[ $# -gt 1 && ! "$2" =~ ^-- ]]; then
                PROJECT_ROOT="$(cd "$2" && pwd)"; shift 2
            else
                PROJECT_ROOT="$(pwd)"; shift
            fi
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Set paths based on install mode
if $PROJECT_MODE; then
    SIDECAR_DIR="$PROJECT_ROOT/.claude/claude-code-sidecar"
    SETTINGS="$PROJECT_ROOT/.claude/settings.json"
    HOOK_COMMAND="python3 .claude/claude-code-sidecar/filter.py"
else
    SIDECAR_DIR="$HOME/.claude/claude-code-sidecar"
    SETTINGS="$HOME/.claude/settings.json"
    HOOK_COMMAND="python3 ~/.claude/claude-code-sidecar/filter.py"
fi

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if $PROJECT_MODE; then
    echo "Uninstalling claude-code-sidecar (project-level: $PROJECT_ROOT)..."
else
    echo "Uninstalling claude-code-sidecar (account-wide)..."
fi

# 1. Remove hook from settings.json
if [ -f "$SETTINGS" ]; then
    python3 - "$SETTINGS" "$HOOK_COMMAND" << 'PYEOF'
import json, sys

settings_path = sys.argv[1]
hook_command = sys.argv[2]

try:
    with open(settings_path) as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    sys.exit(0)

hooks = settings.get("hooks", {})
pre_tool_use = hooks.get("PreToolUse", [])

# Filter out groups containing filter.py
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

# 2. Remove scripts
for script in filter.py delete_policy_engine.py; do
    if [ -e "$SIDECAR_DIR/$script" ]; then
        rm "$SIDECAR_DIR/$script"
        echo -e "${GREEN}✓${NC} Removed $SIDECAR_DIR/$script"
    fi
done

# 3. Remove config files (unless --keep-config)
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

# 4. Remove skill (project-level only)
if $PROJECT_MODE; then
    SKILL_DIR="$PROJECT_ROOT/.claude/skills/sidecar-permissions-config"
    if [ -d "$SKILL_DIR" ]; then
        rm -rf "$SKILL_DIR"
        echo -e "${GREEN}✓${NC} Removed skill from $SKILL_DIR"
    fi
fi

# 5. Remove sidecar directory if empty
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
