import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

function which(cmd) {
  try {
    const out = execSync(process.platform === "win32" ? `where ${cmd}` : `command -v ${cmd}`, {
      stdio: ["ignore", "pipe", "ignore"],
      encoding: "utf8",
    }).trim();
    return out ? out.split(/\r?\n/)[0] : null;
  } catch {
    return null;
  }
}

export function providerSync(args) {
  // We intentionally keep the implementation as a thin wrapper around provider-sync.py,
  // because Node has no built-in sqlite client and we don't want to add dependencies.

  if (!args.db || !args.fromProvider || !args.toProvider) {
    throw new Error("provider-sync requires: --db <path> --from <provider> --to <provider> [--apply]");
  }

  const repoTop = execSync("git rev-parse --show-toplevel", { encoding: "utf8" }).trim();
  const pyScript = path.join(repoTop, "tools", "codex-capsule", "provider-sync.py");
  if (!fs.existsSync(pyScript)) {
    throw new Error(`missing script: ${pyScript}`);
  }

  const py = which("python") ?? which("python3");
  if (!py) {
    console.log("python not found. You can run the SQL manually with sqlite tools:");
    console.log("1) BACKUP the sqlite file");
    console.log("2) UPDATE threads.model_provider\n");
    console.log(`SQL: update threads set model_provider='${args.toProvider}' where model_provider='${args.fromProvider}';`);
    return;
  }

  const cmd = [
    py,
    pyScript,
    "--db",
    args.db,
    "--from",
    args.fromProvider,
    "--to",
    args.toProvider,
  ];
  if (args.apply) cmd.push("--apply");

  execSync(cmd.map(quote).join(" "), { stdio: "inherit" });
}

function quote(s) {
  // Minimal quoting for shell invocation.
  if (process.platform === "win32") {
    return `"${String(s).replaceAll('"', '\\"')}"`;
  }
  return `'${String(s).replaceAll("'", "'\\''")}'`;
}
