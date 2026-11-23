"""Load the plugin as a package named `onsite` for the tests.

The plugin lives in the `onsite` folder of the repository. Hermes
loads it by path under its own name. The tests do the same under a
fixed one, so they never depend on what is on the import path.
"""

from __future__ import annotations

import importlib.util
import sys

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent / "onsite"
PACKAGE = "onsite"


def load_package() -> None:
    if PACKAGE in sys.modules:
        return

    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )

    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)


load_package()
