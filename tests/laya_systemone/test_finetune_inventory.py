"""Fine-tune mode is extra-var only: no inventory file may turn it on."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_inventory_never_enables_finetune_mode():
    root = Path(__file__).resolve().parents[2] / "inventory"
    offenders = [
        str(path.relative_to(root))
        for path in sorted(root.rglob("*.yml"))
        if "laya_systemone_finetune_enabled" in (yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    ]
    assert offenders == [], f"fine-tune mode is extra-var only: {offenders}"
