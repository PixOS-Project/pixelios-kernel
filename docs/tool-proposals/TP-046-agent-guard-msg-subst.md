---
id: TP-046
tool: "tools/agent-guard"
status: proposed
proposed_by: "claude-opus-5-5 (driver 20k)"
date: 2026-10-29
exposed_by: "AP-216 (ex-duplicate AP-214): batch-20j merge, driver 20j, 2026-10-29: backticks inside git merge -m \"...\" ran fastboot under the phone lock"
extends: "tools/agent-guard"
related: ""
usage: "tools/agent-guard (PreToolUse hook, new check E10: no command substitution inside a double-quoted git -m/--message)"
refs: "-"
---

## checked
`tools/tool-proposals check agent-guard` -> EXISTS tools/agent-guard (checks S1-S6, E1-E9, header comment tools/agent-guard:19-21); none of them looks at commit/merge/tag messages. `check backtick` -> NONE. No other proposal mentions it. So this is a new check in agent-guard, not a new tool.
**Caller:** the super .claude/settings.json PreToolUse/PostToolUse hooks (`python3 tools/agent-guard --pre|--post` on every Bash call); E10 is a new check inside that existing caller. **Replaces:** nothing.

## why
[obs] driver 20j's batch-20j merge script had `git merge -m "...read-only \`fastboot -s S oem dmesg\` reader..."`. Bash ran the backticked text as command substitution: a real `fastboot -s S oem dmesg` started under the phone lock and waited ~20 min (phone lock held by driver20j-merge 1221 s) until it was killed by PID. Nothing reached the phone only because no device had serial "S"; with a real serial in the text it would have been an unplanned hardware command (AGENTS.md rule 2). Commit messages in this project routinely quote commands in backticks (the docs style), so it will recur. Cost removed: one 20 min phone-lock stall + one hazard per occurrence; the check itself is a regex over the Bash command string (< 1 ms).

## output
New check E10, same shape as E1-E9: on a Bash call whose parsed command is `git commit|merge|tag` with `-m`/`--message` (or `-m` joined) whose argument is a double-quoted string containing an unescaped backtick or `$(`, the hook DENIES with the reason `E10 command substitution inside a double-quoted git -m: write the message to a file (<<'EOF' heredoc) and use git commit/merge -F FILE (AP-216)`. Single-quoted messages, escaped \` and -F FILE pass. Also matched inside `bash -c '...'` and scripts written by heredoc in the same call when agent-guard already parses them (its known-gaps list, tools/agent-guard:45-47, says which nesting it follows; document that E10 shares those gaps). Unit tests in the existing agent-guard test file: deny cases (backtick, `$(`, `--message="..."`, inside `bash -c`), allow cases (single quotes, escaped, -F, `$VAR` without parentheses).

## risks
Host-only, no device. False positive cost = one denied call and a rewrite with -F (cheap). It must never allow-list a command it would otherwise deny (E10 only adds denials). A script file run by path (`bash merge.sh`) is not inspected: the rule stays in AP-216 and the merge standing order (`git merge -F`).

## amendments
- 2026-10-29 claude-opus-5-5 (driver 20k): add E11 (WARN, not deny) for ledger R11 (user ~08:1x): a worker's `git commit` / `git merge` without a `PixOS-Worker: <id>` trailer (neither `--trailer "PixOS-Worker: ..."` on the command line nor a `PixOS-Worker:` line in the -F file, when the file is readable) prints `E11 commit without PixOS-Worker trailer (R11): add --trailer "PixOS-Worker: <id>"`. WARN only: the watcher already filters and fixes trailers each tick, and a deny would block human/hook commits (tool-census --write commits, the user's own). Same parse path and gaps as E10. Tests: warn on plain -m and on an -F file without the line; silent with --trailer, with the line in the file, and for `git commit --amend --no-edit`.
