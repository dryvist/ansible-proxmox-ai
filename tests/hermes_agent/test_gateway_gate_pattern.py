"""The one-gateway gate's process pattern must match the real ExecStart line
and reject a Studio-spawned child by its parent pid. Runs bare or under pytest."""
import subprocess
from pathlib import Path

from _role_files import role_tasks_text

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
PS = (
    "   1234       1 /usr/local/lib/hermes-agent/venv/bin/python /usr/local/lib/hermes-agent/hermes gateway run --replace\n"
    "   4121     347 /usr/local/lib/hermes-agent/venv/bin/python /usr/local/lib/hermes-agent/hermes gateway run --replace\n"
    "   9999       1 /usr/bin/python3 /usr/local/bin/hermes-web-ui serve\n"
)


def _awk_program() -> str:
    text = role_tasks_text(ROLE, "verify.yml")
    line = next(l for l in text.splitlines() if "ps -eo pid=,ppid=,args=" in l)
    return line.split("awk ", 1)[1].strip().strip("'")


def test_the_gate_matches_the_real_gateway_line_and_reports_its_parent():
    out = subprocess.run(["awk", _awk_program()], input=PS, capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == ["1234 1", "4121 347"]


if __name__ == "__main__":
    test_the_gate_matches_the_real_gateway_line_and_reports_its_parent()
    print("all checks passed")
