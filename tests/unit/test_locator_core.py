"""Tests for file hashing and code slicing."""

import hashlib
import tempfile
from pathlib import Path

import pytest

from index.locator import compute_file_content_hash, extract_code_slice


# ---------------------------------------------------------------------------
# compute_file_content_hash
# ---------------------------------------------------------------------------

class TestComputeFileContentHash:
    def test_deterministic(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello world")
        h1 = compute_file_content_hash(f)
        h2 = compute_file_content_hash(f)
        assert h1 == h2

    def test_correct_sha256(self, tmp_path):
        f = tmp_path / "test.py"
        content = "print('hello')"
        f.write_text(content)
        expected = hashlib.sha256(content.encode()).hexdigest()
        assert compute_file_content_hash(f) == expected

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("x = 1")
        f2.write_text("x = 2")
        assert compute_file_content_hash(f1) != compute_file_content_hash(f2)

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        h = compute_file_content_hash(f)
        assert h == hashlib.sha256(b"").hexdigest()

    def test_binary_content(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01\x02\xff")
        h = compute_file_content_hash(f)
        assert h == hashlib.sha256(b"\x00\x01\x02\xff").hexdigest()


# ---------------------------------------------------------------------------
# extract_code_slice
# ---------------------------------------------------------------------------

class TestExtractCodeSlice:
    def _write_file(self, tmp_path, lines):
        f = tmp_path / "test.py"
        f.write_text("\n".join(lines) + "\n")
        return tmp_path, "test.py"

    def test_single_line(self, tmp_path):
        repo, fp = self._write_file(tmp_path, ["line1", "line2", "line3"])
        result = extract_code_slice(repo, fp, 2, 2)
        assert result.strip() == "line2"

    def test_multi_line_range(self, tmp_path):
        repo, fp = self._write_file(tmp_path, ["a", "b", "c", "d", "e"])
        result = extract_code_slice(repo, fp, 2, 4)
        assert "b" in result
        assert "c" in result
        assert "d" in result
        assert "a" not in result
        assert "e" not in result

    def test_full_file(self, tmp_path):
        lines = ["line1", "line2", "line3"]
        repo, fp = self._write_file(tmp_path, lines)
        result = extract_code_slice(repo, fp, 1, 3)
        for line in lines:
            assert line in result

    def test_start_line_clamped_to_one(self, tmp_path):
        repo, fp = self._write_file(tmp_path, ["a", "b", "c"])
        result = extract_code_slice(repo, fp, 0, 1)
        assert "a" in result

    def test_negative_start_line(self, tmp_path):
        repo, fp = self._write_file(tmp_path, ["a", "b"])
        result = extract_code_slice(repo, fp, -5, 1)
        assert "a" in result

    def test_end_before_start_clamped(self, tmp_path):
        repo, fp = self._write_file(tmp_path, ["a", "b", "c"])
        result = extract_code_slice(repo, fp, 3, 1)
        # end < start → end is clamped to start
        assert "c" in result
