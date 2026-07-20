import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "codex-dialogues.py"


def run_cli(args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def write_rollout(path: Path, include_unknown: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {"timestamp": "2026-07-20T00:00:00Z", "type": "session_meta", "payload": {"model": "gpt-4.1", "cwd": "/tmp/repo"}},
        {
            "timestamp": "2026-07-20T00:00:01Z",
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello from input_text"}]},
        },
        {
            "timestamp": "2026-07-20T00:00:02Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "hello from event_msg", "kind": "plain"},
        },
        {
            "timestamp": "2026-07-20T00:00:03Z",
            "type": "response_item",
            "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "assistant output_text"}]},
        },
        {
            "timestamp": "2026-07-20T00:00:04Z",
            "type": "response_item",
            "payload": {"type": "function_call", "name": "search", "arguments": {"q": "x"}},
        },
        {
            "timestamp": "2026-07-20T00:00:05Z",
            "type": "response_item",
            "payload": {"type": "function_call_output", "name": "search", "output": "ok"},
        },
        {"timestamp": "2026-07-20T00:00:06Z", "type": "turn_context", "payload": {"summary": "compact"}},
    ]
    if include_unknown:
        events.append({"timestamp": "2026-07-20T00:00:07Z", "type": "mystery_event", "payload": {"x": "??"}})
        events.append({"timestamp": "2026-07-20T00:00:08Z", "type": "response_item", "payload": {"type": "mystery_payload", "value": "??"}})
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def create_legacy_db(path: Path):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "create table threads (id text primary key, title text, model_provider text, updated_at text)"
        )
        conn.execute("create table messages (thread_id text, role text, content text, created_at text)")
        conn.execute(
            "insert into threads (id, title, model_provider, updated_at) values (?, ?, ?, ?)",
            ("legacy-1", "legacy", "openai", "2026-07-20T00:00:00Z"),
        )
        conn.execute(
            "insert into messages (thread_id, role, content, created_at) values (?, ?, ?, ?)",
            ("legacy-1", "user", "legacy message", "2026-07-20T00:00:01Z"),
        )
        conn.commit()


