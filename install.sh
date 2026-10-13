#!/usr/bin/env bash
# Run local setup first; install a service for this checkout.
set -euo pipefail
cd "$(dirname "$0")"
python3 scripts/setup.py
CHECKOUT="$(pwd)"
PYTHON="$CHECKOUT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then PYTHON="$(command -v python3)"; fi
mkdir -p "$HOME/.config/systemd/user"
python3 - "$CHECKOUT" "$PYTHON" <<'PY'
import sys
from pathlib import Path
checkout, python = sys.argv[1:]
def quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
service = Path('cadence.service').read_text()
service = service.replace('WorkingDirectory=%h/cadence', 'WorkingDirectory=' + checkout.replace('%', '%%'))
service = service.replace('ExecStart=/usr/bin/python3 -m cadence run', 'ExecStart=' + quote(python) + ' -m cadence run')
(Path.home() / '.config/systemd/user/cadence.service').write_text(service)
PY
systemctl --user daemon-reload
systemctl --user enable --now cadence.service
echo 'Cadence is running. Dashboard: http://localhost:8787'
echo 'Logs: journalctl --user -u cadence -f'
