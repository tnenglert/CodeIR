"""Tests for the TypeScript frontend."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_typescript")

from index.typescript_language import TypeScriptFrontend


@pytest.fixture
def frontend() -> TypeScriptFrontend:
    return TypeScriptFrontend()


def test_extracts_typescript_entities_and_module_qualified_names(
    tmp_path: Path,
    frontend: TypeScriptFrontend,
) -> None:
    ts_file = tmp_path / "src" / "services" / "userService.ts"
    ts_file.parent.mkdir(parents=True)
    ts_file.write_text(
        """
export interface User {
  id: string;
}

export type UserId = string;

export const fetchUser = async (id: UserId): Promise<User> => {
  return loadRecord(id);
};

export const DEFAULT_TIMEOUT = 5000;

export class UserService extends BaseService implements Worker {
  async getUser(id: UserId): Promise<User> {
    return fetchUser(id);
  }
}

export namespace API {
  export function createClient(): UserService {
    return new UserService();
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    entities = frontend.parse_entities_from_file(ts_file)
    qualified_names = {entity["qualified_name"] for entity in entities}
    kinds = {entity["kind"] for entity in entities}

    assert "services.userService.User" in qualified_names
    assert "services.userService.UserId" in qualified_names
    assert "services.userService.fetchUser" in qualified_names
    assert "services.userService.DEFAULT_TIMEOUT" in qualified_names
    assert "services.userService.UserService" in qualified_names
    assert "services.userService.UserService.getUser" in qualified_names
    assert "services.userService.API" in qualified_names
    assert "services.userService.API.createClient" in qualified_names
    assert {"interface", "type_alias", "async_function", "constant", "class", "async_method", "namespace", "function"} <= kinds

    fetch_user = next(entity for entity in entities if entity["qualified_name"] == "services.userService.fetchUser")
    assert fetch_user["semantic"]["calls"] == ["loadRecord"]
    assert "A" in fetch_user["semantic"]["flags"]
    assert fetch_user["semantic"]["type_sig"]["return_type"] == "Promise<User>"


def test_build_import_map_resolves_relative_and_named_imports(
    tmp_path: Path,
    frontend: TypeScriptFrontend,
) -> None:
    service_file = tmp_path / "src" / "services" / "userService.ts"
    service_file.parent.mkdir(parents=True)
    service_file.write_text(
        """
export default class UserService {}
export async function fetchUser(id: string) {
  return id;
}
export function helper() {
  return 1;
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    utils_file = tmp_path / "src" / "utils" / "helpers.ts"
    utils_file.parent.mkdir(parents=True)
    utils_file.write_text("export function slugify(value: string) { return value; }\n", encoding="utf-8")

    route_file = tmp_path / "src" / "routes" / "userRoutes.ts"
    route_file.parent.mkdir(parents=True)
    route_file.write_text(
        """
import UserService, { fetchUser as loadUser, helper } from "../services/userService";
import * as utils from "../utils/helpers";
""".strip()
        + "\n",
        encoding="utf-8",
    )

    parsed = frontend.parse_ast(route_file)
    assert parsed is not None
    import_map = frontend.build_import_map(parsed, route_file, tmp_path)

    assert import_map["UserService"] == "services.userService.UserService"
    assert import_map["loadUser"] == "services.userService.fetchUser"
    assert import_map["helper"] == "services.userService.helper"
    assert import_map["utils"] == "utils.helpers"


def test_typescript_classification_and_domain(
    tmp_path: Path,
    frontend: TypeScriptFrontend,
) -> None:
    route_file = tmp_path / "src" / "routes" / "user-routes.ts"
    route_file.parent.mkdir(parents=True)
    route_file.write_text(
        """
import express from "express";

const router = express.Router();

router.get("/users", async (_req, res) => {
  return res.json(await loadUsers());
});

router.post("/users", async (_req, res) => {
  return res.status(201).json(await createUser());
});
""".strip()
        + "\n",
        encoding="utf-8",
    )

    parsed = frontend.parse_ast(route_file)
    assert parsed is not None
    assert frontend.classify_file(Path("src/routes/user-routes.ts"), parsed) == "router"
    assert frontend.classify_domain(Path("src/routes/user-routes.ts"), parsed) == "http"


def test_declaration_files_are_supported(
    tmp_path: Path,
    frontend: TypeScriptFrontend,
) -> None:
    dts_file = tmp_path / "src" / "types" / "api.d.ts"
    dts_file.parent.mkdir(parents=True)
    dts_file.write_text(
        """
export interface ApiResponse<T> {
  data: T;
}

export type ApiError = {
  message: string;
};
""".strip()
        + "\n",
        encoding="utf-8",
    )

    parsed = frontend.parse_ast(dts_file)
    assert parsed is not None
    entities = frontend.parse_entities_from_file(dts_file, tree=parsed)
    qualified_names = {entity["qualified_name"] for entity in entities}

    assert "types.api.ApiResponse" in qualified_names
    assert "types.api.ApiError" in qualified_names
    assert frontend.classify_file(Path("src/types/api.d.ts"), parsed) == "schema"
