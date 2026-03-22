# Claude Code Sidecar

A PreToolUse hook for Claude Code that intercepts tool calls and applies permission rules. Supports four permission engines — **list-based** (block/allow/ask/alter for bash), **risk-level** (numeric 0–4 for bash), **deletion policy** (file-aware `rm` control), and **tool engine** (rules for non-Bash tools and MCP calls) — individually or combined.

## How It Works

Every tool call Claude makes passes through `filter.py`, which loads rules from four config files and evaluates the call using the appropriate engines. Bash/Shell commands use the list-based, risk-level, and deletion engines. All other tools (Read, Write, Edit, Grep, Glob) and MCP calls use the tool engine.

### List-Based Engine (`permissions.toml`)

```
Command
  │
  ├─ BLOCKLIST match?  ──→  DENY  (command blocked, reason shown to Claude)
  │
  ├─ ALTERLIST match?  ──→  REWRITE + ALLOW  (command modified, runs without prompting)
  │
  ├─ ASKLIST match?    ──→  ASK  (user prompted to confirm)
  │
  ├─ ALLOWLIST match?  ──→  ALLOW  (runs without prompting)
  │
  └─ no match          ──→  PASSTHROUGH  (Claude Code's default permission flow)
```

### Risk-Level Engine (`commands-risks.toml`)

```
Command
  │
  ├─ Matched rule with highest risk level
  │    │
  │    ├─ risk in allow list   ──→  ALLOW
  │    ├─ risk in ask list     ──→  ASK
  │    ├─ risk in block list   ──→  DENY
  │    ├─ risk > block_above   ──→  DENY
  │    └─ not mapped           ──→  ASK (safe default)
  │
  └─ no match  ──→  PASSTHROUGH
```

### Deletion Engine (`delete-policy.toml`)

```
rm command
  │
  ├─ Parse file paths from command
  │    │
  │    ├─ Project-scoped rule match?  ──→  use project rule action
  │    │
  │    ├─ Global rule match?          ──→  use global rule action
  │    │
  │    └─ no match                    ──→  default_action (ask/block/allow)
  │
  └─ not an rm command  ──→  PASSTHROUGH
```

Rules combine glob patterns with optional git conditions (`tracked`, `clean`, `committed`). For multi-file `rm` commands, each file is evaluated independently and the most restrictive result wins. The deletion engine runs alongside the list/risk engines — its result is merged using most-restrictive-wins logic.

Enable or disable via `settings.toml`:

```toml
[deletion]
enabled = true   # set to false to disable
```

### Tool Engine (`permissions.toml` → `[[tool.*]]`)

```
Non-Bash tool call (Read, Write, Edit, Grep, Glob, MCP)
  │
  ├─ BLOCKLIST match?  ──→  DENY
  │
  ├─ ALTERLIST match?  ──→  REWRITE + ALLOW
  │
  ├─ ASKLIST match?    ──→  ASK
  │
  ├─ ALLOWLIST match?  ──→  ALLOW
  │
  └─ no match          ──→  PASSTHROUGH
```

Rules match on tool name (regex) and optional field predicates (AND logic). MCP tools use names like `mcp__server__action` — matched by the same regex system.

Enable or disable via `settings.toml`:

```toml
[tool_engine]
enabled = true   # set to false to disable
```

### Mode Selection (`settings.toml`)

| Mode     | Behavior |
|----------|----------|
| `lists`  | List-based engine only |
| `risk`   | Risk-level engine only |
| `both`   | Both engines; **most restrictive** decision wins |

In `both` mode, if one engine returns passthrough and the other has an opinion, the opinion takes effect. If both have opinions, the more restrictive one wins (`block > ask > approve`).

## Quick Start

### Account-Wide Installation (default)

Applies to all projects:

```bash
git clone https://github.com/snagnever/claude-code-sidecar.git /tmp/claude-code-sidecar
cd /tmp/claude-code-sidecar
./install.sh
```

This copies `filter.py`, `delete_policy_engine.py`, and config files to `~/.claude/claude-code-sidecar/` and registers the hook in `~/.claude/settings.json`.

### PreToolUse hook matcher (`settings.json`)

The install script registers `hooks.PreToolUse` with **`"matcher": ".*"`** so `filter.py` runs for **all** tool types: Bash/Shell, Read, Write, Edit, Grep, Glob, **MCP**, and anything else Claude exposes. If the matcher is only `"Bash"`, those non-Bash calls **never reach the hook**, so `[[tool.*]]` rules in `permissions.toml` (including MCP allowlists) do nothing. Re-run `./install.sh` to upgrade an existing hook from `"Bash"` to `".*"`.

