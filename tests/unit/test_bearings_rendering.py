"""Tests for bearings rendering details."""

from ir.classifier import generate_category_file, generate_context_file


def test_collapsed_patterns_do_not_show_module_ids() -> None:
    modules = [
        {"file_path": f"pkg_{idx}/models.py", "category": "schema", "domain": "db", "entity_count": 1, "deps_internal": ""}
        for idx in range(6)
    ]
    module_ids = {mod["file_path"]: f"MD{idx}" for idx, mod in enumerate(modules, start=1)}

    content = generate_context_file("demo", modules, total_entities=6, module_ids=module_ids)

    assert "models.py ×6 | 6 entities" in content
    assert "[MD" not in content


def test_context_file_renders_domain_when_known() -> None:
    modules = [
        {
            "file_path": "src/flask/templating.py",
            "category": "core_logic",
            "domain": "ui",
            "entity_count": 17,
            "deps_internal": "helpers,sessions",
        }
    ]
    module_ids = {modules[0]["file_path"]: "MD1"}

    content = generate_context_file("demo", modules, total_entities=17, module_ids=module_ids)

    assert "dom:ui" in content
    assert "deps:helpers,sessions" in content


def test_context_file_hides_unknown_domain_and_empty_deps() -> None:
    modules = [
        {
            "file_path": "src/flask/app.py",
            "category": "core_logic",
            "domain": "unknown",
            "entity_count": 41,
            "deps_internal": "",
        }
    ]
    module_ids = {modules[0]["file_path"]: "MD1"}

    content = generate_context_file("demo", modules, total_entities=41, module_ids=module_ids)

    assert "dom:" not in content
    assert "deps:" not in content


def test_category_file_renders_domain_and_deps_when_known() -> None:
    modules = [
        {
            "file_path": "src/flask/templating.py",
            "category": "core_logic",
            "domain": "ui",
            "entity_count": 17,
            "deps_internal": "helpers,sessions",
        }
    ]
    module_ids = {modules[0]["file_path"]: "MD1"}

    content = generate_category_file("demo", "core_logic", modules, module_ids)

    assert "dom:ui" in content
    assert "deps:helpers,sessions" in content


def test_category_file_disambiguates_duplicate_filenames() -> None:
    modules = [
        {
            "file_path": "src/flask/views.py",
            "category": "router",
            "domain": "ui",
            "entity_count": 8,
            "deps_internal": "",
        },
        {
            "file_path": "examples/celery/src/task_app/views.py",
            "category": "router",
            "domain": "ui",
            "entity_count": 4,
            "deps_internal": "",
        },
    ]
    module_ids = {mod["file_path"]: f"MD{idx}" for idx, mod in enumerate(modules, start=1)}

    content = generate_category_file("demo", "router", modules, module_ids)

    assert "flask/views.py" in content
    assert "task_app/views.py" in content


def test_context_file_disambiguates_individually_listed_duplicate_filenames() -> None:
    modules = [
        {
            "file_path": "src/flask/views.py",
            "category": "router",
            "domain": "ui",
            "entity_count": 8,
            "deps_internal": "",
        },
        {
            "file_path": "examples/celery/src/task_app/views.py",
            "category": "router",
            "domain": "ui",
            "entity_count": 6,
            "deps_internal": "",
        },
    ]
    module_ids = {mod["file_path"]: f"MD{idx}" for idx, mod in enumerate(modules, start=1)}

    content = generate_context_file("demo", modules, total_entities=14, module_ids=module_ids)

    assert "flask/views.py" in content
    assert "task_app/views.py" in content
    assert content.count("dom:ui") == 2


def test_duplicate_filenames_are_disambiguated_within_each_category() -> None:
    modules = [
        {
            "file_path": "src/flask/views.py",
            "category": "router",
            "domain": "ui",
            "entity_count": 8,
            "deps_internal": "",
        },
        {
            "file_path": "examples/celery/src/task_app/views.py",
            "category": "task",
            "domain": "task",
            "entity_count": 6,
            "deps_internal": "",
        },
    ]
    module_ids = {mod["file_path"]: f"MD{idx}" for idx, mod in enumerate(modules, start=1)}

    content = generate_context_file("demo", modules, total_entities=14, module_ids=module_ids)

    assert "flask/views.py" not in content
    assert "task_app/views.py" not in content
    assert content.count("views.py |") == 2


def test_pattern_collapse_threshold_is_strictly_greater_than_five() -> None:
    modules = [
        {
            "file_path": f"pkg_{idx}/models.py",
            "category": "schema",
            "domain": "db",
            "entity_count": 1,
            "deps_internal": "",
        }
        for idx in range(5)
    ]
    module_ids = {mod["file_path"]: f"MD{idx}" for idx, mod in enumerate(modules, start=1)}

    content = generate_context_file("demo", modules, total_entities=5, module_ids=module_ids)

    assert "models.py ×5" not in content
    assert "pkg_0/models.py" in content
    assert content.count("dom:db") == 5


def test_collapsed_patterns_report_empty_modules() -> None:
    modules = [
        {
            "file_path": f"pkg_{idx}/settings.py",
            "category": "config",
            "domain": "unknown",
            "entity_count": 0,
            "deps_internal": "",
        }
        for idx in range(6)
    ]
    module_ids = {mod["file_path"]: f"MD{idx}" for idx, mod in enumerate(modules, start=1)}

    content = generate_context_file("demo", modules, total_entities=0, module_ids=module_ids)

    assert "settings.py ×6 | 6 empty" in content
    assert "dom:" not in content
    assert "[MD" not in content
