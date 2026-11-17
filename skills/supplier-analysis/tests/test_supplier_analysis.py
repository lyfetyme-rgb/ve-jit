import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skill.py"
PAYLOAD = {"supplier": {"name": "Acme", "sla_otif_pct": 95, "sla_fill_rate_pct": 98},
           "pos": [{"units_ordered": 100, "units_received": 92, "on_time": False}]}


def test_skill_contract():
    result = subprocess.run([sys.executable, str(SKILL)], input=json.dumps(PAYLOAD),
                            capture_output=True, text=True, timeout=10)
    body = json.loads(result.stdout)
    assert result.returncode == 0
    assert body["ok"] is True
    assert isinstance(body.get("output"), dict)
