"""Unit tests for filter.py — list engine, risk engine, tool engine, and decision logic."""

import pytest

from filter import (
    DECISION_RANK,
    _merge_results,
    apply_alter,
    apply_tool_alter,
    check_list,
    check_risk,
    check_tool_list,
    decide,
    decide_lists,
    decide_risk,
    decide_tool,
    load_config,
    match_tool_rule,
    most_restrictive,
)


# =====================================================================
# check_list
# =====================================================================

class TestCheckList:
    """Tests for check_list() — regex matching against rule lists."""

    def test_search_match_default(self):
        """Default match mode is re.search (finds pattern anywhere)."""
        rules = [{"pattern": r"sudo", "reason": "no sudo"}]
        assert check_list("please sudo me", rules) is not None

    def test_match_mode_match(self):
        """Explicit match='match' uses re.match (anchored to start)."""
        rules = [{"pattern": r"git status", "reason": "ok", "match": "match"}]
        assert check_list("git status", rules) is not None
        assert check_list("echo git status", rules) is None

    def test_no_match_returns_none(self):
        rules = [{"pattern": r"dangerous", "reason": "bad"}]
        assert check_list("safe command", rules) is None

    def test_first_match_wins(self):
        """Order matters — first matching rule is returned."""
        rules = [
            {"pattern": r"rm", "reason": "first"},
            {"pattern": r"rm", "reason": "second"},
        ]
        result = check_list("rm file.txt", rules)
        assert result["reason"] == "first"

    def test_empty_rules_returns_none(self):
        assert check_list("anything", []) is None

    def test_regex_special_chars(self):
        """Regex patterns with word boundaries and groups work."""
        rules = [{"pattern": r"\bsudo\b", "reason": "no sudo"}]
        assert check_list("sudo rm -rf", rules) is not None
        assert check_list("pseudocode", rules) is None

    def test_multiline_command(self):
        """re.search matches across a multiline command string."""
        rules = [{"pattern": r"sudo", "reason": "no sudo"}]
        assert check_list("echo hello\nsudo rm", rules) is not None


# =====================================================================
# apply_alter
# =====================================================================

class TestApplyAlter:
    """Tests for apply_alter() — command rewriting."""

    def test_sub_pattern_replacement(self):
        rule = {
            "sub_pattern": r"\brsync\b",
            "sub_replacement": "rsync --dry-run",
            "reason": "safety",
        }
        assert apply_alter("rsync src/ dest/", rule) == "rsync --dry-run src/ dest/"

    def test_sub_replaces_only_first(self):
        """sub_pattern uses count=1 — only first occurrence."""
        rule = {
            "sub_pattern": r"echo",
            "sub_replacement": "ECHO",
            "reason": "test",
        }
        assert apply_alter("echo hello; echo world", rule) == "ECHO hello; echo world"

    def test_prepend(self):
        rule = {"prepend": "DRY_RUN=1 ", "reason": "test"}
        assert apply_alter("deploy.sh", rule) == "DRY_RUN=1 deploy.sh"

    def test_append(self):
        rule = {"append": " --dry-run", "reason": "test"}
        assert apply_alter("git clean -fd", rule) == "git clean -fd --dry-run"

    def test_no_rewrite_fields(self):
        """Rule with no rewrite fields returns command unchanged."""
        rule = {"reason": "test"}
        assert apply_alter("anything", rule) == "anything"

    def test_sub_takes_priority_over_prepend(self):
        """When both sub and prepend are present, sub wins."""
        rule = {
            "sub_pattern": r"foo",
            "sub_replacement": "bar",
            "prepend": "PREFIX ",
            "reason": "test",
        }
        assert apply_alter("foo baz", rule) == "bar baz"


# =====================================================================
# check_risk
# =====================================================================

