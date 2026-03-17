import { execSync } from "node:child_process";

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

export function dialogues(argv) {
  const repoTop = execSync("git rev-parse --show-toplevel", { encoding: "utf8" }).trim();
  const pyScript = `${repoTop}/tools/codex-capsule/codex-dialogues.py`;

  const py = which("python") ?? which("python3");
  if (!py) {
    throw new Error("python not found; dialogues manager requires python (stdlib sqlite3)");
  }

  const cmd = [py, pyScript, ...argv];
  execSync(cmd.map(quote).join(" "), { stdio: "inherit" });
}

function quote(s) {
  if (process.platform === "win32") return `"${String(s).replaceAll('"', '\\"')}"`;
  return `'${String(s).replaceAll("'", "'\\''")}'`;
}
