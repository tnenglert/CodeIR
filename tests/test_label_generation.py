from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from index.labelgen import generate_candidate_labels, write_labels_template


SAMPLE_ARTIFACT = """# Compression Sample Report

## Sample 1
- entity_id: `TSTN.41`
- qualified_name: `TestResetPassword.test_invalid_token`
- kind: `async_method`

### After (compressed IR)
```text
AMT TSTN.41 C=reset_password_token #AUTH #TEST
```

## Sample 2
- entity_id: `AUTH.01`
- qualified_name: `auth.verify_access_token`
- kind: `function`

### After (compressed IR)
```text
FN AUTH.01 C=decode_jwt F=R #AUTH #CORE
```
"""


class TestLabelGeneration(unittest.TestCase):
    def test_template_and_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = root / "artifacts"
            artifacts.mkdir(parents=True, exist_ok=True)
            artifact_file = artifacts / "compression_samples_20260212T123000Z.md"
            artifact_file.write_text(SAMPLE_ARTIFACT, encoding="utf-8")

            template_path = root / "labels_template.json"
            write_labels_template(template_path)
            self.assertTrue(template_path.exists())
            template = json.loads(template_path.read_text(encoding="utf-8"))
            self.assertIsInstance(template, list)
            self.assertTrue(template)
            self.assertIn("query", template[0])
            self.assertIn("expected_entity_ids", template[0])

            output_path = root / "labels_generated.json"
            result = generate_candidate_labels(
                artifacts_dir=artifacts,
                output_path=output_path,
                max_labels=5,
            )
            self.assertEqual(result["count"], 2)
            self.assertTrue(output_path.exists())

            labels = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(len(labels), 2)
            self.assertIn("query", labels[0])
            self.assertIn("expected_entity_ids", labels[0])
            self.assertEqual(labels[0]["expected_entity_ids"], ["TSTN.41"])


if __name__ == "__main__":
    unittest.main()
