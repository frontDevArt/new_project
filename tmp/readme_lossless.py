"""Фаза 7 M3.5: README ужат без потерь.

Каждая непустая строка README коммита <rev> (по умолчанию 17e7f9d — «Результат
фазы 6», последний большой README) должна дословно найтись в нынешнем
README.md или в docs/устройство.md.

    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe tmp/readme_lossless.py [rev]
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(rev: str = "17e7f9d") -> int:
    old = subprocess.run(["git", "show", f"{rev}:README.md"], cwd=ROOT, check=True,
                         capture_output=True).stdout.decode("utf-8")
    have = {line.strip()
            for path in (ROOT / "README.md", ROOT / "docs" / "устройство.md")
            for line in path.read_text(encoding="utf-8").splitlines()}
    lines = [(n, line.strip()) for n, line in enumerate(old.splitlines(), 1) if line.strip()]
    lost = [(n, line) for n, line in lines if line not in have]
    print(f"README {rev}: непустых строк {len(lines)}; не найдено дословно: {len(lost)}")
    for n, line in lost:
        print(f"  {n}: {line}")
    return 1 if lost else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
