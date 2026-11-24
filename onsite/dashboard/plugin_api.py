"""HTTP routes for the desktop half, mounted at /api/plugins/onsite/.

Hermes imports this file by path, on its own, so it cannot use a
relative import. It finds the plugin package instead: the copy Hermes
already loaded in this process when there is one, so the routes share
its circuit breakers, or a private copy when there is not. State that
has to be shared between processes lives in files either way.

The handlers are plain `def`, not `async def`. FastAPI then runs them
on a worker thread, so a slow probe cannot stall the server's loop.

Both routes take a POST body. A folder path in a query string would
end up in access logs.
"""

from __future__ import annotations

import importlib.util
import sys

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi import Body


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
LOADED_NAME = "hermes_plugins.onsite"
PRIVATE_NAME = "onsite_dashboard"

router = APIRouter()


def load_private_package():
    spec = importlib.util.spec_from_file_location(
        PRIVATE_NAME,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )

    module = importlib.util.module_from_spec(spec)
    sys.modules[PRIVATE_NAME] = module
    spec.loader.exec_module(module)

    return module


def find_package():
    # Looked up on each request. A plugin reload replaces the module,
    # and a stale reference would keep answering from the old one.
    loaded = sys.modules.get(LOADED_NAME)

    if loaded is not None:
        return loaded

    private = sys.modules.get(PRIVATE_NAME)

    if private is not None:
        return private

    return load_private_package()


def find_runtime():
    package = find_package()

    return package, package.get_runtime()


def load_api(package):
    from importlib import import_module

    return import_module(f"{package.__name__}.api")


@router.post("/status")
def status(body: Any = Body(default=None)) -> dict:
    package, runtime = find_runtime()

    api = load_api(package)

    return api.status_payload(runtime, body, with_targets=api.wants_rows(body))


@router.post("/proposals")
def proposals(body: Any = Body(default=None)) -> dict:
    package, runtime = find_runtime()

    return load_api(package).proposals_payload(runtime, body)


@router.post("/targets")
def targets(body: Any = Body(default=None)) -> dict:
    package, runtime = find_runtime()

    return load_api(package).targets_payload(runtime)


@router.post("/targets/save")
def save_target(body: Any = Body(default=None)) -> dict:
    package, runtime = find_runtime()

    return load_api(package).save_target_payload(runtime, body)


@router.post("/targets/remove")
def remove_target(body: Any = Body(default=None)) -> dict:
    package, runtime = find_runtime()

    return load_api(package).remove_target_payload(runtime, body)


@router.post("/targets/test")
def test_target(body: Any = Body(default=None)) -> dict:
    package, runtime = find_runtime()

    return load_api(package).test_target_payload(runtime, body)


@router.post("/browse")
def browse(body: Any = Body(default=None)) -> dict:
    package, runtime = find_runtime()

    return load_api(package).browse_payload(runtime, body)


@router.post("/open")
def open_folder(body: Any = Body(default=None)) -> dict:
    package, runtime = find_runtime()

    return load_api(package).open_payload(runtime, body)

