#!/usr/bin/env python3
"""Validate the signed app payload inside an exported TestFlight IPA."""
from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def _run(command: list[str]) -> str:
    result = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout


def _read_entitlements(app_path: Path) -> dict:
    raw = _run(["codesign", "-d", "--entitlements", ":-", str(app_path)])
    return plistlib.loads(raw.encode("utf-8"))


def _read_plist(path: Path) -> dict:
    with path.open("rb") as handle:
        return plistlib.load(handle)


def _extract_ipa_safely(archive: zipfile.ZipFile, destination: Path) -> None:
    for info in archive.infolist():
        member_path = Path(info.filename)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise SystemExit(f"Refusing unsafe IPA member path: {info.filename}")
        archive.extract(info, destination)


def verify_ipa(
    ipa_path: Path,
    expected_version: str,
    expected_build: str,
    expected_bundle_id: str,
) -> dict:
    if not ipa_path.is_file():
        raise SystemExit(f"IPA does not exist: {ipa_path}")

    with tempfile.TemporaryDirectory(prefix="oshireader-ipa-") as tmp:
        extract_dir = Path(tmp)
        with zipfile.ZipFile(ipa_path) as archive:
            _extract_ipa_safely(archive, extract_dir)

        apps = sorted((extract_dir / "Payload").glob("*.app"))
        if len(apps) != 1:
            raise SystemExit(f"Expected exactly one app payload, found {len(apps)}")

        app_path = apps[0]
        info = _read_plist(app_path / "Info.plist")
        entitlements = _read_entitlements(app_path)

        version = str(info.get("CFBundleShortVersionString", ""))
        build = str(info.get("CFBundleVersion", ""))
        bundle_id = str(info.get("CFBundleIdentifier", ""))
        configured_apns_environment = info.get("OshiReaderAPNSEnvironment")
        aps_environment = entitlements.get("aps-environment")
        get_task_allow = entitlements.get("get-task-allow")

        failures = []
        if bundle_id != expected_bundle_id:
            failures.append(f"bundle id {bundle_id!r} != expected {expected_bundle_id!r}")
        if version != expected_version:
            failures.append(f"version {version!r} != expected {expected_version!r}")
        if build != expected_build:
            failures.append(f"build {build!r} != expected {expected_build!r}")
        if aps_environment != "production":
            failures.append(f"aps-environment {aps_environment!r} != 'production'")
        if configured_apns_environment != "production":
            failures.append(
                "OshiReaderAPNSEnvironment "
                f"{configured_apns_environment!r} != 'production'"
            )
        if get_task_allow is not False:
            failures.append(f"get-task-allow {get_task_allow!r} != false")
        if failures:
            raise SystemExit("IPA verification failed: " + "; ".join(failures))

        return {
            "ipa": str(ipa_path),
            "app": app_path.name,
            "bundle_id": bundle_id,
            "version": version,
            "build": build,
            "aps_environment": aps_environment,
            "configured_apns_environment": configured_apns_environment,
            "get_task_allow": get_task_allow,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ipa", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--bundle-id", default="com.otterpia.oshireader.plus")
    args = parser.parse_args(argv)

    result = verify_ipa(args.ipa, args.version, args.build, args.bundle_id)
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
