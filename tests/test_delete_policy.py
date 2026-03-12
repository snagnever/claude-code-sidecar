"""Unit tests for delete_policy_engine.py — rm parsing, glob matching, deletion decisions."""

from unittest.mock import patch

import pytest

from delete_policy_engine import (
    _ACTION_RANK,
    _GIT_CHECKERS,
    _glob_match,
    _match_deletion_rule,
    decide_deletion,
    parse_rm_paths,
)


# =====================================================================
# parse_rm_paths
# =====================================================================

class TestParseRmPaths:
    """Tests for parse_rm_paths() — extracting file paths from rm commands."""

    def test_non_rm_command(self):
        assert parse_rm_paths("ls -la") is None

    def test_non_rm_similar_name(self):
        assert parse_rm_paths("rmdir empty") is None

    def test_simple_rm(self):
        assert parse_rm_paths("rm file.txt") == ["file.txt"]

    def test_rm_with_flag_r(self):
        assert parse_rm_paths("rm -r dir/") == ["dir/"]

    def test_rm_with_flag_rf(self):
        assert parse_rm_paths("rm -rf dir/") == ["dir/"]

    def test_rm_combined_flags(self):
        """Combined short flags like -rv are handled."""
        assert parse_rm_paths("rm -rv file.txt") == ["file.txt"]

    def test_rm_long_flags(self):
        assert parse_rm_paths("rm --force --recursive dir/") == ["dir/"]

    def test_double_dash_separator(self):
        """Everything after -- is treated as paths, even if it looks like a flag."""
        assert parse_rm_paths("rm -- -weird-file") == ["-weird-file"]

    def test_multiple_files(self):
        assert parse_rm_paths("rm a.txt b.txt c.txt") == ["a.txt", "b.txt", "c.txt"]

    def test_shell_metachar_dollar(self):
        """Shell metacharacters make the command unparseable (returns [])."""
        assert parse_rm_paths("rm $HOME/file") == []

    def test_shell_metachar_backtick(self):
        assert parse_rm_paths("rm `echo file`") == []

    def test_shell_metachar_pipe(self):
        assert parse_rm_paths("rm file | tee log") == []

    def test_shell_metachar_glob_star(self):
        assert parse_rm_paths("rm *.txt") == []

    def test_shell_metachar_question(self):
        assert parse_rm_paths("rm file?.txt") == []

    def test_unknown_flag(self):
        """Unknown flags return [] for safety."""
        assert parse_rm_paths("rm --unknown-flag file") == []

    def test_rm_no_args(self):
        """rm with no paths returns empty list."""
        assert parse_rm_paths("rm") == []

    def test_rm_only_flags(self):
        """rm with only flags and no paths."""
        assert parse_rm_paths("rm -rf") == []

    def test_shlex_parse_failure(self):
        """Unterminated quote → shlex.split raises ValueError → None."""
        assert parse_rm_paths("rm 'unterminated") is None

    def test_empty_string(self):
        assert parse_rm_paths("") is None

    def test_quoted_path(self):
        assert parse_rm_paths('rm "path with spaces/file.txt"') == ["path with spaces/file.txt"]

    def test_flag_after_double_dash(self):
        """Flags after -- are treated as paths."""
        assert parse_rm_paths("rm -- --not-a-flag") == ["--not-a-flag"]


# =====================================================================
# _glob_match
# =====================================================================

class TestGlobMatch:
    """Tests for _glob_match() — enhanced glob matching."""

    def test_standard_match(self):
        assert _glob_match("build/output.js", "build/**") is True

    def test_nested_match(self):
        assert _glob_match("build/sub/deep/file.js", "build/**") is True

    def test_no_match(self):
        assert _glob_match("src/main.py", "build/**") is False

    def test_star_star_slash_matches_root(self):
        """'**/*' with ** prefix should also match root-level files."""
        assert _glob_match("file.pyc", "**/*.pyc") is True

    def test_star_star_slash_matches_nested(self):
        assert _glob_match("sub/dir/file.pyc", "**/*.pyc") is True

    def test_extension_match(self):
        assert _glob_match("config.env", "*.env") is True

    def test_extension_no_match(self):
        assert _glob_match("config.toml", "*.env") is False

    def test_exact_filename(self):
        assert _glob_match("Makefile", "Makefile") is True

    def test_directory_trailing_slash_matches(self):
        """'build/' matches 'build/**' via fnmatch."""
        assert _glob_match("build/", "build/**") is True


