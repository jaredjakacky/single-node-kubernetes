#!/usr/bin/env python3

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parent.parent
HELPER = REPOSITORY_ROOT / "scripts" / "verify-workflow-policy.py"
WORKFLOWS = REPOSITORY_ROOT / ".github" / "workflows"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
IMAGE_DIGEST = "a" * 64


def run_helper(directory: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER), str(directory)],
        check=False,
        capture_output=True,
        text=True,
    )


def require_success(name: str, workflow: str) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        directory = pathlib.Path(temporary_directory)
        (directory / "fixture.yaml").write_text(workflow, encoding="utf-8")
        completed = run_helper(directory)
    if completed.returncode != 0:
        raise AssertionError(
            f"{name}: expected success, stdout={completed.stdout!r}, "
            f"stderr={completed.stderr!r}"
        )
    print(f"PASS: {name}")


def require_failure(name: str, workflow: str, expected_reason: str) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        directory = pathlib.Path(temporary_directory)
        (directory / "invalid.yaml").write_text(workflow, encoding="utf-8")
        completed = run_helper(directory)
    if completed.returncode == 0:
        raise AssertionError(f"{name}: expected failure")
    if expected_reason not in completed.stderr:
        raise AssertionError(
            f"{name}: expected {expected_reason!r} in stderr={completed.stderr!r}"
        )
    print(f"PASS: {name}")


def workflow(
    *,
    runner: str = "ubuntu-24.04",
    checkout: str = f"actions/checkout@{COMMIT_SHA}",
    checkout_options: str = "        with:\n          persist-credentials: false\n",
    extra_steps: str = "",
) -> str:
    return f"""name: Fixture
on: push
jobs:
  validate:
    runs-on: {runner}
    steps:
      - uses: {checkout}
{checkout_options}{extra_steps}"""


def main() -> None:
    repository_result = run_helper(WORKFLOWS)
    if repository_result.returncode != 0:
        raise AssertionError(
            "repository workflows violate policy: "
            f"stdout={repository_result.stdout!r}, stderr={repository_result.stderr!r}"
        )
    print("PASS: repository workflows satisfy workflow execution policy")

    require_success("commit-pinned checkout accepted", workflow())
    require_success(
        "digest-pinned Docker action accepted",
        workflow(
            extra_steps=(
                "      - uses: docker://rhysd/actionlint:1.7.12@sha256:"
                f"{IMAGE_DIGEST}\n"
            )
        ),
    )
    require_success(
        "local action accepted",
        workflow(extra_steps="      - uses: ./.github/actions/local-check\n"),
    )
    require_success(
        "digest-pinned job container and service accepted",
        f"""name: Fixture
on: push
jobs:
  validate:
    runs-on: ubuntu-24.04
    container: registry.example.test/tool:1@sha256:{IMAGE_DIGEST}
    services:
      database:
        image: registry.example.test/db:1@sha256:{IMAGE_DIGEST}
    steps:
      - run: true
""",
    )
    require_success(
        "commit-pinned reusable workflow accepted",
        f"""name: Fixture
on: push
jobs:
  reusable:
    uses: example/project/.github/workflows/ci.yml@{COMMIT_SHA}
""",
    )

    require_failure(
        "ubuntu-latest rejected",
        workflow(runner="ubuntu-latest"),
        "moving GitHub-hosted runner aliases are prohibited",
    )
    require_failure(
        "nonstandard runner rejected",
        workflow(runner="ubuntu-22.04"),
        "runner must be exactly ubuntu-24.04",
    )
    require_failure(
        "mutable checkout tag rejected",
        workflow(checkout="actions/checkout@v7"),
        "full 40-character commit SHA",
    )
    require_failure(
        "mutable remote action tag rejected",
        workflow(extra_steps="      - uses: actions/setup-python@v7\n"),
        "full 40-character commit SHA",
    )
    require_failure(
        "tag-only Docker action rejected",
        workflow(extra_steps="      - uses: docker://rhysd/actionlint:1.7.12\n"),
        "Docker action references must use a full sha256 digest",
    )
    require_failure(
        "checkout without credential policy rejected",
        workflow(checkout_options=""),
        "actions/checkout must set persist-credentials: false",
    )
    require_failure(
        "checkout credential persistence rejected",
        workflow(
            checkout_options="        with:\n          persist-credentials: true\n"
        ),
        "actions/checkout must set persist-credentials: false",
    )
    require_failure(
        "case-varied checkout without credential policy rejected",
        workflow(checkout=f"Actions/Checkout@{COMMIT_SHA}", checkout_options=""),
        "actions/checkout must set persist-credentials: false",
    )
    require_failure(
        "quoted checkout false rejected",
        workflow(
            checkout_options='        with:\n          persist-credentials: "false"\n'
        ),
        "actions/checkout must set persist-credentials: false",
    )
    require_failure(
        "tag-only job container rejected",
        """name: Fixture
on: push
jobs:
  validate:
    runs-on: ubuntu-24.04
    container: python:3.14
    steps:
      - run: true
""",
        "container image references must use a full sha256 digest",
    )
    require_failure(
        "mutable reusable workflow rejected",
        """name: Fixture
on: push
jobs:
  reusable:
    uses: example/project/.github/workflows/ci.yml@main
""",
        "full 40-character commit SHA",
    )
    require_failure(
        "duplicate policy key rejected",
        """name: Fixture
on: push
jobs:
  validate:
    runs-on: ubuntu-24.04
    runs-on: ubuntu-latest
    steps:
      - run: true
""",
        "found duplicate key 'runs-on'",
    )
    print("All workflow execution policy tests passed")


if __name__ == "__main__":
    main()
