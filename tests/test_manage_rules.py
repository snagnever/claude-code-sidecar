"""Unit tests for manage_rules.py — TOML block parsing, formatting, and CLI commands."""

import argparse
import os
import textwrap

import pytest
import tomllib

from manage_rules import (
    Block,
    _find_fields_end,
    build_parser,
    cmd_add,
    cmd_list,
    cmd_remove,
    format_rule_toml,
    parse_blocks,
    quote_toml,
)


# =====================================================================
# parse_blocks
# =====================================================================

class TestParseBlocks:
    """Tests for parse_blocks() — TOML array-of-tables parsing."""

    def test_single_block(self):
        lines = [
            "[[bash.blocklist]]",
            "pattern = 'sudo'",
            "reason  = \"no sudo\"",
        ]
        blocks = parse_blocks(lines)
        assert len(blocks) == 1
        assert blocks[0].list_name == "blocklist"
        assert blocks[0].pattern == "sudo"
        assert blocks[0].start == 0
        assert blocks[0].end == 3

    def test_multiple_blocks_same_type(self):
        lines = [
            "[[bash.blocklist]]",
            "pattern = 'sudo'",
            "reason  = \"no sudo\"",
            "",
            "[[bash.blocklist]]",
            "pattern = 'rm -rf'",
            "reason  = \"dangerous\"",
        ]
        blocks = parse_blocks(lines)
        assert len(blocks) == 2
        assert blocks[0].pattern == "sudo"
        assert blocks[1].pattern == "rm -rf"

    def test_mixed_list_types(self):
        lines = [
            "[[bash.blocklist]]",
            "pattern = 'sudo'",
            "reason  = \"no sudo\"",
            "",
            "[[bash.allowlist]]",
            "pattern = 'ls'",
            "reason  = \"safe\"",
        ]
        blocks = parse_blocks(lines)
        assert len(blocks) == 2
        assert blocks[0].list_name == "blocklist"
        assert blocks[1].list_name == "allowlist"

    def test_empty_file(self):
        assert parse_blocks([]) == []

    def test_comments_between_blocks(self):
        lines = [
            "# Header comment",
            "",
            "[[bash.blocklist]]",
            "pattern = 'sudo'",
            "reason  = \"no\"",
            "",
            "# Another comment",
            "[[bash.asklist]]",
            "pattern = 'rm'",
            "reason  = \"ask\"",
        ]
        blocks = parse_blocks(lines)
        assert len(blocks) == 2
        assert blocks[0].start == 2
        assert blocks[1].start == 7

    def test_command_field_for_risk(self):
        lines = [
            "[[bash.risk]]",
            "command = \"ls\"",
            "risk    = 0",
            "reason  = \"safe\"",
        ]
        blocks = parse_blocks(lines)
        assert len(blocks) == 1
        assert blocks[0].pattern == "ls"
        assert blocks[0].list_name == "risk"

    def test_fields_end_excludes_trailing_blanks(self):
        lines = [
            "[[bash.blocklist]]",
            "pattern = 'sudo'",
            "reason  = \"no\"",
            "",
            "",
            "[[bash.asklist]]",
            "pattern = 'rm'",
            "reason  = \"ask\"",
        ]
        blocks = parse_blocks(lines)
        # First block fields_end should be after reason (line 3), not blank lines
        assert blocks[0].fields_end == 3
        assert blocks[0].end == 5  # before [[bash.asklist]]

    def test_pattern_with_double_quotes(self):
        lines = [
            '[[bash.blocklist]]',
            'pattern = "\\\\bsudo\\\\b"',
            'reason  = "no sudo"',
        ]
        blocks = parse_blocks(lines)
        assert len(blocks) == 1
        assert blocks[0].pattern == "\\\\bsudo\\\\b"


# =====================================================================
# quote_toml
# =====================================================================

class TestQuoteToml:
    """Tests for quote_toml() — TOML string quoting."""

    def test_literal_quoting(self):
        assert quote_toml("simple") == "'simple'"

    def test_single_quote_fallback(self):
        """Strings with single quotes fall back to double-quoted."""
        result = quote_toml("it's here")
        assert result.startswith('"')
        assert "it's here" in result

    def test_backslash_escaping(self):
        result = quote_toml("back\\slash", literal=False)
        assert result == '"back\\\\slash"'

    def test_double_quote_escaping(self):
        result = quote_toml('say "hello"', literal=False)
        assert result == '"say \\"hello\\""'

    def test_literal_false(self):
        """literal=False always uses double quotes."""
        result = quote_toml("simple", literal=False)
        assert result == '"simple"'

    def test_regex_pattern(self):
        """Regex patterns with special chars are properly quoted."""
        result = quote_toml(r"\bsudo\b")
        assert result == r"'\bsudo\b'"


# =====================================================================
# format_rule_toml
# =====================================================================

