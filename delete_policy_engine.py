"""
delete_policy_engine.py — Deletion policy engine for Claude Code sidecar
================================================================

Evaluates `rm` commands against rules in delete-policy.toml.
Each rule combines glob path patterns, git status conditions, and
per-project overrides to decide: allow, ask, or block.

Called by filter.py as one of three engines (list, risk, deletion).
Returns the same decision tuple format: (decision, reason, updated_input).

RULE FIELDS:
  paths  (required) — list of glob patterns matched against the file path
  action (required) — "allow" | "ask" | "block"
  reason (required) — human-readable explanation (shown on ask/block)
  git    (optional) — "tracked" | "clean" | "committed" | "any"

PROJECT-SCOPED RULES:
  [[projects]] entries apply only when $CLAUDE_PROJECT_DIR matches.
  Project rules are checked before global rules.

MULTI-FILE COMMANDS:
  For `rm file1 file2`, each file is evaluated independently.
  The most restrictive result wins (block > ask > allow).
"""

import fnmatch
import os
import shlex
import subprocess


# Restrictiveness ranking (subset of filter.py's DECISION_RANK,
# kept independent to avoid circular imports)
_ACTION_RANK = {"approve": 1, "ask": 2, "block": 3}


# ---------------------------------------------------------------------------
# rm command parsing
# ---------------------------------------------------------------------------

# Flags that rm accepts (not file paths)
_RM_SHORT_FLAGS = {"-r", "-f", "-i", "-v", "-d", "-R", "-I", "-P", "-W"}
_RM_LONG_FLAGS = {
    "--recursive", "--force", "--verbose", "--dir", "--interactive",
    "--one-file-system", "--no-preserve-root", "--preserve-root",
}


def parse_rm_paths(command: str) -> list[str] | None:
    """Extract file paths from an rm command.

    Returns:
      None       — command is not an rm command
      []         — rm command but paths could not be parsed (subshells, etc.)
      [path,...] — list of file path strings
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    if not tokens or tokens[0] != "rm":
        return None

    paths: list[str] = []
    past_double_dash = False

    for token in tokens[1:]:
        if past_double_dash:
            paths.append(token)
            continue
        if token == "--":
            past_double_dash = True
            continue
        # Skip known flags (including combined short flags like -rf)
        if token.startswith("-") and not past_double_dash:
            # Combined short flags: -rf, -rv, etc.
            if len(token) >= 2 and all(
                f"-{c}" in _RM_SHORT_FLAGS for c in token[1:]
            ):
                continue
            if token in _RM_SHORT_FLAGS or token in _RM_LONG_FLAGS:
                continue
            # Unknown flag — treat as unparseable for safety
            return []
        # Check for shell metacharacters we can't resolve statically
        if any(c in token for c in ("$", "`", "(", ")", "|", ";", "&", "*", "?")):
            return []
        paths.append(token)

    return paths


# ---------------------------------------------------------------------------
# Git status checks
# ---------------------------------------------------------------------------

def _git_check(args: list[str], cwd: str) -> bool:
    """Run a git command and return True if it succeeds (exit code 0)."""
    try:
        result = subprocess.run(
            args, cwd=cwd, capture_output=True,
            timeout=5, check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def git_is_tracked(filepath: str, cwd: str) -> bool:
    """Check if a file is tracked by git (in the index)."""
    return _git_check(["git", "ls-files", "--error-unmatch", "--", filepath], cwd)


def git_is_clean(filepath: str, cwd: str) -> bool:
    """Check if a tracked file has no uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", filepath],
            cwd=cwd, capture_output=True,
            timeout=5, text=True, check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def git_is_committed(filepath: str, cwd: str) -> bool:
    """Check if a file has at least one commit in history."""
    return _git_check(["git", "log", "--oneline", "-1", "--", filepath], cwd)


_GIT_CHECKERS = {
    "tracked": git_is_tracked,
    "clean": git_is_clean,
    "committed": git_is_committed,
}


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------