# =====================================================================
# _match_deletion_rule (with mocked git)
# =====================================================================

class TestMatchDeletionRule:
    """Tests for _match_deletion_rule() — single rule matching."""

    def test_path_matches_no_git_condition(self):
        rule = {"paths": ["build/**"], "action": "allow", "reason": "artifacts"}
        assert _match_deletion_rule("build/output.js", rule, "/tmp") is True

    def test_path_no_match(self):
        rule = {"paths": ["build/**"], "action": "allow", "reason": "artifacts"}
        assert _match_deletion_rule("src/main.py", rule, "/tmp") is False

    def test_git_any_always_passes(self):
        rule = {"paths": ["**/*"], "git": "any", "action": "allow", "reason": "all"}
        assert _match_deletion_rule("anything.txt", rule, "/tmp") is True

    def test_git_tracked_passes(self):
        """When git_is_tracked returns True, the rule matches."""
        rule = {"paths": ["**/*"], "git": "tracked", "action": "allow", "reason": "tracked"}
        # Patch the dict entry directly since _GIT_CHECKERS holds function refs
        with patch.dict(_GIT_CHECKERS, {"tracked": lambda fp, cwd: True}):
            assert _match_deletion_rule("file.py", rule, "/tmp") is True

    def test_git_tracked_fails(self):
        rule = {"paths": ["**/*"], "git": "tracked", "action": "allow", "reason": "tracked"}
        with patch.dict(_GIT_CHECKERS, {"tracked": lambda fp, cwd: False}):
            assert _match_deletion_rule("untracked.py", rule, "/tmp") is False

    def test_git_clean_passes(self):
        rule = {"paths": ["**/*"], "git": "clean", "action": "allow", "reason": "clean"}
        with patch.dict(_GIT_CHECKERS, {"clean": lambda fp, cwd: True}):
            assert _match_deletion_rule("file.py", rule, "/tmp") is True

    def test_git_clean_fails(self):
        rule = {"paths": ["**/*"], "git": "clean", "action": "ask", "reason": "dirty"}
        with patch.dict(_GIT_CHECKERS, {"clean": lambda fp, cwd: False}):
            assert _match_deletion_rule("dirty.py", rule, "/tmp") is False

    def test_git_committed_passes(self):
        rule = {"paths": ["**/*"], "git": "committed", "action": "allow", "reason": "committed"}
        with patch.dict(_GIT_CHECKERS, {"committed": lambda fp, cwd: True}):
            assert _match_deletion_rule("file.py", rule, "/tmp") is True

    def test_git_committed_fails(self):
        rule = {"paths": ["**/*"], "git": "committed", "action": "block", "reason": "no history"}
        with patch.dict(_GIT_CHECKERS, {"committed": lambda fp, cwd: False}):
            assert _match_deletion_rule("file.py", rule, "/tmp") is False

    def test_multiple_globs_any_match(self):
        """Rule matches if ANY glob in paths matches."""
        rule = {"paths": ["build/**", "dist/**"], "action": "allow", "reason": "artifacts"}
        assert _match_deletion_rule("dist/bundle.js", rule, "/tmp") is True

    def test_no_paths_field(self):
        """Rule with empty paths never matches."""
        rule = {"paths": [], "action": "allow", "reason": "empty"}
        assert _match_deletion_rule("anything.txt", rule, "/tmp") is False


# =====================================================================
# decide_deletion (with mocked git)
# =====================================================================