class TestCheckRisk:
    """Tests for check_risk() — risk level matching."""

    def test_command_prefix_exact(self):
        rules = [{"command": "ls", "risk": 0, "reason": "safe"}]
        assert check_risk("ls", rules) is not None

    def test_command_prefix_with_space(self):
        rules = [{"command": "ls", "risk": 0, "reason": "safe"}]
        assert check_risk("ls -la", rules) is not None

    def test_command_prefix_with_tab(self):
        rules = [{"command": "ls", "risk": 0, "reason": "safe"}]
        assert check_risk("ls\t-la", rules) is not None

    def test_command_prefix_no_substring_match(self):
        """'rm' should NOT match 'rmdir' — word boundary awareness."""
        rules = [{"command": "rm", "risk": 2, "reason": "delete"}]
        assert check_risk("rmdir empty_dir", rules) is None

    def test_pattern_regex_match(self):
        rules = [{"pattern": r"rm\s+-rf", "risk": 4, "reason": "dangerous"}]
        assert check_risk("rm -rf /", rules) is not None

    def test_pattern_no_match(self):
        rules = [{"pattern": r"rm\s+-rf", "risk": 4, "reason": "dangerous"}]
        assert check_risk("rm file.txt", rules) is None

    def test_both_command_and_pattern_or_logic(self):
        """A rule with both command + pattern uses OR logic."""
        rules = [{"command": "git push", "pattern": r"git\s+push\s+--force", "risk": 3, "reason": "push"}]
        # Matches via command prefix
        assert check_risk("git push origin main", rules) is not None
        # Matches via pattern
        assert check_risk("git  push --force", rules) is not None

    def test_highest_risk_wins(self):
        """When multiple rules match, highest risk is returned."""
        rules = [
            {"command": "rm", "risk": 2, "reason": "normal delete"},
            {"pattern": r"rm\s+-rf", "risk": 4, "reason": "recursive delete"},
        ]
        result = check_risk("rm -rf /", rules)
        assert result["risk"] == 4
        assert result["reason"] == "recursive delete"

    def test_no_match_returns_none(self):
        rules = [{"command": "ls", "risk": 0, "reason": "safe"}]
        assert check_risk("unknown-command", rules) is None

    def test_empty_rules(self):
        assert check_risk("anything", []) is None

    def test_missing_risk_field_defaults_zero(self):
        """Rules without a risk field default to risk 0."""
        rules = [
            {"command": "ls", "reason": "safe"},
            {"command": "cat", "risk": 1, "reason": "read"},
        ]
        result = check_risk("ls", rules)
        assert result.get("risk", 0) == 0

    def test_multi_word_command_prefix(self):
        """'git commit' matches 'git commit -m test' but not 'git checkout'."""
        rules = [{"command": "git commit", "risk": 2, "reason": "modifies repo"}]
        assert check_risk("git commit -m test", rules) is not None
        assert check_risk("git checkout main", rules) is None


# =====================================================================
# decide_lists
# =====================================================================

class TestDecideLists:
    """Tests for decide_lists() — list-based engine decision logic."""

    def test_blocklist_hit(self, lists_config):
        decision, reason, updated = decide_lists("sudo rm -rf /", lists_config)
        assert decision == "block"
        assert reason is not None

    def test_alterlist_hit(self, lists_config):
        decision, reason, updated = decide_lists("rsync src/ dest/", lists_config)
        assert decision == "alter"
        assert reason is not None
        assert updated == {"command": "rsync --dry-run src/ dest/"}

    def test_asklist_hit(self, lists_config):
        decision, reason, updated = decide_lists("rm file.txt", lists_config)
        assert decision == "ask"
        assert reason is not None
        assert updated is None

    def test_allowlist_hit(self, lists_config):
        decision, reason, updated = decide_lists("ls -la", lists_config)
        assert decision == "approve"
        assert updated is None

    def test_no_match_passthrough(self, lists_config):
        decision, reason, updated = decide_lists("unknown-tool --flag", lists_config)
        assert decision == "passthrough"
        assert reason is None
        assert updated is None

    def test_block_priority_over_ask(self, lists_config):
        """A command matching both blocklist and asklist is blocked (block checked first)."""
        # "sudo rm file" matches blocklist (sudo) — should block, not ask
        decision, _, _ = decide_lists("sudo rm file", lists_config)
        assert decision == "block"

    def test_empty_config(self, minimal_config):
        decision, reason, updated = decide_lists("anything", minimal_config)
        assert decision == "passthrough"

    def test_allowlist_match_mode(self, lists_config):
        """Allowlist with match='match' requires command to match from start."""
        decision, _, _ = decide_lists("git status", lists_config)
        assert decision == "approve"
        # "echo git status" should NOT match (match mode is anchored)
        decision2, _, _ = decide_lists("echo git status", lists_config)
        assert decision2 != "approve"