def _glob_match(filepath: str, pattern: str) -> bool:
    """Match a filepath against a glob pattern.

    Handles the edge case where '**/*' should also match root-level files
    (fnmatch requires a directory separator for '**/*').
    """
    if fnmatch.fnmatch(filepath, pattern):
        return True
    # '**/' prefix: also try matching without it (for root-level files)
    if pattern.startswith("**/"):
        return fnmatch.fnmatch(filepath, pattern[3:])
    return False


def _match_deletion_rule(filepath: str, rule: dict, cwd: str) -> bool:
    """Check if a file path matches a single deletion rule."""
    # Check path globs
    paths = rule.get("paths", [])
    if not any(_glob_match(filepath, pat) for pat in paths):
        return False
    # Check git condition (if specified)
    git_cond = rule.get("git", "any")
    if git_cond != "any":
        checker = _GIT_CHECKERS.get(git_cond)
        if checker and not checker(filepath, cwd):
            return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def decide_deletion(
    command: str, deletion_config: dict, cwd: str,
    project_dir: str | None,
) -> tuple[str, str | None, dict | None]:
    """Evaluate an rm command against deletion policy rules.

    Args:
      command:         the bash command string
      deletion_config: parsed delete-policy.toml dict
      cwd:             working directory for path resolution and git checks
      project_dir:     $CLAUDE_PROJECT_DIR or None

    Returns a decision tuple:
      ("approve",     reason, None) — auto-allow
      ("ask",         reason, None) — prompt user
      ("block",       reason, None) — deny
      ("passthrough", None,   None) — not an rm command (no opinion)
    """
    if not deletion_config:
        return ("passthrough", None, None)

    paths = parse_rm_paths(command)
    if paths is None:
        return ("passthrough", None, None)

    # Map user-facing action names to internal decision types
    action_map = {"allow": "approve", "ask": "ask", "block": "block"}
    raw_default = deletion_config.get("default_action", "ask")
    default_action = action_map.get(raw_default, "ask")

    if not paths:
        # Unparseable rm command — apply default action
        return (default_action, "Could not parse rm targets — applying default policy", None)

    # Build rule list: project-scoped rules first, then global rules
    rules: list[dict] = []
    if project_dir:
        for project in deletion_config.get("projects", []):
            if project.get("project") == project_dir:
                rules.extend(project.get("rules", []))
                break
    rules.extend(deletion_config.get("rules", []))

    # Evaluate each file path; aggregate with most-restrictive-wins
    overall_action = "approve"
    overall_reason = None

    for filepath in paths:
        # Normalize path relative to cwd
        if os.path.isabs(filepath):
            try:
                filepath = os.path.relpath(filepath, cwd)
            except ValueError:
                pass  # different drives on Windows — keep as-is
        else:
            filepath = os.path.relpath(
                os.path.normpath(os.path.join(cwd, filepath)), cwd
            )

        # Paths outside the working directory — apply default action
        if filepath.startswith("..") or os.path.isabs(filepath):
            file_action = default_action
            file_reason = (
                f"Path '{filepath}' is outside working directory"
                " — default policy"
            )
        else:
            # Find first matching rule
            file_action = default_action
            file_reason = (
                f"No deletion rule matched '{filepath}'"
                " — default policy"
            )
            for rule in rules:
                if _match_deletion_rule(filepath, rule, cwd):
                    raw_action = rule.get("action", raw_default)
                    file_action = action_map.get(raw_action, "ask")
                    file_reason = rule.get(
                        "reason",
                        f"Deletion rule matched '{filepath}'",
                    )
                    break

        # Aggregate: most restrictive wins
        file_rank = _ACTION_RANK.get(file_action, 0)
        overall_rank = _ACTION_RANK.get(overall_action, 0)
        if file_rank > overall_rank:
            overall_action = file_action
            overall_reason = file_reason

    return (overall_action, overall_reason, None)
