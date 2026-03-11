#!/usr/bin/env python3
"""
manage_rules.py — Add, remove, and list rules across config files
=================================================================

Usage:
  manage_rules.py add <list> <pattern> <reason> [options]
  manage_rules.py remove <list> <pattern>
  manage_rules.py list [<list>]

Lists: blocklist, alterlist, asklist, allowlist, risk

Files are auto-routed:
  - risk rules → commands-risks.toml
  - list rules → permissions.toml
"""

import argparse
import os
import re
import sys
import tomllib
from typing import NamedTuple


PERMISSIONS_FILENAME = "permissions.toml"
RISKS_FILENAME = "commands-risks.toml"
VALID_LISTS = ("blocklist", "alterlist", "asklist", "allowlist", "risk")
LIST_TYPES = ("blocklist", "alterlist", "asklist", "allowlist")
# Matches any TOML array-of-tables header: [[...]]
HEADER_RE = re.compile(r"^\[\[bash\.(\w+)\]\]")
# Matches a pattern field line in either quoting style
PATTERN_LINE_RE = re.compile(r"""^pattern\s*=\s*(?:'([^']*)'|"((?:[^"\\]|\\.)*)")""")
# Matches a command field line in either quoting style
COMMAND_LINE_RE = re.compile(r"""^command\s*=\s*(?:'([^']*)'|"((?:[^"\\]|\\.)*)")""")
# Matches a risk field line (integer)
RISK_LINE_RE = re.compile(r"""^risk\s*=\s*(\d+)""")


