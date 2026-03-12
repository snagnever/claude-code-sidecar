# Agent Installation Guide

Step-by-step instructions for an AI agent to install and configure claude-code-sidecar.

## Prerequisites

1. Verify Python 3.11+ is available:
   ```bash
   python3 --version
   ```
   Must output `Python 3.11` or higher (required for `tomllib`).

2. Verify the Claude Code config directory exists:
   ```bash
   ls ~/.claude/
   ```
   If it does not exist, Claude Code has not been initialized. Run `claude` first.

## Install

1. **Download the repository.** You must clone the project from GitHub before running the install script:
   ```bash
   git clone https://github.com/snagnever/claude-code-sidecar.git /tmp/claude-code-sidecar
   cd /tmp/claude-code-sidecar
   ```

2. Run the install script:
   ```bash
   ./install.sh
   ```

   This performs three actions:
   - Copies `filter.py`, `delete_policy_engine.py`, and config files (`settings.toml`, `commands-risks.toml`, `permissions.toml`, `delete-policy.toml`) to `~/.claude/claude-code-sidecar/`
   - Makes `filter.py` and `delete_policy_engine.py` executable
   - Adds the hook entry to `~/.claude/settings.json` under `hooks.PreToolUse`

## Verify Installation

1. Check that files exist:
   ```bash
   ls -la ~/.claude/claude-code-sidecar/
   ```

2. Check that the hook is registered in settings.json:
   ```bash
   python3 -c "import json; s=json.load(open('$HOME/.claude/settings.json')); print(json.dumps(s.get('hooks',{}), indent=2))"
   ```
   Should show a `PreToolUse` entry with `filter.py`.

3. Test each decision type by piping JSON through the hook:

   **Block (deny):**
   ```bash
   echo '{"tool_input":{"command":"rm -rf /"}}' | python3 ~/.claude/claude-code-sidecar/filter.py
   ```
   Expected: JSON output with `"permissionDecision": "deny"`.

   **Alter (rewrite + allow):**
   ```bash
   echo '{"tool_input":{"command":"rsync src/ dest/"}}' | python3 ~/.claude/claude-code-sidecar/filter.py
   ```
   Expected: JSON output with `"permissionDecision": "allow"` and `"updatedInput"` containing `"rsync --dry-run src/ dest/"`.

   **Ask (escalate to user):**
   ```bash
   echo '{"tool_input":{"command":"npm install express"}}' | python3 ~/.claude/claude-code-sidecar/filter.py
   ```
   Expected: JSON output with `"permissionDecision": "ask"`.

   **Allow (auto-approve):**
   ```bash
   echo '{"tool_input":{"command":"git status"}}' | python3 ~/.claude/claude-code-sidecar/filter.py
   ```
   Expected: JSON output with `"permissionDecision": "allow"` and empty reason.

   **Passthrough (no match):**
   ```bash
   echo '{"tool_input":{"command":"some-unknown-command"}}' | python3 ~/.claude/claude-code-sidecar/filter.py
   ```
   Expected: No output (empty stdout), exit code 0.

## Migrate Existing Permissions from settings.json

After installation, check whether `~/.claude/settings.json` already contains `permissions.allow` or `permissions.ask` entries with `Bash(...)` rules. If it does, **ask the user** if they'd like to convert those rules into `permissions.toml` format.

### How to check

```bash
python3 -c "
import json, pathlib
s = json.loads(pathlib.Path.home().joinpath('.claude/settings.json').read_text())
perms = s.get('permissions', {})
bash_allow = [r for r in perms.get('allow', []) if r.startswith('Bash(')]
bash_ask   = [r for r in perms.get('ask', [])   if r.startswith('Bash(')]
print(f'Allow rules: {len(bash_allow)}')
print(f'Ask rules:   {len(bash_ask)}')
for r in bash_allow: print(f'  allow: {r}')
for r in bash_ask:   print(f'  ask:   {r}')
"
```

If any rules exist, show them to the user and ask:

> "Your `settings.json` already has **N** Bash permission rules. Would you like me to convert them into `permissions.toml` rules? This moves your permissions into the hook's config, which supports regex matching, alter/rewrite rules, blocklist priority, and TOML comments."

### Conversion mapping

