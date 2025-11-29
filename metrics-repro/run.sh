#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="${SCRIPT_DIR}/output"

mkdir -p "${OUT_DIR}"

python3 "${SCRIPT_DIR}/collect_metrics.py" --repo-root "${REPO_ROOT}" --output-json "${OUT_DIR}/metrics.json" "$@"
python3 "${SCRIPT_DIR}/render_report.py" "${OUT_DIR}/metrics.json" > "${OUT_DIR}/metrics-report.md"
