"""
PowerShell steps must be ASCII.

Windows PowerShell 5.1 reads the runner's generated .ps1 in the system ANSI
code page. A UTF-8 em dash (E2 80 94) becomes "â€”", and its last byte is
cp1252's right double quotation mark — which PowerShell treats as a string
delimiter. One warning message with an em dash broke the whole
"Use Git Bash and long paths (Windows)" step with a ParserError.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
FILES = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")) + \
    sorted((REPO_ROOT / ".github" / "actions").glob("*/action.yml"))


def _steps(doc):
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            yield step
    for step in ((doc.get("runs") or {}).get("steps") or []):
        yield step


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_powershell_run_blocks_are_ascii(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    offenders = []
    for step in _steps(doc):
        if str(step.get("shell", "")).lower() in ("powershell", "pwsh"):
            for n, line in enumerate(str(step.get("run", "")).splitlines(), 1):
                if any(ord(c) > 127 for c in line):
                    offenders.append(f"{step.get('name')}: line {n}: {line.strip()[:80]}")
    assert not offenders, "non-ASCII in PowerShell (breaks Windows PowerShell 5.1):\n" + "\n".join(offenders)
