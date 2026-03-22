"""End-to-end integration tests for filter.py main() — stdin JSON → stdout JSON."""

import io
import json
import os
import sys
from unittest.mock import patch

import pytest

from filter import main


def run_main(input_data: dict, config_override=None, env_vars=None) -> tuple[str, str, int]:
    """Run main() with given input, capture stdout/stderr and exit code.

    Returns (stdout, stderr, exit_code).
    """
    stdin_text = json.dumps(input_data)
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    exit_code = 0

    try:
        with patch("sys.stdin", io.StringIO(stdin_text)), \
             patch("sys.stdout", captured_out), \
             patch("sys.stderr", captured_err):
            if config_override is not None:
                with patch("filter.load_config", return_value=config_override):
                    main()
            else:
                main()
    except SystemExit as e:
        exit_code = e.code if e.code is not None else 0

    return captured_out.getvalue(), captured_err.getvalue(), exit_code


class TestMainBlock:
    """Tests for block decisions via main()."""

    def test_block_outputs_deny(self):
        config = {
            "mode": "lists",
            "risk": {},
            "deletion_enabled": False,
            "deletion": {},
            "bash": {
                "blocklist": [{"pattern": r"\bsudo\b", "reason": "No sudo"}],
            },
        }
        stdout, _, _ = run_main({"tool_input": {"command": "sudo rm"}}, config)
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "No sudo" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_block_reason_includes_show_command(self):
        config = {
            "mode": "lists",
            "risk": {},
            "deletion_enabled": False,
            "deletion": {},
            "bash": {
                "blocklist": [{"pattern": r"test", "reason": "Blocked"}],
            },
        }
        stdout, _, _ = run_main({"tool_input": {"command": "test"}}, config)
        output = json.loads(stdout)
        assert "Show the users the full command" in output["hookSpecificOutput"]["permissionDecisionReason"]


class TestMainAlter:
    """Tests for alter decisions via main()."""

    def test_alter_outputs_allow_with_updated_input(self):
        config = {
            "mode": "lists",
            "risk": {},
            "deletion_enabled": False,
            "deletion": {},
            "bash": {
                "alterlist": [{
                    "pattern": r"\brsync\b(?!.*--dry-run)",
                    "sub_pattern": r"\brsync\b",
                    "sub_replacement": "rsync --dry-run",
                    "reason": "Safety",
                }],
            },
        }
        stdout, _, _ = run_main({"tool_input": {"command": "rsync src/ dest/"}}, config)
        output = json.loads(stdout)
        hook = output["hookSpecificOutput"]
        assert hook["permissionDecision"] == "allow"
        assert hook["updatedInput"]["command"] == "rsync --dry-run src/ dest/"


class TestMainAsk:
    """Tests for ask decisions via main()."""

    def test_ask_outputs_ask(self):
        config = {
            "mode": "lists",
            "risk": {},
            "deletion_enabled": False,
            "deletion": {},
            "bash": {
                "asklist": [{"pattern": r"\brm\b", "reason": "Confirm deletion"}],
            },
        }
        stdout, _, _ = run_main({"tool_input": {"command": "rm file.txt"}}, config)
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "ask"


class TestMainApprove:
    """Tests for approve decisions via main()."""

    def test_approve_outputs_allow(self):
        config = {
            "mode": "lists",
            "risk": {},
            "deletion_enabled": False,
            "deletion": {},
            "bash": {
                "allowlist": [{"pattern": r"\bls\b", "reason": "Safe"}],
            },
        }
        stdout, _, _ = run_main({"tool_input": {"command": "ls -la"}}, config)
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert output["hookSpecificOutput"]["permissionDecisionReason"] == ""


class TestMainPassthrough:
    """Tests for passthrough (no output, exit 0)."""

    def test_passthrough_exits_zero(self):
        config = {
            "mode": "lists",
            "risk": {},
            "deletion_enabled": False,
            "deletion": {},
            "bash": {},
        }
        stdout, _, exit_code = run_main({"tool_input": {"command": "unknown"}}, config)
        assert stdout == ""
        assert exit_code == 0


class TestMainErrorHandling:
    """Tests for error handling in main()."""

    def test_malformed_json_exits_zero(self):
        """Malformed JSON input → exit 0 (passthrough)."""
        captured_out = io.StringIO()
        exit_code = 0

        try:
            with patch("sys.stdin", io.StringIO("not json")), \
                 patch("sys.stdout", captured_out):
                main()
        except SystemExit as e:
            exit_code = e.code if e.code is not None else 0

        assert exit_code == 0
        assert captured_out.getvalue() == ""

    def test_missing_command_field(self):
        """Missing command field → empty string → passthrough."""
        config = {
            "mode": "lists",
            "risk": {},
            "deletion_enabled": False,
            "deletion": {},
            "bash": {},
        }
        stdout, _, exit_code = run_main({"tool_input": {}}, config)
        assert stdout == ""
        assert exit_code == 0

    def test_missing_tool_input(self):
        """Missing tool_input field → empty command → passthrough."""
        config = {
            "mode": "lists",
            "risk": {},
            "deletion_enabled": False,
            "deletion": {},
            "bash": {},
        }
        stdout, _, exit_code = run_main({}, config)
        assert stdout == ""
        assert exit_code == 0


