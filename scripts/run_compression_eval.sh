#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEMANTICIR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CLI="${SEMANTICIR_DIR}/cli.py"
DEFAULT_FIXTURE="${SEMANTICIR_DIR}/_fastapi-users-master"
ARTIFACTS_DIR="${SEMANTICIR_DIR}/tests/_artifacts"
TIMESTAMP="$(date -u +"%Y%m%dT%H%M%SZ")"

FIXTURE_PATH="${1:-${DEFAULT_FIXTURE}}"
LABEL_COUNT="${2:-20}"

if [[ ! -d "${FIXTURE_PATH}" ]]; then
  echo "error: fixture path not found: ${FIXTURE_PATH}" >&2
  echo "usage: $(basename "$0") [fixture_path] [label_count]" >&2
  exit 2
fi

mkdir -p "${ARTIFACTS_DIR}"

echo "== SemanticIR Compression Eval Quick Start =="
echo "semanticir_dir: ${SEMANTICIR_DIR}"
echo "fixture_path: ${FIXTURE_PATH}"
echo "artifacts_dir: ${ARTIFACTS_DIR}"
echo "timestamp: ${TIMESTAMP}"
echo

echo "[1/6] Index fixture in hybrid mode"
python3 "${CLI}" index "${FIXTURE_PATH}" --mode hybrid
echo

echo "[2/6] Generate random before/after compression samples"
python3 -m unittest "${SEMANTICIR_DIR}/tests/test_compression_sampling.py"
echo

echo "[3/6] Write labels template + candidate labels from latest sample artifact"
LABEL_TEMPLATE="${ARTIFACTS_DIR}/labels_template_${TIMESTAMP}.json"
LABEL_CANDIDATES="${ARTIFACTS_DIR}/labels_candidates_${TIMESTAMP}.json"
python3 "${CLI}" labels-template --output "${LABEL_TEMPLATE}"
python3 "${CLI}" labels-from-samples --artifacts-dir "${ARTIFACTS_DIR}" --output "${LABEL_CANDIDATES}" --count "${LABEL_COUNT}"
echo

echo "[4/6] Run unlabeled mode scoreboard (a,b,hybrid)"
SCOREBOARD_UNLABELED="${ARTIFACTS_DIR}/scoreboard_unlabeled_${TIMESTAMP}.md"
python3 "${CLI}" eval "${FIXTURE_PATH}" --modes a,b,hybrid --output "${SCOREBOARD_UNLABELED}"
echo

echo "[5/6] Run labeled mode scoreboard (using generated candidates)"
SCOREBOARD_LABELED="${ARTIFACTS_DIR}/scoreboard_labeled_${TIMESTAMP}.md"
python3 "${CLI}" eval "${FIXTURE_PATH}" --modes a,b,hybrid --labels "${LABEL_CANDIDATES}" --output "${SCOREBOARD_LABELED}"
echo

echo "[6/6] Done"
echo "Outputs:"
echo "  labels_template:   ${LABEL_TEMPLATE}"
echo "  labels_candidates: ${LABEL_CANDIDATES}"
echo "  scoreboard_raw:    ${SCOREBOARD_UNLABELED}"
echo "  scoreboard_labeled:${SCOREBOARD_LABELED}"
echo "  samples:           ${ARTIFACTS_DIR}/compression_samples_<timestamp>.md"