def config_path_for(list_name: str) -> str:
    """Return the config file path for a given list type."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if list_name == "risk":
        return os.path.join(script_dir, RISKS_FILENAME)
    return os.path.join(script_dir, PERMISSIONS_FILENAME)


def load_config_for(list_name: str) -> dict:
    """Load the appropriate config file for a given list type."""
    path = config_path_for(list_name)
    with open(path, "rb") as f:
        return tomllib.load(f)


# ---------------------------------------------------------------------------
# Block parsing (line-based)
# ---------------------------------------------------------------------------

class Block(NamedTuple):
    list_name: str
    pattern: str       # pattern or command value (used as identifier)
    start: int         # first line index (the [[bash.*]] header)
    fields_end: int    # one past the last field line (before trailing blanks/comments)
    end: int           # one past the last line before the next header


def _find_fields_end(lines: list[str], start: int, end: int) -> int:
    """Find the line after the last TOML field line in a block (skip trailing blanks/comments)."""
    last_field = start
    for i in range(start, end):
        stripped = lines[i].strip()
        # A field line is either the [[header]] or a key = value line
        if stripped and (HEADER_RE.match(stripped) or "=" in stripped):
            last_field = i + 1
    return last_field


def parse_blocks(lines: list[str]) -> list[Block]:
    """Parse the TOML file into block ranges."""
    blocks: list[Block] = []
    current_list: str | None = None
    current_start: int = 0
    current_pattern: str = ""

    for i, line in enumerate(lines):
        m = HEADER_RE.match(line.strip())
        if m:
            # Close previous block
            if current_list is not None:
                fe = _find_fields_end(lines, current_start, i)
                blocks.append(Block(current_list, current_pattern, current_start, fe, i))
            current_list = m.group(1)
            current_start = i
            current_pattern = ""
            continue
        if current_list is not None:
            # Check for pattern field
            pm = PATTERN_LINE_RE.match(line.strip())
            if pm:
                current_pattern = pm.group(1) if pm.group(1) is not None else pm.group(2)
            # Check for command field (risk rules may use command instead of pattern)
            cm = COMMAND_LINE_RE.match(line.strip())
            if cm and not current_pattern:
                current_pattern = cm.group(1) if cm.group(1) is not None else cm.group(2)

    # Close last block
    if current_list is not None:
        fe = _find_fields_end(lines, current_start, len(lines))
        blocks.append(Block(current_list, current_pattern, current_start, fe, len(lines)))

    return blocks


# ---------------------------------------------------------------------------
# TOML formatting
# ---------------------------------------------------------------------------

def quote_toml(value: str, literal: bool = True) -> str:
    """Quote a string for TOML. Use literal (single-quote) unless it contains single quotes."""
    if literal and "'" not in value:
        return f"'{value}'"
    # Fall back to basic string with escaping
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_rule_toml(list_name: str, pattern: str, reason: str, **kwargs: str) -> str:
    """Build a TOML block for a new rule."""
    lines = [f"[[bash.{list_name}]]"]

    # Collect fields in display order
    fields: list[tuple[str, str]] = []

    if list_name == "risk" and kwargs.get("is_command"):
        # Risk rule with command prefix match
        fields.append(("command", quote_toml(pattern, literal=False)))
    else:
        fields.append(("pattern", quote_toml(pattern, literal=True)))

    if "sub_pattern" in kwargs:
        fields.append(("sub_pattern", quote_toml(kwargs["sub_pattern"], literal=True)))
    if "sub_replacement" in kwargs:
        fields.append(("sub_replacement", quote_toml(kwargs["sub_replacement"], literal=False)))
    if "prepend" in kwargs:
        fields.append(("prepend", quote_toml(kwargs["prepend"], literal=False)))
    if "append" in kwargs:
        fields.append(("append", quote_toml(kwargs["append"], literal=False)))

    if "risk_level" in kwargs:
        fields.append(("risk", kwargs["risk_level"]))  # bare integer, no quoting

    fields.append(("reason", quote_toml(reason, literal=False)))

    if "match" in kwargs:
        fields.append(("match", quote_toml(kwargs["match"], literal=False)))

    # Align = signs
    max_key_len = max(len(k) for k, _ in fields)
    for key, val in fields:
        lines.append(f"{key:<{max_key_len}} = {val}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_add(args: argparse.Namespace) -> None:
    pattern = args.pattern
    list_name = args.list

    # Validate regex (skip for risk rules with --command flag)
    is_command = list_name == "risk" and args.is_command
    if not is_command:
        try:
            re.compile(pattern)
        except re.error as e:
            print(f"Invalid regex pattern: {e}", file=sys.stderr)
            sys.exit(1)

    # Validate alterlist fields
    if list_name == "alterlist":
        has_sub = bool(args.sub_pattern or args.sub_replacement)
        has_prepend = bool(args.prepend)
        has_append = bool(args.append)
        if not (has_sub or has_prepend or has_append):
            print("Alterlist rules need at least one rewrite field: "
                  "--sub-pattern/--sub-replacement, --prepend, or --append",
                  file=sys.stderr)
            sys.exit(1)
        if bool(args.sub_pattern) != bool(args.sub_replacement):
            print("--sub-pattern and --sub-replacement must be used together",
                  file=sys.stderr)
            sys.exit(1)

    # Validate risk fields
    if list_name == "risk":
        if args.risk_level is None:
            print("Risk rules require --risk-level (0-3)", file=sys.stderr)
            sys.exit(1)
        if args.risk_level < 0 or args.risk_level > 3:
            print("Risk level must be 0-3", file=sys.stderr)
            sys.exit(1)

    # Check for duplicates
    try:
        config = load_config_for(list_name)
        existing = config.get("bash", {}).get(list_name, [])
        if list_name == "risk":
            # For risk rules, check both command and pattern fields
            field = "command" if is_command else "pattern"
            if any(r.get(field) == pattern for r in existing):
                print(f"Rule with {field} {pattern!r} already exists in {list_name}", file=sys.stderr)
                sys.exit(1)
        else:
            if any(r["pattern"] == pattern for r in existing):
                print(f"Rule with pattern {pattern!r} already exists in {list_name}", file=sys.stderr)
                sys.exit(1)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        pass  # file issues will surface when we try to write

    # Build extra kwargs
    extra: dict[str, str] = {}
    if args.sub_pattern:
        extra["sub_pattern"] = args.sub_pattern
    if args.sub_replacement:
        extra["sub_replacement"] = args.sub_replacement
    if args.prepend:
        extra["prepend"] = args.prepend
    if args.append:
        extra["append"] = args.append
    if args.match:
        extra["match"] = args.match
    if list_name == "risk":
        extra["risk_level"] = str(args.risk_level)
        if is_command:
            extra["is_command"] = "true"

    block_text = format_rule_toml(list_name, pattern, args.reason, **extra)

    # Find insertion point: after the last block of the same list
    path = config_path_for(list_name)
    try:
        with open(path, "r") as f:
            content = f.read()
    except FileNotFoundError:
        # Create the file with a minimal header
        content = f"# {os.path.basename(path)}\n\n"

    lines = content.splitlines(keepends=True)
    blocks = parse_blocks([l.rstrip("\n") for l in lines])

    # Find last block index for this list
    last_idx = -1
    for i, b in enumerate(blocks):
        if b.list_name == list_name:
            last_idx = i

    if last_idx >= 0:
        # Insert after the last field line of the last block in this list
        insert_at = blocks[last_idx].fields_end
        new_content = ("".join(lines[:insert_at]) + "\n"
                       + block_text
                       + "".join(lines[insert_at:]))
    else:
        # No existing blocks for this list — append to end
        if not content.endswith("\n"):
            content += "\n"
        new_content = content + "\n" + block_text

    # Atomic write
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        f.write(new_content)
    os.replace(tmp_path, path)

    target_file = os.path.basename(path)
    print(f"Added to {list_name} in {target_file}: {pattern}")


def cmd_remove(args: argparse.Namespace) -> None:
    path = config_path_for(args.list)
    with open(path, "r") as f:
        lines = f.readlines()

    stripped = [l.rstrip("\n") for l in lines]
    blocks = parse_blocks(stripped)

    # Find the target block
    target = None
    for b in blocks:
        if b.list_name == args.list and b.pattern == args.pattern:
            target = b
            break

    if target is None:
        print(f"No rule with pattern/command {args.pattern!r} found in {args.list}", file=sys.stderr)
        sys.exit(1)

    start = target.start
    end = target.end

    # Include preceding comment lines that belong to this rule (non-blank, starting with #)
    while start > 0 and stripped[start - 1].startswith("#") and not HEADER_RE.match(stripped[start - 1]):
        # Don't eat section separator comments (# ---...---)
        if re.match(r"^# -{3,}", stripped[start - 1]):
            break
        start -= 1

    # Include one blank line before the block if present
    if start > 0 and stripped[start - 1].strip() == "":
        start -= 1

    # Skip trailing blank lines after the block
    while end < len(lines) and stripped[end].strip() == "":
        end += 1

    new_lines = lines[:start] + lines[end:]

    # Atomic write
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        f.writelines(new_lines)
    os.replace(tmp_path, path)

    target_file = os.path.basename(path)
    print(f"Removed from {args.list} in {target_file}: {args.pattern}")


def cmd_list(args: argparse.Namespace) -> None:
    lists_to_show = [args.list] if args.list else list(VALID_LISTS)

    for list_name in lists_to_show:
        try:
            config = load_config_for(list_name)
        except (FileNotFoundError, tomllib.TOMLDecodeError):
            config = {}

        rules = config.get("bash", {}).get(list_name, [])
        header = f"{list_name.upper()} ({len(rules)} rule{'s' if len(rules) != 1 else ''})"
        print(header)

        if not rules:
            print("  (empty)\n")
            continue

        if list_name == "risk":
            for i, rule in enumerate(rules, 1):
                identifier = rule.get("command") or rule.get("pattern", "?")
                match_type = "command" if "command" in rule else "pattern"
                print(f"  {i}. [{match_type}] {identifier}")
                print(f"     risk:   {rule.get('risk', '?')}")
                print(f"     reason: {rule.get('reason', '')}")
        else:
            for i, rule in enumerate(rules, 1):
                print(f"  {i}. {rule['pattern']}")
                print(f"     reason: {rule['reason']}")
                match = rule.get("match", "search")
                if match != "search":
                    print(f"     match:  {match}")
                for extra in ("sub_pattern", "sub_replacement", "prepend", "append"):
                    if extra in rule:
                        print(f"     {extra}: {rule[extra]}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage_rules",
        description="Add, remove, and list rules in permissions.toml and commands-risks.toml",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # add
    p_add = sub.add_parser("add", help="Add a rule to a list")
    p_add.add_argument("list", choices=VALID_LISTS, help="Target list")
    p_add.add_argument("pattern", help="Regex pattern (or command prefix with --command)")
    p_add.add_argument("reason", help="Human-readable reason")
    p_add.add_argument("--match", choices=("search", "match"), default=None,
                        help="Match mode (default: search for block/alter/ask, match for allow)")
    p_add.add_argument("--sub-pattern", default=None, help="Substitution regex (alterlist)")
    p_add.add_argument("--sub-replacement", default=None, help="Replacement string (alterlist)")
    p_add.add_argument("--prepend", default=None, help="String to prepend (alterlist)")
    p_add.add_argument("--append", default=None, help="String to append (alterlist)")
    p_add.add_argument("--command", dest="is_command", action="store_true",
                        help="Treat pattern as a command prefix instead of regex (risk rules)")
    p_add.add_argument("--risk-level", type=int, default=None,
                        help="Risk level 0-3 (required for risk rules)")

    # remove
    p_rm = sub.add_parser("remove", help="Remove a rule by its pattern or command")
    p_rm.add_argument("list", choices=VALID_LISTS, help="Target list")
    p_rm.add_argument("pattern", help="Exact pattern/command string to remove")

    # list
    p_ls = sub.add_parser("list", help="List current rules")
    p_ls.add_argument("list", nargs="?", choices=VALID_LISTS, default=None,
                       help="Show only this list (default: all)")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "add":
        cmd_add(args)
    elif args.command == "remove":
        cmd_remove(args)
    elif args.command == "list":
        cmd_list(args)


if __name__ == "__main__":
    main()
