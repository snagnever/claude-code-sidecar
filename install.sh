#!/usr/bin/env bash
# install.sh — Install claude-code-sidecar for Claude Code
# Usage: ./install.sh [--link] [--project [path]]
#   --link            Use symbolic links instead of copies (for development)
#   --project [path]  Install to project-level .claude/ (default: current directory)
# Idempotent: safe to run multiple times.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Config files to install
CONFIG_FILES=("settings.toml" "commands-risks.toml" "permissions.toml" "delete-policy.toml")

# Parse args
USE_LINKS=false
PROJECT_MODE=false
PROJECT_ROOT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --link) USE_LINKS=true; shift ;;
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
    echo "Installing claude-code-sidecar (project-level: $PROJECT_ROOT)..."
else
    echo "Installing claude-code-sidecar (account-wide)..."
fi

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
python3 - "$SETTINGS" "$HOOK_COMMAND" << 'PYEOF'
import json, sys

settings_path = sys.argv[1]
hook_command = sys.argv[2]

# Read existing settings or start fresh
try:
    with open(settings_path) as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    settings = {}

# Ensure hooks.PreToolUse exists
hooks = settings.setdefault("hooks", {})
pre_tool_use = hooks.setdefault("PreToolUse", [])

# Check if filter.py hook already registered (any matcher)
existing_group = None
for group in pre_tool_use:
    for h in group.get("hooks", []):
        if hook_command in h.get("command", ""):
            existing_group = group
            break
    if existing_group:
        break

if existing_group:
    # Upgrade: update matcher from "Bash" to ".*" if needed
    if existing_group.get("matcher") != ".*":
        existing_group["matcher"] = ".*"
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
        print("UPGRADED")
    else:
        print("EXISTS")
else:
    pre_tool_use.append({
        "matcher": ".*",
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
PYEOF

RESULT=$(python3 - "$SETTINGS" "$HOOK_COMMAND" << 'PYEOF'
import json, sys
settings_path = sys.argv[1]
hook_command = sys.argv[2]
try:
    with open(settings_path) as f:
        settings = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    print("MISSING")
    sys.exit(0)

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

# 4. Install skill (project-level only)
if $PROJECT_MODE; then
    SKILL_SRC="$SCRIPT_DIR/skills/sidecar-permissions-config/SKILL.md"
    SKILL_DST="$PROJECT_ROOT/.claude/skills/sidecar-permissions-config/SKILL.md"
    if [ -f "$SKILL_SRC" ]; then
        mkdir -p "$(dirname "$SKILL_DST")"
        if $USE_LINKS; then
            ln -sf "$SKILL_SRC" "$SKILL_DST"
        else
            sed 's|~/.claude/claude-code-sidecar/|.claude/claude-code-sidecar/|g' \
                "$SKILL_SRC" > "$SKILL_DST"
        fi
        echo -e "${GREEN}✓${NC} Skill installed to $(dirname "$SKILL_DST")/"
    fi
fi

# 5. Advisory
echo ""
echo -e "${GREEN}Installation complete!${NC}"
if $USE_LINKS; then
    echo -e "${YELLOW}Dev mode:${NC} Files are symlinked — edits to the project take effect immediately."
fi
echo ""
echo "Config files:"
echo "  settings.toml       — mode selection, risk thresholds, engine toggles"
echo "  commands-risks.toml — command-to-risk-level mappings"
echo "  permissions.toml    — block/allow/ask/alter lists (bash + tool/MCP)"
echo "  delete-policy.toml  — deletion policy rules"
echo ""
echo "Engines:"
echo "  Bash engines (lists/risk/both) — control Bash/Shell commands"
echo "  Tool engine                    — control Tools (Read/Write/Edit/...) and MCP calls"
echo "  Deletion engine                — specialized rm policy"
echo ""
echo "Edit config at: $SIDECAR_DIR/"
if $PROJECT_MODE; then
    echo ""
    echo -e "${YELLOW}Project-level install:${NC} Rules apply only when Claude Code runs in $PROJECT_ROOT."
    echo "The hook uses a relative path, so the project can be moved without reinstalling."
fi
echo ""
echo -e "${YELLOW}Note:${NC} If you have rules in ~/.claude/settings.json under"
echo "permissions.allow or permissions.ask, they still apply alongside this hook."
echo "Consider consolidating them into permissions.toml for a single source of truth."
echo ""
echo -e "${YELLOW}PreToolUse matcher:${NC} This install sets hooks.PreToolUse to matcher \".*\"."
echo "  All tool types (Bash, Read/Write, MCP, …) must pass through the hook for"
echo "  permissions.toml [[tool.*]] rules to apply. If matcher is only \"Bash\","
echo "  MCP and other tools never reach filter.py — re-run this script to upgrade."
