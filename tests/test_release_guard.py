from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.release_guard import (
    ReleaseGuardError,
    ensure_tag_available,
    ensure_clean_worktree,
    manifest_version,
    validate_release,
    validate_tag,
    validate_version,
    verify_remote_branch,
    verify_target_commit,
)


@pytest.mark.parametrize(
    ("version", "channel"),
    [("2.5.2", "stable"), ("2.5.2-beta.1", "prerelease"), ("2.5.2-rc.2", "prerelease"), ("2.5.2-dev.1", "prerelease")],
)
def test_validate_version_accepts_release_spellings(version: str, channel: str) -> None:
    validate_version(version, channel)


@pytest.mark.parametrize(
    ("version", "channel"),
    [("2.5.2-beta.1", "stable"), ("2.5.2", "prerelease"), ("2.5.2-01", "prerelease"), ("2.5.2-", "prerelease")],
)
def test_validate_version_rejects_wrong_channel_or_suffix(version: str, channel: str) -> None:
    with pytest.raises(ReleaseGuardError):
        validate_version(version, channel)


def test_validate_tag_requires_manifest_version_with_v_prefix() -> None:
    validate_tag("v2.5.2-beta.1", "2.5.2-beta.1")
    with pytest.raises(ReleaseGuardError):
        validate_tag("v2.5.2", "2.5.2-beta.1")


def test_verify_target_commit_requires_full_existing_sha(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "file").write_text("ok", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "file"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "test"], check=True)
    commit = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()

    verify_target_commit(tmp_path, commit)
    with pytest.raises(ReleaseGuardError):
        verify_target_commit(tmp_path, commit[:8])


def test_validate_release_reads_manifest_at_target_and_rejects_existing_tag(tmp_path: Path) -> None:
    manifest = tmp_path / "custom_components/osservaprezzi_carburanti"
    manifest.mkdir(parents=True)
    (manifest / "manifest.json").write_text(json.dumps({"version": "2.5.2"}), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "test"], check=True)
    commit = subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["git", "-C", str(tmp_path), "tag", "v2.5.2"], check=True)

    assert manifest_version(tmp_path, commit) == "2.5.2"
    with pytest.raises(ReleaseGuardError, match="already exists locally"):
        ensure_tag_available(tmp_path, "v2.5.2", "origin")

    subprocess.run(["git", "-C", str(tmp_path), "tag", "-d", "v2.5.2"], check=True, capture_output=True)
    remote = tmp_path.parent / f"{tmp_path.name}.remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", str(remote)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "push", "-q", "origin", "HEAD:refs/heads/master"], check=True)
    ensure_clean_worktree(tmp_path)
    verify_remote_branch(tmp_path, "origin", "master", commit)
    with pytest.raises(ReleaseGuardError, match="does not match"):
        verify_remote_branch(tmp_path, "origin", "master", "0" * 40)
    (tmp_path / "untracked").write_text("dirty", encoding="utf-8")
    with pytest.raises(ReleaseGuardError, match="working tree is dirty"):
        ensure_clean_worktree(tmp_path)
    (tmp_path / "untracked").unlink()
    validate_release(tmp_path, "2.5.2", "v2.5.2", commit, "stable", "master", "origin")
    subprocess.run(["git", "-C", str(tmp_path), "tag", "v2.5.2"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "push", "-q", "origin", "v2.5.2"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "tag", "-d", "v2.5.2"], check=True, capture_output=True)

    with pytest.raises(ReleaseGuardError, match="already exists on origin"):
        ensure_tag_available(tmp_path, "v2.5.2", "origin")
