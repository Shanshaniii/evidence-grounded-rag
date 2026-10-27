"""Resolve the supplied bundle or an explicitly selected legacy project.

No filesystem scans, network calls, model access, or authorization reads occur.
The bundled dependencies are the uploaded historical implementations, not stubs.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

PRODUCTION_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = PRODUCTION_ROOT.parent


def project_root() -> Path:
    value = os.environ.get("RAG_PROJECT_ROOT")
    return Path(value).expanduser().resolve() if value else PRODUCTION_ROOT.parents[1]


def dependency_root(name: str) -> Path:
    explicit = os.environ.get("RAG_DEPENDENCY_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve() / name
    packaged = BUNDLE_ROOT / "dependencies" / name
    return packaged if packaged.is_dir() else project_root() / "experiments" / name


def checker_source() -> Path:
    explicit = os.environ.get("RAG_CHECKER_SRC")
    if explicit:
        return Path(explicit).expanduser().resolve()
    bundled = BUNDLE_ROOT / "candidate" / "src"
    return bundled if bundled.is_dir() else project_root() / "next_v2" / "src"


def configure_imports() -> None:
    paths = [PRODUCTION_ROOT / "src", checker_source(),
             dependency_root("E03_dev_runner_v4") / "src"]
    for path in reversed(paths):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def load_dependency(module_name: str, relative_path: str):
    name, _, suffix = relative_path.partition("/")
    path = dependency_root(name) / suffix
    if not path.is_file():
        raise FileNotFoundError(f"required_bundled_dependency_missing: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot_load_supplied_dependency: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
