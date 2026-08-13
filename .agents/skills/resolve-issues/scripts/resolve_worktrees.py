#!/usr/bin/env python3
"""Managed worktree lifecycle for repo-local resolve-issues runs.

The manager isolates parallel writers, integrates reviewed commits on a run
branch, and removes only work that is safely reachable from the published
target. It never stashes, resets, force-removes, copies credentials, or edits a
tracked ignore file.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


SCHEMA_VERSION = 1
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SENSITIVE = (
    re.compile(r"(^|/)\.env($|\.)"),
    re.compile(r"\.(pem|key|p12|pfx)$", re.IGNORECASE),
    re.compile(r"(^|/)(credentials?|secrets?)(\.|/|$)", re.IGNORECASE),
)
SAFE_ENV_NAMES = {".env.example", ".env.sample", ".env.template"}
FORBIDDEN_GIT = {"reset", "clean", "stash", "pull", "checkout", "switch"}


class LifecycleError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_token(value: str, label: str) -> str:
    if not TOKEN.fullmatch(value):
        raise LifecycleError(f"invalid {label}: {value!r}")
    return value


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    if not args:
        raise LifecycleError("missing git arguments")
    if args[0] in FORBIDDEN_GIT:
        raise LifecycleError(f"forbidden git command: {args[0]}")
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise LifecycleError(f"git {' '.join(args)} failed: {detail}")
    return result


def discover_repo(value: Optional[str]) -> Path:
    start = Path(value or os.getcwd()).resolve()
    result = run_git(start, "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip()).resolve()


def common_git_dir(repo: Path) -> Path:
    raw = run_git(repo, "rev-parse", "--git-common-dir").stdout.strip()
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


def manifests_dir(repo: Path) -> Path:
    return common_git_dir(repo) / "codex" / "resolve-worktrees"


def manifest_path(repo: Path, run_id: str) -> Path:
    return manifests_dir(repo) / f"{validate_token(run_id, 'run id')}.json"


def managed_root(repo: Path, run_id: str) -> Path:
    root = (repo / ".worktrees" / "resolve" / validate_token(run_id, "run id")).resolve()
    expected = (repo / ".worktrees" / "resolve").resolve()
    if expected not in root.parents:
        raise LifecycleError("managed worktree path escaped its root")
    return root


@contextmanager
def locked_manifest(repo: Path, run_id: str) -> Iterator[Path]:
    directory = manifests_dir(repo)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / f"{validate_token(run_id, 'run id')}.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield manifest_path(repo, run_id)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise LifecycleError(f"run is not initialized: {path.stem}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise LifecycleError("unsupported worktree manifest version")
    return data


def write_manifest(path: Path, data: dict[str, Any]) -> None:
    data["updated_at"] = utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def add_local_exclude(repo: Path) -> None:
    exclude = common_git_dir(repo) / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if ".worktrees/" not in existing.splitlines():
        with exclude.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(".worktrees/\n")


def ref_exists(repo: Path, ref: str) -> bool:
    return run_git(repo, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 0


def rev_parse(repo: Path, ref: str) -> str:
    return run_git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def head(repo: Path) -> str:
    return rev_parse(repo, "HEAD")


def clean(repo: Path) -> bool:
    return not run_git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout.strip()


def operation_in_progress(repo: Path) -> bool:
    git_dir_raw = run_git(repo, "rev-parse", "--git-dir").stdout.strip()
    git_dir = Path(git_dir_raw)
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    markers = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
    return any((git_dir / marker).exists() for marker in markers)


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    return run_git(repo, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def issue_key(issue: str, attempt: int) -> str:
    return f"{validate_token(issue, 'issue id')}:a{attempt}"


def branch_for(
    run_id: str, issue: Optional[str] = None, attempt: Optional[int] = None
) -> str:
    if issue is None:
        return f"codex/resolve/{run_id}/integration"
    return f"codex/resolve/{run_id}/issue-{issue}-a{attempt}"


def ensure_no_sensitive_files(worktree: Path, base_sha: str) -> None:
    status = run_git(worktree, "status", "--porcelain=v1", "--untracked-files=all").stdout
    candidates: set[str] = set()
    for line in status.splitlines():
        candidates.add(line[3:].split(" -> ")[-1])
    committed_names = run_git(
        worktree,
        "log",
        "--format=",
        "--name-only",
        f"{base_sha}..HEAD",
        "--",
    ).stdout
    candidates.update(name for name in committed_names.splitlines() if name)

    bad: list[str] = []
    for relative in candidates:
        name = Path(relative).name
        if name in SAFE_ENV_NAMES:
            continue
        if any(pattern.search(relative) for pattern in SENSITIVE):
            bad.append(relative)
    if bad:
        raise LifecycleError("refusing to checkpoint possible credentials: " + ", ".join(sorted(bad)))


def entry_for(data: dict[str, Any], issue: str, attempt: int) -> dict[str, Any]:
    key = issue_key(issue, attempt)
    try:
        return data["issues"][key]
    except KeyError as exc:
        raise LifecycleError(f"unknown managed issue attempt: {key}") from exc


def cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    repo = discover_repo(args.repo)
    run_id = validate_token(args.run, "run id")
    target = validate_token(args.target, "target branch")
    add_local_exclude(repo)
    with locked_manifest(repo, run_id) as path:
        if path.exists():
            data = load_manifest(path)
            if data["target_branch"] != target:
                raise LifecycleError("existing run uses a different target branch")
            return data
        target_ref = f"refs/heads/{target}"
        if not ref_exists(repo, target_ref):
            raise LifecycleError(f"target branch does not exist: {target}")
        target_sha = rev_parse(repo, target_ref)
        base_sha = rev_parse(repo, args.base) if args.base else target_sha
        root = managed_root(repo, run_id)
        integration_path = root / "integration"
        integration_branch = branch_for(run_id)
        integration_path.parent.mkdir(parents=True, exist_ok=True)
        if ref_exists(repo, f"refs/heads/{integration_branch}"):
            raise LifecycleError(f"integration branch already exists without a manifest: {integration_branch}")
        run_git(repo, "worktree", "add", "-b", integration_branch, str(integration_path), base_sha)
        data = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "repo": str(repo),
            "target_branch": target,
            "initial_target_sha": target_sha,
            "base_sha": base_sha,
            "state": "active",
            "created_at": utc_now(),
            "integration": {
                "branch": integration_branch,
                "path": str(integration_path),
                "head": head(integration_path),
                "state": "active",
            },
            "issues": {},
            "published_sha": None,
        }
        write_manifest(path, data)
        return data


def cmd_create(args: argparse.Namespace) -> dict[str, Any]:
    repo = discover_repo(args.repo)
    run_id = validate_token(args.run, "run id")
    issue = validate_token(args.issue, "issue id")
    if args.attempt < 1:
        raise LifecycleError("attempt must be positive")
    with locked_manifest(repo, run_id) as path:
        data = load_manifest(path)
        key = issue_key(issue, args.attempt)
        if key in data["issues"]:
            return data["issues"][key]
        integration_path = Path(data["integration"]["path"])
        if not integration_path.exists() or not clean(integration_path) or operation_in_progress(integration_path):
            raise LifecycleError("integration worktree must exist and be clean before creating an issue worktree")
        base_sha = head(integration_path)
        branch = branch_for(run_id, issue, args.attempt)
        worktree = managed_root(repo, run_id) / f"issue-{issue}-a{args.attempt}"
        if ref_exists(repo, f"refs/heads/{branch}") or worktree.exists():
            raise LifecycleError(f"unmanaged branch or path already exists for {key}")
        run_git(repo, "worktree", "add", "-b", branch, str(worktree), base_sha)
        entry = {
            "issue": issue,
            "attempt": args.attempt,
            "branch": branch,
            "path": str(worktree),
            "base_sha": base_sha,
            "checkpoint_sha": None,
            "approved_sha": None,
            "integrated_sha": None,
            "state": "active",
            "correction_rounds": 0,
            "last_error": None,
            "created_at": utc_now(),
        }
        data["issues"][key] = entry
        write_manifest(path, data)
        return entry


def cmd_checkpoint(args: argparse.Namespace) -> dict[str, Any]:
    repo = discover_repo(args.repo)
    with locked_manifest(repo, args.run) as path:
        data = load_manifest(path)
        entry = entry_for(data, args.issue, args.attempt)
        worktree = Path(entry["path"])
        if not worktree.exists():
            raise LifecycleError("issue worktree is missing; run recover before continuing")
        if operation_in_progress(worktree):
            raise LifecycleError("issue worktree has an unfinished Git operation")
        if not is_ancestor(worktree, entry["base_sha"], head(worktree)):
            raise LifecycleError("issue branch no longer descends from its pinned base")
        ensure_no_sensitive_files(worktree, entry["base_sha"])
        if not clean(worktree):
            run_git(worktree, "add", "--all")
            staged = run_git(worktree, "diff", "--cached", "--quiet", check=False)
            if staged.returncode not in (0, 1):
                raise LifecycleError("could not inspect staged issue changes")
            if staged.returncode == 1:
                message = args.message or f"Resolve issue {args.issue} (attempt {args.attempt})"
                run_git(worktree, "commit", "-m", message)
        checkpoint_sha = head(worktree)
        if checkpoint_sha == entry["base_sha"]:
            raise LifecycleError("checkpoint contains no committed changes")
        if entry.get("checkpoint_sha") and entry["checkpoint_sha"] != checkpoint_sha:
            entry["correction_rounds"] = int(entry.get("correction_rounds", 0)) + 1
        entry.update(
            {
                "checkpoint_sha": checkpoint_sha,
                "approved_sha": None,
                "state": "checkpointed",
                "last_error": None,
            }
        )
        write_manifest(path, data)
        return entry


def cmd_approve(args: argparse.Namespace) -> dict[str, Any]:
    repo = discover_repo(args.repo)
    with locked_manifest(repo, args.run) as path:
        data = load_manifest(path)
        entry = entry_for(data, args.issue, args.attempt)
        worktree = Path(entry["path"])
        actual = head(worktree)
        if entry.get("checkpoint_sha") != args.expected_head or actual != args.expected_head:
            raise LifecycleError("review approval does not match the current checkpoint")
        if not clean(worktree) or operation_in_progress(worktree):
            raise LifecycleError("reviewed worktree must be clean and have no unfinished operation")
        entry["approved_sha"] = actual
        entry["state"] = "approved"
        write_manifest(path, data)
        return entry


def integration_order(entry: dict[str, Any]) -> tuple[int, str, int]:
    issue = str(entry["issue"])
    number = int(issue) if issue.isdigit() else 10**9
    return number, issue, int(entry["attempt"])


def cmd_integrate(args: argparse.Namespace) -> dict[str, Any]:
    repo = discover_repo(args.repo)
    with locked_manifest(repo, args.run) as path:
        data = load_manifest(path)
        entry = entry_for(data, args.issue, args.attempt)
        expected = args.expected_head
        integration_path = Path(data["integration"]["path"])
        if entry.get("state") == "integrated" and entry.get("checkpoint_sha") == expected:
            if not integration_path.exists() or not is_ancestor(
                repo, expected, head(integration_path)
            ):
                raise LifecycleError(
                    "manifest says integrated but the approved checkpoint is not reachable"
                )
            return entry
        if entry.get("state") != "approved" or entry.get("approved_sha") != expected:
            raise LifecycleError("only the exact approved checkpoint may be integrated")
        pending = sorted(
            (item for item in data["issues"].values() if item.get("state") == "approved"),
            key=integration_order,
        )
        if pending and pending[0] is not entry:
            raise LifecycleError(f"integrate approved issue {pending[0]['issue']} first")
        issue_worktree = Path(entry["path"])
        if head(issue_worktree) != expected or not clean(issue_worktree):
            raise LifecycleError("approved issue worktree changed after review")
        if not clean(integration_path) or operation_in_progress(integration_path):
            raise LifecycleError("integration worktree is not clean")
        if is_ancestor(integration_path, expected, head(integration_path)):
            entry["state"] = "integrated"
            entry["integrated_sha"] = head(integration_path)
            write_manifest(path, data)
            return entry
        entry["state"] = "integrating"
        write_manifest(path, data)
        result = run_git(
            integration_path,
            "merge",
            "--no-ff",
            "--no-edit",
            expected,
            check=False,
        )
        if result.returncode != 0:
            entry["state"] = "conflict_preserved"
            entry["last_error"] = result.stderr.strip() or result.stdout.strip()
            data["integration"]["state"] = "conflict_preserved"
            write_manifest(path, data)
            raise LifecycleError("integration conflict preserved in the integration worktree")
        entry["state"] = "integrated"
        entry["integrated_sha"] = head(integration_path)
        entry["last_error"] = None
        data["integration"].update({"head": head(integration_path), "state": "active"})
        write_manifest(path, data)
        return entry


def cmd_fail(args: argparse.Namespace) -> dict[str, Any]:
    repo = discover_repo(args.repo)
    with locked_manifest(repo, args.run) as path:
        data = load_manifest(path)
        entry = entry_for(data, args.issue, args.attempt)
        entry["state"] = "failed_preserved"
        entry["last_error"] = args.reason
        write_manifest(path, data)
        return entry


def cmd_archive(args: argparse.Namespace) -> dict[str, Any]:
    """Remove a clean failed worktree while retaining its branch and exact SHA."""
    repo = discover_repo(args.repo)
    with locked_manifest(repo, args.run) as path:
        data = load_manifest(path)
        entry = entry_for(data, args.issue, args.attempt)
        worktree = Path(entry["path"])
        branch_ref = f"refs/heads/{entry['branch']}"
        if entry.get("state") == "archived_preserved":
            if worktree.exists() or not ref_exists(repo, branch_ref):
                raise LifecycleError("archived attempt no longer matches its preserved state")
            return entry
        if entry.get("state") != "failed_preserved":
            raise LifecycleError("only a failed attempt may be archived")
        if not ref_exists(repo, branch_ref):
            raise LifecycleError("failed attempt branch is missing")
        if worktree.exists():
            if worktree.resolve() == Path.cwd().resolve():
                raise LifecycleError("failed worktree is the current directory")
            if not clean(worktree) or operation_in_progress(worktree):
                raise LifecycleError(
                    "failed worktree must be checkpointed and clean before archival"
                )
            if head(worktree) != rev_parse(repo, branch_ref):
                raise LifecycleError("failed worktree HEAD does not match its branch")
            run_git(repo, "worktree", "remove", str(worktree))
        entry["archived_sha"] = rev_parse(repo, branch_ref)
        entry["state"] = "archived_preserved"
        write_manifest(path, data)
        return entry


def safe_to_remove(repo: Path, data: dict[str, Any], entry: dict[str, Any]) -> tuple[bool, str]:
    state = entry.get("state")
    if state not in {"integrated", "cleanup_ready", "removed"}:
        return False, f"state is {entry.get('state')}"
    worktree = Path(entry["path"])
    if state == "removed" and worktree.exists():
        return False, "manifest says removed but worktree still exists"
    if worktree.exists():
        if worktree.resolve() == Path.cwd().resolve():
            return False, "worktree is the current directory"
        if not clean(worktree):
            return False, "worktree is dirty"
        if operation_in_progress(worktree):
            return False, "Git operation is in progress"

    integration_path = Path(data["integration"]["path"])
    integration_ref = f"refs/heads/{data['integration']['branch']}"
    if integration_path.exists():
        integration_head = head(integration_path)
    elif ref_exists(repo, integration_ref):
        integration_head = rev_parse(repo, integration_ref)
    elif data.get("published_sha"):
        integration_head = str(data["published_sha"])
    else:
        return False, "integration commit is unavailable"
    checkpoint = entry.get("checkpoint_sha")
    if not checkpoint or not is_ancestor(repo, checkpoint, integration_head):
        return False, "checkpoint is not reachable from integration"
    issue_ref = f"refs/heads/{entry['branch']}"
    if ref_exists(repo, issue_ref):
        issue_head = rev_parse(repo, issue_ref)
        if not is_ancestor(repo, issue_head, integration_head):
            return False, "issue branch head is not reachable from integration"
    if state == "removed":
        return True, "already removed"
    if not worktree.exists():
        return True, "already absent"
    return True, "safe"


def remove_entry_worktree(repo: Path, data: dict[str, Any], entry: dict[str, Any], apply: bool) -> dict[str, Any]:
    if entry.get("state") == "archived_preserved":
        worktree = Path(entry["path"])
        safe = not worktree.exists() and ref_exists(repo, f"refs/heads/{entry['branch']}")
        return {
            "issue": entry["issue"],
            "attempt": entry["attempt"],
            "safe": safe,
            "reason": "failed branch retained" if safe else "archived attempt is inconsistent",
            "retained": safe,
        }
    allowed, reason = safe_to_remove(repo, data, entry)
    result = {"issue": entry["issue"], "attempt": entry["attempt"], "safe": allowed, "reason": reason}
    if not allowed or not apply:
        return result
    worktree = Path(entry["path"])
    if worktree.exists():
        run_git(repo, "worktree", "remove", str(worktree))
    entry["state"] = "removed"
    result["removed"] = True
    return result


def cmd_cleanup(args: argparse.Namespace) -> dict[str, Any]:
    repo = discover_repo(args.repo)
    with locked_manifest(repo, args.run) as path:
        data = load_manifest(path)
        selected = list(data["issues"].values())
        if args.issue:
            selected = [entry_for(data, args.issue, args.attempt)]
        results = [remove_entry_worktree(repo, data, entry, args.apply) for entry in selected]
        write_manifest(path, data)
        return {"apply": args.apply, "results": results}


def target_worktree(repo: Path, target_branch: str) -> Optional[Path]:
    raw = run_git(repo, "worktree", "list", "--porcelain").stdout
    current_path: Optional[Path] = None
    for line in raw.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line.removeprefix("worktree ")).resolve()
        elif line == f"branch refs/heads/{target_branch}" and current_path:
            return current_path
    return None


def cmd_publish(args: argparse.Namespace) -> dict[str, Any]:
    repo = discover_repo(args.repo)
    with locked_manifest(repo, args.run) as path:
        data = load_manifest(path)
        unfinished = [
            f"{entry['issue']}:a{entry['attempt']}={entry['state']}"
            for entry in data["issues"].values()
            if entry.get("state") not in {"integrated", "removed", "archived_preserved"}
        ]
        if unfinished:
            raise LifecycleError("cannot publish with unfinished attempts: " + ", ".join(unfinished))
        target_sha = rev_parse(repo, f"refs/heads/{data['target_branch']}")
        recorded_publish = data.get("published_sha")
        if recorded_publish:
            if not is_ancestor(repo, recorded_publish, target_sha):
                raise LifecycleError(
                    "target branch no longer contains the recorded published commit"
                )
            return {
                "target": data["target_branch"],
                "published_sha": recorded_publish,
            }
        integration_path = Path(data["integration"]["path"])
        if not clean(integration_path) or operation_in_progress(integration_path):
            raise LifecycleError("integration worktree must be clean before publishing")
        integration_head = head(integration_path)
        if target_sha == integration_head:
            data["published_sha"] = integration_head
            data["state"] = "published"
            data["integration"]["head"] = integration_head
            write_manifest(path, data)
            return {
                "target": data["target_branch"],
                "published_sha": integration_head,
            }
        expected_target = args.expected_target or data["initial_target_sha"]
        if target_sha != expected_target:
            raise LifecycleError("target branch advanced; preserve the integration branch for a deliberate merge")
        target_path = target_worktree(repo, data["target_branch"])
        if target_path is None:
            raise LifecycleError("target branch has no worktree; publish the integration branch manually")
        if not clean(target_path) or operation_in_progress(target_path):
            raise LifecycleError("target worktree is dirty; integration branch was preserved")
        run_git(target_path, "merge", "--ff-only", data["integration"]["branch"])
        published = head(target_path)
        data["published_sha"] = published
        data["state"] = "published"
        data["integration"]["head"] = head(integration_path)
        write_manifest(path, data)
        return {"target": data["target_branch"], "published_sha": published}


def delete_merged_branch(repo: Path, branch: str, target_sha: str) -> bool:
    ref = f"refs/heads/{branch}"
    if not ref_exists(repo, ref):
        return False
    branch_sha = rev_parse(repo, ref)
    if not is_ancestor(repo, branch_sha, target_sha):
        raise LifecycleError(f"refusing to delete unmerged managed branch: {branch}")
    run_git(repo, "branch", "-d", branch)
    return True


def cmd_close(args: argparse.Namespace) -> dict[str, Any]:
    repo = discover_repo(args.repo)
    with locked_manifest(repo, args.run) as path:
        data = load_manifest(path)
        if data.get("state") == "closed":
            return data
        if data.get("state") != "published" or not data.get("published_sha"):
            raise LifecycleError("publish the integration branch before closing the run")
        cleanup_results = [remove_entry_worktree(repo, data, entry, True) for entry in data["issues"].values()]
        blocked = [item for item in cleanup_results if not item["safe"]]
        if blocked:
            write_manifest(path, data)
            raise LifecycleError("run preserved because one or more issue worktrees are not cleanup-safe")
        integration_path = Path(data["integration"]["path"])
        if integration_path.exists():
            if integration_path.resolve() == Path.cwd().resolve() or not clean(integration_path) or operation_in_progress(integration_path):
                raise LifecycleError("integration worktree is not cleanup-safe")
            run_git(repo, "worktree", "remove", str(integration_path))
        target_sha = rev_parse(repo, f"refs/heads/{data['target_branch']}")
        deleted: list[str] = []
        retained: list[str] = []
        for entry in data["issues"].values():
            if entry.get("state") == "archived_preserved":
                retained.append(entry["branch"])
                continue
            if delete_merged_branch(repo, entry["branch"], target_sha):
                deleted.append(entry["branch"])
        if delete_merged_branch(repo, data["integration"]["branch"], target_sha):
            deleted.append(data["integration"]["branch"])
        data["integration"]["state"] = "removed"
        data["state"] = "closed"
        data["deleted_branches"] = deleted
        data["retained_branches"] = retained
        write_manifest(path, data)
        root = managed_root(repo, args.run)
        for candidate in (root, root.parent, root.parent.parent):
            try:
                candidate.rmdir()
            except OSError:
                break
        return data


def cmd_recover(args: argparse.Namespace) -> dict[str, Any]:
    repo = discover_repo(args.repo)
    with locked_manifest(repo, args.run) as path:
        data = load_manifest(path)
        integration_path = Path(data["integration"]["path"])
        integration = {
            "path": str(integration_path),
            "path_exists": integration_path.exists(),
            "ref_exists": ref_exists(
                repo, f"refs/heads/{data['integration']['branch']}"
            ),
            "state": data["integration"]["state"],
        }
        if integration_path.exists():
            integration["dirty"] = not clean(integration_path)
            integration["operation_in_progress"] = operation_in_progress(
                integration_path
            )
            integration["head"] = head(integration_path)
        report: list[dict[str, Any]] = []
        for entry in data["issues"].values():
            worktree = Path(entry["path"])
            ref = f"refs/heads/{entry['branch']}"
            item = {
                "issue": entry["issue"],
                "attempt": entry["attempt"],
                "path_exists": worktree.exists(),
                "ref_exists": ref_exists(repo, ref),
                "state": entry["state"],
            }
            if worktree.exists():
                item["dirty"] = not clean(worktree)
                item["operation_in_progress"] = operation_in_progress(worktree)
                item["head"] = head(worktree)
            report.append(item)
        return {
            "run": args.run,
            "state": data["state"],
            "integration": integration,
            "entries": report,
        }


def cmd_list(args: argparse.Namespace) -> dict[str, Any]:
    repo = discover_repo(args.repo)
    if args.run:
        with locked_manifest(repo, args.run) as path:
            data = load_manifest(path)
        return {"runs": [data], "git_worktrees": run_git(repo, "worktree", "list", "--porcelain").stdout}
    directory = manifests_dir(repo)
    runs = []
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            runs.append(load_manifest(path))
    return {"runs": runs, "git_worktrees": run_git(repo, "worktree", "list", "--porcelain").stdout}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo", help="repository path; defaults to the current repository")
    sub = result.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--run", required=True)
    init.add_argument("--target", default="main")
    init.add_argument("--base")
    init.set_defaults(handler=cmd_init)

    create = sub.add_parser("create")
    create.add_argument("--run", required=True)
    create.add_argument("--issue", required=True)
    create.add_argument("--attempt", type=int, default=1)
    create.set_defaults(handler=cmd_create)

    for name, handler in (("checkpoint", cmd_checkpoint), ("approve", cmd_approve), ("integrate", cmd_integrate)):
        command = sub.add_parser(name)
        command.add_argument("--run", required=True)
        command.add_argument("--issue", required=True)
        command.add_argument("--attempt", type=int, default=1)
        if name == "checkpoint":
            command.add_argument("--message")
        else:
            command.add_argument("--expected-head", required=True)
        command.set_defaults(handler=handler)

    fail = sub.add_parser("fail")
    fail.add_argument("--run", required=True)
    fail.add_argument("--issue", required=True)
    fail.add_argument("--attempt", type=int, default=1)
    fail.add_argument("--reason", required=True)
    fail.set_defaults(handler=cmd_fail)

    archive = sub.add_parser("archive")
    archive.add_argument("--run", required=True)
    archive.add_argument("--issue", required=True)
    archive.add_argument("--attempt", type=int, default=1)
    archive.set_defaults(handler=cmd_archive)

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--run", required=True)
    cleanup.add_argument("--issue")
    cleanup.add_argument("--attempt", type=int, default=1)
    cleanup.add_argument("--apply", action="store_true", help="perform safe removals; default is a dry run")
    cleanup.set_defaults(handler=cmd_cleanup)

    publish = sub.add_parser("publish")
    publish.add_argument("--run", required=True)
    publish.add_argument("--expected-target")
    publish.set_defaults(handler=cmd_publish)

    close = sub.add_parser("close")
    close.add_argument("--run", required=True)
    close.set_defaults(handler=cmd_close)

    recover = sub.add_parser("recover")
    recover.add_argument("--run", required=True)
    recover.set_defaults(handler=cmd_recover)

    listing = sub.add_parser("list")
    listing.add_argument("--run")
    listing.set_defaults(handler=cmd_list)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        payload = args.handler(args)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except (LifecycleError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"error": str(exc), "command": args.command}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
