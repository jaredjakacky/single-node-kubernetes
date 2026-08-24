#!/usr/bin/env python3

"""Enforce immutable execution policy for repository GitHub Actions workflows."""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Any

import yaml
from yaml.constructor import ConstructorError

PINNED_RUNNER = "ubuntu-24.04"
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
DOCKER_ACTION = re.compile(r"docker://[^@\s]+@sha256:[0-9a-f]{64}")
CONTAINER_IMAGE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")
REMOTE_ACTION = re.compile(r"[^/@\s]+/[^@\s]+")


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping
)


@dataclass(frozen=True)
class Violation:
    path: pathlib.Path
    reference: str
    reason: str

    def render(self, root: pathlib.Path) -> str:
        try:
            display_path = self.path.relative_to(root)
        except ValueError:
            display_path = self.path
        return f"{display_path}: {self.reference}: {self.reason}"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "workflow_directory",
        nargs="?",
        type=pathlib.Path,
        default=pathlib.Path(".github/workflows"),
    )
    return parser.parse_args()


def violation(path: pathlib.Path, reference: str, reason: str) -> list[Violation]:
    return [Violation(path, reference, reason)]


def validate_runner(
    path: pathlib.Path, reference: str, runner: object
) -> list[Violation]:
    if runner == PINNED_RUNNER:
        return []
    if isinstance(runner, str) and re.fullmatch(
        r"(?:ubuntu|windows|macos)-latest", runner
    ):
        reason = (
            f"moving GitHub-hosted runner aliases are prohibited; use {PINNED_RUNNER}"
        )
    else:
        reason = f"runner must be exactly {PINNED_RUNNER}"
    return violation(path, reference, reason)


def validate_action(
    path: pathlib.Path, reference: str, action: object
) -> list[Violation]:
    if not isinstance(action, str):
        return violation(path, reference, "action reference must be a scalar string")
    if action.startswith("./"):
        return []
    if action.startswith("docker://"):
        if DOCKER_ACTION.fullmatch(action):
            return []
        return violation(
            path,
            reference,
            "Docker action references must use a full sha256 digest",
        )
    if "@" not in action:
        return violation(
            path,
            reference,
            "remote GitHub Action reference is missing an immutable commit SHA",
        )
    location, revision = action.rsplit("@", maxsplit=1)
    if not REMOTE_ACTION.fullmatch(location) or not COMMIT_SHA.fullmatch(revision):
        return violation(
            path,
            reference,
            "remote GitHub Action references must use a full 40-character commit SHA",
        )
    return []


def validate_container_image(
    path: pathlib.Path, reference: str, image: object
) -> list[Violation]:
    if isinstance(image, str) and CONTAINER_IMAGE.fullmatch(image):
        return []
    return violation(
        path,
        reference,
        "container image references must use a full sha256 digest",
    )


def action_location(action: object) -> str | None:
    if (
        not isinstance(action, str)
        or action.startswith("docker://")
        or "@" not in action
    ):
        return None
    return action.rsplit("@", maxsplit=1)[0]


def validate_checkout(
    path: pathlib.Path, reference: str, step: dict[object, object]
) -> list[Violation]:
    location = action_location(step.get("uses"))
    if location is None or location.casefold() != "actions/checkout":
        return []
    options = step.get("with")
    if not isinstance(options, dict) or options.get("persist-credentials") is not False:
        return violation(
            path,
            f"{reference}.with.persist-credentials",
            "actions/checkout must set persist-credentials: false",
        )
    return []


def validate_steps(
    path: pathlib.Path, job_reference: str, steps: object
) -> list[Violation]:
    if steps is None:
        return []
    if not isinstance(steps, list):
        return violation(path, f"{job_reference}.steps", "steps must be a sequence")

    violations: list[Violation] = []
    for index, step in enumerate(steps):
        reference = f"{job_reference}.steps[{index}]"
        if not isinstance(step, dict):
            violations.extend(violation(path, reference, "step must be a mapping"))
            continue
        if "uses" in step:
            violations.extend(validate_action(path, f"{reference}.uses", step["uses"]))
            violations.extend(validate_checkout(path, reference, step))
    return violations


def validate_job(path: pathlib.Path, job_name: object, job: object) -> list[Violation]:
    reference = f"jobs.{job_name}"
    if not isinstance(job, dict):
        return violation(path, reference, "job must be a mapping")

    violations: list[Violation] = []
    if "uses" in job:
        violations.extend(validate_action(path, f"{reference}.uses", job["uses"]))
        if "runs-on" in job:
            violations.extend(
                validate_runner(path, f"{reference}.runs-on", job["runs-on"])
            )
    else:
        violations.extend(
            validate_runner(path, f"{reference}.runs-on", job.get("runs-on"))
        )

    if "container" in job:
        container = job["container"]
        image = container.get("image") if isinstance(container, dict) else container
        violations.extend(
            validate_container_image(path, f"{reference}.container", image)
        )

    services = job.get("services")
    if services is not None:
        if not isinstance(services, dict):
            violations.extend(
                violation(path, f"{reference}.services", "services must be a mapping")
            )
        else:
            for service_name, service in services.items():
                image = service.get("image") if isinstance(service, dict) else None
                violations.extend(
                    validate_container_image(
                        path, f"{reference}.services.{service_name}.image", image
                    )
                )

    violations.extend(validate_steps(path, reference, job.get("steps")))
    return violations


def scan_workflow(path: pathlib.Path) -> list[Violation]:
    try:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        return violation(path, "document", f"cannot parse workflow YAML: {error}")
    if not isinstance(document, dict):
        return violation(path, "document", "workflow must be a YAML mapping")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        return violation(path, "jobs", "workflow must define at least one job mapping")
    return [
        item
        for job_name, job in jobs.items()
        for item in validate_job(path, job_name, job)
    ]


def workflow_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted((*directory.glob("*.yml"), *directory.glob("*.yaml")))


def main() -> None:
    arguments = parse_arguments()
    directory = arguments.workflow_directory.resolve()
    files = workflow_files(directory)
    if not files:
        raise SystemExit(f"No GitHub Actions workflows found under {directory}")

    violations = [item for path in files for item in scan_workflow(path)]
    if violations:
        root = pathlib.Path.cwd().resolve()
        for item in violations:
            print(item.render(root), file=sys.stderr)
        raise SystemExit(1)

    print(
        f"Workflow policy check passed: files={len(files)} "
        f"runner={PINNED_RUNNER} immutable_actions=true "
        "checkout_credentials=false"
    )


if __name__ == "__main__":
    main()
