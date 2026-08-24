#!/usr/bin/env python3
"""Verify bootstrap validation task names render without Ansible warnings."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

ANSIBLE_ROOT = Path(__file__).resolve().parent.parent
ANSIBLE_PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
PLAYBOOK = """---
- name: Test bootstrap validation task-name rendering
  hosts: localhost
  connection: local
  gather_facts: false

  tasks:
    - name: Import the real bootstrap validation path without executing modules
      ansible.builtin.import_role:
        name: kubernetes_bootstrap
        tasks_from: validate.yml
      when: false
"""
EXPECTED_TASK_NAMES = (
    "TASK [kubernetes_bootstrap : Read control-plane static Pod manifests]",
    "TASK [kubernetes_bootstrap : Derive static Pod manifest invariants]",
)
FORBIDDEN_OUTPUT = (
    "Encountered 1 template error",
    "'item' is undefined",
    "[WARNING]:",
)


def main() -> None:
    environment = os.environ.copy()
    environment["ANSIBLE_FORCE_COLOR"] = "0"
    environment["NO_COLOR"] = "1"

    with tempfile.TemporaryDirectory() as directory:
        playbook = Path(directory) / "task-name-warning.yml"
        playbook.write_text(PLAYBOOK, encoding="utf-8")
        completed = subprocess.run(
            [str(ANSIBLE_PLAYBOOK), "--inventory", "localhost,", str(playbook)],
            cwd=ANSIBLE_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise AssertionError(
            "bootstrap task-name warning fixture failed: "
            f"returncode={completed.returncode}\n{output}"
        )
    for task_name in EXPECTED_TASK_NAMES:
        if task_name not in output:
            raise AssertionError(f"expected rendered task name {task_name!r}\n{output}")
    for warning in FORBIDDEN_OUTPUT:
        if warning in output:
            raise AssertionError(f"unexpected Ansible warning {warning!r}\n{output}")

    print("Bootstrap validation task names rendered without Ansible warnings")


if __name__ == "__main__":
    main()