| settings.json pattern | permissions.toml equivalent |
|---|---|
| `Bash(cmd*)` in `permissions.allow` | `[[bash.allowlist]]` with `pattern = '(?s:cmd.*)\Z'`, `match = "match"` |
| `Bash(cmd *arg*)` in `permissions.allow` | `[[bash.allowlist]]` with `pattern = '(?s:cmd\ arg.*)\Z'`, `match = "match"` |
| `Bash(cmd*\|*subcmd*)` in `permissions.allow` | `[[bash.allowlist]]` with `pattern = '(?s:cmd(?>.*?\|)(?>.*?subcmd).*)\Z'`, `match = "match"` |
| `Bash(cmd*)` in `permissions.ask` | `[[bash.asklist]]` with `pattern = '\bcmd\b'`, `match = "search"` |

Key differences to explain to the user:
- **settings.json** uses glob patterns (`*` = anything); **permissions.toml** uses regex
- **permissions.toml** adds blocklist (deny) and alterlist (rewrite) tiers that settings.json cannot express
- Rules in `permissions.toml` are evaluated by the hook _before_ Claude Code's built-in permission check
- After migration, the converted `Bash(...)` entries can be removed from `settings.json` since the hook now handles them

### After conversion

Once the user confirms, generate the equivalent `[[bash.allowlist]]` and `[[bash.asklist]]` entries, append them to `~/.claude/claude-code-sidecar/permissions.toml`, and offer to remove the migrated `Bash(...)` entries from `settings.json`.

### Simplify converted rules

After generating the 1:1 conversion, **suggest consolidating** related rules into fewer, grouped entries. Since `permissions.toml` supports full regex, many individual glob rules can be merged.

Example — these individual settings.json entries:

```
Bash(git diff*)
Bash(git log*)
Bash(git status*)
Bash(git branch*)
Bash(git checkout*)
Bash(git fetch*)
Bash(git show*)
```

Become a single allowlist rule:

```toml
[[bash.allowlist]]
pattern = '(?s:git\ (diff|log|status|branch|checkout|fetch|show).*)\Z'
reason  = "Read-only git operations"
match   = "match"
```

Similarly:

```
Bash(poetry run poe*)
Bash(poetry run ruff*)
Bash(poetry run mypy*)
Bash(poetry run pytest*)
```

Becomes:

```toml
[[bash.allowlist]]
pattern = '(?s:poetry\ run\ (poe|ruff|mypy|pytest).*)\Z'
reason  = "Backend tooling"
match   = "match"
```

Ask the user:

> "I can also simplify the converted rules by grouping related commands into single regex patterns. This makes `permissions.toml` easier to read and maintain. Would you like me to do that?"

## Customize Permissions

Edit `~/.claude/claude-code-sidecar/permissions.toml` to add or modify rules.

### Add a blocklist rule

```toml
[[bash.blocklist]]
pattern = '\bdangerous-command\b'
reason  = "This command is blocked for safety"
match   = "search"
```

### Add an alterlist rule

```toml
[[bash.alterlist]]
pattern         = '\bmy-command\b(?!.*--safe)'
sub_pattern     = '\bmy-command\b'
sub_replacement = "my-command --safe"
reason          = "Added --safe flag"
match           = "search"
```

### Add an asklist rule

```toml
[[bash.asklist]]
pattern = '\brisky-command\b'
reason  = "This command needs user confirmation"
match   = "search"
```

### Add an allowlist rule

```toml
[[bash.allowlist]]
pattern = '(?s:safe-command.*)\Z'
reason  = "Known safe command"
match   = "match"
```

## Troubleshooting

### Hook not firing
- Check `~/.claude/settings.json` has the `hooks.PreToolUse` entry with matcher `"Bash"`
- Run `claude --debug` to see hook execution in logs
- Verify the command path: `python3 ~/.claude/claude-code-sidecar/filter.py`

### Config parse error
- Validate TOML syntax: `python3 -c "import tomllib; tomllib.load(open('$HOME/.claude/claude-code-sidecar/permissions.toml','rb'))"`
- If the config is broken, the hook fails open (passthrough) — it won't block Claude Code

### Permission denied
- Ensure scripts are executable: `chmod +x ~/.claude/claude-code-sidecar/filter.py ~/.claude/claude-code-sidecar/delete_policy_engine.py`

### Python version too old
- The script requires Python 3.11+ for `tomllib` (stdlib)
- Check with: `python3 --version`

## Uninstall

```bash
cd /tmp/claude-code-sidecar  # or wherever the project is
./uninstall.sh                      # removes hook and config
./uninstall.sh --keep-config        # removes hook, keeps permissions.toml
```

This removes the hook entry from `~/.claude/settings.json` and deletes the files from `~/.claude/claude-code-sidecar/`.
