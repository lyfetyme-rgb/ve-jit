import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skill.py"
PAYLOAD = {"items": [{"sku": "DAIRY-001", "on_hand": 12, "reorder_point": 10, "daily_velocity": 4.5}]}


def test_skill_contract():
    result = subprocess.run([sys.executable, str(SKILL)], input=json.dumps(PAYLOAD),
                            capture_output=True, text=True, timeout=10)
    body = json.loads(result.stdout)
    assert result.returncode == 0
    assert body["ok"] is True
    assert isinstance(body.get("output"), dict)
