#!/usr/bin/env node
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { providerSync } from "./provider-sync.mjs";
import { dialogues } from "./dialogues.mjs";

function sh(cmd) {
  return execSync(cmd, { stdio: ["ignore", "pipe", "pipe"], encoding: "utf8" }).trimEnd();
}

function parseArgs(argv) {
  // command: capsule | provider-sync
  const out = {
    cmd: "capsule",
    base: "upstream/main",
    head: "HEAD",
    title: "Context Capsule",
    notes: null,
    maxDiffChars: 18000,
    // provider-sync
    db: null,
    fromProvider: null,
    toProvider: null,
    apply: false,
  };

  const first = argv[0];
  if (first && !first.startsWith("-")) {
    out.cmd = first;
    argv = argv.slice(1);
  }

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--base") out.base = argv[++i];
    else if (a === "--head") out.head = argv[++i];
    else if (a === "--title") out.title = argv[++i];
    else if (a === "--notes") out.notes = argv[++i];
    else if (a === "--max-diff-chars") out.maxDiffChars = Number(argv[++i] ?? out.maxDiffChars);
    else if (a === "--db") out.db = argv[++i];
    else if (a === "--from") out.fromProvider = argv[++i];
    else if (a === "--to") out.toProvider = argv[++i];
    else if (a === "--apply") out.apply = true;
    else if (a === "-h" || a === "--help") {
      console.log(
        `codex-tools (codex-capsule suite)\n\n` +
          `Commands:\n` +
          `  capsule        Export git range into a Context Capsule (seed + replay).\n` +
          `  provider-sync  Migrate VS Code Codex sqlite threads.model_provider (fix history visibility).\n` +
          `  dialogues      Conversation manager (scan/stats/export-thread).\n\n` +
          `Capsule usage:\n` +
          `  node index.mjs capsule --base upstream/main --head HEAD [--title "..."] [--notes notes.md]\n\n` +
          `Provider-sync usage:\n` +
          `  node index.mjs provider-sync --db <path-to-state.sqlite> --from openai --to newapi [--apply]\n\n` +
          `Dialogues usage (python backend):\n` +
          `  python codex-dialogues.py scan --root ~/.codex\n` +
          `  python codex-dialogues.py stats --db ~/.codex/state_5.sqlite --preview 20\n` +
          `  python codex-dialogues.py export-thread --db ... --thread-id ... --out thread.md\n`
      );
      process.exit(0);
    }
  }
  return out;
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function trunc(s, max) {
  if (s.length <= max) return s;
  return s.slice(0, max) + `\n\n[truncated to ${max} chars]`;
}

function readOptional(filePath) {
  if (!filePath) return null;
  const abs = path.resolve(process.cwd(), filePath);
  if (!fs.existsSync(abs)) throw new Error(`notes file not found: ${abs}`);
  return fs.readFileSync(abs, "utf8").trimEnd();
}

