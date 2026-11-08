"""openloft-memory — plugin registration.

Loads under Hermes' namespaced plugin importer. ``register(ctx)`` is called
exactly once at startup; it must construct a fresh provider instance per load
(multiplexed-gateway rule) and never perform network I/O (catalog scan / doctor
block sockets during registration).

The file imports cleanly from BOTH parentages: as a real package sibling
inside Hermes, and as an unparented file — pytest 9's Directory collector wraps
the plugin root itself in a Package node and imports its ``__init__.py``
standalone whenever tests run from this repo. That harness-only path registers
the directory as a proper (parented) package and re-executes once; Hermes never
takes it.
"""

from pathlib import Path

if __package__ in (None, ""):
    import importlib.util
    import sys

    _spec = importlib.util.spec_from_file_location(
        "openloft_memory", __file__,
        submodule_search_locations=[str(Path(__file__).resolve().parent)])
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["openloft_memory"] = _mod
    _spec.loader.exec_module(_mod)
    globals().update(_mod.__dict__)
else:
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
