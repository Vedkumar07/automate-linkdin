#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo 'Install Python 3 first.' >&2; exit 1; }
python3 scripts/setup.py
if [[ "${1:-}" == "--local" ]]; then
  echo 'Local setup ready. Run python3 -m cadence init, then python3 -m cadence run.'
  exit 0
fi
docker compose version >/dev/null
docker info >/dev/null
export CADENCE_UID="$(id -u)" CADENCE_GID="$(id -g)"
# Save the identity for later direct docker compose commands.
python3 - <<'PY'
import os
from pathlib import Path
p = Path('.env')
lines = [line for line in p.read_text().splitlines() if not line.startswith(('CADENCE_UID=', 'CADENCE_GID='))]
lines += [f'{key}={os.environ[key]}' for key in ('CADENCE_UID', 'CADENCE_GID')]
p.write_text('\n'.join(lines) + '\n')
p.chmod(0o600)
PY
docker compose up -d --wait ollama
# Follow model choices in the user's editable settings.
models=$(python3 - <<'PYMODELS'
import json
from pathlib import Path
cfg = json.loads(Path('config.json').read_text())
for model in dict.fromkeys((cfg.get('model', 'qwen2.5:7b'), cfg.get('visionModel', 'gemma3:4b'))):
    print(model)
PYMODELS
)
while IFS= read -r model; do
  docker compose exec -T ollama ollama pull "$model"
done <<< "$models"

docker compose up -d --build --wait cadence
echo 'Cadence is ready: http://localhost:8787'
echo 'Edit .env for Telegram and LinkedIn, then run: docker compose restart cadence'
