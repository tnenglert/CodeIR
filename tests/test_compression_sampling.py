from __future__ import annotations

import random
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli.py"
FASTAPI_FIXTURE = ROOT / "tests" / "testRepositories" / "_fastapi-users-master"
ARTIFACTS_DIR = ROOT / "tests" / "_artifacts"


def _extract_source_slice(repo_path: Path, file_path: str, start_line: int, end_line: int) -> str:
    abs_path = (repo_path / file_path).resolve()
    lines = abs_path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    start = max(1, start_line)
    end = max(start, end_line)
    return "".join(lines[start - 1 : end])


class TestCompressionSampling(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not FASTAPI_FIXTURE.exists():
            raise unittest.SkipTest(f"Fixture not found: {FASTAPI_FIXTURE}")

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_generate_random_before_after_samples(self) -> None:
        entities_db = FASTAPI_FIXTURE / ".semanticir" / "entities.db"
        self.assertTrue(FASTAPI_FIXTURE.exists(), msg=f"Missing fixture: {FASTAPI_FIXTURE}")

        # Index at all levels to get L0-L3 rows
        index = self.run_cli("index", str(FASTAPI_FIXTURE), "--level", "all")
        self.assertEqual(index.returncode, 0, msg=index.stderr or index.stdout)
        self.assertTrue(entities_db.exists(), msg=f"Missing DB: {entities_db}")

        conn = sqlite3.connect(entities_db)
        conn.row_factory = sqlite3.Row

        # Load all rows with mode info
        all_rows = conn.execute(
            """
            SELECT
              e.id AS entity_id,
              e.qualified_name,
              e.file_path,
              e.start_line,
              e.end_line,
              e.kind,
              r.ir_text,
              r.mode,
              r.source_char_count,
              r.ir_char_count,
              r.source_token_count,
              r.ir_token_count,
              r.compression_ratio
            FROM entities AS e
            JOIN ir_rows AS r ON r.entity_id = e.id
            ORDER BY e.id, r.mode
            """
        ).fetchall()
        conn.close()

        # Group rows by (entity_id, mode)
        rows_by_id: dict[str, dict[str, sqlite3.Row]] = {}
        for row in all_rows:
            eid = str(row["entity_id"])
            mode = str(row["mode"])
            rows_by_id.setdefault(eid, {})[mode] = row

        # Get entity IDs that have at least L0 and L1
        entity_ids = [eid for eid, modes in rows_by_id.items() if "L0" in modes and "L1" in modes]
        self.assertGreaterEqual(len(entity_ids), 20, msg="Not enough entities with L0+L1")

        sample_count = random.randint(20, 30)
        sample_ids = random.sample(entity_ids, min(sample_count, len(entity_ids)))

        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = ARTIFACTS_DIR / f"compression_samples_{timestamp}.md"

        lines: list[str] = []
        lines.append("# Compression Sample Report")
        lines.append("")
        lines.append(f"- fixture: `{FASTAPI_FIXTURE}`")
        lines.append(f"- generated_utc: `{timestamp}`")
        lines.append(f"- sample_size: `{len(sample_ids)}`")
        lines.append("")

        for idx, eid in enumerate(sample_ids, start=1):
            modes = rows_by_id[eid]
            # Use L0 row for source metadata
            base_row = modes.get("L0") or next(iter(modes.values()))

            src = _extract_source_slice(
                repo_path=FASTAPI_FIXTURE,
                file_path=str(base_row["file_path"]),
                start_line=int(base_row["start_line"]),
                end_line=int(base_row["end_line"]),
            )
            src_clean = re.sub(r"\s+\Z", "", src)
            lines.append(f"## Sample {idx}")
            lines.append(f"- entity_id: `{eid}`")
            lines.append(f"- qualified_name: `{base_row['qualified_name']}`")
            lines.append(f"- kind: `{base_row['kind']}`")
            lines.append(f"- location: `{base_row['file_path']}:{base_row['start_line']}-{base_row['end_line']}`")
            lines.append(
                "- metrics:"
                f" src_chars={base_row['source_char_count']},"
                f" src_tokens={base_row['source_token_count']}"
            )
            lines.append("")
            lines.append("### Before (source)")
            lines.append("```python")
            lines.append(src_clean)
            lines.append("```")
            lines.append("")
            lines.append("### After (compressed IR by level)")
            for level in ("L0", "L1", "L2", "L3"):
                if level in modes:
                    mrow = modes[level]
                    lines.append(f"- {level}: ir_chars={mrow['ir_char_count']}, ir_tokens={mrow['ir_token_count']}, ratio={float(mrow['compression_ratio']):.4f}")
            lines.append("")
            lines.append("```text")
            for level in ("L0", "L1", "L2", "L3"):
                if level in modes:
                    ir_text = str(modes[level]["ir_text"]).split("\n")[0]  # first line only for L0
                    lines.append(f"[{level}] {ir_text}")
            lines.append("```")
            lines.append("")

        report_path.write_text("\n".join(lines), encoding="utf-8")

        self.assertTrue(report_path.exists(), msg=f"Report was not created: {report_path}")
        self.assertGreater(report_path.stat().st_size, 0, msg="Report is empty")
        print(f"\ncompression sample report: {report_path}")


if __name__ == "__main__":
    unittest.main()
