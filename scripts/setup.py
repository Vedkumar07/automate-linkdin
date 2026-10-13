#!/usr/bin/env python3
"""Create editable local settings without replacing existing files."""
from pathlib import Path
import shutil


def setup(root: Path) -> None:
    for name in ('config.json', 'voice.md', 'profile.md', 'stories.md'):
        target = root / name
        if not target.exists():
            shutil.copyfile(root / 'templates' / name, target)
            print(f'Created {name}')
    env = root / '.env'
    if not env.exists():
        shutil.copyfile(root / '.env.example', env)
        env.chmod(0o600)
        print('Created .env (permissions: owner only)')
    (root / 'data').mkdir(exist_ok=True)
    print('Local settings are ready. Add your writing samples and credentials before running Cadence.')


if __name__ == '__main__':
    setup(Path(__file__).resolve().parent.parent)
