#!/usr/bin/env python3
"""
filter.py - Claude Code PreToolUse Hook for Permission Filtering
================================================================

WHAT THIS FILE DOES:
Intercepts tool calls before execution. Loads permission rules from config
files (same directory as this script) and checks the call against engines:

  BASH ENGINES (for Bash/Shell tool calls):

    LIST-BASED ENGINE (permissions.toml → [[bash.*]]):
      1. BLOCKLIST  — always denied, regardless of other lists
      2. ALTERLIST  — command is rewritten and auto-approved
      3. ASKLIST    — escalated to the user for confirmation
      4. ALLOWLIST  — auto-approved without prompting

    RISK-LEVEL ENGINE (commands-risks.toml):
      Each command has a numeric risk level (0-4). Action mappings in
      settings.toml determine what happens for each risk level.

    DELETION ENGINE (delete-policy.toml → delete_policy_engine.py):
      Specialized policy for `rm` commands. Evaluates file paths against
      rules combining glob patterns, git status conditions, and
      project-scoped overrides.

  TOOL ENGINE (for all other tools and MCP calls):

    Unified rules in permissions.toml under [[tool.*]] sections. Each rule
    specifies a list of tool names (exact or regex) and optional field-level
    predicates. Supports blocklist/alterlist/asklist/allowlist with the same
    priority ordering as the bash list engine.

    MCP tools are just tools with names like "mcp__server__action" — matched
    by the same regex system, no special engine needed.

MODE (settings.toml):
  mode = "lists"  — list-based engine only (default)
  mode = "risk"   — risk-level engine only
  mode = "both"   — both engines; most restrictive decision wins

If no engine matches, the hook exits silently (passthrough) and Claude Code's
normal permission flow takes over.

CONFIG FILES:
  settings.toml       — mode selection, risk thresholds, engine toggles
  commands-risks.toml — command-to-risk-level mappings
  permissions.toml    — block/allow/ask/alter lists (bash + tool)
  delete-policy.toml  — deletion policy rules

HOOK REGISTRATION:
Registered in ~/.claude/settings.json under hooks.PreToolUse.
- Use matcher ".*" so Bash, Read/Write/Edit/Grep/Glob, MCP, and all other tools run
  through this hook. If matcher is only "Bash", the tool engine never sees non-Bash
  calls (MCP allowlists/blocklists in permissions.toml will not apply).
- Cursor (third-party skills): "Bash|Shell" covers bash only; use ".*" for MCP/tools.

HOOK OUTPUT (PreToolUse JSON):
  permissionDecision "deny"   → block the command
  permissionDecision "allow"  → run without prompting (with optional updatedInput)
  permissionDecision "ask"    → prompt the user for confirmation
  (no output / exit 0)        → fall through to normal permission flow
"""

import json
import os
import re
import sys
import tomllib

from delete_policy_engine import decide_deletion


