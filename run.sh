#!/usr/bin/env bash
# Routine Runner launcher (POSIX). Forwards all arguments to run.py.
#   ./run.sh serve --reload
#   ./run.sh seed
set -euo pipefail

cd "$(dirname "$0")"

# Activate a local virtualenv if present.
if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

PYTHON="${PYTHON:-python3}"
exec "$PYTHON" run.py "$@"
