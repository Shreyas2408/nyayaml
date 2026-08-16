"""Prompt registry — loads and manages prompt templates from YAML files."""

from pathlib import Path
from typing import Any

import yaml


PROMPTS_DIR = Path(__file__).parent


def load_yaml(filepath: Path) -> dict[str, Any]:
    """Load a YAML file and return its contents."""
    with open(filepath, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class PromptRegistry:
    """Registry for loading and accessing prompt templates."""

    def __init__(self) -> None:
        self._system_prompts: dict[str, dict] = {}
        self._templates: dict[str, dict] = {}
        self._load_all()

    def _load_all(self) -> None:
        """Load all prompts from system_prompts/ and templates/ directories."""
        sys_dir = PROMPTS_DIR / "system_prompts"
        tmpl_dir = PROMPTS_DIR / "templates"

        if sys_dir.exists():
            for f in sys_dir.glob("*.yaml"):
                self._system_prompts[f.stem] = load_yaml(f)

        if tmpl_dir.exists():
            for f in tmpl_dir.glob("*.yaml"):
                self._templates[f.stem] = load_yaml(f)

    def get_system_prompt(self, name: str) -> dict:
        """Get a system prompt by name."""
        return self._system_prompts.get(name, {})

    def get_template(self, name: str) -> dict:
        """Get a prompt template by name."""
        return self._templates.get(name, {})


# Singleton instance
prompt_registry = PromptRegistry()
