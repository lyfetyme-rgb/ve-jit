"""Approved local AI skills — skill execution contract.

Contract (polyglot, subprocess-isolated):

  TypeScript run.ts  (in production edge) ── JSON via stdin ──▶  Python skill.py
  Python returns structured JSON via stdout; the caller validates, then the
  payload passes the deterministic policy engine before any workflow runs.

In this Python application the same contract is honoured: the runner spawns
`skills/<name>/skill.py` as an isolated subprocess with JSON on stdin, a hard
timeout, and parses JSON on stdout. Skills never receive database handles and
are physically unable to write operational state from within the subprocess.

Only skills marked `approved=True` in `skills/<name>/config/default.json` may
be executed.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

SKILLS_ROOT = Path(__file__).resolve().parents[3] / "skills"
DEFAULT_TIMEOUT_SECONDS = 10


class SkillError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def list_skills() -> list[dict]:
    if not SKILLS_ROOT.exists():
        return []
    found = []
    for skill_dir in sorted(SKILLS_ROOT.iterdir()):
        if not skill_dir.is_dir():
            continue
        config = _read_json(skill_dir / "config" / "default.json")
        schema = _read_json(skill_dir / "schemas" / "input.schema.json")
        found.append({
            "name": skill_dir.name,
            "skill_py": (skill_dir / "skill.py").exists(),
            "run_ts": (skill_dir / "run.ts").exists(),
            "approved": bool(config.get("approved", False)) if isinstance(config, dict) else False,
            "config": config if isinstance(config, dict) else {},
            "input_schema": schema if isinstance(schema, dict) else None,
        })
    return found


def run_skill(name: str, payload: dict[str, Any], *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    safe_name = Path(name).name
    skill_dir = SKILLS_ROOT / safe_name
    skill_py = skill_dir / "skill.py"
    if not skill_py.exists():
        raise SkillError(404, f"skill {safe_name!r} not found at {skill_py}")

    config = _read_json(skill_dir / "config" / "default.json")
    if not isinstance(config, dict) or not config.get("approved", False):
        raise SkillError(403, f"skill {safe_name!r} is not approved; set approved=true in skills/{safe_name}/config/default.json")

    try:
        completed = subprocess.run(
            [sys.executable, str(skill_py)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise SkillError(504, f"skill {safe_name!r} timed out after {timeout}s")

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0:
        raise SkillError(500, f"skill {safe_name!r} exited {completed.returncode}: {stderr[-500:]}")
    try:
        result = json.loads(stdout)
    except ValueError:
        raise SkillError(502, f"skill {safe_name!r} returned non-JSON stdout: {stdout[-200:]!r}")

    if not isinstance(result, dict) or "ok" not in result:
        raise SkillError(502, f"skill {safe_name!r} output must be a JSON object with an 'ok' field")

    return {
        "skill": safe_name,
        "ok": bool(result.get("ok")),
        "output": result.get("output"),
        "confidence": result.get("confidence"),
        "errors": result.get("errors"),
        "duration_ms": None,  # measured by caller if needed; subprocess timing is not exposed portably
        "stdout_bytes": len(stdout),
    }


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
