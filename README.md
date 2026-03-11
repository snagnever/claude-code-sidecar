# Claude Code Sidecar

A PreToolUse hook for Claude Code that intercepts Bash commands and applies permission rules. Supports two permission engines — **list-based** (block/allow/ask/alter) and **risk-level** (numeric 0–4) — or both simultaneously.

## How It Works

Every Bash command Claude tries to run passes through `filter.py`, which loads rules from three config files and evaluates the command using one or both engines.

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

### Mode Selection (`settings.toml`)

| Mode     | Behavior |
|----------|----------|
| `lists`  | List-based engine only |
| `risk`   | Risk-level engine only |
| `both`   | Both engines; **most restrictive** decision wins |

In `both` mode, if one engine returns passthrough and the other has an opinion, the opinion takes effect. If both have opinions, the more restrictive one wins (`block > ask > approve`).

## Quick Start

```bash
git clone https://github.com/snagnever/claude-code-sidecar.git /tmp/claude-code-sidecar
cd /tmp/claude-code-sidecar
./install.sh
```

This copies `filter.py` and config files to `~/.claude/claude-code-sidecar/` and registers the hook in `~/.claude/settings.json`.

For development (symlinks instead of copies, so edits take effect immediately):

```bash
./install.sh --link
```

To remove:

```bash
./uninstall.sh              # removes everything
./uninstall.sh --keep-config  # keeps your config customizations
```

## Configuration

### File Structure

```
~/.claude/claude-code-sidecar/
├── filter.py             # Hook script (logic only)
├── settings.toml         # Mode selection + risk thresholds
├── commands-risks.toml   # Command → risk level mappings
└── permissions.toml      # Block/allow/ask/alter lists
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
| Claude Code CLI | `Bash` | Full support |
| Claude Code Desktop | `Bash` | Full support |
| Cursor (Third-party skills) | `Bash\|Shell` | Change matcher in settings.json |
| VS Code Extension | — | Hooks may not be supported |

## Requirements

- Python 3.11+ (uses `tomllib` from stdlib)
- No external dependencies (stdlib only: `json`, `os`, `re`, `sys`, `tomllib`)

