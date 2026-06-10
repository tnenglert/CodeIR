"""Tests for Python frontend parsing and semantic extraction."""

from pathlib import Path

from index.python_language import PythonFrontend


def test_python_frontend_preserves_deep_attribute_call_chains(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text(
        """
class Service:
    def run(self):
        self.handlers.auth.login()
        client.api.v1.fetch()
""".strip()
        + "\n",
        encoding="utf-8",
    )

    frontend = PythonFrontend()
    entities = frontend.parse_entities_from_file(source, include_semantic=True)
    run_entity = next(entity for entity in entities if entity["qualified_name"] == "service.Service.run")

    calls = set(run_entity["semantic"]["calls"])
    assert "handlers.auth.login" in calls
    assert "client.api.v1.fetch" in calls
