"""Tests for the Rust frontend."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_rust")

from index.rust_language import RustFrontend


@pytest.fixture
def frontend() -> RustFrontend:
    return RustFrontend()


def test_extracts_rust_entities_and_module_qualified_names(tmp_path: Path, frontend: RustFrontend) -> None:
    src_dir = tmp_path / "src" / "api"
    src_dir.mkdir(parents=True)
    rust_file = src_dir / "client.rs"
    rust_file.write_text(
        """
pub struct Client;

impl Client {
    pub async fn send(&self) -> Result<(), AppError> {
        helper()?;
        Ok(())
    }
}

pub fn helper() -> Result<(), AppError> {
    Ok(())
}

pub trait Worker {
    fn work(&self);
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    entities = frontend.parse_entities_from_file(rust_file)
    qualified_names = {entity["qualified_name"] for entity in entities}
    kinds = {entity["kind"] for entity in entities}

    assert "api.client.Client" in qualified_names
    assert "api.client.Client.send" in qualified_names
    assert "api.client.helper" in qualified_names
    assert "api.client.Worker" in qualified_names
    assert "api.client.Worker.work" in qualified_names
    assert {"struct", "async_method", "function", "trait", "trait_method"} <= kinds


def test_build_import_map_resolves_internal_rust_paths(tmp_path: Path, frontend: RustFrontend) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo-app"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    src_dir = tmp_path / "src" / "handlers"
    src_dir.mkdir(parents=True)
    rust_file = src_dir / "user.rs"
    rust_file.write_text(
        """
use crate::models::{User, Role as UserRole};
use crate::util::helper;

pub fn load_user() {
    helper();
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    parsed = frontend.parse_ast(rust_file)
    assert parsed is not None
    import_map = frontend.build_import_map(parsed, rust_file, tmp_path)

    assert import_map["User"] == "models.User"
    assert import_map["UserRole"] == "models.Role"
    assert import_map["helper"] == "util.helper"


def test_rust_classification_and_domain(tmp_path: Path, frontend: RustFrontend) -> None:
    rust_file = tmp_path / "src" / "db" / "models.rs"
    rust_file.parent.mkdir(parents=True)
    rust_file.write_text(
        """
use sqlx::query;

pub struct User {
    pub id: i64,
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    parsed = frontend.parse_ast(rust_file)
    assert parsed is not None
    assert frontend.classify_file(Path("src/db/models.rs"), parsed) == "schema"
    assert frontend.classify_domain(Path("src/db/models.rs"), parsed) == "db"
