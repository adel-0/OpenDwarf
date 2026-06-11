"""Scenario spec loader.

A scenario YAML has this structure:

    name: wolf-survival
    description: Survive or deliberately flee a single-wolf encounter.
    save: wolf_encounter          # DF save directory name under DF's save/
    max_wallclock_seconds: 600
    max_llm_calls: 50             # optional hard limit; runner kills session if exceeded
    success_predicate:
      all_of:
        - survived: true
        - llm_calls: 50

The ``save`` field names the DF save directory (e.g. ``save/wolf_encounter/``
inside the Dwarf Fortress data directory).  Capturing a save is a manual step —
see evals/README.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Scenario:
    name: str
    description: str
    save: str
    max_wallclock_seconds: int
    success_predicate: Any
    max_llm_calls: int | None = None

    @classmethod
    def load(cls, path: Path) -> "Scenario":
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return cls(
            name=data["name"],
            description=data["description"],
            save=data["save"],
            max_wallclock_seconds=int(data["max_wallclock_seconds"]),
            success_predicate=data["success_predicate"],
            max_llm_calls=data.get("max_llm_calls"),
        )

    @classmethod
    def find(cls, name_or_path: str, scenarios_dir: Path | None = None) -> "Scenario":
        """Find a scenario by name or file path."""
        p = Path(name_or_path)
        if p.exists():
            return cls.load(p)
        # Search in scenarios_dir
        if scenarios_dir is None:
            scenarios_dir = Path(__file__).parent / "scenarios"
        # Try exact filename first, then with .yaml/.yml suffix
        for candidate in [
            scenarios_dir / name_or_path,
            scenarios_dir / f"{name_or_path}.yaml",
            scenarios_dir / f"{name_or_path}.yml",
        ]:
            if candidate.exists():
                return cls.load(candidate)
        raise FileNotFoundError(
            f"Scenario '{name_or_path}' not found. "
            f"Checked {scenarios_dir}. "
            f"Available: {[f.stem for f in scenarios_dir.glob('*.yaml')]}"
        )