function main() {
  const args = parseArgs(process.argv.slice(2));

  if (args.cmd === "provider-sync") {
    return providerSync(args);
  }

  if (args.cmd === "dialogues") {
    // Forward unknown args to the python backend. We re-parse from argv because
    // parseArgs is command-specific.
    const argv = process.argv.slice(3);
    return dialogues(argv);
  }

  if (args.cmd !== "capsule") {
    throw new Error(`unknown command: ${args.cmd}`);
  }

  // Basic repo metadata
  const repoTop = sh("git rev-parse --show-toplevel");
  const originUrl = sh("git remote get-url origin");
  const branch = sh("git branch --show-current || true");
  const status = sh("git status --porcelain=v1 || true");

  // Resolve refs
  const baseSha = sh(`git rev-parse ${args.base}`);
  const headSha = sh(`git rev-parse ${args.head}`);

  // Fix: when base==head, exporting base..head would be empty and misleading.
  // In that case, include working-tree changes vs HEAD.
  const isEmptyRange = baseSha === headSha;
  const diffRange = isEmptyRange ? `HEAD` : `${baseSha}..${headSha}`;

  const changedFiles = sh(`git diff --name-only ${diffRange}`)
    .split("\n")
    .filter(Boolean);

  const shortlog = isEmptyRange
    ? sh(`git log --oneline --decorate -n 20 ${headSha} || true`)
    : sh(`git log --oneline --decorate -n 20 ${baseSha}..${headSha} || true`);

  const diff = sh(`git diff ${diffRange}`);
  const diffTrunc = trunc(diff, args.maxDiffChars);

  const notes = readOptional(args.notes);

  const capsule = {
    tool: "codex-capsule",
    version: 2,
    generatedAt: new Date().toISOString(),
    repo: {
      top: repoTop,
      origin: originUrl,
      branch,
      status,
    },
    range: {
      base: args.base,
      baseSha,
      head: args.head,
      headSha,
      emptyRange: isEmptyRange,
      diffRange,
    },
    title: args.title,
    notes,
    changedFiles,
    shortlog,
    diff: diffTrunc,
  };

  const outRoot = path.join(repoTop, ".codex-capsule");
  const replayDir = path.join(outRoot, "replay");
  ensureDir(replayDir);

  fs.writeFileSync(path.join(outRoot, "capsule.json"), JSON.stringify(capsule, null, 2) + "\n");

  const seed = renderSeed(capsule);
  fs.writeFileSync(path.join(outRoot, "seed.md"), seed);

  const replay = renderReplay(capsule);
  replay.forEach((msg, idx) => {
    const n = String(idx + 1).padStart(2, "0");
    fs.writeFileSync(path.join(replayDir, `${n}.md`), msg);
  });

  const replaySh = `#!/usr/bin/env bash\nset -euo pipefail\n\nDIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n\nfor f in \"$DIR\"/*.md; do\n  echo \"=== $f ===\"\n  cat \"$f\"\n  echo\n  echo \"(paste the above into Codex, then press enter to continue)\"\n  read -r _\n  echo\n\ndone\n`;
  fs.writeFileSync(path.join(replayDir, "replay.sh"), replaySh);
  fs.chmodSync(path.join(replayDir, "replay.sh"), 0o755);

  console.log(`Wrote: ${outRoot}`);
  console.log(`- seed: ${path.join(outRoot, "seed.md")}`);
  console.log(`- replay: ${replayDir}`);
}

function renderSeed(c) {
  const files = c.changedFiles.slice(0, 30).map((f) => `- ${f}`).join("\n");
  const more = c.changedFiles.length > 30 ? `\n- ... (+${c.changedFiles.length - 30} more)` : "";

  return (
    `# ${c.title}\n\n` +
    `You are resuming work in repo: ${c.repo.origin}\n` +
    `Git range: ${c.range.base} (${c.range.baseSha.slice(0, 7)}) .. ${c.range.head} (${c.range.headSha.slice(0, 7)})\n\n` +
    `## What changed (files)\n${files}${more}\n\n` +
    `## Recent commits (shortlog)\n\n\`\`\`\n${c.shortlog}\n\`\`\`\n\n` +
    (c.notes ? `## Notes\n\n${c.notes}\n\n` : "") +
    `## Patch (diff, possibly truncated)\n\n\`\`\`diff\n${c.diff}\n\`\`\`\n\n` +
    `## Instructions\n- Read the diff above and the changed files.\n- Summarize intent, risks, and how to verify.\n- If something looks wrong, propose a minimal fix and tests.\n`
  );
}

function renderReplay(c) {
  const msgs = [];
  msgs.push(
    `You are resuming work in repo ${c.repo.origin}.\n` +
      `Target range: ${c.range.base}..${c.range.head}.\n` +
      `Your job: reconstruct the most important context and next steps.\n`
  );

  msgs.push(
    `Changed files (${c.changedFiles.length}):\n` +
      c.changedFiles.map((f) => `- ${f}`).join("\n")
  );

  if (c.shortlog?.trim()) {
    msgs.push(`Recent commits:\n\n\`\`\`\n${c.shortlog}\n\`\`\``);
  }

  if (c.notes?.trim()) {
    msgs.push(`Operator notes (treat as ground truth):\n\n${c.notes}`);
  }

  msgs.push(
    `Here is the patch (diff, may be truncated). Read it carefully and restate what it does:\n\n\`\`\`diff\n${c.diff}\n\`\`\``
  );

  msgs.push(
    `Now produce:\n` +
      `1) a 5-bullet summary\n` +
      `2) verification steps (commands)\n` +
      `3) likely edge cases/regressions\n` +
      `4) next PR candidates (small, safe)\n`
  );

  // Ensure each message isn't enormous; keep replay chunk sizes reasonable.
  return msgs.map((m) => m.trimEnd() + "\n");
}

main();
