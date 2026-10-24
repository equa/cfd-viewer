"""doit build orchestration for equa-cfd-viewer."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from equa.ci.tasks import create_tasks

globals().update(create_tasks(
    name="equa_cfd_viewer",
    # No build leg: this repo ships Python, an image, or nothing, so `doit build`
    # is inert.
    build_legs=set(),
    # package_files: the artifact paths `doit package` zips, relative to
    # install/<cfg>/. Scaffolded empty; fill in as the build starts installing
    # real files (see the sibling repos' dodo.py for the pattern).
    package_files=[],
    lint_kinds={"py", "md", },
    # lint_paths: per-kind path overrides. The scaffold lints the doit shim
    # and tests; extend as source lands (e.g. {"c": ["src", "tests"]}).
    lint_paths={
        "py": ["src/", "foamviz/", "main.py", "hatch_build.py", "dodo.py", "tests/", "tools/"],
        "md": ["docs", "*.md"],
    },
    test_kinds={"py", },
    integration_dir=None,
    include={
        "typecheck_py",
        "check_gitignored_refs",
        "adr_index",
        "adr_index_check",
        "sync_secrets",
        "relock",
        "cspell_stub",
    },
))

# Repo-owned tasks (adopt-service-archetype D4): what no lint or test kind
# covers -- the client build, the browser suite, the wheel, and the two
# contracts cfd-backend's cfd-viz depends on.

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"

# Files a wheel must carry to be a working install (viewer-build spec).
WHEEL_MUST_HAVE = [
    "equa/cfd_viewer/__main__.py",
    "equa/cfd_viewer/web/index.html",
]
EXPORT = ["uv", "export", "--quiet", "--no-dev", "--no-hashes", "--no-emit-project",
          "--format", "requirements-txt"]


def _web_sources() -> list[str]:
    return [str(p) for p in sorted((WEB / "src").rglob("*")) if p.is_file()] + [
        str(WEB / "index.html"), str(WEB / "vite.config.js"), str(WEB / "package.json"),
    ]


def task_web_install() -> dict[str, Any]:
    """Install web/'s npm dependencies from its lockfile."""
    return {
        "actions": [["npm", "ci", "--no-audit", "--no-fund", "--prefix", str(WEB)]],
        "file_dep": [str(WEB / "package-lock.json")],
        "targets": [str(WEB / "node_modules" / ".package-lock.json")],
    }


def task_web_build() -> dict[str, Any]:
    """Build the client into web/dist."""
    return {
        "actions": [["npm", "run", "--silent", "--prefix", str(WEB), "build"]],
        "task_dep": ["web_install"],
        "file_dep": _web_sources(),
        "targets": [str(WEB / "dist" / "index.html")],
    }


def task_chromium() -> dict[str, Any]:
    """Install Playwright's headless Chromium (and, as root, its system libraries)."""
    # As thermarest's test_ui does: CI runs as root in equa-base-ci, so
    # --with-deps can apt-get the libraries. Without root that would fail; the
    # dev container has them already.
    deps = ["--with-deps"] if os.name == "posix" and os.geteuid() == 0 else []
    return {"actions": [["uv", "run", "playwright", "install", *deps, "--only-shell", "chromium"]],
            "uptodate": [False], "verbosity": 2}


# The factory's test_py runs `pytest -m "not integration"`, and a -m on the
# command line replaces one in addopts, so the browser suite is deselected here.
def task_test_py() -> dict[str, Any]:
    """Run pytest without the browser suite."""
    return {"actions": [["uv", "run", "pytest", "tests/", "-v",
                         "-m", "not integration and not browser"]],
            "verbosity": 2}


def task_test_ui() -> dict[str, Any]:
    """Run the browser suite (tests/check_client.py) in Chromium."""
    return {"actions": [["uv", "run", "pytest", "tests/", "-v", "-m", "browser"]],
            "task_dep": ["web_build", "chromium"], "verbosity": 2}


_factory_task_test = task_test  # noqa: F821 -- defined by create_tasks above