# =====================================================================
# decide_risk
# =====================================================================

class TestDecideRisk:
    """Tests for decide_risk() — risk-level engine decision logic."""

    def test_allow_level(self, risk_config):
        decision, reason, _ = decide_risk("ls -la", risk_config)
        assert decision == "approve"

    def test_ask_level(self, risk_config):
        decision, reason, _ = decide_risk("rm file.txt", risk_config)
        assert decision == "ask"

    def test_block_level(self, risk_config):
        decision, reason, _ = decide_risk("rm -rf /", risk_config)
        assert decision == "block"

    def test_block_above_threshold(self):
        """Risk level above block_above is blocked even if not in block set."""
        config = {
            "risk": {"allow": [0], "ask": [1], "block": [2], "block_above": 2},
            "bash": {"risk": [{"command": "danger", "risk": 5, "reason": "very high"}]},
        }
        decision, _, _ = decide_risk("danger zone", config)
        assert decision == "block"

    def test_unmapped_level_defaults_to_ask(self):
        """Risk level not in any set defaults to ask."""
        config = {
            "risk": {"allow": [0], "ask": [], "block": [], "block_above": 10},
            "bash": {"risk": [{"command": "mystery", "risk": 5, "reason": "unknown level"}]},
        }
        decision, _, _ = decide_risk("mystery cmd", config)
        assert decision == "ask"

    def test_no_match_passthrough(self, risk_config):
        decision, reason, _ = decide_risk("unknown-tool", risk_config)
        assert decision == "passthrough"
        assert reason is None

    def test_missing_risk_config_uses_defaults(self):
        """Empty risk config uses default thresholds."""
        config = {
            "risk": {},
            "bash": {"risk": [{"command": "test", "risk": 0, "reason": "test"}]},
        }
        decision, _, _ = decide_risk("test cmd", config)
        assert decision == "approve"

    def test_no_risk_rules(self):
        """Config with no risk rules → passthrough."""
        config = {"risk": {"allow": [0]}, "bash": {}}
        decision, _, _ = decide_risk("anything", config)
        assert decision == "passthrough"


# =====================================================================
# most_restrictive
# =====================================================================

class TestMostRestrictive:
    """Tests for most_restrictive() — comparing two decision tuples."""

    def test_block_wins_over_ask(self):
        a = ("block", "blocked", None)
        b = ("ask", "asking", None)
        assert most_restrictive(a, b) == a

    def test_ask_wins_over_approve(self):
        a = ("approve", "ok", None)
        b = ("ask", "check", None)
        assert most_restrictive(a, b) == b

    def test_approve_wins_over_passthrough(self):
        a = ("approve", None, None)
        b = ("passthrough", None, None)
        assert most_restrictive(a, b) == a

    def test_same_rank_first_wins(self):
        """When ranks are equal, first argument (a) wins."""
        a = ("approve", "reason-a", None)
        b = ("alter", "reason-b", {"command": "rewritten"})
        # approve and alter are same rank (1)
        assert most_restrictive(a, b) == a

    def test_block_vs_passthrough(self):
        a = ("passthrough", None, None)
        b = ("block", "no", None)
        assert most_restrictive(a, b) == b

    def test_alter_same_rank_as_approve(self):
        assert DECISION_RANK["alter"] == DECISION_RANK["approve"]


# =====================================================================
# _merge_results
# =====================================================================

class TestMergeResults:
    """Tests for _merge_results() — merging multiple engine results."""

    def test_all_passthrough(self):
        result = _merge_results(
            ("passthrough", None, None),
            ("passthrough", None, None),
        )
        assert result == ("passthrough", None, None)

    def test_single_active_result(self):
        result = _merge_results(
            ("passthrough", None, None),
            ("ask", "check this", None),
        )
        assert result == ("ask", "check this", None)

    def test_block_wins_over_ask(self):
        result = _merge_results(
            ("ask", "check", None),
            ("block", "denied", None),
        )
        assert result[0] == "block"

    def test_three_results(self):
        result = _merge_results(
            ("approve", "ok", None),
            ("passthrough", None, None),
            ("ask", "confirm", None),
        )
        assert result[0] == "ask"

    def test_approve_and_alter(self):
        """approve and alter are same rank — first active wins."""
        result = _merge_results(
            ("alter", "rewritten", {"command": "new"}),
            ("approve", "ok", None),
        )
        assert result[0] == "alter"


