import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skill.py"
PAYLOAD = {"history": [10, 12, 11, 13, 12, 14, 13, 15], "horizon": 3, "method": "ses"}


def test_skill_contract():
    result = subprocess.run([sys.executable, str(SKILL)], input=json.dumps(PAYLOAD),
                            capture_output=True, text=True, timeout=10)
    body = json.loads(result.stdout)
    assert result.returncode == 0
    assert body["ok"] is True
    assert isinstance(body.get("output"), dict)