class TestMainModes:
    """Tests for mode-specific behavior through main()."""

    def test_risk_mode_block(self):
        config = {
            "mode": "risk",
            "risk": {"allow": [0], "ask": [1], "block": [2, 3, 4], "block_above": 4},
            "deletion_enabled": False,
            "deletion": {},
            "bash": {
                "risk": [{"pattern": r"\bsudo\b", "risk": 4, "reason": "Privilege escalation"}],
            },
        }
        stdout, _, _ = run_main({"tool_input": {"command": "sudo cmd"}}, config)
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_both_mode_most_restrictive(self):
        config = {
            "mode": "both",
            "risk": {"allow": [0, 1], "ask": [2], "block": [3, 4], "block_above": 4},
            "deletion_enabled": False,
            "deletion": {},
            "bash": {
                "allowlist": [{"pattern": r"\bls\b", "reason": "Safe"}],
                "risk": [{"command": "ls", "risk": 0, "reason": "Safe read"}],
            },
        }
        stdout, _, _ = run_main({"tool_input": {"command": "ls -la"}}, config)
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"


# =====================================================================
# Tool engine integration tests (non-Bash tools and MCP calls)
# =====================================================================

TOOL_CONFIG = {
    "mode": "lists",
    "risk": {},
    "deletion_enabled": False,
    "deletion": {},
    "tool_engine_enabled": True,
    "bash": {},
    "tool": {
        "blocklist": [
            {
                "tools": ["Write", "Edit"],
                "reason": "Cannot modify secrets",
                "fields": {"file_path": r"\.(env|pem|key)$"},
            },
            {
                "tools": ["mcp__plugin_dangerous-server_.*"],
                "reason": "This MCP server is blocked",
            },
        ],
        "asklist": [
            {
                "tools": ["Write"],
                "reason": "Confirm CI/CD change",
                "fields": {"file_path": r"\.github/workflows/"},
            },
        ],
        "allowlist": [
            {
                "tools": ["Read", "Grep", "Glob"],
                "reason": "Read-only tools are safe",
            },
            {
                "tools": ["mcp__plugin_context7_context7__.*"],
                "reason": "Docs lookups safe",
            },
        ],
    },
}


class TestToolEngineBlock:
    """Tool engine block decisions via main()."""

    def test_block_write_to_secrets(self):
        data = {"tool_name": "Write", "tool_input": {"file_path": "app/.env", "content": "SECRET=x"}}
        stdout, _, _ = run_main(data, TOOL_CONFIG)
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "secrets" in output["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_block_reason_includes_tool_name(self):
        data = {"tool_name": "Edit", "tool_input": {"file_path": "key.pem"}}
        stdout, _, _ = run_main(data, TOOL_CONFIG)
        output = json.loads(stdout)
        assert "Edit" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_block_mcp_server(self):
        data = {"tool_name": "mcp__plugin_dangerous-server_do-evil", "tool_input": {"arg": "val"}}
        stdout, _, _ = run_main(data, TOOL_CONFIG)
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestToolEngineAsk:
    """Tool engine ask decisions via main()."""

    def test_ask_write_ci_config(self):
        data = {"tool_name": "Write", "tool_input": {"file_path": ".github/workflows/ci.yml"}}
        stdout, _, _ = run_main(data, TOOL_CONFIG)
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "ask"


class TestToolEngineAllow:
    """Tool engine allow decisions via main()."""

    def test_allow_read(self):
        data = {"tool_name": "Read", "tool_input": {"file_path": "/any/file.py"}}
        stdout, _, _ = run_main(data, TOOL_CONFIG)
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert output["hookSpecificOutput"]["permissionDecisionReason"] == ""

    def test_allow_mcp_docs(self):
        data = {"tool_name": "mcp__plugin_context7_context7__query-docs", "tool_input": {"q": "react"}}
        stdout, _, _ = run_main(data, TOOL_CONFIG)
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"


class TestToolEnginePassthrough:
    """Tool engine passthrough (no rule matches)."""

    def test_passthrough_unknown_tool(self):
        data = {"tool_name": "Write", "tool_input": {"file_path": "app/main.py"}}
        stdout, _, exit_code = run_main(data, TOOL_CONFIG)
        assert stdout == ""
        assert exit_code == 0


class TestToolEngineDisabled:
    """Tool engine disabled via settings."""

    def test_disabled_passes_through(self):
        config = dict(TOOL_CONFIG)
        config["tool_engine_enabled"] = False
        data = {"tool_name": "Write", "tool_input": {"file_path": "app/.env"}}
        stdout, _, exit_code = run_main(data, config)
        assert stdout == ""
        assert exit_code == 0


class TestToolEngineRouting:
    """Verify main() routes Bash vs non-Bash correctly."""

    def test_bash_still_uses_bash_engine(self):
        """Bash tool calls go through the bash engine, not the tool engine."""
        config = {
            "mode": "lists",
            "risk": {},
            "deletion_enabled": False,
            "deletion": {},
            "tool_engine_enabled": True,
            "bash": {
                "blocklist": [{"pattern": r"\bsudo\b", "reason": "No sudo"}],
            },
            "tool": {},
        }
        data = {"tool_name": "Bash", "tool_input": {"command": "sudo rm -rf /"}}
        stdout, _, _ = run_main(data, config)
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_shell_uses_bash_engine(self):
        """Shell tool calls (Cursor compat) also use the bash engine."""
        config = {
            "mode": "lists",
            "risk": {},
            "deletion_enabled": False,
            "deletion": {},
            "tool_engine_enabled": True,
            "bash": {
                "allowlist": [{"pattern": r"\bls\b", "reason": "Safe"}],
            },
            "tool": {},
        }
        data = {"tool_name": "Shell", "tool_input": {"command": "ls -la"}}
        stdout, _, _ = run_main(data, config)
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "allow"