# =====================================================================
# decide (mode dispatch)
# =====================================================================

class TestDecide:
    """Tests for decide() — top-level mode dispatcher."""

    def test_lists_mode(self, lists_config):
        decision, _, _ = decide("sudo rm", lists_config)
        assert decision == "block"

    def test_risk_mode(self, risk_config):
        decision, _, _ = decide("ls -la", risk_config)
        assert decision == "approve"

    def test_both_mode_most_restrictive(self, both_config):
        # "rm file.txt" is ask from lists, risk 2 (ask) from risk → ask
        decision, _, _ = decide("rm file.txt", both_config)
        assert decision == "ask"

    def test_both_mode_block_from_lists(self, both_config):
        decision, _, _ = decide("sudo rm -rf /", both_config)
        assert decision == "block"

    def test_unknown_mode_falls_back_to_lists(self, lists_config):
        lists_config["mode"] = "invalid"
        decision, _, _ = decide("sudo cmd", lists_config)
        assert decision == "block"

    def test_deletion_engine_disabled(self, lists_config):
        """When deletion_enabled is False, deletion engine doesn't run."""
        lists_config["deletion_enabled"] = False
        decision, _, _ = decide("rm file.txt", lists_config)
        # Should come from lists engine (ask), not deletion engine
        assert decision == "ask"

    def test_deletion_engine_enabled_merges(self, lists_config, deletion_config, monkeypatch):
        """When deletion_enabled is True, deletion engine result is merged."""
        lists_config["deletion_enabled"] = True
        lists_config["deletion"] = deletion_config

        # Mock decide_deletion to return block
        monkeypatch.setattr(
            "filter.decide_deletion",
            lambda cmd, cfg, cwd, proj: ("block", "secrets", None),
        )

        decision, reason, _ = decide("rm .env", lists_config)
        assert decision == "block"
        assert reason == "secrets"

    def test_deletion_passthrough_doesnt_override(self, lists_config, monkeypatch):
        """Deletion engine returning passthrough doesn't change the mode result."""
        lists_config["deletion_enabled"] = True
        lists_config["deletion"] = {}

        monkeypatch.setattr(
            "filter.decide_deletion",
            lambda cmd, cfg, cwd, proj: ("passthrough", None, None),
        )

        decision, _, _ = decide("rm file.txt", lists_config)
        # lists engine says "ask" for rm
        assert decision == "ask"

    def test_passthrough_command(self, lists_config):
        decision, _, _ = decide("unknown-tool --flag", lists_config)
        assert decision == "passthrough"


# =====================================================================
# match_tool_rule
# =====================================================================

