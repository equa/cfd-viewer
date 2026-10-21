"""doit tasks: the same task names as the equa-ci service archetype.

    uv run doit list
    uv run doit ci          # everything a change must pass

cfd-viewer follows equa-ci "in the shadow" (openspec standalone-viewer, design
D9): the task names and what they mean match ``equa.ci.tasks``, but they are
implemented here, without the ``equa-ci`` package from the private feed. A
formal adoption would replace this file with ``create_tasks(...)`` and keep
every name.

What runs where:

- ``lint_py``  ruff check (the archetype lints; it does not format-check)
- ``test_py``  pytest without the browser suite: sources, endpoints, the
               recorded HTTP contract, and the VTK pipeline suite
- ``test_ui``  the browser suite (Chromium, a few minutes)
- ``package``  the wheel, client included, and a check of what it contains
- ``requirements_check``  requirements.txt is exactly what the lock exports

The pipeline suite renders, so ``test_py`` needs a GL backend (CLAUDE.md,
"Environment"); the browser suite needs Playwright's Chromium.
"""

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
PY_PATHS = ["src", "foamviz", "main.py", "hatch_build.py", "dodo.py", "tests", "tools"]

# Files a wheel must carry to be a working install (viewer-build spec).
WHEEL_MUST_HAVE = [
    "equa/cfd_viewer/__main__.py",
    "equa/cfd_viewer/web/index.html",
]
EXPORT = ["uv", "export", "--quiet", "--no-dev", "--no-hashes", "--no-emit-project",
          "--format", "requirements-txt"]

DOIT_CONFIG = {"default_tasks": ["ci"], "verbosity": 2}


def _web_sources():
    return [str(p) for p in sorted((WEB / "src").rglob("*")) if p.is_file()] + [
        str(WEB / "index.html"), str(WEB / "vite.config.js"), str(WEB / "package.json"),
    ]


def task_lint_py():
    """Lint Python with ruff."""
    return {"actions": [["uv", "run", "ruff", "check", *PY_PATHS]], "verbosity": 2}


def task_lint():
    """Run every linter."""
    return {"actions": None, "task_dep": ["lint_py"]}


def task_test_py():
    """Run pytest without the browser suite."""
    return {"actions": [["uv", "run", "pytest", "tests/", "-v", "-m", "not browser"]],
            "verbosity": 2}


def task_test_ui():
    """Run the browser suite (tests/check_client.py) in Chromium."""
    return {"actions": [["uv", "run", "pytest", "tests/", "-v", "-m", "browser"]],
            "task_dep": ["web_build"], "verbosity": 2}


def task_test():
    """Run every test suite."""
    return {"actions": None, "task_dep": ["test_py", "test_ui"]}


def task_web_install():
    """Install web/'s npm dependencies from its lockfile."""
    return {
        "actions": [["npm", "ci", "--no-audit", "--no-fund", "--prefix", str(WEB)]],
        "file_dep": [str(WEB / "package-lock.json")],
        "targets": [str(WEB / "node_modules" / ".package-lock.json")],
    }


def task_web_build():
    """Build the client into web/dist."""
    return {
        "actions": [["npm", "run", "--silent", "--prefix", str(WEB), "build"]],
        "task_dep": ["web_install"],
        "file_dep": _web_sources(),
        "targets": [str(WEB / "dist" / "index.html")],
    }


def check_wheel(wheel):
    """Fail unless *wheel* holds everything a working install needs."""
    names = set(zipfile.ZipFile(wheel).namelist())
    missing = [n for n in WHEEL_MUST_HAVE if n not in names]
    if missing:
        print(f"{wheel.name} is missing: {', '.join(missing)}", file=sys.stderr)
        return False
    print(f"{wheel.name}: {len(names)} files, client included")
    return True


def _package():
    with tempfile.TemporaryDirectory(prefix="cfdv-wheel-") as out:
        subprocess.run(["uv", "build", "--quiet", "--wheel", "-o", out], cwd=ROOT, check=True)
        wheels = list(Path(out).glob("*.whl"))
        return len(wheels) == 1 and check_wheel(wheels[0])


def task_package():
    """Build the wheel and check it carries the client."""
    return {"actions": [_package], "task_dep": ["web_build"], "verbosity": 2}


def _without_header(text):
    """Drop uv's leading comment block, which records the exact command line."""
    lines = text.splitlines()
    while lines and lines[0].startswith("#"):
        lines.pop(0)
    return lines


def _requirements_check():
    exported = subprocess.run(EXPORT, cwd=ROOT, check=True, capture_output=True,
                              text=True).stdout
    committed = (ROOT / "requirements.txt").read_text()
    if _without_header(exported) != _without_header(committed):
        print("requirements.txt differs from the lock; regenerate it with\n  "
              + " ".join(EXPORT) + " -o requirements.txt", file=sys.stderr)
        return False
    return True


def task_requirements_check():
    """Check requirements.txt is exactly what uv.lock exports (cfd-viz installs it)."""
    return {"actions": [_requirements_check], "file_dep": ["uv.lock", "requirements.txt"],
            "verbosity": 2}


def task_ci():
    """Everything a change must pass."""
    return {"actions": None,
            "task_dep": ["lint", "requirements_check", "test", "package"]}