### Project-Level Installation

Applies only when Claude Code runs in a specific project:

```bash
cd /path/to/your/project
/path/to/claude-code-sidecar/install.sh --project
```

Or specify a project path explicitly:

```bash
./install.sh --project /path/to/your/project
```

This installs to `<project>/.claude/claude-code-sidecar/`, registers the hook in `<project>/.claude/settings.json`, and installs the configuration skill to `<project>/.claude/skills/`.

### Development Mode

For both account-wide and project-level, use `--link` to symlink instead of copy (edits take effect immediately):

```bash
./install.sh --link                    # account-wide dev mode
./install.sh --project --link          # project-level dev mode
```

### Coexistence

Account-wide and project-level hooks can be active simultaneously. Claude Code runs all matching hooks — the most restrictive combined result applies. Project-level rules can add restrictions but cannot override account-level blocks.

### Uninstall

```bash
./uninstall.sh                         # account-wide
./uninstall.sh --project               # project-level (current directory)
./uninstall.sh --project /path/to/proj # project-level (explicit path)
./uninstall.sh --keep-config           # keeps your config customizations
```

## Configuration

### File Structure

Account-wide:

```
~/.claude/claude-code-sidecar/
├── filter.py                 # Hook entry point (list + risk engines, merging)
├── delete_policy_engine.py   # Deletion engine (rm-specific policy)
├── settings.toml             # Mode selection, risk thresholds, deletion toggle
├── commands-risks.toml       # Command → risk level mappings
├── permissions.toml          # Block/allow/ask/alter lists
└── delete-policy.toml        # Deletion policy rules (glob + git conditions)
```

Project-level:

```
<project>/
└── .claude/
    ├── settings.json                          # Hook registration (auto-generated)
    ├── claude-code-sidecar/
    │   ├── filter.py
    │   ├── delete_policy_engine.py
    │   ├── settings.toml
    │   ├── commands-risks.toml
    │   ├── permissions.toml
    │   └── delete-policy.toml
    └── skills/
        └── sidecar-permissions-config/
            └── SKILL.md                       # Config skill (project-level only)
```

### settings.toml

```toml
version = 1
mode    = "both"   # "lists" | "risk" | "both"

[risk]
allow       = [0, 1]   # these risk levels auto-allow
ask         = [2]       # these risk levels prompt the user
block       = [3, 4]   # these risk levels are denied
block_above = 4         # anything above this is also denied

[deletion]
enabled = true          # enable/disable the deletion policy engine

[tool_engine]
enabled = true          # enable/disable the tool engine for non-Bash tools and MCP calls
```

### commands-risks.toml

Each rule assigns a numeric risk level (0–4) to a command:

| Level | Meaning  | Default action |
|-------|----------|---------------|
| 0     | Safe     | Allow |
| 1     | Low      | Allow |
| 2     | Medium   | Ask |
| 3     | High     | Block |
| 4     | Critical | Block |

Rules support two matching modes:

```toml
# Prefix match — matches "ls", "ls -la", etc. (not "lsblk")
[[bash.risk]]
command = "ls"
risk    = 0
reason  = "Read-only directory listing"

# Regex match — matches anywhere in the command
[[bash.risk]]
pattern = 'rm\s+-rf'
risk    = 3
reason  = "Recursive force delete"
```

When multiple rules match, the one with the **highest risk level** wins.

### delete-policy.toml

Controls which files can be deleted via `rm` commands. Each rule combines glob patterns with an optional git condition:

```toml
version = 1
default_action = "ask"   # "ask" | "block" | "allow" — applies when no rule matches

# Build artifacts — always safe to delete
[[rules]]
paths  = ["build/**", "dist/**", "__pycache__/**", "*.pyc"]
action = "allow"
reason = "Build artifacts are always safe to delete"

# Secrets — never delete via automation
[[rules]]
paths  = ["*.env", "*.pem", "*.key", "*.secret"]
action = "block"
reason = "Never delete secrets via automation"

# Git-tracked files — recoverable from history
[[rules]]
paths  = ["**/*"]
git    = "tracked"
action = "allow"
reason = "Git-tracked files are recoverable from history"
```