SETTINGS_FILENAME = "settings.toml"
RISKS_FILENAME = "commands-risks.toml"
PERMISSIONS_FILENAME = "permissions.toml"
DELETION_FILENAME = "delete-policy.toml"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_toml(script_dir: str, filename: str) -> dict:
    """Load a TOML file from the script directory. Returns {} if missing."""
    path = os.path.join(script_dir, filename)
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def load_config() -> dict:
    """Load and merge all config files into a single dict.

    Produces a dict with:
      - "mode": str (from settings.toml, default "lists")
      - "risk": dict with thresholds (from settings.toml)
      - "bash": dict with list rules + risk rules merged
      - "tool": dict with tool engine lists (blocklist/alterlist/asklist/allowlist)
      - "deletion": dict with deletion policy (from delete-policy.toml)
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))

    settings = _load_toml(script_dir, SETTINGS_FILENAME)
    risks = _load_toml(script_dir, RISKS_FILENAME)
    permissions = _load_toml(script_dir, PERMISSIONS_FILENAME)
    deletion = _load_toml(script_dir, DELETION_FILENAME)

    # Start with settings as the base
    config: dict = {}
    config["mode"] = settings.get("mode", "lists")
    config["risk"] = settings.get("risk", {})

    # Deletion engine toggle (from settings.toml)
    deletion_settings = settings.get("deletion", {})
    config["deletion_enabled"] = deletion_settings.get("enabled", True)

    # Tool engine toggle (from settings.toml)
    tool_engine_settings = settings.get("tool_engine", {})
    config["tool_engine_enabled"] = tool_engine_settings.get("enabled", True)

    # Merge bash sections: permissions has lists, risks has risk rules
    bash: dict = {}
    for key in ("blocklist", "alterlist", "asklist", "allowlist"):
        rules = permissions.get("bash", {}).get(key, [])
        if rules:
            bash[key] = rules
    risk_rules = risks.get("bash", {}).get("risk", [])
    if risk_rules:
        bash["risk"] = risk_rules

    config["bash"] = bash

    # Tool engine sections (from permissions.toml [[tool.*]])
    tool: dict = {}
    for key in ("blocklist", "alterlist", "asklist", "allowlist"):
        rules = permissions.get("tool", {}).get(key, [])
        if rules:
            tool[key] = rules
    config["tool"] = tool

    # Deletion policy
    config["deletion"] = deletion

    return config


# ---------------------------------------------------------------------------
# Rule matching — list-based engine
# ---------------------------------------------------------------------------

def check_list(command: str, rules: list[dict]) -> dict | None:
    """Check command against a list of rules. Returns first matching rule or None."""
    for rule in rules:
        match_fn = re.search if rule.get("match", "search") == "search" else re.match
        if match_fn(rule["pattern"], command):
            return rule
    return None


def apply_alter(command: str, rule: dict) -> str:
    """Apply a declarative alter rule to rewrite a command.

    Supports three rewrite modes (checked in order):
      - sub_pattern + sub_replacement: regex substitution (re.sub, count=1)
      - prepend: string prepended to the entire command
      - append: string appended to the entire command
    """
    if "sub_pattern" in rule and "sub_replacement" in rule:
        return re.sub(rule["sub_pattern"], rule["sub_replacement"], command, count=1)
    if "prepend" in rule:
        return rule["prepend"] + command
    if "append" in rule:
        return command + rule["append"]
    return command


# ---------------------------------------------------------------------------
# Rule matching — risk-level engine
# ---------------------------------------------------------------------------

def check_risk(command: str, rules: list[dict]) -> dict | None:
    """Check command against risk rules. Returns the highest-risk matching rule.

    Risk rules support two matching fields:
      - "command": prefix match (word-boundary aware)
      - "pattern": regex match (re.search)
    A rule can have both fields (OR logic).
    """
    best: dict | None = None
    for rule in rules:
        matched = False
        if "command" in rule:
            cmd = rule["command"]
            matched = command == cmd or command.startswith(cmd + " ") or command.startswith(cmd + "\t")
        if "pattern" in rule:
            matched = matched or bool(re.search(rule["pattern"], command))
        if matched and (best is None or rule.get("risk", 0) > best.get("risk", 0)):
            best = rule
    return best


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------

def decide_lists(command: str, config: dict) -> tuple[str, str | None, dict | None]:
    """Evaluate a command against list-based permission rules.

    Returns:
      ("block",       reason, None)           — deny execution
      ("alter",       reason, updated_input)  — rewrite command and allow
      ("ask",         reason, None)           — escalate to user
      ("approve",     None,   None)           — auto-allow
      ("passthrough", None,   None)           — no opinion, fall through
    """
    bash = config.get("bash", {})

    # 1. Blocklist — always deny
    rule = check_list(command, bash.get("blocklist", []))
    if rule:
        return ("block", rule["reason"], None)

    # 2. Alterlist — rewrite and allow
    rule = check_list(command, bash.get("alterlist", []))
    if rule:
        new_command = apply_alter(command, rule)
        return ("alter", rule["reason"], {"command": new_command})

    # 3. Asklist — escalate to user
    rule = check_list(command, bash.get("asklist", []))
    if rule:
        return ("ask", rule["reason"], None)

    # 4. Allowlist — auto-approve
    rule = check_list(command, bash.get("allowlist", []))
    if rule:
        return ("approve", None, None)

    # 5. No match — passthrough to Claude Code's normal permission flow
    return ("passthrough", None, None)


def decide_risk(command: str, config: dict) -> tuple[str, str | None, dict | None]:
    """Evaluate a command against risk-level rules.

    Action mapping from config["risk"]:
      allow = [0, 1]   — these risk levels auto-allow
      ask   = [2]       — these risk levels prompt the user
      block = [3, 4]   — these risk levels are denied
      block_above = 4   — any risk level above this is also denied
    """
    risk_config = config.get("risk", {})
    allow_levels = set(risk_config.get("allow", [0]))
    ask_levels = set(risk_config.get("ask", [2]))
    block_levels = set(risk_config.get("block", [3]))
    block_above = risk_config.get("block_above", 3)
    rules = config.get("bash", {}).get("risk", [])

    rule = check_risk(command, rules)
    if rule is None:
        return ("passthrough", None, None)

    level = rule.get("risk", 0)
    reason = rule.get("reason", f"Risk level {level}")

    if level in block_levels or level > block_above:
        return ("block", reason, None)
    elif level in ask_levels:
        return ("ask", reason, None)
    elif level in allow_levels:
        return ("approve", reason, None)
    else:
        # Level not explicitly mapped — default to ask
        return ("ask", reason, None)


# ---------------------------------------------------------------------------
# Rule matching — tool engine (non-Bash tools and MCP calls)
# ---------------------------------------------------------------------------

def match_tool_rule(tool_name: str, tool_input: dict, rule: dict) -> bool:
    """Check if a tool call matches a tool rule.

    A rule matches when:
      1. tool_name matches at least one regex in rule["tools"]
      2. All field predicates in rule.get("fields", {}) match (AND logic)
         Each field predicate is a regex matched against str(tool_input[field]).
    """
    # Check tool name against the tools list
    tools_patterns = rule.get("tools", [])
    if not tools_patterns:
        return False
    tool_matched = any(re.search(pat, tool_name) for pat in tools_patterns)
    if not tool_matched:
        return False

    # Check field predicates (AND logic — all must match)
    fields = rule.get("fields", {})
    for field_name, field_pattern in fields.items():
        field_value = str(tool_input.get(field_name, ""))
        if not re.search(field_pattern, field_value):
            return False

    return True


def check_tool_list(tool_name: str, tool_input: dict, rules: list[dict]) -> dict | None:
    """Check a tool call against a list of tool rules. Returns first match or None."""
    for rule in rules:
        if match_tool_rule(tool_name, tool_input, rule):
            return rule
    return None


def apply_tool_alter(tool_input: dict, rule: dict) -> dict:
    """Apply a tool alter rule to produce updated tool_input.

    The rule's "transform" sub-table specifies per-field mutations:
      - sub_pattern + sub_replacement: regex substitution on the field value
      - prepend: string prepended to the field value
      - append: string appended to the field value
    """
    transform = rule.get("transform", {})
    if not transform:
        return dict(tool_input)

    updated = dict(tool_input)
    for field_name, ops in transform.items():
        value = str(updated.get(field_name, ""))
        if isinstance(ops, dict):
            if "sub_pattern" in ops and "sub_replacement" in ops:
                value = re.sub(ops["sub_pattern"], ops["sub_replacement"], value, count=1)
            elif "prepend" in ops:
                value = ops["prepend"] + value
            elif "append" in ops:
                value = value + ops["append"]
        updated[field_name] = value
    return updated


def decide_tool(
    tool_name: str, tool_input: dict, config: dict,
) -> tuple[str, str | None, dict | None]:
    """Evaluate a non-Bash tool call against tool permission rules.

    Same priority order as the bash list engine:
      1. blocklist  → deny
      2. alterlist  → rewrite and allow
      3. asklist    → escalate to user
      4. allowlist  → auto-approve
      5. no match   → passthrough
    """
    tool_config = config.get("tool", {})

    # 1. Blocklist — always deny
    rule = check_tool_list(tool_name, tool_input, tool_config.get("blocklist", []))
    if rule:
        return ("block", rule["reason"], None)

    # 2. Alterlist — rewrite and allow
    rule = check_tool_list(tool_name, tool_input, tool_config.get("alterlist", []))
    if rule:
        updated = apply_tool_alter(tool_input, rule)
        return ("alter", rule["reason"], updated)

    # 3. Asklist — escalate to user
    rule = check_tool_list(tool_name, tool_input, tool_config.get("asklist", []))
    if rule:
        return ("ask", rule["reason"], None)

    # 4. Allowlist — auto-approve
    rule = check_tool_list(tool_name, tool_input, tool_config.get("allowlist", []))
    if rule:
        return ("approve", None, None)

    # 5. No match — passthrough
    return ("passthrough", None, None)


# Restrictiveness ranking for merge logic
DECISION_RANK = {"passthrough": 0, "approve": 1, "alter": 1, "ask": 2, "block": 3}


def most_restrictive(
    a: tuple[str, str | None, dict | None],
    b: tuple[str, str | None, dict | None],
) -> tuple[str, str | None, dict | None]:
    """Return the more restrictive of two decision tuples."""
    if DECISION_RANK.get(a[0], 0) >= DECISION_RANK.get(b[0], 0):
        return a
    return b


def _merge_results(*results: tuple[str, str | None, dict | None]) -> tuple[str, str | None, dict | None]:
    """Merge multiple engine results. Most restrictive non-passthrough wins."""
    active = [r for r in results if r[0] != "passthrough"]
    if not active:
        return ("passthrough", None, None)
    best = active[0]
    for r in active[1:]:
        best = most_restrictive(best, r)
    return best


def decide(
    command: str, config: dict, cwd: str = "", project_dir: str | None = None,
) -> tuple[str, str | None, dict | None]:
    """Top-level decision dispatcher based on mode.

    Modes:
      "lists" — list-based engine only
      "risk"  — risk-level engine only
      "both"  — both engines; most restrictive wins

    The deletion engine (delete-policy.toml) runs independently when enabled,
    and its result is merged with the mode engine using most-restrictive-wins.
    """
    mode = config.get("mode", "lists")

    if mode == "lists":
        engine_result = decide_lists(command, config)
    elif mode == "risk":
        engine_result = decide_risk(command, config)
    elif mode == "both":
        engine_result = _merge_results(
            decide_lists(command, config),
            decide_risk(command, config),
        )
    else:
        engine_result = decide_lists(command, config)

    # Deletion engine — runs independently when enabled
    if config.get("deletion_enabled", True):
        deletion_result = decide_deletion(
            command, config.get("deletion", {}), cwd, project_dir,
        )
        if deletion_result[0] != "passthrough":
            engine_result = _merge_results(engine_result, deletion_result)

    return engine_result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Hook entry point — reads JSON from stdin, decides, outputs JSON."""
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        # Malformed input — let Claude Code handle it normally
        sys.exit(0)

    try:
        config = load_config()
    except tomllib.TOMLDecodeError as e:
        # Config broken — fail open (passthrough) with warning
        print(f"filter: config error: {e}", file=sys.stderr)
        sys.exit(0)

    tool_name: str = data.get("tool_name", "")
    tool_input: dict = data.get("tool_input", {})
    cwd: str = data.get("cwd", os.getcwd())
    project_dir: str | None = os.environ.get("CLAUDE_PROJECT_DIR")

    # Default to Bash when tool_name is missing (backward compatibility)
    if not tool_name:
        tool_name = "Bash"

    # Route: Bash/Shell → bash engines, everything else → tool engine
    if tool_name in ("Bash", "Shell"):
        command: str = tool_input.get("command", "")
        decision, reason, updated_input = decide(command, config, cwd, project_dir)
    elif config.get("tool_engine_enabled", True):
        decision, reason, updated_input = decide_tool(tool_name, tool_input, config)
    else:
        # Tool engine disabled — passthrough
        sys.exit(0)

    if decision == "block":
        block_reason = reason or "Blocked by permission hook"
        if tool_name in ("Bash", "Shell"):
            block_reason += ". Show the users the full command so they can run by themselves."
        else:
            block_reason += f". Blocked tool call: {tool_name}."
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": block_reason,
            },
        }
    elif decision == "alter":
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": reason or "Tool input rewritten for safety",
                "updatedInput": updated_input,
            },
        }
    elif decision == "ask":
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason or "Confirm this tool call",
            },
        }
    elif decision == "approve":
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "",
            },
        }
    else:
        # passthrough: no output, exit 0 → normal permission flow
        sys.exit(0)

    print(json.dumps(output))


if __name__ == "__main__":
    main()
