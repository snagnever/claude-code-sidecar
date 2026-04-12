---
name: sidecar-permissions-config
description: Configures the Claude Code permission sidecar by editing TOML config files. Use when adding, removing, or modifying permission rules for bash commands, tools (Read/Write/Edit/Grep/Glob), MCP calls, risk levels, blocklist/allowlist/asklist/alterlist entries, or changing the sidecar mode and risk thresholds.
---

# Sidecar permissions configuration

Edit the TOML config files directly using the Edit tool. Do NOT use `manage_rules.py` — direct editing avoids shell quoting issues with regex and gives full control over formatting.

## Config files

All config files live in the same directory as `filter.py`. Default location: `~/.claude/claude-code-sidecar/` (account-wide) or `<project>/.claude/claude-code-sidecar/` (project-level).

| File | Purpose |
|------|---------|
| `settings.toml` | Mode (`lists` / `risk` / `both`), risk thresholds, engine toggles, active profile |
| `permissions.toml` | Bash rules (`[[bash.*]]`) + Tool/MCP rules (`[[tool.*]]`) + profile-scoped rule sets |
| `commands-risks.toml` | Risk-level rules: command-to-risk-level mappings (0–4), including profile-scoped risk rules |
| `delete-policy.toml` | Deletion policy: glob patterns + git conditions for `rm` commands, including profile-scoped overrides |

## `hooks.PreToolUse` matcher (`settings.json`)

The sidecar is useless for **tools and MCP** unless the hook runs for those tool types. In `~/.claude/settings.json` (or project `.claude/settings.json`), the `PreToolUse` group that runs `filter.py` must use **`"matcher": ".*"`** (or equivalent broad pattern). If the matcher is only `"Bash"`, only shell commands reach `filter.py` — `[[tool.*]]` in `permissions.toml` never applies to Read, Write, MCP, etc. The install script sets `".*"` and upgrades an existing `"Bash"` matcher when you re-run `./install.sh`.

## settings.toml

```toml
version = 1
mode    = "both"   # "lists" | "risk" | "both"
# active_profile = "strict"   # optional default profile

[risk]
allow       = [0, 1]
ask         = [2]
block       = [3, 4]
block_above = 4

[deletion]
enabled = true     # enable/disable deletion policy engine (delete-policy.toml)

[tool_engine]
enabled = true     # enable/disable tool engine for non-Bash tools and MCP calls
```

Profiles:

- Enable one by default with `active_profile = "<name>"`
- Override it per hook call with top-level `permission_profile`
- Define settings under `[profiles.<name>]`
- Use `base = "default"` to layer over the top-level template, or `base = "clean"` to start empty

In `both` mode, both engines evaluate independently and the most restrictive decision wins. Restrictiveness ranking: `block > ask > approve/alter > passthrough`.

## permissions.toml

Four lists evaluated in priority order (first list match wins, blocklist always checked first):

| List | Action | Default `match` mode |
|------|--------|---------------------|
| `blocklist` | DENY | `search` |
| `alterlist` | Rewrite + ALLOW | `search` |
| `asklist` | Prompt user | `search` |
| `allowlist` | Auto-ALLOW | `match` |

### Rule format

```toml
[[bash.blocklist]]
pattern = '\bsudo\b'
reason  = "sudo is not allowed"
match   = "search"          # optional: "search" (anywhere) or "match" (from start)
```

Profile-scoped bash rules use `[[profiles.<name>.bash.blocklist]]`, `[[profiles.<name>.bash.asklist]]`, etc. Profile-scoped tool rules use `[[profiles.<name>.tool.allowlist]]`, etc.

### Alterlist rewrite fields

Provide at least one (checked in order): `sub_pattern` + `sub_replacement`, `prepend`, or `append`.

```toml
[[bash.alterlist]]
pattern         = '\brsync\b(?!.*--dry-run)'
sub_pattern     = '\brsync\b'
sub_replacement = "rsync --dry-run"
reason          = "Added --dry-run to rsync for safety"
```

### Allowlist — use `match` mode with `\Z` anchor

```toml
[[bash.allowlist]]
pattern = '(?s:git\ (diff|log|status).*)\Z'
reason  = "Read-only git operations"
match   = "match"
```

For multi-line commands, wrap in `(?s:...)` for dotall mode.

## commands-risks.toml

| Level | Meaning | Default action |
|-------|---------|---------------|
| 0 | Safe | Allow |
| 1 | Low | Allow |
| 2 | Medium | Ask |
| 3 | High | Block |
| 4 | Critical | Block |

