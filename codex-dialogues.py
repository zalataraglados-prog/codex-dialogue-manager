#!/usr/bin/env python3
r"""codex-dialogues

A small *conversation manager* for VS Code Codex local state.

Features:
- Scan a Codex state directory for sqlite DBs.
- Show thread/provider distribution (what lives where).
- Export thread metadata (and messages if discoverable) to markdown.
- Provider migration (fix history visibility after switching providers), backup-first.

This is intentionally dependency-free (stdlib only).

Typical locations:
- Windows: C:\Users\<you>\.codex\state_5.sqlite
- macOS/Linux: ~/.codex/state_5.sqlite

IMPORTANT:
- Close VS Code before running `provider-sync --apply`.
- This tool only modifies `threads.model_provider` and makes a timestamped backup.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import os
import shutil
import sqlite3
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _expand(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))


def _find_state_dbs(root: str) -> List[str]:
    root = _expand(root)
    if not os.path.isdir(root):
        return []
    # Common filenames: state_5.sqlite, state.sqlite, etc.
    pats = ["state_*.sqlite", "state.sqlite", "*.sqlite"]
    out: List[str] = []
    for pat in pats:
        out.extend(glob.glob(os.path.join(root, pat)))
    # keep only files
    out = [p for p in out if os.path.isfile(p)]
    # stable sort: prefer state_*.sqlite
    out.sort(key=lambda p: (0 if os.path.basename(p).startswith("state_") else 1, p))
    return out


def _conn(db: str) -> sqlite3.Connection:
    return sqlite3.connect(db)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("select 1 from sqlite_master where type='table' and name=? limit 1", (name,))
    return cur.fetchone() is not None


def _cols(conn: sqlite3.Connection, table: str) -> List[str]:
    cur = conn.execute(f"pragma table_info({table})")
    return [r[1] for r in cur.fetchall()]


def _assert_threads_schema(conn: sqlite3.Connection) -> None:
    if not _has_table(conn, "threads"):
        raise SystemExit("schema mismatch: missing table 'threads'")
    cols = set(_cols(conn, "threads"))
    if "model_provider" not in cols:
        raise SystemExit("schema mismatch: threads.model_provider column not found")


def _provider_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    _assert_threads_schema(conn)
    cur = conn.execute(
        "select coalesce(model_provider,'') as p, count(1) from threads group by p order by count(1) desc"
    )
    return {p: int(c) for p, c in cur.fetchall()}


def _threads_preview(conn: sqlite3.Connection, limit: int = 20) -> List[Dict[str, str]]:
    _assert_threads_schema(conn)
    cols = set(_cols(conn, "threads"))

    # best-effort select columns that often exist
    select_cols: List[str] = []
    for c in ["id", "title", "name", "model_provider", "updated_at", "created_at"]:
        if c in cols:
            select_cols.append(c)

    if not select_cols:
        select_cols = ["model_provider"]

    q = f"select {', '.join(select_cols)} from threads order by rowid desc limit ?"
    cur = conn.execute(q, (limit,))

    out: List[Dict[str, str]] = []
    for row in cur.fetchall():
        d: Dict[str, str] = {}
        for i, c in enumerate(select_cols):
            v = row[i]
            d[c] = "" if v is None else str(v)
        out.append(d)
    return out


def _backup_db(db_path: str) -> str:
    bak = f"{db_path}.bak-codex-dialogues-{_ts()}"
    shutil.copy2(db_path, bak)
    return bak


def cmd_scan(args: argparse.Namespace) -> int:
    dbs = _find_state_dbs(args.root)
    if not dbs:
        print(f"no sqlite dbs found under: {args.root}")
        return 1

    for db in dbs:
        print(db)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    db = _expand(args.db)
    with _conn(db) as conn:
        counts = _provider_counts(conn)
        print("provider counts:")
        for p, c in counts.items():
            label = p if p else "(NULL/empty)"
            print(f"  {label}: {c}")

        if args.preview:
            rows = _threads_preview(conn, limit=args.preview)
            print("\nthreads preview:")
            for r in rows:
                print("- " + ", ".join([f"{k}={v}" for k, v in r.items()]))

    return 0


def cmd_provider_sync(args: argparse.Namespace) -> int:
    db = _expand(args.db)
    if not os.path.exists(db):
        raise SystemExit(f"db not found: {db}")

    with _conn(db) as conn:
        before = _provider_counts(conn)
        n = conn.execute("select count(1) from threads where model_provider = ?", (args.from_provider,)).fetchone()[0]
        print("[before] provider counts:")
        for p, c in before.items():
            label = p if p else "(NULL/empty)"
            print(f"  {label}: {c}")
        print(f"candidate rows (from={args.from_provider}): {int(n)}")

        if not args.apply:
            print("dry-run only; re-run with --apply to make changes")
            return 0

    print("IMPORTANT: close VS Code before applying.")
    bak = _backup_db(db)
    print(f"backup written: {bak}")

    with _conn(db) as conn2:
        _assert_threads_schema(conn2)
        conn2.execute(
            "update threads set model_provider = ? where model_provider = ?",
            (args.to_provider, args.from_provider),
        )
        conn2.commit()
        after = _provider_counts(conn2)

    print("[after] provider counts:")
    for p, c in after.items():
        label = p if p else "(NULL/empty)"
        print(f"  {label}: {c}")

    print("done. In VS Code, run: Developer: Reload Window")
    return 0


def cmd_export_thread(args: argparse.Namespace) -> int:
    db = _expand(args.db)
    out = _expand(args.out)

    with _conn(db) as conn:
        _assert_threads_schema(conn)
        cols = set(_cols(conn, "threads"))

        if "id" not in cols:
            raise SystemExit("cannot export: threads.id column not found")

        row = conn.execute("select * from threads where id = ? limit 1", (args.thread_id,)).fetchone()
        if not row:
            raise SystemExit(f"thread not found: {args.thread_id}")

        # map row
        all_cols = _cols(conn, "threads")
        meta = {all_cols[i]: ("" if row[i] is None else str(row[i])) for i in range(len(all_cols))}

        # Try to find messages-like table
        tables = [r[0] for r in conn.execute("select name from sqlite_master where type='table' order by name").fetchall()]
        msg_table = None
        msg_thread_col = None
        for t in ["messages", "turns", "events"]:
            if t in tables:
                cols_t = _cols(conn, t)
                for c in ["thread_id", "threadId"]:
                    if c in cols_t:
                        msg_table = t
                        msg_thread_col = c
                        break
            if msg_table:
                break

        messages: List[Dict[str, str]] = []
        if msg_table and msg_thread_col:
            cols_m = _cols(conn, msg_table)
            # pick a subset
            pick = []
            for c in [msg_thread_col, "role", "content", "text", "created_at", "createdAt"]:
                if c in cols_m and c not in pick:
                    pick.append(c)
            if not pick:
                pick = cols_m
            q = f"select {', '.join(pick)} from {msg_table} where {msg_thread_col} = ? order by rowid asc"
            cur = conn.execute(q, (args.thread_id,))
            for r in cur.fetchall():
                d = {pick[i]: ("" if r[i] is None else str(r[i])) for i in range(len(pick))}
                messages.append(d)

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# Codex thread export: {args.thread_id}\n\n")
        f.write(f"DB: {db}\n\n")
        f.write("## Thread metadata\n\n")
        for k, v in meta.items():
            f.write(f"- {k}: {v}\n")

        if messages:
            f.write("\n## Messages (best-effort)\n\n")
            for m in messages:
                f.write("---\n")
                for k, v in m.items():
                    f.write(f"**{k}**: {v}\n\n")
        else:
            f.write("\n## Messages\n\n")
            f.write("No messages table found (exported metadata only).\n")

    print(f"wrote: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="codex-dialogues")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="List sqlite DBs under a Codex state root")
    p_scan.add_argument("--root", default="~/.codex", help="Codex state directory (default: ~/.codex)")
    p_scan.set_defaults(fn=cmd_scan)

    p_stats = sub.add_parser("stats", help="Show provider distribution and optional thread preview")
    p_stats.add_argument("--db", required=True, help="Path to state sqlite (e.g. ~/.codex/state_5.sqlite)")
    p_stats.add_argument("--preview", type=int, default=0, help="Show N most recent threads")
    p_stats.set_defaults(fn=cmd_stats)

    p_ps = sub.add_parser("provider-sync", help="Migrate threads.model_provider (backup-first)")
    p_ps.add_argument("--db", required=True)
    p_ps.add_argument("--from", dest="from_provider", required=True)
    p_ps.add_argument("--to", dest="to_provider", required=True)
    p_ps.add_argument("--apply", action="store_true")
    p_ps.set_defaults(fn=cmd_provider_sync)

    p_exp = sub.add_parser("export-thread", help="Export a thread to markdown (metadata + best-effort messages)")
    p_exp.add_argument("--db", required=True)
    p_exp.add_argument("--thread-id", required=True)
    p_exp.add_argument("--out", required=True, help="Output markdown path")
    p_exp.set_defaults(fn=cmd_export_thread)

    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
