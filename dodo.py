"""doit build orchestration for equa-cfd-viewer."""

from __future__ import annotations

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
        "py": ["src/", "dodo.py", "tests/"],
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
