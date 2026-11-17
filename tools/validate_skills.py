"""CI guard for the skills tree.

Enforces, for every skills/<name>/:
  - schemas/input.schema.json and schemas/output.schema.json parse and look like JSON Schema objects
  - config/default.json parses and declares an explicit boolean 'approved'
  - skill.py performs no database access (static import scan for connection libraries)
  - pyproject.toml declares zero dependencies (stdlib-only)
  - a contract test exists
Exit code 0 only when every skill passes.
"""

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"

BANNED_IMPORTS = [
    "psycopg", "psycopg2", "psycopg_pool", "asyncpg", "sqlalchemy", "aiosqlite",
    "sqlite3", "pymongo", "motor", "redis", "kafka", "aiokafka", "duckdb",
    "pymysql", "mysql", "cx_Oracle", "boto3", "couchdb", "cassandra", "elasticsearch",
]
BANNED_PATTERN = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z0-9_]+)")


def fail(errors, msg):
    errors.append(msg)
    print(f"FAIL  {msg}")


def check_skill(skill_dir: Path) -> list[str]:
    errors = []
    name = skill_dir.name

    # --- schemas parse and structurally resemble JSON Schema
    for schema_name in ("input.schema.json", "output.schema.json"):
        schema_path = skill_dir / "schemas" / schema_name
        if not schema_path.exists():
            fail(errors, f"{name}: missing schemas/{schema_name}")
            continue
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            fail(errors, f"{name}: schemas/{schema_name} is not valid JSON: {exc}")
            continue
        if not isinstance(schema, dict) or "type" not in schema:
            fail(errors, f"{name}: schemas/{schema_name} must be a JSON Schema object with a 'type' key")

    # --- config parses and has explicit boolean approval
    config_path = skill_dir / "config" / "default.json"
    if not config_path.exists():
        fail(errors, f"{name}: missing config/default.json")
    else:
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(config.get("approved"), bool):
                fail(errors, f"{name}: config/default.json 'approved' must be an explicit boolean")
        except ValueError as exc:
            fail(errors, f"{name}: config/default.json is not valid JSON: {exc}")

    # --- static import scan: no database access from skills
    skill_py = skill_dir / "skill.py"
    if not skill_py.exists():
        fail(errors, f"{name}: missing skill.py")
    else:
        for lineno, line in enumerate(skill_py.read_text(encoding="utf-8").splitlines(), start=1):
            match = BANNED_PATTERN.match(line)
            if match:
                module = match.group(1)
                if module in BANNED_IMPORTS:
                    fail(errors, f"{name}: skill.py:{lineno} imports banned module {module!r} (database access forbidden)")

    # --- pyproject declares zero dependencies (stdlib-only)
    pyproject_path = skill_dir / "pyproject.toml"
    if not pyproject_path.exists():
        fail(errors, f"{name}: missing pyproject.toml")
    else:
        try:
            pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
            dependencies = pyproject.get("project", {}).get("dependencies", [])
            if dependencies:
                fail(errors, f"{name}: pyproject.toml declares dependencies {dependencies}; skills must be stdlib-only")
        except tomllib.TOMLDecodeError as exc:
            fail(errors, f"{name}: pyproject.toml is not valid TOML: {exc}")

    # --- counterpart files exist
    for rel in ("run.ts", "package.json"):
        if not (skill_dir / rel).exists():
            fail(errors, f"{name}: missing {rel}")
    tests_dir = skill_dir / "tests"
    if not tests_dir.is_dir() or not any(tests_dir.glob("test_*.py")):
        fail(errors, f"{name}: missing contract test under tests/test_*.py")

    if not errors:
        print(f"PASS  {name}")
    return errors


def main() -> int:
    if not SKILLS.is_dir():
        print(f"FAIL  skills directory not found at {SKILLS}")
        return 1
    total_errors = []
    for entry in sorted(SKILLS.iterdir()):
        if entry.is_dir():
            total_errors.extend(check_skill(entry))
    if total_errors:
        print(f"\n{len(total_errors)} problem(s) found")
        return 1
    print("\nAll skills pass the schema, approval and isolation checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
