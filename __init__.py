"""openloft-memory — plugin registration.

Loads under Hermes' namespaced plugin importer. ``register(ctx)`` is called
exactly once at startup; it must construct a fresh provider instance per load
(multiplexed-gateway rule) and never perform network I/O (catalog scan / doctor
block sockets during registration).
"""

from pathlib import Path

from .provider import OpenLoftMemoryProvider

SKILLS_DIR = Path(__file__).parent / "skills"


def register(ctx) -> None:
    ctx.register_memory_provider(OpenLoftMemoryProvider())
    skill_md = SKILLS_DIR / "memory-hygiene" / "SKILL.md"
    if skill_md.exists():
        ctx.register_skill(
            "memory-hygiene",
            skill_md,
            "Curate OpenLoft memory from the agent side",
        )