def task_test() -> dict[str, Any]:
    """Run all test suites for this component."""
    task = _factory_task_test()
    task["task_dep"] = [*task["task_dep"], "test_ui"]
    return task


def check_wheel(wheel: Path) -> bool:
    """Fail unless *wheel* holds everything a working install needs."""
    names = set(zipfile.ZipFile(wheel).namelist())
    missing = [n for n in WHEEL_MUST_HAVE if n not in names]
    if missing:
        print(f"{wheel.name} is missing: {', '.join(missing)}", file=sys.stderr)
        return False
    print(f"{wheel.name}: {len(names)} files, client included")
    return True


def _wheel_check() -> bool:
    with tempfile.TemporaryDirectory(prefix="cfdv-wheel-") as out:
        subprocess.run(["uv", "build", "--quiet", "--wheel", "-o", out], cwd=ROOT, check=True)
        wheels = list(Path(out).glob("*.whl"))
        return len(wheels) == 1 and check_wheel(wheels[0])


# Not `package`: the factory's package zips package_files per OS (D4).
def task_wheel_check() -> dict[str, Any]:
    """Build the wheel and check it carries the client."""
    return {"actions": [_wheel_check], "task_dep": ["web_build"], "verbosity": 2}


def _without_header(text: str) -> list[str]:
    """Drop uv's leading comment block, which records the exact command line."""
    lines = text.splitlines()
    while lines and lines[0].startswith("#"):
        lines.pop(0)
    return lines


def _requirements_check() -> bool:
    exported = subprocess.run(EXPORT, cwd=ROOT, check=True, capture_output=True,
                              text=True).stdout
    committed = (ROOT / "requirements.txt").read_text()
    if _without_header(exported) != _without_header(committed):
        print("requirements.txt differs from the lock; regenerate it with\n  "
              + " ".join(EXPORT) + " -o requirements.txt", file=sys.stderr)
        return False
    return True


def task_requirements_check() -> dict[str, Any]:
    """Check requirements.txt is exactly what uv.lock exports (cfd-viz installs it)."""
    return {"actions": [_requirements_check], "file_dep": ["uv.lock", "requirements.txt"],
            "verbosity": 2}


def task_cockpit_contract() -> dict[str, Any]:
    """Mirror cfd-backend's cfd-viz build and runtime on a clone of HEAD (commit first)."""
    return {"actions": [["uv", "run", "python", "tests/check_cockpit_contract.py"]],
            "task_dep": ["chromium"], "verbosity": 2}


# The devcontainer's FROM tag must follow the base the template renders into
# ci.yml (adopt-service-archetype D10), as thermarest checks its own.
_DEVCONTAINER = Path(".devcontainer/cfd-viewer/Containerfile")
_CI_WORKFLOW = Path(".github/workflows/ci.yml")
_BASE_TAG = re.compile(r"ghcr\.io/equa/equa-base[a-z-]*:(\S+)")


def task_check_devcontainer_base() -> dict[str, Any]:
    """Fail unless the devcontainer's base tag matches ci.yml's."""

    def check() -> bool:
        from_tag = next(m.group(1) for line in _DEVCONTAINER.read_text().splitlines()
                        if line.startswith("FROM") and (m := _BASE_TAG.search(line)))
        ci_tags = set(_BASE_TAG.findall(_CI_WORKFLOW.read_text()))
        if ci_tags != {from_tag}:
            print(f"{_DEVCONTAINER} FROM tag {from_tag} != {_CI_WORKFLOW} base tag "
                  f"{', '.join(sorted(ci_tags))}")
            return False
        return True

    return {"actions": [check], "file_dep": [str(_DEVCONTAINER), str(_CI_WORKFLOW)],
            "verbosity": 2}


_factory_task_ci = task_ci  # noqa: F821 -- defined by create_tasks above


def task_ci() -> dict[str, Any]:
    """Run the full local CI suite (lint + test + optional typecheck)."""
    task = _factory_task_ci()
    task["task_dep"] = [*task["task_dep"], "wheel_check", "requirements_check", "cockpit_contract",
                        "check_devcontainer_base"]
    return task
