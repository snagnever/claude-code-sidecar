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

2. Run the install script. Choose **account-wide** or **project-level**:

   **Account-wide** (applies to all projects):
   ```bash
   ./install.sh
   ```

   **Project-level** (applies only to a specific project):
   ```bash
   ./install.sh --project /path/to/your/project
   # or from the project directory:
   cd /path/to/your/project && /tmp/claude-code-sidecar/install.sh --project
   ```

   This performs three actions:
   - Copies `filter.py`, `delete_policy_engine.py`, and config files (`settings.toml`, `commands-risks.toml`, `permissions.toml`, `delete-policy.toml`) to the sidecar directory
   - Makes `filter.py` and `delete_policy_engine.py` executable
   - Adds or updates the hook entry in `settings.json` under `hooks.PreToolUse` with **`"matcher": ".*"`** (so Bash, Read/Write, MCP, and other tools are intercepted — an older `"Bash"` matcher is upgraded on re-run)

   The installed config files now support named permission profiles. Profiles live inline in those same TOML files and can either layer on top of the default template or start from a clean baseline.

   For project-level installs, the skill file is also installed to `<project>/.claude/skills/`.

   | Mode | Sidecar directory | Settings file |
   |------|-------------------|---------------|
   | Account-wide | `~/.claude/claude-code-sidecar/` | `~/.claude/settings.json` |
   | Project-level | `<project>/.claude/claude-code-sidecar/` | `<project>/.claude/settings.json` |

## Verify Installation

1. Check that files exist (adjust path for project-level):
   ```bash
   # Account-wide:
   ls -la ~/.claude/claude-code-sidecar/
   # Project-level:
   ls -la /path/to/project/.claude/claude-code-sidecar/
   ```

2. Check that the hook is registered in settings.json and the matcher is broad enough for MCP/tools:
   ```bash
   # Account-wide:
   python3 -c "import json; s=json.load(open('$HOME/.claude/settings.json')); print(json.dumps(s.get('hooks',{}), indent=2))"
   # Project-level:
   python3 -c "import json; s=json.load(open('/path/to/project/.claude/settings.json')); print(json.dumps(s.get('hooks',{}), indent=2))"
   ```
   Should show a `PreToolUse` entry with `filter.py` and **`"matcher": ".*"`** (not only `"Bash"`). If the matcher is Bash-only, `permissions.toml` `[[tool.*]]` rules never run for MCP.

3. Test each decision type by piping JSON through the hook (use the correct path):

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

   **Tool engine / MCP (optional, default `permissions.toml`):**
   ```bash
   echo '{"tool_name":"mcp__example-server__browser_navigate","tool_input":{}}' | python3 ~/.claude/claude-code-sidecar/filter.py
   ```
   Expected: JSON with `"permissionDecision": "allow"` if your `permissions.toml` allowlists `mcp__.*__browser_.*` (as in the repo default). If you get empty stdout, confirm `[tool_engine] enabled = true` in sidecar `settings.toml` and that the hook’s PreToolUse matcher is `".*"` (see troubleshooting).

4. Verify profile activation works:

   **Persistent profile via `settings.toml`:**
   ```toml
   active_profile = "strict"

   [profiles.strict]
   base = "default"
   ```

   **Per-call override via hook payload:**
   ```bash
   echo '{"tool_input":{"command":"git status"},"permission_profile":"strict"}' | python3 ~/.claude/claude-code-sidecar/filter.py
   ```

   **Unknown profile should fail closed:**
   ```bash
   echo '{"tool_input":{"command":"git status"},"permission_profile":"does-not-exist"}' | python3 ~/.claude/claude-code-sidecar/filter.py
   ```
   Expected: JSON output with `"permissionDecision": "deny"` and a reason naming the unknown profile.

## Permission Profiles

Profiles define named variants of the installed permission set. They are not separate files; they live inside the existing TOML config files.

- `settings.toml`
  - `active_profile = "<name>"` enables a profile by default
  - `[profiles.<name>]` defines profile metadata and its baseline
