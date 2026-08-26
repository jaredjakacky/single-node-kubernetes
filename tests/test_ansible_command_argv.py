#!/usr/bin/env python3

"""Reject malformed structured command arguments before Ansible executes them."""

from __future__ import annotations

import pathlib
import re
import unittest
from collections.abc import Iterator
from typing import Any

import yaml

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parent.parent
ANSIBLE_ROOT = REPOSITORY_ROOT / "ansible"
FIXTURES_ROOT = pathlib.Path(__file__).resolve().parent / "fixtures"
COMMAND_MODULES = frozenset({"command", "ansible.builtin.command"})
DYNAMIC_ITEM_ARGV = re.compile(r"^\{\{\s*item\s*\}\}$")


class CommandArgvContractError(AssertionError):
    """Describe malformed argv with its exact YAML location."""


def yaml_documents(path: pathlib.Path) -> Iterator[Any]:
    with path.open(encoding="utf-8") as stream:
        yield from yaml.safe_load_all(stream)


def validate_string_list(argv: Any, location: str) -> int:
    if not isinstance(argv, list):
        raise CommandArgvContractError(
            f"{location}: command argv must be a list; " f"found {type(argv).__name__}"
        )
    if not argv:
        raise CommandArgvContractError(f"{location}: command argv must not be empty")
    for index, argument in enumerate(argv):
        if not isinstance(argument, str):
            raise CommandArgvContractError(
                f"{location}[{index}]: command argv elements must be strings; "
                f"found {type(argument).__name__}"
            )
    return len(argv)


def validate_command(
    arguments: Any,
    task: dict[str, Any],
    location: str,
) -> tuple[int, int]:
    if not isinstance(arguments, dict) or "argv" not in arguments:
        return (0, 0)

    argv = arguments["argv"]
    if isinstance(argv, list):
        return (1, validate_string_list(argv, f"{location}.argv"))

    if not isinstance(argv, str) or DYNAMIC_ITEM_ARGV.fullmatch(argv) is None:
        raise CommandArgvContractError(
            f"{location}.argv: command argv must be a literal list, or dynamic "
            "argv must be exactly '{{ item }}' and resolve from a literal loop "
            "of string lists"
        )

    loop = task.get("loop")
    if not isinstance(loop, list) or not loop:
        raise CommandArgvContractError(
            f"{location}.argv: '{{{{ item }}}}' must resolve from a non-empty "
            "literal loop"
        )

    argument_count = 0
    for index, command in enumerate(loop):
        argument_count += validate_string_list(
            command,
            f"{location}.resolved_loop[{index}]",
        )
    return (len(loop), argument_count)


def validate_node(value: Any, location: str) -> tuple[int, int]:
    command_count = 0
    argument_count = 0
    if isinstance(value, list):
        for index, item in enumerate(value):
            commands, arguments = validate_node(item, f"{location}[{index}]")
            command_count += commands
            argument_count += arguments
        return (command_count, argument_count)
    if not isinstance(value, dict):
        return (command_count, argument_count)

    for key, item in value.items():
        item_location = f"{location}.{key}"
        if key in COMMAND_MODULES:
            commands, arguments = validate_command(item, value, item_location)
            command_count += commands
            argument_count += arguments
        commands, arguments = validate_node(item, item_location)
        command_count += commands
        argument_count += arguments
    return (command_count, argument_count)


def validate_path(path: pathlib.Path) -> tuple[int, int]:
    command_count = 0
    argument_count = 0
    for index, document in enumerate(yaml_documents(path)):
        if document is None:
            continue
        commands, arguments = validate_node(document, f"{path}:document[{index}]")
        command_count += commands
        argument_count += arguments
    return (command_count, argument_count)


def ansible_yaml_paths() -> list[pathlib.Path]:
    return sorted(
        path for pattern in ("*.yml", "*.yaml") for path in ANSIBLE_ROOT.rglob(pattern)
    )


class AnsibleCommandArgvContractTests(unittest.TestCase):
    def test_all_repository_ansible_yaml(self) -> None:
        paths = ansible_yaml_paths()
        self.assertTrue(paths, "no Ansible YAML files were discovered")
        command_count = 0
        argument_count = 0
        for path in paths:
            commands, arguments = validate_path(path)
            command_count += commands
            argument_count += arguments
        self.assertGreater(command_count, 0, "no structured command argv were found")
        self.assertGreater(argument_count, command_count)

    def test_unquoted_stdin_token_is_parsed_as_a_nested_null_and_rejected(self) -> None:
        fixture = FIXTURES_ROOT / "ansible-command-argv-invalid-null-sequence.yml"
        task = next(yaml_documents(fixture))[0]
        argv = task["ansible.builtin.command"]["argv"]
        self.assertEqual(argv[-1], [None])
        self.assertNotIsInstance(argv[-1], str)
        with self.assertRaisesRegex(
            CommandArgvContractError,
            r"argv\[3\]: command argv elements must be strings; found list",
        ):
            validate_path(fixture)

    def test_quoted_stdin_token_is_the_literal_string_and_is_accepted(self) -> None:
        fixture = FIXTURES_ROOT / "ansible-command-argv-valid-stdin.yml"
        task = next(yaml_documents(fixture))[0]
        argv = task["ansible.builtin.command"]["argv"]
        self.assertEqual(argv[-1], "-")
        self.assertIsInstance(argv[-1], str)
        self.assertEqual(validate_path(fixture), (1, 4))

    def test_every_non_string_literal_type_is_rejected(self) -> None:
        invalid_arguments = (["nested"], {"mapping": "value"}, None, 7, True, 1.5)
        for argument in invalid_arguments:
            with self.subTest(argument=argument):
                with self.assertRaisesRegex(
                    CommandArgvContractError,
                    r"command argv elements must be strings",
                ):
                    validate_string_list(["/usr/bin/example", argument], "fixture.argv")

    def test_literal_argv_container_must_be_a_list(self) -> None:
        for argv in ({"mapping": "value"}, None, 7, True, 1.5):
            with self.subTest(argv=argv):
                with self.assertRaisesRegex(
                    CommandArgvContractError,
                    r"command argv must be a literal list",
                ):
                    validate_command({"argv": argv}, {}, "fixture.command")

    def test_literal_loop_command_arrays_are_resolved_and_validated(self) -> None:
        valid_task = {
            "ansible.builtin.command": {"argv": "{{ item }}"},
            "loop": [["/usr/bin/kubectl", "get", "nodes"]],
        }
        self.assertEqual(validate_node(valid_task, "fixture"), (1, 3))

        invalid_task = {
            "ansible.builtin.command": {"argv": "{{ item }}"},
            "loop": [["/usr/bin/kubectl", None]],
        }
        with self.assertRaisesRegex(
            CommandArgvContractError,
            r"resolved_loop\[0\]\[1\].*found NoneType",
        ):
            validate_node(invalid_task, "fixture")

    def test_unresolved_dynamic_argv_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            CommandArgvContractError,
            r"dynamic argv must be exactly",
        ):
            validate_node(
                {"ansible.builtin.command": {"argv": "{{ generated_argv }}"}},
                "fixture",
            )


if __name__ == "__main__":
    unittest.main()
