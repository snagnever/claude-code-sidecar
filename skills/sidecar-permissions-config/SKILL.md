---
name: sidecar-permissions-config
description: Configures the Claude Code bash permission sidecar by editing TOML config files. Use when adding, removing, or modifying permission rules, risk levels, blocklist/allowlist/asklist/alterlist entries, or changing the sidecar mode and risk thresholds.
---

# Sidecar permissions configuration

Edit the TOML config files directly using the Edit tool. Do NOT use `manage_rules.py` — direct editing avoids shell quoting issues with regex and gives full control over formatting.

## Config files

All config files live in the same directory as `filter.py`. Default location: `~/.claude/claude-code-sidecar/` (account-wide) or `<project>/.claude/claude-code-sidecar/` (project-level).

| File | Purpose |
|------|---------|
| `settings.toml` | Mode (`lists` / `risk` / `both`), risk thresholds, deletion toggle |
| `permissions.toml` | List-based rules: blocklist, alterlist, asklist, allowlist |
| `commands-risks.toml` | Risk-level rules: command-to-risk-level mappings (0–4) |
| `delete-policy.toml` | Deletion policy: glob patterns + git conditions for `rm` commands |

## settings.toml

```toml
version = 1
mode    = "both"   # "lists" | "risk" | "both"

[risk]
allow       = [0, 1]
ask         = [2]
block       = [3, 4]
block_above = 4

[deletion]
enabled = true     # enable/disable deletion policy engine (delete-policy.toml)
```

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

## TOML quoting

- Use literal strings (single quotes) for regex: `'\bsudo\b'`
- If the regex contains single quotes, use double-quoted strings: `"psql\\b(?!.*read_only)"`
