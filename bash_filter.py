#!/usr/bin/env python3
"""
bash_filter.py - Claude Code PreToolUse Hook for Bash Command Filtering
========================================================================

WHAT THIS FILE DOES:
Intercepts every Bash/Shell tool call before execution. Loads permission rules
from up to three config files (same directory as this script) and checks the
command against two engines:

  LIST-BASED ENGINE (permissions.toml):
    1. BLOCKLIST  — always denied, regardless of other lists
    2. ALTERLIST  — command is rewritten and auto-approved
    3. ASKLIST    — escalated to the user for confirmation
    4. ALLOWLIST  — auto-approved without prompting

  RISK-LEVEL ENGINE (commands-risks.toml):
    Each command has a numeric risk level (0-3). Thresholds in settings.toml
    determine whether the command is auto-allowed, asked, or blocked.

MODE (settings.toml):
  mode = "lists"  — list-based engine only (default)
  mode = "risk"   — risk-level engine only
  mode = "both"   — both engines; most restrictive decision wins

If no engine matches, the hook exits silently (passthrough) and Claude Code's
normal permission flow takes over.

CONFIG FILES:
  settings.toml       — mode selection and risk thresholds
  commands-risks.toml — command-to-risk-level mappings
  permissions.toml    — block/allow/ask/alter lists

HOOK REGISTRATION:
Registered in ~/.claude/settings.json under hooks.PreToolUse.
- Claude Code: use matcher "Bash".
- Cursor (with Third-party skills): use matcher "Bash|Shell".

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


SETTINGS_FILENAME = "settings.toml"
RISKS_FILENAME = "commands-risks.toml"
PERMISSIONS_FILENAME = "permissions.toml"


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
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))

    settings = _load_toml(script_dir, SETTINGS_FILENAME)
    risks = _load_toml(script_dir, RISKS_FILENAME)
    permissions = _load_toml(script_dir, PERMISSIONS_FILENAME)

    # Start with settings as the base
    config: dict = {}
    config["mode"] = settings.get("mode", "lists")
    config["risk"] = settings.get("risk", {})

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

    Thresholds from config["risk"]:
      allow_below: risk levels strictly below this → auto-allow
      block_above: risk levels strictly above this → auto-block
      Between (inclusive) → ask user
    """
    risk_config = config.get("risk", {})
    allow_below = risk_config.get("allow_below", 1)
    block_above = risk_config.get("block_above", 2)
    rules = config.get("bash", {}).get("risk", [])

    rule = check_risk(command, rules)
    if rule is None:
        return ("passthrough", None, None)

    level = rule.get("risk", 0)
    reason = rule.get("reason", f"Risk level {level}")

    if level < allow_below:
        return ("approve", reason, None)
    elif level > block_above:
        return ("block", reason, None)
    else:
        return ("ask", reason, None)


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


def decide(command: str, config: dict) -> tuple[str, str | None, dict | None]:
    """Top-level decision dispatcher based on mode.

    Modes:
      "lists" — list-based engine only
      "risk"  — risk-level engine only
      "both"  — both engines; most restrictive wins
    """
    mode = config.get("mode", "lists")

    if mode == "lists":
        return decide_lists(command, config)
    elif mode == "risk":
        return decide_risk(command, config)
    elif mode == "both":
        list_result = decide_lists(command, config)
        risk_result = decide_risk(command, config)

        # If both passthrough, passthrough
        if list_result[0] == "passthrough" and risk_result[0] == "passthrough":
            return ("passthrough", None, None)
        # If one is passthrough, use the other (risk can grant permissions)
        if list_result[0] == "passthrough":
            return risk_result
        if risk_result[0] == "passthrough":
            return list_result
        # Both have opinions — most restrictive wins
        return most_restrictive(list_result, risk_result)
    else:
        # Unknown mode — default to lists
        return decide_lists(command, config)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        # Malformed input — let Claude Code handle it normally
        sys.exit(0)

    try:
        config = load_config()
    except tomllib.TOMLDecodeError as e:
        # Config broken — fail open (passthrough) with warning
        print(f"bash_filter: config error: {e}", file=sys.stderr)
        sys.exit(0)

    # Warn about misconfigured risk thresholds
    mode = config.get("mode", "lists")
    if mode in ("risk", "both"):
        risk_cfg = config.get("risk", {})
        allow_below = risk_cfg.get("allow_below", 1)
        block_above = risk_cfg.get("block_above", 2)
        if allow_below > block_above + 1:
            print(
                f"bash_filter: warning: allow_below ({allow_below}) > block_above+1 "
                f"({block_above + 1}), no ask zone exists",
                file=sys.stderr,
            )

    command: str = data.get("tool_input", {}).get("command", "")
    decision, reason, updated_input = decide(command, config)

    if decision == "block":
        block_reason = reason or "Blocked by permission hook"
        block_reason += ". Show the users the full command so they can run by themselves."
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
                "permissionDecisionReason": reason or "Command rewritten for safety",
                "updatedInput": updated_input,
            },
        }
    elif decision == "ask":
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason or "Confirm this command",
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
