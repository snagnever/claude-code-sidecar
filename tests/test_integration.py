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