#### Rule Fields

| Field    | Required | Description |
|----------|----------|-------------|
| `paths`  | yes      | List of glob patterns matched against each file path |
| `action` | yes      | `"allow"`, `"ask"`, or `"block"` |
| `reason` | yes      | Human-readable explanation (shown on ask/block) |
| `git`    | no       | Git condition — rule is skipped if the condition fails |

#### Git Conditions

| Value       | Meaning |
|-------------|---------|
| `tracked`   | File is in the git index |
| `clean`     | File has no uncommitted changes |
| `committed` | File has at least one commit in history |
| `any`       | No git check (same as omitting the field) |

#### Project-Scoped Rules

Rules can be scoped to a specific project directory. Project rules are checked before global rules:

```toml
[[projects]]
project = "/path/to/project"

  [[projects.rules]]
  paths  = ["tmp/**", "logs/**"]
  action = "allow"
  reason = "Temp files for this project"
```

### permissions.toml

Contains the four lists — same format as before:

```toml
[[bash.blocklist]]
pattern = 'rm\s+-rf|rm\s+-fr'
reason  = "Recursive force delete (rm -rf) is not allowed"
match   = "search"

[[bash.alterlist]]
pattern         = '\brsync\b(?!.*--dry-run)'
sub_pattern     = '\brsync\b'
sub_replacement = "rsync --dry-run"
reason          = "Added --dry-run to rsync for safety"

[[bash.asklist]]
pattern = '\brm\s+'
reason  = "rm command — confirm file deletion"

[[bash.allowlist]]
pattern = '(?s:git\ (diff|log|status|branch|show).*)\Z'
reason  = "Read-only git operations"
match   = "match"
```

### Tool/MCP Rules (`[[tool.*]]` in permissions.toml)

The same file also contains rules for non-Bash tools and MCP calls:

```toml
# Block writes to secrets
[[tool.blocklist]]
tools  = ["Write", "Edit"]
reason = "Cannot modify secrets"
[tool.blocklist.fields]
file_path = '\.(env|pem|key)$'

# Allow read-only tools
[[tool.allowlist]]
tools  = ["Read", "Grep", "Glob"]
reason = "Read-only tools are always safe"

# Block an entire MCP server
[[tool.blocklist]]
tools  = ["mcp__plugin_dangerous-server_.*"]
reason = "This MCP server is not authorized"

# Ask before memory writes
[[tool.asklist]]
tools  = ["mcp__plugin_episodic-memory_episodic-memory__write"]
reason = "Confirm memory write"
```

**Browser / Playwright MCP:** Many MCP servers (for example Microsoft `@playwright/mcp` and Cursor’s IDE browser) expose tools named `browser_navigate`, `browser_fill_form`, `browser_click`, etc. The internal `tool_name` looks like `mcp__<server>__browser_*`. The server segment may not contain the word `playwright`, so match on `browser_*` (see the default `[[tool.allowlist]]` at the end of the repo’s `permissions.toml`).

### Rule Fields (Tool Engine)

| Field     | Required | Description |
|-----------|----------|-------------|
| `tools`   | yes      | List of tool name patterns (regex, tested with `re.search`) |
| `reason`  | yes      | Human-readable explanation |
| `fields`  | no       | Sub-table of field predicates — all must match (AND logic) |

Common tool_input fields: `Read` → `file_path`; `Write` → `file_path`, `content`; `Edit` → `file_path`, `old_string`, `new_string`; `Grep` → `pattern`, `path`; `Glob` → `pattern`, `path`. MCP fields vary by server.

### List Reference

| List        | Priority | Default `match` | Behavior | What Claude Sees |
|-------------|----------|-----------------|----------|-----------------|
| `blocklist` | 1st      | `search`        | Command denied | Reason message |
| `alterlist` | 2nd      | `search`        | Command rewritten and auto-approved | Rewritten command |
| `asklist`   | 3rd      | `search`        | User prompted to confirm | Reason in permission dialog |
| `allowlist` | 4th      | `match`         | Command auto-approved | Nothing (runs silently) |

### Rule Fields (Lists)

| Field              | Required | Description |
|--------------------|----------|-------------|
| `pattern`          | yes      | Regex for detection |
| `reason`           | yes      | Human-readable explanation |
| `match`            | no       | `"search"` (anywhere, default) or `"match"` (from start) |

Alterlist rewrite fields (at least one required):

