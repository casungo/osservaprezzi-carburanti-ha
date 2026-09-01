#!/usr/bin/env python3
"""Validate the inputs used to create an Osservaprezzi release."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


MANIFEST_PATH = Path("custom_components/osservaprezzi_carburanti/manifest.json")
_NUMBER = r"(?:0|[1-9][0-9]*)"
_STABLE_VERSION = re.compile(rf"^{_NUMBER}\.{_NUMBER}\.{_NUMBER}$")
_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
_PRERELEASE_VERSION = re.compile(
    rf"^{_NUMBER}\.{_NUMBER}\.{_NUMBER}-{_IDENTIFIER}(?:\.{_IDENTIFIER})*$"
)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class ReleaseGuardError(ValueError):
    """Raised when a release input cannot be verified."""


def validate_version(version: str, channel: str) -> None:
    """Require the project's stable or prerelease version spelling."""
    if channel == "stable" and _STABLE_VERSION.fullmatch(version):
        return
    if channel == "prerelease" and _PRERELEASE_VERSION.fullmatch(version):
        return
    if channel == "stable":
        expected = "plain X.Y.Z"
    else:
        expected = "X.Y.Z with a valid SemVer prerelease suffix"
    raise ReleaseGuardError(f"{channel} version must be {expected}: {version!r}")


def validate_tag(tag: str, version: str) -> None:
    """Require the tag to name exactly the requested manifest version."""
    expected = f"v{version}"
    if tag != expected:
        raise ReleaseGuardError(f"tag must be {expected!r}, got {tag!r}")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise ReleaseGuardError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def verify_target_commit(repo: Path, commit: str) -> None:
    """Require a full, locally resolvable commit ID."""
    if not _COMMIT.fullmatch(commit):
        raise ReleaseGuardError("target commit must be a 40-character lowercase SHA-1")
    resolved = _git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}")
    if resolved != commit:
        raise ReleaseGuardError(f"target commit does not resolve exactly: {commit}")


def ensure_clean_worktree(repo: Path) -> None:
    """Reject tracked or untracked changes before release verification."""
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ReleaseGuardError("working tree is dirty; commit or remove all changes first")


def verify_remote_branch(repo: Path, remote: str, branch: str, commit: str) -> None:
    """Require the target commit to equal the live remote branch head."""
    try:
        checked_branch = _git(repo, "check-ref-format", "--branch", branch)
    except ReleaseGuardError as exc:
        raise ReleaseGuardError(f"invalid release branch: {branch!r}") from exc
    if checked_branch != branch:
        raise ReleaseGuardError(f"release branch must be explicit: {branch!r}")

    _git(repo, "remote", "get-url", remote)
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", remote, f"refs/heads/{branch}"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 2:
        raise ReleaseGuardError(f"remote branch does not exist on {remote}: {branch}")
    if result.returncode:
        detail = result.stderr.strip() or "remote branch lookup failed"
        raise ReleaseGuardError(f"could not verify branch on {remote}: {detail}")

    lines = result.stdout.strip().splitlines()
    if len(lines) != 1:
        raise ReleaseGuardError(f"could not verify unique remote branch head: {branch}")
    remote_commit, remote_ref = lines[0].split("\t", maxsplit=1)
    expected_ref = f"refs/heads/{branch}"
    if remote_ref != expected_ref or remote_commit != commit:
        raise ReleaseGuardError(
            f"target commit {commit} does not match {remote}/{branch} head {remote_commit}"
        )


def manifest_version(repo: Path, commit: str) -> str:
    """Read the manifest version from the exact release target commit."""
    try:
        data = json.loads(_git(repo, "show", f"{commit}:{MANIFEST_PATH}"))
    except json.JSONDecodeError as exc:
        raise ReleaseGuardError("manifest in target commit is not valid JSON") from exc
    version = data.get("version")
    if not isinstance(version, str):
        raise ReleaseGuardError("manifest version must be a string")
    return version


def ensure_tag_available(repo: Path, tag: str, remote: str) -> None:
    """Reject a tag that already exists locally or on the release remote."""
    local = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/tags/{tag}"],
        cwd=repo,
        check=False,
    )
    if local.returncode == 0:
        raise ReleaseGuardError(f"tag already exists locally: {tag}")
    if local.returncode > 1:
        raise ReleaseGuardError("could not inspect local tags")

    _git(repo, "remote", "get-url", remote)
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--refs", remote, f"refs/tags/{tag}"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        raise ReleaseGuardError(f"tag already exists on {remote}: {tag}")
    if result.returncode != 2:
        detail = result.stderr.strip() or "remote tag lookup failed"
        raise ReleaseGuardError(f"could not verify tags on {remote}: {detail}")


def validate_release(
    repo: Path,
    version: str,
    tag: str,
    commit: str,
    channel: str,
    branch: str,
    remote: str,
) -> None:
    """Run every pre-tag release guard."""
    validate_version(version, channel)
    validate_tag(tag, version)
    verify_target_commit(repo, commit)
    ensure_clean_worktree(repo)
    verify_remote_branch(repo, remote, branch, commit)
    target_version = manifest_version(repo, commit)
    if target_version != version:
        raise ReleaseGuardError(
            f"manifest version in {commit} is {target_version!r}, expected {version!r}"
        )
    ensure_tag_available(repo, tag, remote)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=("stable", "prerelease"), required=True)
    parser.add_argument("--version", required=True, help="manifest version without the v prefix")
    parser.add_argument("--tag", required=True, help="exact Git tag, normally v<version>")
    parser.add_argument("--commit", required=True, help="full 40-character target commit SHA")
    parser.add_argument("--branch", required=True, help="remote branch whose head must equal --commit")
    parser.add_argument("--remote", default="origin", help="remote checked for an existing tag")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        validate_release(
            repo,
            args.version,
            args.tag,
            args.commit,
            args.channel,
            args.branch,
            args.remote,
        )
    except ReleaseGuardError as exc:
        print(f"release guard failed: {exc}", file=sys.stderr)
        return 2
    print(f"release guard passed: {args.tag} -> {args.commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
