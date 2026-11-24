"""
Test suite bootstrap for FantasyRecap.
Allows running `python3 -m unittest discover -s tests -v` both from the parent
repository root and from inside `FantasyRecap/` even in sandboxed environments
where the parent directory is not listable.
"""

import importlib.util
import sys
from pathlib import Path

_RECAP_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _RECAP_DIR.parent

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if "FantasyRecap" not in sys.modules:
    try:
        import FantasyRecap  # noqa: F401
    except ModuleNotFoundError:
        spec = importlib.util.spec_from_file_location(
            "FantasyRecap",
            str(_RECAP_DIR / "__init__.py"),
            submodule_search_locations=[str(_RECAP_DIR)],
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["FantasyRecap"] = mod
        spec.loader.exec_module(mod)
