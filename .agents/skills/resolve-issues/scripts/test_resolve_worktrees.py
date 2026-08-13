from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional


SCRIPT = Path(__file__).resolve().with_name("resolve_worktrees.py")


class ResolveWorktreesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repo = Path(self.temporary_directory.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Resolve Worktrees Test")
        self.git("config", "user.email", "resolve-worktrees@example.test")
        (self.repo / "README.md").write_text("initial\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-m", "Initial commit")
        self.initial_sha = self.git("rev-parse", "HEAD").stdout.strip()

    def git(
        self, *arguments: str, cwd: Optional[Path] = None
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", "-C", str(cwd or self.repo), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            self.fail(
                f"git {' '.join(arguments)} failed ({result.returncode}):\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def manager(
        self, *arguments: str, expected_returncode: int = 0
    ) -> tuple[dict[str, object], subprocess.CompletedProcess[str]]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            expected_returncode,
            result.returncode,
            f"manager {' '.join(arguments)} returned {result.returncode}:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        stream = result.stdout if result.returncode == 0 else result.stderr
        return json.loads(stream), result

    def test_full_lifecycle_integrates_cleans_publishes_and_closes(self) -> None:
        initialized, _ = self.manager("init", "--run", "run1")
        self.assertEqual(self.initial_sha, initialized["initial_target_sha"])

        created, _ = self.manager("create", "--run", "run1", "--issue", "001")
        issue_worktree = Path(str(created["path"]))
        (issue_worktree / "feature.txt").write_text("working\n", encoding="utf-8")

        listed, _ = self.manager("list", "--run", "run1")
        self.assertIn("001:a1", listed["runs"][0]["issues"])

        checkpointed, _ = self.manager(
            "checkpoint", "--run", "run1", "--issue", "001"
        )
        checkpoint_sha = str(checkpointed["checkpoint_sha"])
        self.manager(
            "approve",
            "--run",
            "run1",
            "--issue",
            "001",
            "--expected-head",
            checkpoint_sha,
        )
        integrated, _ = self.manager(
            "integrate",
            "--run",
            "run1",
            "--issue",
            "001",
            "--expected-head",
            checkpoint_sha,
        )
        self.assertEqual("integrated", integrated["state"])

        cleaned, _ = self.manager(
            "cleanup", "--run", "run1", "--issue", "001", "--apply"
        )
        self.assertTrue(cleaned["results"][0]["safe"])
        self.assertTrue(cleaned["results"][0]["removed"])
        self.assertFalse(issue_worktree.exists())

        published, _ = self.manager("publish", "--run", "run1")
        self.assertEqual("main", published["target"])
        self.assertEqual("working\n", (self.repo / "feature.txt").read_text(encoding="utf-8"))
        published_again, _ = self.manager("publish", "--run", "run1")
        self.assertEqual(published["published_sha"], published_again["published_sha"])

        closed, _ = self.manager("close", "--run", "run1")
        self.assertEqual("closed", closed["state"])
        worktrees = self.git("worktree", "list", "--porcelain").stdout
        self.assertEqual(1, worktrees.count("worktree "))

    def test_parallel_integrations_preserve_each_approved_checkpoint_as_ancestry(self) -> None:
        initialized, _ = self.manager("init", "--run", "parallel")
        integration_path = Path(str(initialized["integration"]["path"]))

        checkpoints: dict[str, str] = {}
        for issue in ("001", "002"):
            created, _ = self.manager(
                "create", "--run", "parallel", "--issue", issue
            )
            issue_worktree = Path(str(created["path"]))
            (issue_worktree / f"feature-{issue}.txt").write_text(
                f"issue {issue}\n", encoding="utf-8"
            )
            checkpointed, _ = self.manager(
                "checkpoint", "--run", "parallel", "--issue", issue
            )
            checkpoint = str(checkpointed["checkpoint_sha"])
            checkpoints[issue] = checkpoint
            self.manager(
                "approve",
                "--run",
                "parallel",
                "--issue",
                issue,
                "--expected-head",
                checkpoint,
            )

        for issue in ("001", "002"):
            self.manager(
                "integrate",
                "--run",
                "parallel",
                "--issue",
                issue,
                "--expected-head",
                checkpoints[issue],
            )

        integration_head = self.git(
            "rev-parse", "HEAD", cwd=integration_path
        ).stdout.strip()
        for issue, checkpoint in checkpoints.items():
            ancestry = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "merge-base",
                    "--is-ancestor",
                    checkpoint,
                    integration_head,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                0,
                ancestry.returncode,
                f"approved checkpoint for issue {issue} is not retained in integration ancestry",
            )

        cleaned, _ = self.manager("cleanup", "--run", "parallel", "--apply")
        self.assertTrue(all(item["safe"] for item in cleaned["results"]))

    def test_failed_attempt_can_be_archived_before_successful_retry_closes(self) -> None:
        self.manager("init", "--run", "retry")
        failed, _ = self.manager(
            "create", "--run", "retry", "--issue", "001", "--attempt", "1"
        )
        failed_worktree = Path(str(failed["path"]))
        (failed_worktree / "failed.txt").write_text("preserve\n", encoding="utf-8")
        self.manager(
            "checkpoint", "--run", "retry", "--issue", "001", "--attempt", "1"
        )
        self.manager(
            "fail",
            "--run",
            "retry",
            "--issue",
            "001",
            "--attempt",
            "1",
            "--reason",
            "bounded implementation did not satisfy the issue",
        )
        archived, _ = self.manager(
            "archive", "--run", "retry", "--issue", "001", "--attempt", "1"
        )
        self.assertEqual("archived_preserved", archived["state"])
        self.assertFalse(failed_worktree.exists())

        retry, _ = self.manager(
            "create", "--run", "retry", "--issue", "001", "--attempt", "2"
        )
        retry_worktree = Path(str(retry["path"]))
        (retry_worktree / "success.txt").write_text("complete\n", encoding="utf-8")
        checkpointed, _ = self.manager(
            "checkpoint", "--run", "retry", "--issue", "001", "--attempt", "2"
        )
        checkpoint = str(checkpointed["checkpoint_sha"])
        self.manager(
            "approve",
            "--run",
            "retry",
            "--issue",
            "001",
            "--attempt",
            "2",
            "--expected-head",
            checkpoint,
        )
        self.manager(
            "integrate",
            "--run",
            "retry",
            "--issue",
            "001",
            "--attempt",
            "2",
            "--expected-head",
            checkpoint,
        )
        self.manager("publish", "--run", "retry")
        closed, _ = self.manager("close", "--run", "retry")

        retained = str(failed["branch"])
        self.assertIn(retained, closed["retained_branches"])
        self.git("show-ref", "--verify", f"refs/heads/{retained}")
        self.assertTrue((self.repo / "success.txt").exists())
        self.assertFalse((self.repo / "failed.txt").exists())
        self.assertEqual(
            1, self.git("worktree", "list", "--porcelain").stdout.count("worktree ")
        )

    def test_publish_refuses_dirty_main_without_changing_or_losing_work(self) -> None:
        initialized, _ = self.manager("init", "--run", "dirty-main")
        integration_path = Path(str(initialized["integration"]["path"]))
        created, _ = self.manager(
            "create", "--run", "dirty-main", "--issue", "001"
        )
        issue_worktree = Path(str(created["path"]))
        (issue_worktree / "feature.txt").write_text("preserve me\n", encoding="utf-8")
        checkpointed, _ = self.manager(
            "checkpoint", "--run", "dirty-main", "--issue", "001"
        )
        checkpoint = str(checkpointed["checkpoint_sha"])
        self.manager(
            "approve",
            "--run",
            "dirty-main",
            "--issue",
            "001",
            "--expected-head",
            checkpoint,
        )
        self.manager(
            "integrate",
            "--run",
            "dirty-main",
            "--issue",
            "001",
            "--expected-head",
            checkpoint,
        )

        dirty_contents = "user work that must survive\n"
        (self.repo / "README.md").write_text(dirty_contents, encoding="utf-8")
        failure, _ = self.manager(
            "publish", "--run", "dirty-main", expected_returncode=2
        )

        self.assertIn("target worktree is dirty", str(failure["error"]))
        self.assertEqual(
            dirty_contents, (self.repo / "README.md").read_text(encoding="utf-8")
        )
        self.assertEqual(self.initial_sha, self.git("rev-parse", "HEAD").stdout.strip())
        self.assertTrue((integration_path / "feature.txt").exists())
        self.assertTrue(issue_worktree.exists())

    def test_checkpoint_refuses_sensitive_file_and_preserves_worktree(self) -> None:
        self.manager("init", "--run", "sensitive")
        created, _ = self.manager(
            "create", "--run", "sensitive", "--issue", "001"
        )
        issue_worktree = Path(str(created["path"]))
        sensitive = issue_worktree / "credentials.json"
        sensitive.write_text('{"token": "do-not-commit"}\n', encoding="utf-8")

        failure, _ = self.manager(
            "checkpoint",
            "--run",
            "sensitive",
            "--issue",
            "001",
            expected_returncode=2,
        )

        self.assertIn("refusing to checkpoint possible credentials", str(failure["error"]))
        self.assertTrue(sensitive.exists())
        self.assertEqual(str(created["base_sha"]), self.git("rev-parse", "HEAD", cwd=issue_worktree).stdout.strip())
        self.assertIn("credentials.json", self.git("status", "--short", cwd=issue_worktree).stdout)
        recovered, _ = self.manager("recover", "--run", "sensitive")
        self.assertTrue(recovered["entries"][0]["dirty"])
        self.assertEqual("active", recovered["entries"][0]["state"])

    def test_checkpoint_refuses_sensitive_file_already_committed_on_issue_branch(self) -> None:
        self.manager("init", "--run", "committed-sensitive")
        created, _ = self.manager(
            "create", "--run", "committed-sensitive", "--issue", "001"
        )
        issue_worktree = Path(str(created["path"]))
        sensitive = issue_worktree / "credentials.json"
        sensitive.write_text('{"token": "already-committed"}\n', encoding="utf-8")
        self.git("add", "credentials.json", cwd=issue_worktree)
        self.git("commit", "-m", "Accidentally commit credentials", cwd=issue_worktree)
        committed_head = self.git(
            "rev-parse", "HEAD", cwd=issue_worktree
        ).stdout.strip()

        failure, _ = self.manager(
            "checkpoint",
            "--run",
            "committed-sensitive",
            "--issue",
            "001",
            expected_returncode=2,
        )

        self.assertIn("refusing to checkpoint possible credentials", str(failure["error"]))
        self.assertEqual(
            committed_head, self.git("rev-parse", "HEAD", cwd=issue_worktree).stdout.strip()
        )
        listed, _ = self.manager("list", "--run", "committed-sensitive")
        self.assertIsNone(listed["runs"][0]["issues"]["001:a1"]["checkpoint_sha"])

    def test_new_checkpoint_counts_a_review_correction_before_approval(self) -> None:
        self.manager("init", "--run", "corrections")
        created, _ = self.manager(
            "create", "--run", "corrections", "--issue", "001"
        )
        issue_worktree = Path(str(created["path"]))
        feature = issue_worktree / "feature.txt"
        feature.write_text("first candidate\n", encoding="utf-8")
        first, _ = self.manager(
            "checkpoint", "--run", "corrections", "--issue", "001"
        )
        self.assertEqual(0, first["correction_rounds"])

        feature.write_text("corrected candidate\n", encoding="utf-8")
        second, _ = self.manager(
            "checkpoint", "--run", "corrections", "--issue", "001"
        )

        self.assertNotEqual(first["checkpoint_sha"], second["checkpoint_sha"])
        self.assertEqual(1, second["correction_rounds"])

    def test_cleanup_refuses_clean_commits_added_after_integration(self) -> None:
        self.manager("init", "--run", "post-integration")
        created, _ = self.manager(
            "create", "--run", "post-integration", "--issue", "001"
        )
        issue_worktree = Path(str(created["path"]))
        (issue_worktree / "approved.txt").write_text("approved\n", encoding="utf-8")
        checkpointed, _ = self.manager(
            "checkpoint", "--run", "post-integration", "--issue", "001"
        )
        checkpoint = str(checkpointed["checkpoint_sha"])
        self.manager(
            "approve",
            "--run",
            "post-integration",
            "--issue",
            "001",
            "--expected-head",
            checkpoint,
        )
        self.manager(
            "integrate",
            "--run",
            "post-integration",
            "--issue",
            "001",
            "--expected-head",
            checkpoint,
        )

        (issue_worktree / "unreviewed.txt").write_text(
            "must remain recoverable\n", encoding="utf-8"
        )
        self.git("add", "unreviewed.txt", cwd=issue_worktree)
        self.git("commit", "-m", "Unreviewed follow-up", cwd=issue_worktree)

        cleanup, _ = self.manager(
            "cleanup", "--run", "post-integration", "--issue", "001", "--apply"
        )

        self.assertFalse(cleanup["results"][0]["safe"])
        self.assertIn("not reachable", cleanup["results"][0]["reason"])
        self.assertTrue(issue_worktree.exists())
        self.assertTrue((issue_worktree / "unreviewed.txt").exists())

    def test_integration_conflict_is_preserved_for_recovery(self) -> None:
        initialized, _ = self.manager("init", "--run", "conflict")
        integration_path = Path(str(initialized["integration"]["path"]))
        created, _ = self.manager(
            "create", "--run", "conflict", "--issue", "001"
        )
        issue_worktree = Path(str(created["path"]))
        (issue_worktree / "README.md").write_text("issue version\n", encoding="utf-8")
        checkpointed, _ = self.manager(
            "checkpoint", "--run", "conflict", "--issue", "001"
        )
        checkpoint = str(checkpointed["checkpoint_sha"])
        self.manager(
            "approve",
            "--run",
            "conflict",
            "--issue",
            "001",
            "--expected-head",
            checkpoint,
        )

        (integration_path / "README.md").write_text(
            "integration version\n", encoding="utf-8"
        )
        self.git("add", "README.md", cwd=integration_path)
        self.git("commit", "-m", "Create deliberate integration conflict", cwd=integration_path)

        failure, _ = self.manager(
            "integrate",
            "--run",
            "conflict",
            "--issue",
            "001",
            "--expected-head",
            checkpoint,
            expected_returncode=2,
        )

        self.assertIn("integration conflict preserved", str(failure["error"]))
        self.assertIn("UU README.md", self.git("status", "--short", cwd=integration_path).stdout)
        self.git("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=integration_path)
        recovered, _ = self.manager("recover", "--run", "conflict")
        self.assertTrue(recovered["integration"]["dirty"])
        self.assertTrue(recovered["integration"]["operation_in_progress"])
        self.assertEqual("conflict_preserved", recovered["integration"]["state"])
        listed, _ = self.manager("list", "--run", "conflict")
        manifest = listed["runs"][0]
        self.assertEqual("conflict_preserved", manifest["integration"]["state"])
        self.assertEqual("conflict_preserved", manifest["issues"]["001:a1"]["state"])
        cleanup, _ = self.manager("cleanup", "--run", "conflict", "--apply")
        self.assertFalse(cleanup["results"][0]["safe"])
        self.assertTrue(issue_worktree.exists())
        self.assertTrue(integration_path.exists())


if __name__ == "__main__":
    unittest.main()
