"""Shared fixtures for the claude-permission-control test suite."""

import os
import sys
import textwrap

import pytest

# Add project root to path so we can import the modules under test
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_config():
    """Bare config with all keys present but empty rule lists."""
    return {
        "mode": "lists",
        "risk": {},
        "bash": {},
        "deletion": {},
        "deletion_enabled": False,
    }


@pytest.fixture
def lists_config():
    """Config with sample block/alter/ask/allow rules, mode='lists'."""
    return {
        "mode": "lists",
        "risk": {},
        "deletion_enabled": False,
        "deletion": {},
        "bash": {
            "blocklist": [
                {"pattern": r"\bsudo\b", "reason": "No sudo allowed"},
                {"pattern": r"rm\s+-rf\s+/", "reason": "Dangerous recursive delete"},
            ],
            "alterlist": [
                {
                    "pattern": r"\brsync\b(?!.*--dry-run)",
                    "sub_pattern": r"\brsync\b",
                    "sub_replacement": "rsync --dry-run",
                    "reason": "Safety: add --dry-run",
                },
            ],
            "asklist": [
                {"pattern": r"\brm\b", "reason": "Confirm file deletion"},
            ],
            "allowlist": [
                {"pattern": r"(?s:git\ (diff|log|status).*)\Z", "reason": "Safe git read ops", "match": "match"},
                {"pattern": r"\bls\b", "reason": "Directory listing"},
            ],
        },
    }


@pytest.fixture
def risk_config():
    """Config with sample risk rules + thresholds, mode='risk'."""
    return {
        "mode": "risk",
        "risk": {
            "allow": [0, 1],
            "ask": [2],
            "block": [3, 4],
            "block_above": 4,
        },
        "deletion_enabled": False,
        "deletion": {},
        "bash": {
            "risk": [
                {"command": "ls", "risk": 0, "reason": "Safe read-only"},
                {"command": "cat", "risk": 0, "reason": "Safe read-only"},
                {"command": "rm", "risk": 2, "reason": "File deletion"},
                {"command": "git commit", "risk": 2, "reason": "Modifies repo"},
                {"pattern": r"rm\s+-rf", "risk": 4, "reason": "Recursive force delete"},
                {"pattern": r"\bsudo\b", "risk": 4, "reason": "Privilege escalation"},
                {"command": "git push", "risk": 2, "reason": "Pushes to remote"},
                {"pattern": r"git\s+push\s+--force", "risk": 3, "reason": "Force push"},
            ],
        },
    }


@pytest.fixture
def both_config(lists_config, risk_config):
    """Combined config with mode='both'."""
    return {
        "mode": "both",
        "risk": risk_config["risk"],
        "deletion_enabled": False,
        "deletion": {},
        "bash": {**lists_config["bash"], "risk": risk_config["bash"]["risk"]},
    }


@pytest.fixture
def deletion_config():
    """Sample delete-policy.toml dict."""
    return {
        "version": 1,
        "default_action": "ask",
        "rules": [
            {
                "paths": ["build/**", "dist/**", "__pycache__/**", "*.pyc"],
                "action": "allow",
                "reason": "Build artifacts",
            },
            {
                "paths": ["*.env", "*.pem", "*.key"],
                "action": "block",
                "reason": "Never delete secrets",
            },
            {
                "paths": ["**/*"],
                "git": "tracked",
                "action": "allow",
                "reason": "Git-tracked files are recoverable",
            },
        ],
    }


@pytest.fixture
def deletion_config_with_projects(deletion_config):
    """Deletion config with project-scoped rules."""
    config = dict(deletion_config)
    config["projects"] = [
        {
            "project": "/my/project",
            "rules": [
                {
                    "paths": ["tmp/**", "logs/**"],
                    "action": "allow",
                    "reason": "Project temp files",
                },
            ],
        },
    ]
    return config


# ---------------------------------------------------------------------------
# Temp config dir for manage_rules tests
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Create a temp directory with minimal TOML config files for manage_rules tests."""
    permissions = tmp_path / "permissions.toml"
    permissions.write_text(textwrap.dedent("""\
        [[bash.blocklist]]
        pattern = '\\\\bsudo\\\\b'
        reason  = "No sudo"

        [[bash.allowlist]]
        pattern = '\\\\bls\\\\b'
        reason  = "Safe listing"
    """))

    risks = tmp_path / "commands-risks.toml"
    risks.write_text(textwrap.dedent("""\
        [[bash.risk]]
        command = "ls"
        risk    = 0
        reason  = "Safe read-only"
    """))

    # Patch manage_rules to use our temp directory
    monkeypatch.setattr(
        "manage_rules.config_path_for",
        lambda list_name: str(
            tmp_path / ("commands-risks.toml" if list_name == "risk" else "permissions.toml")
        ),
    )

    return tmp_path
