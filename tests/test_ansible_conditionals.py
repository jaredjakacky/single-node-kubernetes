#!/usr/bin/env python3

"""Reject malformed Ansible conditionals before ansible-core executes them."""

from __future__ import annotations

import pathlib
import unittest
from collections.abc import Iterator
from typing import Any

import yaml

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parent.parent
ANSIBLE_ROOT = REPOSITORY_ROOT / "ansible"
FIXTURES_ROOT = pathlib.Path(__file__).resolve().parent / "fixtures"
CONDITIONAL_KEYS = frozenset({"when", "until", "failed_when", "changed_when"})
ASSERT_MODULES = frozenset({"assert", "ansible.builtin.assert"})
TEMPLATE_DELIMITERS = ("{{", "}}", "{%", "%}")


class ConditionalContractError(AssertionError):
    """Describe an unsafe conditional with its exact YAML location."""


def yaml_documents(path: pathlib.Path) -> Iterator[Any]:
    with path.open(encoding="utf-8") as stream:
        yield from yaml.safe_load_all(stream)


def validate_expression(expression: Any, location: str, *, allow_bool: bool) -> None:
    if isinstance(expression, bool) and allow_bool:
        return
    if not isinstance(expression, str):
        raise ConditionalContractError(
            f"{location}: conditional expressions must be strings"
            f"{' or booleans' if allow_bool else ''}; "
            f"found {type(expression).__name__}"
        )
    if not expression.strip():
        raise ConditionalContractError(
            f"{location}: conditional expressions must not be empty"
        )
    delimiter = next(
        (value for value in TEMPLATE_DELIMITERS if value in expression), None
    )
    if delimiter is not None:
        raise ConditionalContractError(
            f"{location}: conditionals are raw Jinja expressions and must not "
            f"contain the {delimiter!r} template delimiter"
        )


def validate_condition_value(
    value: Any,
    location: str,
    *,
    allow_bool: bool,
) -> int:
    if isinstance(value, list):
        if not value:
            raise ConditionalContractError(
                f"{location}: conditional expression lists must not be empty"
            )
        for index, expression in enumerate(value):
            validate_expression(
                expression,
                f"{location}[{index}]",
                allow_bool=allow_bool,
            )
        return len(value)
    validate_expression(value, location, allow_bool=allow_bool)
    return 1


def validate_node(value: Any, location: str) -> int:
    expression_count = 0
    if isinstance(value, list):
        for index, item in enumerate(value):
            expression_count += validate_node(item, f"{location}[{index}]")
        return expression_count
    if not isinstance(value, dict):
        return expression_count

    for key, item in value.items():
        item_location = f"{location}.{key}"
        if key in CONDITIONAL_KEYS:
            expression_count += validate_condition_value(
                item,
                item_location,
                allow_bool=True,
            )
        if key in ASSERT_MODULES:
            if not isinstance(item, dict):
                raise ConditionalContractError(
                    f"{item_location}: assert must use structured arguments"
                )
            if "that" not in item:
                raise ConditionalContractError(
                    f"{item_location}: assert must define that"
                )
            expression_count += validate_condition_value(
                item["that"],
                f"{item_location}.that",
                allow_bool=False,
            )
        expression_count += validate_node(item, item_location)
    return expression_count


def validate_path(path: pathlib.Path) -> int:
    expression_count = 0
    for index, document in enumerate(yaml_documents(path)):
        if document is not None:
            expression_count += validate_node(document, f"{path}:document[{index}]")
    return expression_count


def ansible_yaml_paths() -> list[pathlib.Path]:
    return sorted(
        path for pattern in ("*.yml", "*.yaml") for path in ANSIBLE_ROOT.rglob(pattern)
    )


class AnsibleConditionalContractTests(unittest.TestCase):
    def test_all_repository_ansible_yaml(self) -> None:
        paths = ansible_yaml_paths()
        self.assertTrue(paths, "no Ansible YAML files were discovered")
        expression_count = sum(validate_path(path) for path in paths)
        self.assertGreater(expression_count, 0, "no Ansible conditionals were found")

    def test_colon_space_mapping_regression_is_rejected(self) -> None:
        fixture = FIXTURES_ROOT / "ansible-conditionals-invalid-mapping.yml"
        with self.assertRaisesRegex(
            ConditionalContractError,
            r"conditional expressions must be strings; found dict",
        ):
            validate_path(fixture)

    def test_valid_block_scalar_and_list_conditionals_are_accepted(self) -> None:
        fixture = FIXTURES_ROOT / "ansible-conditionals-valid.yml"
        self.assertEqual(validate_path(fixture), 7)

    def test_malformed_condition_keyword_shapes_are_rejected(self) -> None:
        for keyword in CONDITIONAL_KEYS:
            with self.subTest(keyword=keyword):
                with self.assertRaisesRegex(
                    ConditionalContractError,
                    r"conditional expressions must be strings or booleans; "
                    r"found dict",
                ):
                    validate_node(
                        [{keyword: {"unexpected": "mapping"}}],
                        "fixture",
                    )

    def test_embedded_templates_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            ConditionalContractError,
            r"must not contain the '\{\{' template delimiter",
        ):
            validate_node([{"when": "{{ release_action == 'install' }}"}], "fixture")


if __name__ == "__main__":
    unittest.main()
