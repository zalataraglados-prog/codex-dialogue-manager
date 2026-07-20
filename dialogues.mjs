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
  const pyScript = `${repoTop}/./codex-dialogues.py`;

  const launcher = selectPythonLauncher();
  if (!launcher) {
    throw new Error("python not found; dialogues manager requires python (stdlib sqlite3)");
  }

  const cmd = [...launcher, pyScript, ...argv];
  execSync(cmd.map(quote).join(" "), { stdio: "inherit" });
}

function selectPythonLauncher() {
  if (process.platform === "win32") {
    if (which("py")) return ["py", "-3"];
    const py = which("python");
    return py ? [py] : null;
  }
  const py = which("python3") ?? which("python");
  return py ? [py] : null;
}

function quote(s) {
  if (process.platform === "win32") return `"${String(s).replaceAll('"', '\\"')}"`;
  return `'${String(s).replaceAll("'", "'\\''")}'`;
}
