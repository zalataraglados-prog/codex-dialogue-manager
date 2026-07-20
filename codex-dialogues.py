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
import json
import datetime as _dt
import glob
import os
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _expand(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))


def _find_state_dbs(root: str, *, audit_mode: bool = False) -> List[str]:
    root = _expand(root)
    if not os.path.isdir(root):
        return []
    # Audit mode intentionally stays narrow to avoid unrelated sqlite files.
    if audit_mode:
        pats = [os.path.join(root, "state*.sqlite"), os.path.join(root, "sqlite", "**", "*.sqlite")]
    else:
        pats = [
            os.path.join(root, "state_*.sqlite"),
            os.path.join(root, "state.sqlite"),
            os.path.join(root, "*.sqlite"),
        ]
    out: List[str] = []
    for pat in pats:
        out.extend(glob.glob(pat, recursive=True))
    # keep only files
    out = [p for p in out if os.path.isfile(p)]
    out = sorted(set(os.path.abspath(p) for p in out))
    # stable sort: prefer state_*.sqlite
    out.sort(key=lambda p: (0 if os.path.basename(p).startswith("state_") else 1, p))
    return out


def _conn(db: str, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = f"{Path(os.path.abspath(db)).as_uri()}?mode=ro"
        return sqlite3.connect(uri, uri=True)
    return sqlite3.connect(db)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("select 1 from sqlite_master where type='table' and name=? limit 1", (name,))
    return cur.fetchone() is not None


def _cols(conn: sqlite3.Connection, table: str) -> List[str]:
    cur = conn.execute(f"pragma table_info({table})")
    return [r[1] for r in cur.fetchall()]


def _distribution(conn: sqlite3.Connection, table: str, col: str) -> Dict[str, int]:
    cur = conn.execute(
        f"select coalesce(cast({col} as text), '') as v, count(1) from {table} group by v order by count(1) desc"
    )
    return {v: int(c) for v, c in cur.fetchall()}


def _resolve_rollout_path(db_path: str, rollout_path: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if rollout_path is None:
        return None, "empty"
    raw = str(rollout_path).strip()
    if not raw:
        return None, "empty"

    expanded = os.path.expanduser(raw)
    if os.path.isabs(expanded):
        return os.path.abspath(expanded), None

    base = os.path.abspath(os.path.dirname(db_path))
    resolved = os.path.abspath(os.path.join(base, expanded))
    try:
        common = os.path.commonpath([base, resolved])
    except ValueError:
        return None, "unsafe_relative_path"
    if common != base:
        return None, "unsafe_relative_path"
    return resolved, None


def _thread_rollout_rows(conn: sqlite3.Connection) -> List[Tuple[str, Optional[str]]]:
    cols = set(_cols(conn, "threads"))
    if "rollout_path" not in cols:
        return []
    id_col = "id" if "id" in cols else "rowid"
    q = f"select cast({id_col} as text), rollout_path from threads order by rowid asc"
    cur = conn.execute(q)
    return [(str(r[0]), r[1]) for r in cur.fetchall()]


def _audit_rollout_rows(db_path: str, rows: List[Tuple[str, Optional[str]]]) -> Dict[str, object]:
    counts: Counter[str] = Counter()
    missing: List[Tuple[str, str]] = []
    unsafe: List[Tuple[str, str]] = []
    referenced_safe: set[str] = set()

    for thread_id, rollout_path in rows:
        resolved, err = _resolve_rollout_path(db_path, rollout_path)
        if err == "empty":
            counts["empty"] += 1
            continue
        if err:
            counts["unsafe"] += 1
            unsafe.append((thread_id, "" if rollout_path is None else str(rollout_path)))
            continue

        assert resolved is not None
        normalized = os.path.normcase(os.path.abspath(resolved))
        referenced_safe.add(normalized)
        if os.path.isfile(resolved):
            counts["exists"] += 1
        else:
            counts["missing"] += 1
            missing.append((thread_id, resolved))

    return {
        "counts": dict(counts),
        "missing": missing,
        "unsafe": unsafe,
        "referenced_safe": referenced_safe,
    }


def _discover_rollout_files(root: str) -> List[str]:
    root = _expand(root)
    return sorted(
        [os.path.abspath(p) for p in glob.glob(os.path.join(root, "sessions", "**", "*.jsonl"), recursive=True) if os.path.isfile(p)]
    )


def _event_type(event: Dict[str, object]) -> str:
    for k in ("type", "event_type", "eventType", "kind"):
        v = event.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "unknown"


def _text_from_content(content: object, allowed_types: Optional[set[str]] = None) -> List[str]:
    out: List[str] = []
    if isinstance(content, str):
        t = content.strip()
        if t:
            out.append(t)
        return out
    if not isinstance(content, list):
        return out
    for part in content:
        if not isinstance(part, dict):
            continue
        p_type = str(part.get("type", "")).strip().lower()
        if allowed_types and p_type not in allowed_types:
            continue
        val = part.get("text")
        if isinstance(val, str) and val.strip():
            out.append(val.strip())
    return out


def _with_raw(item: Dict[str, Any], raw_obj: Dict[str, Any], include_raw: bool) -> Dict[str, Any]:
    if include_raw:
        item = dict(item)
        item["raw"] = raw_obj
    return item


def _dedupe_texts(chunks: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        t = chunk.strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _message_texts(payload: Dict[str, Any], *, input_mode: bool = False) -> List[str]:
    preferred_types = {"input_text"} if input_mode else {"output_text"}
    chunks = _text_from_content(payload.get("content"), preferred_types)
    if not chunks:
        chunks = _text_from_content(payload.get("content"), None)
    for key in ("message", "text", "output_text"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            chunks.append(val.strip())
    return _dedupe_texts(chunks)


def _legacy_messages(conn: sqlite3.Connection, thread_id: str) -> Tuple[Optional[str], List[Dict[str, str]]]:
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
        pick = []
        for c in [msg_thread_col, "role", "content", "text", "created_at", "createdAt", "type"]:
            if c in cols_m and c not in pick:
                pick.append(c)
        if not pick:
            pick = cols_m
        q = f"select {', '.join(pick)} from {msg_table} where {msg_thread_col} = ? order by rowid asc"
        cur = conn.execute(q, (thread_id,))
        for r in cur.fetchall():
            d = {pick[i]: ("" if r[i] is None else str(r[i])) for i in range(len(pick))}
            messages.append(d)
    return msg_table, messages


def _parse_rollout_jsonl(path: str, include_raw: bool = False) -> Dict[str, object]:
    parsed: Dict[str, object] = {
        "events_total": 0,
        "session_meta": [],
        "turn_context": [],
        "user_messages": [],
        "assistant_messages": [],
        "event_msg_user_messages": [],
        "event_msg_assistant_messages": [],
        "tool_calls": [],
        "tool_outputs": [],
        "reasoning": [],
        "compaction": [],
        "context_compaction": [],
        "envelope_counts": {},
        "unknown_envelopes": [],
        "unknown_events": [],
    }
    with open(path, "r", encoding="utf-8") as f:
        for idx, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception as e:  # pragma: no cover - defensive
                parsed["unknown_events"].append({"line": idx, "error": f"invalid_json: {e}", "raw": line})
                continue

            parsed["events_total"] = int(parsed["events_total"]) + 1
            if not isinstance(obj, dict):
                parsed["unknown_events"].append({"line": idx, "type": "non_object", "value": repr(obj)})
                continue

            envelope_type = str(obj.get("type", "")).strip()
            if not envelope_type:
                parsed["unknown_events"].append(
                    _with_raw({"line": idx, "reason": "missing_envelope_type"}, obj, include_raw)
                )
                continue

            envelope_counts = parsed["envelope_counts"]
            assert isinstance(envelope_counts, dict)
            envelope_counts[envelope_type] = int(envelope_counts.get(envelope_type, 0)) + 1

            payload = obj.get("payload")
            payload_type = str(payload.get("type", "")).strip() if isinstance(payload, dict) else ""

            if envelope_type == "session_meta":
                item = {"line": idx, "envelope_type": envelope_type, "payload_type": payload_type}
                parsed["session_meta"].append(_with_raw(item, obj, include_raw))
                continue

            if envelope_type == "turn_context":
                item = {
                    "line": idx,
                    "envelope_type": envelope_type,
                    "payload_type": payload_type,
                    "summary": payload.get("summary", "") if isinstance(payload, dict) else "",
                }
                parsed["turn_context"].append(_with_raw(item, obj, include_raw))
                continue

            if envelope_type == "event_msg":
                if not isinstance(payload, dict):
                    parsed["unknown_envelopes"].append(
                        _with_raw({"line": idx, "envelope_type": envelope_type, "reason": "non_object_payload"}, obj, include_raw)
                    )
                    continue
                pt = str(payload.get("type", "")).strip().lower()
                if pt == "user_message":
                    text = str(payload.get("message", "")).strip()
                    if text:
                        parsed["event_msg_user_messages"].append(
                            _with_raw(
                                {"line": idx, "source": "event_msg", "payload_type": pt, "text": text},
                                obj,
                                include_raw,
                            )
                        )
                    continue
                if pt in {"assistant_message", "assistant_response"}:
                    text = str(payload.get("message", "")).strip()
                    if text:
                        parsed["event_msg_assistant_messages"].append(
                            _with_raw(
                                {"line": idx, "source": "event_msg", "payload_type": pt, "text": text},
                                obj,
                                include_raw,
                            )
                        )
                    continue
                if pt in {"function_call", "tool_call"}:
                    parsed["tool_calls"].append(
                        _with_raw(
                            {
                                "line": idx,
                                "source": "event_msg",
                                "payload_type": pt,
                                "name": str(payload.get("name", payload.get("tool_name", ""))).strip(),
                                "arguments": payload.get("arguments"),
                            },
                            obj,
                            include_raw,
                        )
                    )
                    continue
                if pt in {"function_call_output", "tool_output", "tool_result"}:
                    parsed["tool_outputs"].append(
                        _with_raw(
                            {
                                "line": idx,
                                "source": "event_msg",
                                "payload_type": pt,
                                "name": str(payload.get("name", payload.get("tool_name", ""))).strip(),
                                "output": payload.get("output", payload.get("result")),
                            },
                            obj,
                            include_raw,
                        )
                    )
                    continue
                parsed["unknown_envelopes"].append(
                    _with_raw(
                        {"line": idx, "envelope_type": envelope_type, "payload_type": pt, "reason": "unknown_event_msg_payload"},
                        obj,
                        include_raw,
                    )
                )
                continue

            if envelope_type == "response_item":
                if not isinstance(payload, dict):
                    parsed["unknown_envelopes"].append(
                        _with_raw({"line": idx, "envelope_type": envelope_type, "reason": "non_object_payload"}, obj, include_raw)
                    )
                    continue
                pt = str(payload.get("type", "")).strip().lower()
                if pt == "message":
                    role = str(payload.get("role", "")).strip().lower()
                    chunks = _message_texts(payload, input_mode=(role == "user"))
                    if not chunks:
                        continue
                    target = None
                    if role == "user":
                        target = "user_messages"
                    elif role == "assistant":
                        target = "assistant_messages"
                    if target:
                        for text in chunks:
                            parsed[target].append(
                                _with_raw(
                                    {"line": idx, "source": "response_item", "payload_type": pt, "role": role, "text": text},
                                    obj,
                                    include_raw,
                                )
                            )
                    else:
                        parsed["unknown_envelopes"].append(
                            _with_raw(
                                {"line": idx, "envelope_type": envelope_type, "payload_type": pt, "reason": "unknown_message_role"},
                                obj,
                                include_raw,
                            )
                        )
                    continue
                if pt == "agent_message":
                    chunks = _message_texts(payload, input_mode=False)
                    for text in chunks:
                        parsed["assistant_messages"].append(
                            _with_raw(
                                {"line": idx, "source": "response_item", "payload_type": pt, "role": "assistant", "text": text},
                                obj,
                                include_raw,
                            )
                        )
                    continue
                if pt in {"function_call", "tool_call"}:
                    parsed["tool_calls"].append(
                        _with_raw(
                            {
                                "line": idx,
                                "source": "response_item",
                                "payload_type": pt,
                                "name": str(payload.get("name", payload.get("tool_name", ""))).strip(),
                                "arguments": payload.get("arguments"),
                            },
                            obj,
                            include_raw,
                        )
                    )
                    continue
                if pt in {
                    "local_shell_call",
                    "tool_search_call",
                    "custom_tool_call",
                    "web_search_call",
                    "image_generation_call",
                }:
                    parsed["tool_calls"].append(
                        _with_raw(
                            {
                                "line": idx,
                                "source": "response_item",
                                "payload_type": pt,
                                "name": str(payload.get("name", payload.get("tool_name", pt))).strip(),
                                "arguments": payload.get("arguments", payload.get("input")),
                            },
                            obj,
                            include_raw,
                        )
                    )
                    continue
                if pt in {"function_call_output", "tool_output", "tool_result"}:
                    parsed["tool_outputs"].append(
                        _with_raw(
                            {
                                "line": idx,
                                "source": "response_item",
                                "payload_type": pt,
                                "name": str(payload.get("name", payload.get("tool_name", ""))).strip(),
                                "output": payload.get("output", payload.get("result")),
                            },
                            obj,
                            include_raw,
                        )
                    )
                    continue
                if pt in {"tool_search_output", "custom_tool_call_output"}:
                    parsed["tool_outputs"].append(
                        _with_raw(
                            {
                                "line": idx,
                                "source": "response_item",
                                "payload_type": pt,
                                "name": str(payload.get("name", payload.get("tool_name", pt))).strip(),
                                "output": payload.get("output", payload.get("result")),
                            },
                            obj,
                            include_raw,
                        )
                    )
                    continue
                if pt == "reasoning":
                    parsed["reasoning"].append(
                        _with_raw(
                            {
                                "line": idx,
                                "source": "response_item",
                                "payload_type": pt,
                                "summary": str(payload.get("summary", "")).strip(),
                                "text": "\n".join(_message_texts(payload, input_mode=False)),
                            },
                            obj,
                            include_raw,
                        )
                    )
                    continue
                if pt == "compaction":
                    parsed["compaction"].append(
                        _with_raw(
                            {
                                "line": idx,
                                "source": "response_item",
                                "payload_type": pt,
                                "summary": str(payload.get("summary", "")).strip(),
                            },
                            obj,
                            include_raw,
                        )
                    )
                    continue
                if pt == "context_compaction":
                    parsed["context_compaction"].append(
                        _with_raw(
                            {
                                "line": idx,
                                "source": "response_item",
                                "payload_type": pt,
                                "summary": str(payload.get("summary", "")).strip(),
                            },
                            obj,
                            include_raw,
                        )
                    )
                    continue
                parsed["unknown_envelopes"].append(
                    _with_raw(
                        {"line": idx, "envelope_type": envelope_type, "payload_type": pt, "reason": "unknown_response_item_payload"},
                        obj,
                        include_raw,
                    )
                )
                continue

            parsed["unknown_envelopes"].append(
                _with_raw(
                    {"line": idx, "envelope_type": envelope_type, "payload_type": payload_type, "reason": "unknown_envelope_type"},
                    obj,
                    include_raw,
                )
            )

    if not parsed["user_messages"]:
        parsed["user_messages"] = list(parsed["event_msg_user_messages"])
    if not parsed["assistant_messages"]:
        parsed["assistant_messages"] = list(parsed["event_msg_assistant_messages"])
    parsed.pop("event_msg_user_messages", None)
    parsed.pop("event_msg_assistant_messages", None)

    return parsed


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
    for c in [
        "id",
        "title",
        "name",
        "model_provider",
        "history_mode",
        "source",
        "thread_source",
        "memory_mode",
        "archived",
        "cli_version",
        "model",
        "rollout_path",
        "tokens_used",
        "has_user_event",
        "first_user_message",
        "preview",
        "created_at_ms",
        "updated_at_ms",
        "recency_at",
        "recency_at_ms",
        "updated_at",
        "created_at",
    ]:
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
    with sqlite3.connect(db_path) as src, sqlite3.connect(bak) as dst:
        try:
            src.execute("pragma wal_checkpoint(full)")
        except sqlite3.DatabaseError:
            pass
        src.backup(dst)
        dst.commit()
    with sqlite3.connect(bak) as bconn:
        qc = bconn.execute("pragma quick_check").fetchone()
        if not qc or str(qc[0]).lower() != "ok":
            raise SystemExit(f"backup verification failed: quick_check={qc!r}")
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
        _assert_threads_schema(conn)
        cols = set(_cols(conn, "threads"))
        counts = _provider_counts(conn)
        print("provider counts:")
        for p, c in counts.items():
            label = p if p else "(NULL/empty)"
            print(f"  {label}: {c}")

        for c in ["source", "thread_source", "history_mode", "archived", "cli_version", "model", "memory_mode", "has_user_event"]:
            if c in cols:
                print(f"\n{c} distribution:")
                d = _distribution(conn, "threads", c)
                for k, v in d.items():
                    label = k if k else "(NULL/empty)"
                    print(f"  {label}: {v}")

        if "rollout_path" in cols:
            audit = _audit_rollout_rows(db, _thread_rollout_rows(conn))
            rc = audit["counts"]
            print("\nrollout files:")
            print(f"  exists: {int(rc.get('exists', 0))}")
            print(f"  missing: {int(rc.get('missing', 0))}")
            print(f"  empty: {int(rc.get('empty', 0))}")
            print(f"  unsafe_relative_path: {int(rc.get('unsafe', 0))}")

        if args.preview:
            rows = _threads_preview(conn, limit=args.preview)
            print("\nthreads preview:")
            for r in rows:
                if "rollout_path" in r:
                    resolved, err = _resolve_rollout_path(db, r.get("rollout_path"))
                    if err == "empty":
                        r["rollout_exists"] = "empty"
                    elif err:
                        r["rollout_exists"] = err
                    else:
                        r["rollout_exists"] = "yes" if (resolved and os.path.isfile(resolved)) else "no"
                print("- " + ", ".join([f"{k}={v}" for k, v in r.items()]))

    return 0


def cmd_provider_sync(args: argparse.Namespace) -> int:
    db = _expand(args.db)
    if not os.path.exists(db):
        raise SystemExit(f"db not found: {db}")

    with _conn(db) as conn:
        _assert_threads_schema(conn)
        cols = set(_cols(conn, "threads"))
        before = _provider_counts(conn)
        n = conn.execute("select count(1) from threads where model_provider = ?", (args.from_provider,)).fetchone()[0]
        print("[before] provider counts:")
        for p, c in before.items():
            label = p if p else "(NULL/empty)"
            print(f"  {label}: {c}")
        print(f"candidate rows (from={args.from_provider}): {int(n)}")

        if "rollout_path" in cols:
            id_col = "id" if "id" in cols else "rowid"
            cur = conn.execute(
                f"select cast({id_col} as text), rollout_path from threads where model_provider = ?",
                (args.from_provider,),
            )
            audit = _audit_rollout_rows(db, [(str(r[0]), r[1]) for r in cur.fetchall()])
            rc = audit["counts"]
            print("candidate rollout paths:")
            print(f"  exists: {int(rc.get('exists', 0))}")
            print(f"  missing: {int(rc.get('missing', 0))}")
            print(f"  empty: {int(rc.get('empty', 0))}")
            print(f"  unsafe_relative_path: {int(rc.get('unsafe', 0))}")

        if not args.apply:
            print("dry-run only; re-run with --apply to make changes")
            return 0

    print("IMPORTANT: close VS Code before applying.")
    bak = _backup_db(db)
    if not os.path.isfile(bak):
        raise SystemExit(f"backup verification failed: not found {bak}")
    print(f"backup written: {bak}")

    with _conn(db) as conn2:
        conn2.execute("begin immediate")
        conn2.execute("update threads set model_provider = ? where model_provider = ?", (args.to_provider, args.from_provider))
        changed = int(conn2.execute("select changes()").fetchone()[0])
        conn2.commit()
        after = _provider_counts(conn2)

    print(f"changed rows: {changed}")
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

        msg_table, messages = _legacy_messages(conn, args.thread_id)

    rollout_info: Dict[str, str] = {}
    rollout_parsed: Optional[Dict[str, object]] = None
    rollout_path_raw = meta.get("rollout_path", "").strip() if "rollout_path" in meta else ""
    if rollout_path_raw:
        resolved, err = _resolve_rollout_path(db, rollout_path_raw)
        rollout_info["rollout_path"] = rollout_path_raw
        if err:
            rollout_info["rollout_resolved"] = f"(unavailable: {err})"
        elif resolved:
            rollout_info["rollout_resolved"] = resolved
            if os.path.isfile(resolved):
                rollout_parsed = _parse_rollout_jsonl(resolved, include_raw=args.include_raw)
            else:
                rollout_info["rollout_missing"] = "true"

    out_dir = os.path.dirname(out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# Codex thread export: {args.thread_id}\n\n")
        f.write(f"DB: {db}\n\n")
        f.write("## Thread metadata\n\n")
        for k, v in meta.items():
            f.write(f"- {k}: {v}\n")

        f.write("\n## Rollout source\n\n")
        if rollout_info:
            for k, v in rollout_info.items():
                f.write(f"- {k}: {v}\n")
        else:
            f.write("- rollout_path unavailable in this schema or this thread row.\n")

        if rollout_parsed is not None:
            f.write("\n## Rollout summary\n\n")
            f.write(f"- events_total: {rollout_parsed['events_total']}\n")
            f.write(f"- session_meta: {len(rollout_parsed['session_meta'])}\n")
            f.write(f"- turn_context: {len(rollout_parsed['turn_context'])}\n")
            f.write(f"- user_messages: {len(rollout_parsed['user_messages'])}\n")
            f.write(f"- assistant_messages: {len(rollout_parsed['assistant_messages'])}\n")
            f.write(f"- tool_calls: {len(rollout_parsed['tool_calls'])}\n")
            f.write(f"- tool_outputs: {len(rollout_parsed['tool_outputs'])}\n")
            f.write(f"- reasoning: {len(rollout_parsed['reasoning'])}\n")
            f.write(f"- compaction: {len(rollout_parsed['compaction'])}\n")
            f.write(f"- context_compaction: {len(rollout_parsed['context_compaction'])}\n")
            f.write(f"- unknown_envelopes: {len(rollout_parsed['unknown_envelopes'])}\n")
            f.write(f"- unknown_events: {len(rollout_parsed['unknown_events'])}\n")

            for section, key in [
                ("Session meta", "session_meta"),
                ("Turn context", "turn_context"),
                ("User messages", "user_messages"),
                ("Assistant messages", "assistant_messages"),
                ("Tool calls", "tool_calls"),
                ("Tool outputs", "tool_outputs"),
                ("Reasoning", "reasoning"),
                ("Compaction", "compaction"),
                ("Context compaction", "context_compaction"),
                ("Unknown envelopes", "unknown_envelopes"),
                ("Unknown events", "unknown_events"),
            ]:
                items = rollout_parsed[key]
                f.write(f"\n## {section}\n\n")
                if not items:
                    f.write("none\n\n")
                    continue
                for item in items:
                    f.write("```json\n")
                    f.write(json.dumps(item, ensure_ascii=False, indent=2))
                    f.write("\n```\n\n")
            if args.include_raw:
                f.write("\n## Privacy warning\n\n")
                f.write(
                    "Raw event JSON was included because --include-raw was set. "
                    "Raw data may contain local paths, tool outputs, and secrets.\n"
                )
        else:
            f.write("\n## Rollout summary\n\n")
            f.write("No readable rollout file for this thread.\n")

        if messages:
            f.write("\n## Messages (legacy DB fallback)\n\n")
            for m in messages:
                f.write("---\n")
                for k, v in m.items():
                    f.write(f"**{k}**: {v}\n\n")
        else:
            f.write("\n## Messages (legacy DB fallback)\n\n")
            if msg_table:
                f.write(f"No rows found in {msg_table} for this thread.\n")
            else:
                f.write("No messages/turns/events table found (metadata-only fallback).\n")

    print(f"wrote: {out}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    root = _expand(args.root)
    dbs = _find_state_dbs(root, audit_mode=True)
    if not dbs:
        print(f"no sqlite dbs found under: {root}")
        return 1

    referenced_safe: set[str] = set()
    for db in dbs:
        print(f"\nDB: {db}")
        try:
            with _conn(db, read_only=True) as conn:
                if not _has_table(conn, "threads"):
                    print("threads table: missing")
                    continue
                cols = _cols(conn, "threads")
                print(f"threads schema ({len(cols)} cols): {', '.join(cols)}")

                for c in ["model_provider", "source", "thread_source", "history_mode", "archived"]:
                    if c in cols:
                        print(f"{c} distribution:")
                        d = _distribution(conn, "threads", c)
                        for k, v in d.items():
                            label = k if k else "(NULL/empty)"
                            print(f"  {label}: {v}")

                if "rollout_path" in cols:
                    rows = _thread_rollout_rows(conn)
                    audit = _audit_rollout_rows(db, rows)
                    rc = audit["counts"]
                    referenced_safe.update(audit["referenced_safe"])
                    print("rollout validation:")
                    print(f"  exists: {int(rc.get('exists', 0))}")
                    print(f"  missing: {int(rc.get('missing', 0))}")
                    print(f"  empty: {int(rc.get('empty', 0))}")
                    print(f"  unsafe_relative_path: {int(rc.get('unsafe', 0))}")

                    missing = audit["missing"]
                    if missing:
                        print("missing rollout files:")
                        for thread_id, p in missing[:50]:
                            print(f"  thread={thread_id} -> {p}")
                    unsafe = audit["unsafe"]
                    if unsafe:
                        print("unsafe rollout paths:")
                        for thread_id, p in unsafe[:50]:
                            print(f"  thread={thread_id} -> {p}")
                else:
                    print("rollout validation: rollout_path column not present")
        except (sqlite3.Error, OSError) as e:
            print(f"skipping unreadable sqlite: {e}")
            continue

    rollout_files = _discover_rollout_files(root)
    rollout_keys = {os.path.normcase(os.path.abspath(p)) for p in rollout_files}
    orphan_keys = sorted(rollout_keys - referenced_safe)

    print("\nrollout file inventory:")
    print(f"  files_under_sessions: {len(rollout_files)}")
    print(f"  orphan_rollout_files: {len(orphan_keys)}")
    if orphan_keys:
        for p in orphan_keys[:100]:
            print(f"  orphan: {p}")
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
    p_exp.add_argument(
        "--include-raw",
        action="store_true",
        help="Include raw rollout event JSON (may contain paths, tool outputs, and secrets)",
    )
    p_exp.set_defaults(fn=cmd_export_thread)

    p_doc = sub.add_parser("doctor", help="Audit threads schema and rollout file consistency (read-only)")
    p_doc.add_argument("--root", default="~/.codex", help="Codex state directory root (default: ~/.codex)")
    p_doc.set_defaults(fn=cmd_doctor)

    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
