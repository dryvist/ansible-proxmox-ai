"""Per-model numeric values belong in llm-models.d, not role projections."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
MODEL_NUMBER = re.compile(
    r"^\s*(?:context_window|max_input_tokens|max_output_tokens|max_tokens|"
    r"input_cost_per_token|output_cost_per_token|rpm|tpm|max_parallel_requests|"
    r"max_budget):\s*(?:\d+(?:\.\d*)?|\.\d+)\s*(?:#.*)?$"
)


def test_model_numbers_are_projected_from_the_single_catalog():
    source_dirs = [ROOT / "roles/llm_router"]
    violations = [
        f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}"
        for directory in source_dirs
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.suffix in {".yml", ".yaml", ".j2"}
        for line_number, line in enumerate(path.read_text().splitlines(), 1)
        if MODEL_NUMBER.match(line)
    ]
    assert not violations, "Per-model numeric literals outside llm-models.d:\n" + "\n".join(violations)
