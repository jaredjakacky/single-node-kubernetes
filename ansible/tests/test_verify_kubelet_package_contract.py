#!/usr/bin/env python3
"""Regression tests for the kubelet Debian package contract verifier."""

from __future__ import annotations

import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

PACKAGE_VERSION = "1.36.4-1.1"
EXPECTED_DATA_MEMBERS = {
    "etc/kubernetes": (tarfile.DIRTYPE, 0o775, b""),
    "etc/kubernetes/manifests": (tarfile.DIRTYPE, 0o775, b""),
    "etc/kubernetes/manifests/.kubelet-keep": (tarfile.REGTYPE, 0o644, b""),
    "var/lib/kubelet": (tarfile.DIRTYPE, 0o775, b""),
    "var/lib/kubelet/.kubelet-keep": (tarfile.REGTYPE, 0o644, b""),
}
VERIFIER = Path(__file__).with_name("verify-kubelet-package-contract.py")


def make_tar(members: dict[str, tuple[bytes, int, bytes]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path, (member_type, mode, content) in members.items():
            member = tarfile.TarInfo(f"./{path}")
            member.type = member_type
            member.mode = mode
            member.uid = 0
            member.gid = 0
            member.uname = "root"
            member.gname = "root"
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content) if member.isreg() else None)
    return output.getvalue()


def make_control_tar() -> bytes:
    control = "\n".join(
        [
            "Package: kubelet",
            f"Version: {PACKAGE_VERSION}",
            "Architecture: amd64",
            "",
        ]
    ).encode()
    return make_tar({"control": (tarfile.REGTYPE, 0o644, control)})


def ar_member(name: str, content: bytes) -> bytes:
    header = (
        f"{name}/".ljust(16)
        + "0".ljust(12)
        + "0".ljust(6)
        + "0".ljust(6)
        + "100644".ljust(8)
        + str(len(content)).ljust(10)
        + "`\n"
    ).encode("ascii")
    return header + content + (b"\n" if len(content) % 2 else b"")


def make_deb(path: Path, data_members: dict[str, tuple[bytes, int, bytes]]) -> None:
    package = b"!<arch>\n"
    package += ar_member("debian-binary", b"2.0\n")
    package += ar_member("control.tar.gz", make_control_tar())
    package += ar_member("data.tar.gz", make_tar(data_members))
    path.write_bytes(package)


class KubeletPackageContractTest(unittest.TestCase):
    def run_verifier(
        self, data_members: dict[str, tuple[bytes, int, bytes]]
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "kubelet.deb"
            make_deb(package, data_members)
            return subprocess.run(
                [
                    sys.executable,
                    str(VERIFIER),
                    "--version",
                    PACKAGE_VERSION,
                    str(package),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_accepts_exact_relevant_footprint(self) -> None:
        result = self.run_verifier(EXPECTED_DATA_MEMBERS)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ignores_unrelated_package_entry(self) -> None:
        data_members = dict(EXPECTED_DATA_MEMBERS)
        data_members["usr/bin/kubelet"] = (tarfile.REGTYPE, 0o755, b"binary")

        result = self.run_verifier(data_members)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_unexpected_relevant_root_entry(self) -> None:
        data_members = dict(EXPECTED_DATA_MEMBERS)
        data_members["var/lib/etcd/.unexpected"] = (tarfile.REGTYPE, 0o600, b"")

        result = self.run_verifier(data_members)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected=['var/lib/etcd/.unexpected']", result.stderr)

    def test_rejects_missing_relevant_root_entry(self) -> None:
        data_members = dict(EXPECTED_DATA_MEMBERS)
        del data_members["var/lib/kubelet/.kubelet-keep"]

        result = self.run_verifier(data_members)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing=['var/lib/kubelet/.kubelet-keep']", result.stderr)


if __name__ == "__main__":
    unittest.main()