class TestMatchToolRule:
    """Tests for match_tool_rule() — tool name + field matching."""

    def test_exact_tool_name_match(self):
        rule = {"tools": ["Write"], "reason": "test"}
        assert match_tool_rule("Write", {}, rule) is True

    def test_tool_name_regex(self):
        rule = {"tools": [r"mcp__plugin_context7_.*"], "reason": "test"}
        assert match_tool_rule("mcp__plugin_context7_context7__query-docs", {}, rule) is True
        assert match_tool_rule("mcp__plugin_other__action", {}, rule) is False

    def test_multiple_tools_or_logic(self):
        rule = {"tools": ["Write", "Edit"], "reason": "test"}
        assert match_tool_rule("Write", {}, rule) is True
        assert match_tool_rule("Edit", {}, rule) is True
        assert match_tool_rule("Read", {}, rule) is False

    def test_field_matching(self):
        rule = {
            "tools": ["Write"],
            "reason": "test",
            "fields": {"file_path": r"\.env$"},
        }
        assert match_tool_rule("Write", {"file_path": "app/.env"}, rule) is True
        assert match_tool_rule("Write", {"file_path": "app/main.py"}, rule) is False

    def test_field_matching_and_logic(self):
        """All field predicates must match (AND logic)."""
        rule = {
            "tools": ["Write"],
            "reason": "test",
            "fields": {
                "file_path": r"\.py$",
                "content": r"import os",
            },
        }
        assert match_tool_rule("Write", {"file_path": "a.py", "content": "import os\nprint()"}, rule) is True
        assert match_tool_rule("Write", {"file_path": "a.py", "content": "print()"}, rule) is False
        assert match_tool_rule("Write", {"file_path": "a.txt", "content": "import os"}, rule) is False

    def test_missing_field_no_match(self):
        """If tool_input doesn't have the field, it's treated as empty string."""
        rule = {
            "tools": ["Write"],
            "reason": "test",
            "fields": {"file_path": r"\.env$"},
        }
        assert match_tool_rule("Write", {}, rule) is False

    def test_empty_tools_list(self):
        rule = {"tools": [], "reason": "test"}
        assert match_tool_rule("Write", {}, rule) is False

    def test_no_tools_key(self):
        rule = {"reason": "test"}
        assert match_tool_rule("Write", {}, rule) is False

    def test_no_fields_matches_any_input(self):
        """Rule with no fields matches purely on tool name."""
        rule = {"tools": ["Read"], "reason": "test"}
        assert match_tool_rule("Read", {"file_path": "/etc/passwd"}, rule) is True
        assert match_tool_rule("Read", {}, rule) is True

    def test_tool_name_partial_match(self):
        """re.search matches substrings — 'Write' matches 'NotebookWrite' too."""
        rule = {"tools": [r"^Write$"], "reason": "test"}
        assert match_tool_rule("Write", {}, rule) is True
        assert match_tool_rule("NotebookWrite", {}, rule) is False


# =====================================================================
# check_tool_list
# =====================================================================

class TestCheckToolList:
    """Tests for check_tool_list() — iterating tool rules."""

    def test_first_match_wins(self):
        rules = [
            {"tools": ["Write"], "reason": "first", "fields": {"file_path": r".*"}},
            {"tools": ["Write"], "reason": "second", "fields": {"file_path": r".*"}},
        ]
        result = check_tool_list("Write", {"file_path": "any"}, rules)
        assert result["reason"] == "first"

    def test_no_match_returns_none(self):
        rules = [{"tools": ["Edit"], "reason": "test"}]
        assert check_tool_list("Write", {}, rules) is None

    def test_empty_rules(self):
        assert check_tool_list("Write", {}, []) is None


# =====================================================================
# apply_tool_alter
# =====================================================================

class TestApplyToolAlter:
    """Tests for apply_tool_alter() — tool_input field transforms."""

    def test_sub_pattern_replacement(self):
        rule = {
            "reason": "test",
            "transform": {
                "file_path": {"sub_pattern": r"\.tmp$", "sub_replacement": ".bak"},
            },
        }
        result = apply_tool_alter({"file_path": "data.tmp", "content": "hello"}, rule)
        assert result["file_path"] == "data.bak"
        assert result["content"] == "hello"

    def test_prepend(self):
        rule = {
            "reason": "test",
            "transform": {
                "content": {"prepend": "# auto-generated\n"},
            },
        }
        result = apply_tool_alter({"file_path": "a.py", "content": "x = 1"}, rule)
        assert result["content"] == "# auto-generated\nx = 1"

    def test_append(self):
        rule = {
            "reason": "test",
            "transform": {
                "content": {"append": "\n# end of file"},
            },
        }
        result = apply_tool_alter({"file_path": "a.py", "content": "x = 1"}, rule)
        assert result["content"] == "x = 1\n# end of file"

    def test_no_transform(self):
        rule = {"reason": "test"}
        result = apply_tool_alter({"file_path": "a.py"}, rule)
        assert result == {"file_path": "a.py"}

    def test_missing_field_uses_empty_string(self):
        rule = {
            "reason": "test",
            "transform": {
                "content": {"prepend": "header\n"},
            },
        }
        result = apply_tool_alter({"file_path": "a.py"}, rule)
        assert result["content"] == "header\n"

    def test_original_not_mutated(self):
        original = {"file_path": "a.py", "content": "x"}
        rule = {"reason": "test", "transform": {"content": {"append": "!"}}}
        result = apply_tool_alter(original, rule)
        assert original["content"] == "x"
        assert result["content"] == "x!"