class TestFormatRuleToml:
    """Tests for format_rule_toml() — generating TOML blocks."""

    def test_blocklist_rule(self):
        text = format_rule_toml("blocklist", r"\bsudo\b", "No sudo")
        assert "[[bash.blocklist]]" in text
        assert r"'\bsudo\b'" in text
        assert '"No sudo"' in text

    def test_alterlist_with_sub(self):
        text = format_rule_toml(
            "alterlist", r"\brsync\b", "Add dry-run",
            sub_pattern=r"\brsync\b",
            sub_replacement="rsync --dry-run",
        )
        assert "[[bash.alterlist]]" in text
        assert "sub_pattern" in text
        assert "sub_replacement" in text

    def test_risk_rule_with_command(self):
        text = format_rule_toml("risk", "ls", "Safe", risk_level="0", is_command="true")
        assert "[[bash.risk]]" in text
        assert 'command' in text
        assert "risk" in text

    def test_aligned_equals(self):
        """All = signs should be aligned."""
        text = format_rule_toml("blocklist", "test", "A reason")
        lines = text.strip().split("\n")
        # Skip header line
        field_lines = [l for l in lines[1:] if "=" in l]
        eq_positions = [l.index("=") for l in field_lines]
        assert len(set(eq_positions)) == 1, f"= signs not aligned: {eq_positions}"

    def test_risk_rule_with_pattern(self):
        """Risk rule without is_command uses pattern field."""
        text = format_rule_toml("risk", r"rm\s+-rf", "Dangerous", risk_level="4")
        assert "pattern" in text
        assert "command" not in text.split("\n")[1]  # header is line 0


# =====================================================================
# cmd_add (with tmp config files)
# =====================================================================

class TestCmdAdd:
    """Tests for cmd_add() — adding rules to config files."""

    def _make_args(self, list_name, pattern, reason, **kwargs):
        args = argparse.Namespace(
            list=list_name,
            pattern=pattern,
            reason=reason,
            match=kwargs.get("match"),
            sub_pattern=kwargs.get("sub_pattern"),
            sub_replacement=kwargs.get("sub_replacement"),
            prepend=kwargs.get("prepend"),
            append=kwargs.get("append"),
            is_command=kwargs.get("is_command", False),
            risk_level=kwargs.get("risk_level"),
        )
        return args

    def test_add_blocklist_rule(self, tmp_config_dir):
        args = self._make_args("blocklist", r"\bdanger\b", "Dangerous command")
        cmd_add(args)
        # Verify rule was written
        path = tmp_config_dir / "permissions.toml"
        with open(path, "rb") as f:
            config = tomllib.load(f)
        patterns = [r["pattern"] for r in config["bash"]["blocklist"]]
        assert r"\bdanger\b" in patterns

    def test_add_risk_rule(self, tmp_config_dir):
        args = self._make_args("risk", "danger", "Dangerous", is_command=True, risk_level=3)
        cmd_add(args)
        path = tmp_config_dir / "commands-risks.toml"
        with open(path, "rb") as f:
            config = tomllib.load(f)
        commands = [r.get("command") for r in config["bash"]["risk"]]
        assert "danger" in commands

    def test_duplicate_rejected(self, tmp_config_dir):
        """Adding a duplicate pattern exits with error."""
        args = self._make_args("blocklist", r"\\bsudo\\b", "Dup")
        with pytest.raises(SystemExit):
            cmd_add(args)

    def test_invalid_regex_rejected(self, tmp_config_dir):
        """Invalid regex pattern exits with error."""
        args = self._make_args("blocklist", r"[invalid", "Bad regex")
        with pytest.raises(SystemExit):
            cmd_add(args)

    def test_alterlist_needs_rewrite_fields(self, tmp_config_dir):
        """Alterlist without rewrite fields exits with error."""
        args = self._make_args("alterlist", r"\btest\b", "Missing rewrite")
        with pytest.raises(SystemExit):
            cmd_add(args)

    def test_alterlist_sub_pattern_without_replacement(self, tmp_config_dir):
        """sub_pattern without sub_replacement exits with error."""
        args = self._make_args("alterlist", r"\btest\b", "Incomplete", sub_pattern=r"\btest\b")
        with pytest.raises(SystemExit):
            cmd_add(args)

    def test_risk_level_required(self, tmp_config_dir):
        """Risk rules without --risk-level exit with error."""
        args = self._make_args("risk", r"test", "No risk level")
        with pytest.raises(SystemExit):
            cmd_add(args)

    def test_risk_level_out_of_range(self, tmp_config_dir):
        """Risk level > 4 exits with error."""
        args = self._make_args("risk", r"test", "Too high", risk_level=5)
        with pytest.raises(SystemExit):
            cmd_add(args)

    def test_add_allowlist_rule(self, tmp_config_dir):
        args = self._make_args("allowlist", r"\bpwd\b", "Show directory")
        cmd_add(args)
        path = tmp_config_dir / "permissions.toml"
        with open(path, "rb") as f:
            config = tomllib.load(f)
        patterns = [r["pattern"] for r in config["bash"]["allowlist"]]
        assert r"\bpwd\b" in patterns

    def test_add_with_match_mode(self, tmp_config_dir):
        args = self._make_args("allowlist", r"git status", "Git status", match="match")
        cmd_add(args)
        path = tmp_config_dir / "permissions.toml"
        with open(path, "rb") as f:
            config = tomllib.load(f)
        rules = config["bash"]["allowlist"]
        added = [r for r in rules if r["pattern"] == "git status"]
        assert len(added) == 1
        assert added[0]["match"] == "match"


