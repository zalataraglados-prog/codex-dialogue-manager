# codex-capsule

Generate a **Context Capsule** from git state (base → head) to quickly restore Codex context in a new chat/session.

## What it does

Given a git range, it produces:

- `./.codex-capsule/capsule.json` — structured capsule (for tooling)
- `./.codex-capsule/seed.md` — a single **high-signal** message you can paste into Codex
- `./.codex-capsule/replay/01.md ...` — short messages intended to be pasted **one-by-one** (often works better than one huge prompt)

## Usage

From repo root:

```bash
node tools/codex-capsule/index.mjs --base upstream/main --head HEAD
```

Common:

```bash
# current branch vs upstream main
node tools/codex-capsule/index.mjs --base upstream/main --head HEAD

# a named branch
node tools/codex-capsule/index.mjs --base upstream/main --head fix/codespaces-samesite-cookie
```

Optional:

```bash
node tools/codex-capsule/index.mjs --base upstream/main --head HEAD --title "Fix Codespaces Server Actions" --notes notes.md
```

Then paste either:

- `./.codex-capsule/seed.md` (fast)
- or `./.codex-capsule/replay/*.md` in order (best)

## Notes

- This tool **does not** recover hidden model state. It reconstructs the best possible context from git history + diffs.
- Keep secrets out of `--notes`.
