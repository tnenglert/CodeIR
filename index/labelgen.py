"""Label file helpers for compression-mode triage evaluation."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def labels_template() -> List[Dict[str, Any]]:
    """Return starter labels template expected by eval --labels."""
    return [
        {
            "query": "token expiration failing",
            "expected_entity_ids": ["EXMP.01"],
        },
        {
            "query": "invalid password reset token",
            "expected_entity_ids": ["EXMP.02", "EXMP.03"],
        },
    ]


def write_labels_template(output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(labels_template(), indent=2), encoding="utf-8")
    return output_path


def _latest_sampling_artifact(artifacts_dir: Path) -> Path:
    files = sorted(artifacts_dir.glob("compression_samples_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No sampling artifacts found in {artifacts_dir}")
    return files[0]


def _split_identifier_tokens(value: str) -> List[str]:
    raw = re.split(r"[^A-Za-z0-9]+", value)
    tokens: List[str] = []
    for chunk in raw:
        if not chunk:
            continue
        parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", chunk)
        tokens.extend([p.lower() for p in parts if p])
    return [t for t in tokens if len(t) > 1]


def _derive_query(qualified_name: str, kind: str) -> str:
    parts = [p for p in qualified_name.split(".") if p]
    leaf = parts[-1] if parts else qualified_name
    scope = parts[-2] if len(parts) > 1 else ""
    leaf_tokens = _split_identifier_tokens(leaf)
    scope_tokens = _split_identifier_tokens(scope)
    kind_tokens = _split_identifier_tokens(kind)

    query_tokens = (scope_tokens[:2] + leaf_tokens[:4] + kind_tokens[:1])[:6]
    return " ".join(query_tokens) if query_tokens else qualified_name.lower()


def _parse_sampling_markdown(markdown_text: str) -> List[Dict[str, str]]:
    samples: List[Dict[str, str]] = []
    current: Dict[str, str] = {}

    for line in markdown_text.splitlines():
        line = line.strip()
        if line.startswith("## Sample "):
            if current.get("entity_id") and current.get("qualified_name"):
                samples.append(current)
            current = {}
            continue
        if line.startswith("- entity_id:"):
            m = re.search(r"`([^`]+)`", line)
            if m:
                current["entity_id"] = m.group(1)
            continue
        if line.startswith("- qualified_name:"):
            m = re.search(r"`([^`]+)`", line)
            if m:
                current["qualified_name"] = m.group(1)
            continue
        if line.startswith("- kind:"):
            m = re.search(r"`([^`]+)`", line)
            if m:
                current["kind"] = m.group(1)
            continue

    if current.get("entity_id") and current.get("qualified_name"):
        samples.append(current)
    return samples


def generate_candidate_labels(
    artifacts_dir: Path,
    output_path: Optional[Path] = None,
    max_labels: int = 20,
) -> Dict[str, Any]:
    """Generate candidate labels from latest sampling artifact."""
    artifact = _latest_sampling_artifact(artifacts_dir)
    text = artifact.read_text(encoding="utf-8", errors="ignore")
    samples = _parse_sampling_markdown(text)
    if not samples:
        raise ValueError(f"No parseable samples found in artifact: {artifact}")

    labels: List[Dict[str, Any]] = []
    for sample in samples[:max_labels]:
        qname = sample.get("qualified_name", "")
        kind = sample.get("kind", "")
        entity_id = sample.get("entity_id", "")
        if not qname or not entity_id:
            continue
        labels.append(
            {
                "query": _derive_query(qname, kind),
                "expected_entity_ids": [entity_id],
            }
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if output_path is None:
        output_path = artifacts_dir / f"labels_candidates_{timestamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(labels, indent=2), encoding="utf-8")

    return {
        "artifact": str(artifact),
        "output": str(output_path),
        "count": len(labels),
    }