# =====================================================================
# cmd_remove (with tmp config files)
# =====================================================================

class TestCmdRemove:
    """Tests for cmd_remove() — removing rules from config files."""

    def test_remove_existing_rule(self, tmp_config_dir):
        args = argparse.Namespace(list="blocklist", pattern=r"\\bsudo\\b")
        cmd_remove(args)
        path = tmp_config_dir / "permissions.toml"
        with open(path, "rb") as f:
            config = tomllib.load(f)
        blocklist = config.get("bash", {}).get("blocklist", [])
        patterns = [r["pattern"] for r in blocklist]
        assert r"\\bsudo\\b" not in patterns

    def test_remove_missing_pattern_exits(self, tmp_config_dir):
        args = argparse.Namespace(list="blocklist", pattern="nonexistent")
        with pytest.raises(SystemExit):
            cmd_remove(args)

    def test_remove_preserves_other_blocks(self, tmp_config_dir):
        """Removing one rule preserves the rest."""
        # First add another rule
        add_args = argparse.Namespace(
            list="blocklist", pattern=r"\bdanger\b", reason="Dangerous",
            match=None, sub_pattern=None, sub_replacement=None,
            prepend=None, append=None, is_command=False, risk_level=None,
        )
        cmd_add(add_args)

        # Remove the original sudo rule
        rm_args = argparse.Namespace(list="blocklist", pattern=r"\\bsudo\\b")
        cmd_remove(rm_args)

        path = tmp_config_dir / "permissions.toml"
        with open(path, "rb") as f:
            config = tomllib.load(f)
        patterns = [r["pattern"] for r in config["bash"]["blocklist"]]
        assert r"\bdanger\b" in patterns
        assert r"\\bsudo\\b" not in patterns


# =====================================================================
# cmd_list (capture stdout)
# =====================================================================

class TestCmdList:
    """Tests for cmd_list() — listing rules."""

    def test_list_all(self, tmp_config_dir, capsys):
        args = argparse.Namespace(list=None)
        cmd_list(args)
        output = capsys.readouterr().out
        assert "BLOCKLIST" in output
        assert "ALLOWLIST" in output

    def test_list_single_type(self, tmp_config_dir, capsys):
        args = argparse.Namespace(list="blocklist")
        cmd_list(args)
        output = capsys.readouterr().out
        assert "BLOCKLIST" in output
        assert "ALLOWLIST" not in output

    def test_list_empty_type(self, tmp_config_dir, capsys):
        args = argparse.Namespace(list="asklist")
        cmd_list(args)
        output = capsys.readouterr().out
        assert "(empty)" in output

    def test_list_risk_rules(self, tmp_config_dir, capsys):
        args = argparse.Namespace(list="risk")
        cmd_list(args)
        output = capsys.readouterr().out
        assert "RISK" in output
        assert "ls" in output


# =====================================================================
# build_parser
# =====================================================================

class TestBuildParser:
    """Tests for build_parser() — argument parsing."""

    def test_add_command(self):
        parser = build_parser()
        args = parser.parse_args(["add", "blocklist", r"\bsudo\b", "No sudo"])
        assert args.command == "add"
        assert args.list == "blocklist"
        assert args.pattern == r"\bsudo\b"

    def test_remove_command(self):
        parser = build_parser()
        args = parser.parse_args(["remove", "blocklist", r"\bsudo\b"])
        assert args.command == "remove"
        assert args.list == "blocklist"

    def test_list_command(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.command == "list"
        assert args.list is None

    def test_list_with_type(self):
        parser = build_parser()
        args = parser.parse_args(["list", "risk"])
        assert args.list == "risk"

    def test_invalid_list_rejected(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["add", "invalid", "pattern", "reason"])

    def test_add_with_all_options(self):
        parser = build_parser()
        args = parser.parse_args([
            "add", "alterlist", r"\bfoo\b", "Rewrite foo",
            "--sub-pattern", r"\bfoo\b",
            "--sub-replacement", "bar",
            "--match", "search",
        ])
        assert args.sub_pattern == r"\bfoo\b"
        assert args.sub_replacement == "bar"
        assert args.match == "search"
