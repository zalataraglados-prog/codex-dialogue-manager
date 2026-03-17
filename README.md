# codex-tools (codex-capsule suite)

Two small utilities to improve continuity when using Codex:

1) **Context Capsule**: export high-signal context from git (base → head) into pasteable messages.
2) **Provider Sync**: fix VS Code Codex history visibility after switching model providers.

## What it does

Given a git range, it produces:

- `./.codex-capsule/capsule.json` — structured capsule (for tooling)
- `./.codex-capsule/seed.md` — a single **high-signal** message you can paste into Codex
- `./.codex-capsule/replay/01.md ...` — short messages intended to be pasted **one-by-one** (often works better than one huge prompt)

## Usage

### 1) Context Capsule

From repo root:

```bash
node tools/codex-capsule/index.mjs capsule --base upstream/main --head HEAD
```

If `base` and `head` point to the same commit, the tool will include **working-tree changes vs HEAD** instead of producing an empty capsule.

Common:

```bash
# current branch vs upstream main
node tools/codex-capsule/index.mjs capsule --base upstream/main --head HEAD

# a named branch
node tools/codex-capsule/index.mjs capsule --base upstream/main --head fix/codespaces-samesite-cookie
```

Optional:

```bash
node tools/codex-capsule/index.mjs capsule --base upstream/main --head HEAD --title "Fix Codespaces Server Actions" --notes notes.md
```

Then paste either:

- `./.codex-capsule/seed.md` (fast)
- or `./.codex-capsule/replay/*.md` in order (best)

## 2) Provider-switch history visibility (VS Code Codex)

Some Codex builds appear to filter the history list by `threads.model_provider`.
If you switched provider (e.g. account OAuth → API key, or `openai` → `newapi`), older threads may still exist locally
but become **invisible** in the UI.

This repo includes a helper that migrates the provider label in-place (backup-first).

Preferred (wrapper):

```bash
node tools/codex-capsule/index.mjs provider-sync \
  --db "C:\\Users\\<you>\\.codex\\state_5.sqlite" \
  --from openai --to newapi --apply
```

Direct (python):

```bash
python tools/codex-capsule/provider-sync.py \
  --db "C:\\Users\\<you>\\.codex\\state_5.sqlite" \
  --from openai --to newapi --apply
```

It will create a timestamped backup next to the DB file and only updates `threads.model_provider`.

## Notes

- This tool **does not** recover hidden model state. It reconstructs the best possible context from git history + diffs.
- Keep secrets out of `--notes`.
