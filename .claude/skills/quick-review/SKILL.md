---
name: quick-review
description: Run the project's code-reviewer subagent on demand. Defaults to reviewing all uncommitted changes; pass a file path as an argument to scope it to just that file. Use whenever the user wants a quick, on-demand review without waiting for a full commit/PR review flow.
---

Launch the `code-reviewer` subagent (Agent tool, `subagent_type: "code-reviewer"`) to review code.

- **No argument given:** ask it to review all uncommitted changes, exactly per its own default behavior (git status/diff/staged, plus untracked files).
- **An argument given** (a file path): ask it to scope its review to just that file — still looking at its uncommitted changes (or its current full contents if the file is untracked/new).

Pass the argument through verbatim in the prompt you give the subagent so it knows what to scope to. Don't do any review yourself — this skill's only job is to delegate to the subagent and then show the user its findings as-is, without re-summarizing, filtering, or editing them.