# =====================================================================
# decide_tool
# =====================================================================

class TestDecideTool:
    """Tests for decide_tool() — tool engine decision logic."""

    def test_blocklist_hit(self, tool_config):
        decision, reason, _ = decide_tool("Write", {"file_path": "secrets.env"}, tool_config)
        assert decision == "block"
        assert "secrets" in reason.lower()

    def test_blocklist_mcp(self, tool_config):
        decision, reason, _ = decide_tool("mcp__plugin_dangerous-server_do-thing", {}, tool_config)
        assert decision == "block"

    def test_asklist_hit(self, tool_config):
        decision, reason, _ = decide_tool("Edit", {"file_path": ".github/workflows/ci.yml"}, tool_config)
        assert decision == "ask"

    def test_asklist_mcp(self, tool_config):
        decision, reason, _ = decide_tool(
            "mcp__plugin_episodic-memory_episodic-memory__write", {}, tool_config,
        )
        assert decision == "ask"

    def test_allowlist_hit(self, tool_config):
        decision, _, _ = decide_tool("Read", {"file_path": "/any/file.py"}, tool_config)
        assert decision == "approve"

    def test_allowlist_mcp(self, tool_config):
        decision, _, _ = decide_tool("mcp__plugin_context7_context7__query-docs", {}, tool_config)
        assert decision == "approve"

    def test_alterlist_hit(self, tool_config):
        decision, reason, updated = decide_tool(
            "Write", {"file_path": "deploy.sh", "content": "echo hello"}, tool_config,
        )
        assert decision == "alter"
        assert updated["content"].startswith("#!/usr/bin/env bash")
        assert "echo hello" in updated["content"]

    def test_passthrough_no_match(self, tool_config):
        decision, _, _ = decide_tool("Write", {"file_path": "app/main.py"}, tool_config)
        assert decision == "passthrough"

    def test_empty_tool_config(self, minimal_config):
        minimal_config["tool"] = {}
        decision, _, _ = decide_tool("Write", {"file_path": "a.py"}, minimal_config)
        assert decision == "passthrough"

    def test_block_priority_over_ask(self, tool_config):
        """A tool call matching both blocklist and asklist is blocked."""
        # .env file in .github/workflows/ matches both blocklist (secrets) and asklist (CI/CD)
        decision, _, _ = decide_tool(
            "Write", {"file_path": ".github/workflows/secret.env"}, tool_config,
        )
        assert decision == "block"

    def test_block_priority_over_allow(self, tool_config):
        """Blocklist takes priority even if tool is in allowlist by name."""
        # Read is in the allowlist, but let's add a blocklist rule for Read with a field match
        tool_config["tool"]["blocklist"].append({
            "tools": ["Read"],
            "reason": "Cannot read secrets",
            "fields": {"file_path": r"\.key$"},
        })
        decision, _, _ = decide_tool("Read", {"file_path": "private.key"}, tool_config)
        assert decision == "block"


# =====================================================================
# permissions.toml — Docker read-only rules (integration with repo config)
# =====================================================================


class TestDockerReadonlyPermissions:
    """Repo permissions allowlist + risks for docker inspection commands."""

    def test_lists_allow_docker_ps_compose(self):
        config = load_config()
        assert decide_lists("docker ps -a", config)[0] == "approve"
        assert decide_lists("docker compose config", config)[0] == "approve"

    def test_lists_passthrough_docker_run(self):
        config = load_config()
        assert decide_lists("docker run --rm hello-world", config)[0] == "passthrough"

    def test_lists_ask_docker_volume_rm(self):
        config = load_config()
        assert decide_lists("docker volume rm myvol", config)[0] == "ask"

    def test_risk_zero_for_docker_ps(self):
        config = load_config()
        decision, reason, _ = decide_risk("docker ps", config)
        assert decision == "approve"
        assert reason is not None