class TestDecideDeletion:
    """Tests for decide_deletion() — full deletion policy evaluation."""

    def test_non_rm_command(self, deletion_config):
        result = decide_deletion("ls -la", deletion_config, "/tmp", None)
        assert result == ("passthrough", None, None)

    def test_empty_config(self):
        result = decide_deletion("rm file.txt", {}, "/tmp", None)
        assert result == ("passthrough", None, None)

    def test_build_artifact_allowed(self, deletion_config):
        """Build artifact file inside build/ directory → approve."""
        result = decide_deletion("rm build/output.js", deletion_config, "/tmp", None)
        assert result[0] == "approve"

    def test_pyc_artifact_allowed(self, deletion_config):
        result = decide_deletion("rm cache.pyc", deletion_config, "/tmp", None)
        assert result[0] == "approve"

    def test_secret_file_blocked(self, deletion_config):
        result = decide_deletion("rm prod.env", deletion_config, "/tmp", None)
        assert result[0] == "block"
        assert "secrets" in result[1].lower()

    def test_pem_file_blocked(self, deletion_config):
        result = decide_deletion("rm server.pem", deletion_config, "/tmp", None)
        assert result[0] == "block"

    def test_unmatched_file_default_action(self, deletion_config):
        """File not matching any rule (git tracked check fails) → default_action (ask)."""
        with patch.dict(_GIT_CHECKERS, {"tracked": lambda fp, cwd: False}):
            result = decide_deletion("rm random.dat", deletion_config, "/tmp", None)
        assert result[0] == "ask"
        assert "default policy" in result[1].lower()

    def test_multi_file_most_restrictive(self, deletion_config):
        """Multi-file rm: most restrictive wins (block > allow)."""
        # build/out.js → allow, prod.env → block → overall block
        result = decide_deletion("rm build/out.js prod.env", deletion_config, "/tmp", None)
        assert result[0] == "block"

    def test_git_tracked_allowed(self, deletion_config):
        """Git-tracked file matches the wildcard rule → approve."""
        with patch.dict(_GIT_CHECKERS, {"tracked": lambda fp, cwd: True}):
            result = decide_deletion("rm src/main.py", deletion_config, "/tmp", None)
        assert result[0] == "approve"

    def test_unparseable_rm_default_action(self, deletion_config):
        """rm with shell metacharacters → unparseable → default action."""
        result = decide_deletion("rm $HOME/file", deletion_config, "/tmp", None)
        assert result[0] == "ask"
        assert "parse" in result[1].lower()

    def test_outside_cwd_default_action(self, deletion_config):
        """Absolute path outside cwd → default action."""
        result = decide_deletion("rm /etc/passwd", deletion_config, "/home/user", None)
        assert result[0] == "ask"
        assert "outside" in result[1].lower()

    def test_project_scoped_rules_first(self, deletion_config_with_projects):
        """Project-scoped rules are checked before global rules."""
        result = decide_deletion(
            "rm tmp/debug.log",
            deletion_config_with_projects,
            "/my/project",
            "/my/project",
        )
        assert result[0] == "approve"

    def test_project_scoped_no_match_falls_to_global(self, deletion_config_with_projects):
        """If project rule doesn't match, global rules are still checked."""
        result = decide_deletion(
            "rm build/out.js",
            deletion_config_with_projects,
            "/my/project",
            "/my/project",
        )
        assert result[0] == "approve"

    def test_default_action_block(self):
        """Config with default_action='block' blocks unmatched files."""
        config = {"default_action": "block", "rules": []}
        result = decide_deletion("rm anything.txt", config, "/tmp", None)
        assert result[0] == "block"

    def test_default_action_allow(self):
        """Config with default_action='allow' allows unmatched files."""
        config = {"default_action": "allow", "rules": []}
        result = decide_deletion("rm anything.txt", config, "/tmp", None)
        assert result[0] == "approve"

    def test_rm_with_flags_file_inside_dir(self, deletion_config):
        """rm -rf of a file inside build/ is allowed."""
        result = decide_deletion("rm -rf build/output.js", deletion_config, "/tmp", None)
        assert result[0] == "approve"

    def test_rm_no_files(self, deletion_config):
        """rm with no files (only flags) → default action."""
        result = decide_deletion("rm -rf", deletion_config, "/tmp", None)
        assert result[0] == "ask"

    def test_multiple_files_all_allowed(self):
        """Multiple files all matching allow rules → approve."""
        config = {
            "default_action": "ask",
            "rules": [{"paths": ["*.pyc", "*.tmp"], "action": "allow", "reason": "temp files"}],
        }
        result = decide_deletion("rm a.pyc b.tmp", config, "/tmp", None)
        assert result[0] == "approve"

    def test_multiple_files_mixed_ask_and_allow(self):
        """One file allowed, one triggers ask → overall ask."""
        config = {
            "default_action": "ask",
            "rules": [{"paths": ["*.pyc"], "action": "allow", "reason": "temp"}],
        }
        result = decide_deletion("rm a.pyc b.txt", config, "/tmp", None)
        assert result[0] == "ask"