| Field              | Description |
|--------------------|-------------|
| `sub_pattern`      | Regex for substitution (used with `re.sub`) |
| `sub_replacement`  | Replacement string (supports `\1` backreferences) |
| `prepend`          | String prepended to the entire command |
| `append`           | String appended to the entire command |

### Rule Fields (Risk)

| Field     | Required | Description |
|-----------|----------|-------------|
| `command` | *        | Prefix match (word-boundary aware) |
| `pattern` | *        | Regex match (`re.search`) |
| `risk`    | yes      | Integer 0–4 |
| `reason`  | yes      | Human-readable explanation |

*At least one of `command` or `pattern` is required. Both can be present (OR logic).

## Managing Rules via CLI

```bash
# List all rules (from both config files)
python3 manage_rules.py list

# List only a specific type
python3 manage_rules.py list risk
python3 manage_rules.py list blocklist

# Add a risk rule (prefix match)
python3 manage_rules.py add risk "node" "Run Node.js" --command --risk-level 1

# Add a risk rule (regex match)
python3 manage_rules.py add risk 'curl.*\|' "Curl pipe" --risk-level 3

# Add a list rule
python3 manage_rules.py add blocklist 'rm\s+-rf' "Recursive force delete"

# Remove rules
python3 manage_rules.py remove risk "node"
python3 manage_rules.py remove blocklist 'rm\s+-rf'
```

Rules are auto-routed to the correct config file:
- `risk` rules → `commands-risks.toml`
- `blocklist`/`alterlist`/`asklist`/`allowlist` rules → `permissions.toml`

## Match Methods

- **`search`** (default for lists): regex matches anywhere in the command (`re.search`). Use for blocklist/asklist/alterlist.
- **`match`** (for allowlist): regex matches from the start of the command (`re.match`). Use for allowlist where you want to approve only commands that *start with* a known safe pattern.

### Multi-line Commands

Use `(?s:...)` to enable dotall mode (`.` matches newlines):

```toml
[[bash.allowlist]]
pattern = '(?s:poetry\ run\ pytest.*)\Z'
reason  = "Backend tooling"
match   = "match"
```

## "Both" Mode — Merge Semantics

When `mode = "both"`, both engines evaluate the command independently, then the most restrictive result is used:

| Lists result | Risk result | Final decision |
|-------------|-------------|---------------|
| passthrough | passthrough | passthrough |
| passthrough | allow       | **allow** (risk can grant permissions) |
| allow       | passthrough | allow |
| allow       | ask         | **ask** (more restrictive wins) |
| ask         | block       | **block** (more restrictive wins) |
| block       | allow       | **block** (most restrictive) |

Restrictiveness ranking: `block > ask > approve/alter > passthrough`

## Relation to settings.json Permissions

Claude Code has built-in permission lists in `~/.claude/settings.json` under `permissions.allow` and `permissions.ask`. This hook runs **in addition** to those built-in permissions.

The hook evaluates first. If it returns a decision (deny/allow/ask), that takes precedence. If it passes through (no match), Claude Code's built-in permissions apply.

## Error Handling

If a config file is missing, the hook skips it gracefully. If all config files are missing, the hook **fails open**: it exits silently (passthrough). A broken TOML file also triggers fail-open with a warning on stderr. This ensures a broken config never locks you out of Claude Code.

## Compatibility

| Environment | Matcher | Notes |
|-------------|---------|-------|
| Claude Code CLI | `.*` | **Use `.*`** so Bash + tools + MCP reach `filter.py` |
| Claude Code Desktop | `.*` | Same — `Bash` alone skips Read/Write/MCP |
| Cursor (Third-party skills) | `.*` | Use `.*` for MCP/tools; `Bash`/`Bash|Shell` is shell-only |
| VS Code Extension | `.*` | Recent versions do support |

The `.*` matcher intercepts all tool types. Using **`Bash` only** means the hook never sees MCP or other tools — `permissions.toml` `[[tool.*]]` rules will not apply to them. To scope behavior, use lists inside `permissions.toml` (or disable `[tool_engine]` in `settings.toml`) rather than narrowing the matcher. The tool engine can also be disabled via `settings.toml` while keeping the broad matcher.

## Requirements

- Python 3.11+ (uses `tomllib` from stdlib)
- No external dependencies (stdlib only: `json`, `os`, `re`, `sys`, `tomllib`, `subprocess`, `pathlib`, `fnmatch`)

