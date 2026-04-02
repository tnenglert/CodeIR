"""Tests for bearings rendering details."""

from ir.classifier import generate_context_file


def test_collapsed_patterns_do_not_show_module_ids() -> None:
    modules = [
        {"file_path": f"pkg_{idx}/models.py", "category": "schema", "entity_count": 1, "deps_internal": ""}
        for idx in range(6)
    ]
    module_ids = {mod["file_path"]: f"MD{idx}" for idx, mod in enumerate(modules, start=1)}

    content = generate_context_file("demo", modules, total_entities=6, module_ids=module_ids)

    assert "models.py ×6 | 6 entities" in content
    assert "[MD" not in content
