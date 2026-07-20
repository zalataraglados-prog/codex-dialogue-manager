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
        {"type": "user_message", "role": "user", "content": "hello"},
        {"type": "assistant_message", "role": "assistant", "content": "world"},
        {"type": "tool_call", "tool_name": "search", "arguments": {"q": "x"}},
        {"type": "tool_output", "tool_name": "search", "output": "ok"},
        {"type": "compaction_item", "summary": "compact"},
    ]
    if include_unknown:
        events.append({"type": "mystery_event", "payload": "??"})
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
        "title text",
        "name text",
        "model_provider text",
        "rollout_path text",
        "history_mode text",
        "source text",
        "thread_source text",
        "archived integer",
        "cli_version text",
        "model text",
        "updated_at text",
        "created_at text",
        "last_used_at text",
        "recency_score real",
        "c16 text",
        "c17 text",
        "c18 text",
        "c19 text",
        "c20 text",
        "c21 text",
        "c22 text",
        "c23 text",
        "c24 text",
        "c25 text",
        "c26 text",
        "c27 text",
        "c28 text",
        "c29 text",
        "c30 text",
        "c31 text",
        "c32 text",
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
                  id, title, name, model_provider, rollout_path, history_mode, source, thread_source, archived,
                  cli_version, model, updated_at, created_at, last_used_at, recency_score
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid,
                    f"title-{tid}",
                    f"name-{tid}",
                    provider,
                    rollout,
                    mode,
                    source,
                    thread_source,
                    archived,
                    cli_version,
                    model,
                    "2026-07-20T00:00:00Z",
                    "2026-07-19T00:00:00Z",
                    "2026-07-20T01:00:00Z",
                    0.5,
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
        self.assertIn("rollout_exists=yes", result.stdout)

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
        self.assertIn("## Compaction items", content)
        self.assertIn("## Unknown events", content)
        self.assertIn("mystery_event", content)

    def test_export_thread_parses_absolute_rollout(self):
        out = self.root / "out-abs.md"
        result = run_cli(["export-thread", "--db", str(self.db_current), "--thread-id", "t-abs", "--out", str(out)])
        self.assertEqual(result.returncode, 0, msg=result.stderr + "\n" + result.stdout)
        content = out.read_text(encoding="utf-8")
        self.assertIn(str(self.abs_rollout_path), content)
        self.assertIn("events_total", content)

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
