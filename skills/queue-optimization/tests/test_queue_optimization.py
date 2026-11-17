import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skill.py"
PAYLOAD = {"lanes": [{"lane_id": "lane_1", "customers_in_line": 8, "average_wait_time": 420, "cashier_speed": 18}], "target_wait_seconds": 180}


def test_skill_contract():
    result = subprocess.run([sys.executable, str(SKILL)], input=json.dumps(PAYLOAD),
                            capture_output=True, text=True, timeout=10)
    body = json.loads(result.stdout)
    assert result.returncode == 0
    assert body["ok"] is True
    assert isinstance(body.get("output"), dict)