### Two matching modes

```toml
# Prefix match — "ls", "ls -la" but NOT "lsblk"
[[bash.risk]]
command = "ls"
risk    = 0
reason  = "Read-only directory listing"

# Regex match — re.search anywhere in command
[[bash.risk]]
pattern = 'rm\s+-rf'
risk    = 3
reason  = "Recursive force delete"
```

At least one of `command` or `pattern` required. Both can be present (OR logic). When multiple rules match, **highest risk wins**.

Profile-scoped risk rules use `[[profiles.<name>.bash.risk]]`.

## delete-policy.toml

Controls which files can be deleted via `rm` commands. Rules combine glob patterns with optional git conditions:

```toml
version = 1
default_action = "ask"   # applies when no rule matches

[[rules]]
paths  = ["build/**", "dist/**", "*.pyc"]
action = "allow"
reason = "Build artifacts are always safe to delete"

[[rules]]
paths  = ["*.env", "*.pem", "*.key"]
action = "block"
reason = "Never delete secrets via automation"

[[rules]]
paths  = ["**/*"]
git    = "tracked"       # "tracked" | "clean" | "committed" | "any"
action = "allow"
reason = "Git-tracked files are recoverable"
```

Project-scoped rules (checked before global rules):

```toml
[[projects]]
project = "/path/to/project"

  [[projects.rules]]
  paths  = ["tmp/**"]
  action = "allow"
  reason = "Temp files for this project"
```

Profile-scoped deletion rules use `[profiles.<name>]`, `[[profiles.<name>.rules]]`, and `[[profiles.<name>.projects]]`.

## Tool engine — permissions.toml (`[[tool.*]]` sections)

The tool engine controls non-Bash tools (Read, Write, Edit, Grep, Glob) and MCP calls. Rules use the same four lists as bash (blocklist/alterlist/asklist/allowlist), but with tool-specific matching.

### Rule format

```toml
[[tool.blocklist]]
tools  = ["Write", "Edit"]       # list of tool name regexes
reason = "Cannot modify secrets"
[tool.blocklist.fields]           # optional: field predicates (AND logic)
file_path = '\.(env|pem|key)$'
```

### Rule fields

| Field     | Required | Description |
|-----------|----------|-------------|
| `tools`   | yes      | List of tool name patterns (regex, tested with `re.search`) |
| `reason`  | yes      | Human-readable explanation |
| `fields`  | no       | Sub-table of field predicates — all must match (AND logic) |

### Common tool_input fields by tool type

| Tool | Fields |
|------|--------|
| Read | `file_path`, `offset`, `limit` |
| Write | `file_path`, `content` |
| Edit | `file_path`, `old_string`, `new_string` |
| Grep | `pattern`, `path`, `glob`, `type` |
| Glob | `pattern`, `path` |
| MCP | varies by server/tool |

### MCP calls

MCP tools use names like `mcp__<server>__<action>` — match them with regex in the `tools` list. Browser automation (Playwright MCP, Cursor IDE browser, etc.) often uses actions `browser_navigate`, `browser_fill_form`, `browser_click`, … the `<server>` part may be a sanitized package id that does **not** contain the substring `playwright`; matching `mcp__.*__browser_.*` is a practical default (see `permissions.toml` in repo).

```toml
# Block an entire MCP server
[[tool.blocklist]]
tools  = ["mcp__plugin_dangerous-server_.*"]
reason = "This MCP server is not authorized"

# Ask before memory writes
[[tool.asklist]]
tools  = ["mcp__plugin_episodic-memory_episodic-memory__write"]
reason = "Confirm memory write"

# Allow documentation lookups
[[tool.allowlist]]
tools  = ["mcp__plugin_context7_context7__.*"]
reason = "Documentation lookups are safe"
```

### Alterlist for tools

```toml
[[tool.alterlist]]
tools  = ["Write"]
reason = "Appended safety header to shell scripts"
[tool.alterlist.fields]
file_path = '\.sh$'
[tool.alterlist.transform]
content = { prepend = "#!/usr/bin/env bash\nset -euo pipefail\n" }
```

Transform options per field: `sub_pattern` + `sub_replacement`, `prepend`, or `append`.

## TOML quoting

- Use literal strings (single quotes) for regex: `'\bsudo\b'`
- If the regex contains single quotes, use double-quoted strings: `"psql\\b(?!.*read_only)"`