- Hook payload
  - top-level `permission_profile` selects a profile for one call only
- Precedence
  - `permission_profile` overrides `active_profile`
- Baselines
  - `base = "default"` layers profile settings/rules ahead of the top-level template
  - `base = "clean"` starts from an empty baseline

Example:

```toml
# settings.toml
active_profile = "strict"

[profiles.strict]
base = "default"
description = "Stricter prompts for risky sessions"

[profiles.strict.risk]
allow       = [0]
ask         = [1, 2]
block       = [3, 4]
block_above = 4

# permissions.toml
[[profiles.strict.bash.asklist]]
pattern = '\bgit\s+push\b'
reason  = "Always confirm pushes in strict mode"

[[profiles.strict.tool.blocklist]]
tools  = ["Write"]
reason = "No writes in strict mode"

# commands-risks.toml
[[profiles.strict.bash.risk]]
command = "rm"
risk    = 3
reason  = "Treat rm as high risk in strict mode"

# delete-policy.toml
[profiles.strict]
default_action = "block"
```

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

Edit `settings.toml`, `commands-risks.toml`, and `delete-policy.toml` when you need profile settings, profile-specific risk mappings, or profile-specific deletion rules.

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

### Add a profile-specific rule

```toml
[[profiles.strict.bash.asklist]]
pattern = '\bgit\s+push\b'
reason  = "Always confirm pushes in strict mode"
match   = "search"
```

### Add a clean profile

```toml
# settings.toml
[profiles.lockdown]
base = "clean"

[profiles.lockdown.tool_engine]
enabled = true

# permissions.toml
[[profiles.lockdown.tool.allowlist]]
tools  = ["Read", "Grep", "Glob"]
reason = "Read-only tools only"
```

## Troubleshooting

### Hook not firing
- Check the relevant `settings.json` has the `hooks.PreToolUse` entry:
  - Account-wide: `~/.claude/settings.json`
  - Project-level: `<project>/.claude/settings.json`
- **Matcher must include MCP and other tools:** use `"."` or `".*"` so the hook runs for non-Bash tools. If the matcher is only `"Bash"`, Read/Write/MCP calls never reach `filter.py` — `permissions.toml` `[[tool.*]]` rules will not apply.
- Run `claude --debug` to see hook execution in logs
- Verify the command path resolves correctly

### Config parse error
- Validate TOML syntax for each file:
  - `python3 -c "import tomllib; tomllib.load(open('<sidecar-dir>/settings.toml','rb'))"`
  - `python3 -c "import tomllib; tomllib.load(open('<sidecar-dir>/permissions.toml','rb'))"`
  - `python3 -c "import tomllib; tomllib.load(open('<sidecar-dir>/commands-risks.toml','rb'))"`
  - `python3 -c "import tomllib; tomllib.load(open('<sidecar-dir>/delete-policy.toml','rb'))"`
- If the config is broken, the hook fails open (passthrough) — it won't block Claude Code

### Profile not taking effect
- Check `active_profile` in `settings.toml`
- Check whether the hook payload includes `permission_profile`
- Confirm the profile name exists under `profiles.<name>` in the relevant TOML files
- Unknown profile names are denied intentionally; verify the exact spelling

### Permission denied
- Ensure scripts are executable: `chmod +x <sidecar-dir>/filter.py <sidecar-dir>/delete_policy_engine.py`

### Python version too old
- The script requires Python 3.11+ for `tomllib` (stdlib)
- Check with: `python3 --version`

## Uninstall

```bash
cd /tmp/claude-code-sidecar  # or wherever the repo is

# Account-wide:
./uninstall.sh                                # removes hook and config
./uninstall.sh --keep-config                  # removes hook, keeps config files

# Project-level:
./uninstall.sh --project /path/to/project     # removes hook, config, and skill
./uninstall.sh --project --keep-config        # removes hook and skill, keeps config
```

This removes the hook entry from `settings.json` and deletes the sidecar files.