def create_current_db(path: Path, rel_rollout: str, abs_rollout: str, missing_rollout: str):
    col_defs = [
        "id text primary key",
        "rollout_path text",
        "created_at text",
        "updated_at text",
        "source text",
        "model_provider text",
        "cwd text",
        "title text",
        "sandbox_policy text",
        "approval_mode text",
        "tokens_used integer",
        "has_user_event integer",
        "archived integer",
        "archived_at text",
        "git_sha text",
        "git_branch text",
        "git_origin_url text",
        "cli_version text",
        "first_user_message text",
        "agent_nickname text",
        "agent_role text",
        "memory_mode text",
        "model text",
        "reasoning_effort text",
        "agent_path text",
        "created_at_ms integer",
        "updated_at_ms integer",
        "thread_source text",
        "preview text",
        "recency_at text",
        "recency_at_ms integer",
        "history_mode text",
    ]
    assert len(col_defs) == 32
    with sqlite3.connect(path) as conn:
        conn.execute(f"create table threads ({', '.join(col_defs)})")
        conn.execute("create table turns (thread_id text, role text, content text, created_at text)")
        rows = [
            ("t-rel", "openai", rel_rollout, "full", "cli", "cli", 0, "1.2.3", "gpt-4.1"),
            ("t-abs", "openai", abs_rollout, "full", "cli", "cli", 0, "1.2.3", "gpt-4.1"),
            ("t-missing", "openai", missing_rollout, "summary", "imported", "sync", 1, "1.2.3", "gpt-4.1"),
            ("t-other", "newapi", "", "summary", "imported", "sync", 0, "1.2.3", "gpt-4.1"),
        ]
        for tid, provider, rollout, mode, source, thread_source, archived, cli_version, model in rows:
            conn.execute(
                """
                insert into threads (
                  id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                  title, sandbox_policy, approval_mode, tokens_used, has_user_event,
                  archived, archived_at, git_sha, git_branch, git_origin_url, cli_version,
                  first_user_message, agent_nickname, agent_role, memory_mode, model,
                  reasoning_effort, agent_path, created_at_ms, updated_at_ms, thread_source,
                  preview, recency_at, recency_at_ms, history_mode
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid,
                    rollout,
                    "2026-07-19T00:00:00Z",
                    "2026-07-20T00:00:00Z",
                    source,
                    provider,
                    "/tmp/repo",
                    f"title-{tid}",
                    "workspace-write",
                    "on-request",
                    321,
                    1 if tid != "t-other" else 0,
                    archived,
                    "" if not archived else "2026-07-21T00:00:00Z",
                    "abc123",
                    "feature/test",
                    "https://github.com/example/repo.git",
                    cli_version,
                    f"first-{tid}",
                    "codex",
                    "assistant",
                    mode,
                    model,
                    "medium",
                    "/agents/codex",
                    1721433600000,
                    1721520000000,
                    thread_source,
                    f"preview-{tid}",
                    "2026-07-20T01:00:00Z",
                    1721523600000,
                    mode,
                ),
            )
        conn.execute(
            "insert into turns (thread_id, role, content, created_at) values (?, ?, ?, ?)",
            ("t-missing", "assistant", "fallback row", "2026-07-20T00:10:00Z"),
        )
        conn.commit()


class CodexDialoguesFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / ".codex"
        self.root.mkdir(parents=True, exist_ok=True)

        self.db_current = self.root / "state_5.sqlite"
        self.db_legacy = self.root / "state_legacy.sqlite"

        self.rel_rollout = "sessions/rel/thread-rel.jsonl"
        self.abs_rollout_path = (self.root / "sessions/abs/thread-abs.jsonl").resolve()
        self.missing_rollout = "sessions/missing/thread-missing.jsonl"

        write_rollout(self.root / self.rel_rollout, include_unknown=True)
        write_rollout(self.abs_rollout_path)
        write_rollout(self.root / "sessions/orphan/unreferenced.jsonl")

        create_current_db(
            self.db_current,
            self.rel_rollout,
            str(self.abs_rollout_path),
            self.missing_rollout,
        )
        create_legacy_db(self.db_legacy)

    def test_doctor_reports_schema_missing_and_orphans(self):
        result = run_cli(["doctor", "--root", str(self.root)])
        self.assertEqual(result.returncode, 0, msg=result.stderr + "\n" + result.stdout)
        self.assertIn("threads schema (32 cols)", result.stdout)
        self.assertIn("history_mode distribution:", result.stdout)
        self.assertIn("rollout validation:", result.stdout)
        self.assertIn("missing: 1", result.stdout)
        self.assertIn("orphan_rollout_files: 1", result.stdout)

    def test_stats_reports_rollout_and_optional_fields(self):
        result = run_cli(["stats", "--db", str(self.db_current), "--preview", "10"])
        self.assertEqual(result.returncode, 0, msg=result.stderr + "\n" + result.stdout)
        self.assertIn("rollout files:", result.stdout)
        self.assertIn("history_mode distribution:", result.stdout)
        self.assertIn("source distribution:", result.stdout)
        self.assertIn("thread_source distribution:", result.stdout)
        self.assertIn("archived distribution:", result.stdout)
        self.assertIn("cli_version distribution:", result.stdout)
        self.assertIn("model distribution:", result.stdout)
        self.assertIn("memory_mode distribution:", result.stdout)
        self.assertIn("has_user_event distribution:", result.stdout)
        self.assertIn("rollout_exists=yes", result.stdout)
        self.assertIn("created_at_ms=", result.stdout)
        self.assertIn("updated_at_ms=", result.stdout)
        self.assertIn("recency_at=", result.stdout)
        self.assertIn("recency_at_ms=", result.stdout)
        self.assertIn("preview=", result.stdout)
        self.assertIn("first_user_message=", result.stdout)
        self.assertIn("tokens_used=", result.stdout)
        self.assertIn("has_user_event=", result.stdout)
        self.assertIn("memory_mode=", result.stdout)

    def test_export_thread_parses_relative_rollout_and_unknown_events(self):
        out = self.root / "out-rel.md"
        result = run_cli(["export-thread", "--db", str(self.db_current), "--thread-id", "t-rel", "--out", str(out)])
        self.assertEqual(result.returncode, 0, msg=result.stderr + "\n" + result.stdout)
        content = out.read_text(encoding="utf-8")
        self.assertIn("## Rollout source", content)
        self.assertIn("## User messages", content)
        self.assertIn("## Assistant messages", content)
        self.assertIn("## Tool calls", content)
        self.assertIn("## Tool outputs", content)
        self.assertIn("## Turn context", content)
        self.assertIn("## Unknown envelopes", content)
        self.assertIn("mystery_event", content)
        self.assertIn("hello from input_text", content)
        self.assertIn("assistant output_text", content)
        self.assertNotIn("\"raw\":", content)

    def test_export_thread_parses_absolute_rollout(self):
        out = self.root / "out-abs.md"
        result = run_cli(["export-thread", "--db", str(self.db_current), "--thread-id", "t-abs", "--out", str(out)])
        self.assertEqual(result.returncode, 0, msg=result.stderr + "\n" + result.stdout)
        content = out.read_text(encoding="utf-8")
        self.assertIn(str(self.abs_rollout_path), content)
        self.assertIn("events_total", content)

    def test_export_thread_include_raw_adds_warning_and_raw_payload(self):
        out = self.root / "out-rel-raw.md"
        result = run_cli(
            [
                "export-thread",
                "--db",
                str(self.db_current),
                "--thread-id",
                "t-rel",
                "--out",
                str(out),
                "--include-raw",
            ]
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr + "\n" + result.stdout)
        content = out.read_text(encoding="utf-8")
        self.assertIn("## Privacy warning", content)
        self.assertIn("\"raw\":", content)

    def test_export_thread_legacy_fallback(self):
        out = self.root / "out-legacy.md"
        result = run_cli(["export-thread", "--db", str(self.db_legacy), "--thread-id", "legacy-1", "--out", str(out)])
        self.assertEqual(result.returncode, 0, msg=result.stderr + "\n" + result.stdout)
        content = out.read_text(encoding="utf-8")
        self.assertIn("No readable rollout file for this thread.", content)
        self.assertIn("Messages (legacy DB fallback)", content)
        self.assertIn("legacy message", content)

    def test_provider_sync_dry_run_and_apply(self):
        before_openai = self._provider_count(self.db_current, "openai")
        self.assertEqual(before_openai, 3)

        dry = run_cli(
            [
                "provider-sync",
                "--db",
                str(self.db_current),
                "--from",
                "openai",
                "--to",
                "newapi",
            ]
        )
        self.assertEqual(dry.returncode, 0, msg=dry.stderr + "\n" + dry.stdout)
        self.assertIn("dry-run only", dry.stdout)
        self.assertIn("candidate rollout paths:", dry.stdout)
        self.assertEqual(self._provider_count(self.db_current, "openai"), 3)

        apply = run_cli(
            [
                "provider-sync",
                "--db",
                str(self.db_current),
                "--from",
                "openai",
                "--to",
                "newapi",
                "--apply",
            ]
        )
        self.assertEqual(apply.returncode, 0, msg=apply.stderr + "\n" + apply.stdout)
        self.assertIn("backup written:", apply.stdout)
        self.assertIn("changed rows: 3", apply.stdout)
        self.assertEqual(self._provider_count(self.db_current, "openai"), 0)
        self.assertEqual(self._provider_count(self.db_current, "newapi"), 4)

        backups = list(self.root.glob("state_5.sqlite.bak-codex-dialogues-*"))
        self.assertTrue(backups, "expected backup file to be created")

    @staticmethod
    def _provider_count(db: Path, provider: str) -> int:
        with sqlite3.connect(db) as conn:
            row = conn.execute("select count(1) from threads where model_provider = ?", (provider,)).fetchone()
        return int(row[0])


if __name__ == "__main__":
    unittest.main()
