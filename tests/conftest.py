"""Test bootstrap — register the plugin directory as an importable package.

The on-disk directory name contains hyphens (fine for Hermes' namespaced
loader, invalid for importlib), so tests import under the synthetic package
name ``openloft_memory``. ``__init__.py`` is registered but NOT executed — it
imports ``agent.*`` which only exists inside a Hermes runtime.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = "openloft_memory"

if PKG not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PKG, str(ROOT / "__init__.py"), submodule_search_locations=[str(ROOT)])
    pkg = importlib.util.module_from_spec(spec)
    pkg.__path__ = [str(ROOT)]
    sys.modules[PKG] = pkg
