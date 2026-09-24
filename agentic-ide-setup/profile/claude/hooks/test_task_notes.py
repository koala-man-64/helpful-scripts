import subprocess
import tempfile
import unittest
from pathlib import Path

from task_notes import (
    context_lines,
    main_repo_name,
    note_key,
    note_path,
    read_note,
    task_note_lines,
)


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def sample_repo(parent: Path, name: str = "sample-repo", branch: str = "task/AB1-thing") -> Path:
    root = parent / name
    root.mkdir()
    git(root, "init", "-q", "-b", branch)
    (root / "f.txt").write_text("1", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "init")
    return root


class NoteKeyTests(unittest.TestCase):
    def test_slugs_repo_and_branch(self):
        self.assertEqual(
            note_key("asset-allocation-jobs", "feature/AB1044-gold lock"),
            "asset-allocation-jobs--feature-AB1044-gold-lock",
        )

    def test_empty_parts_get_placeholder(self):
        self.assertEqual(note_key("", "///"), "unnamed--unnamed")

    def test_path_is_markdown_in_notes_dir(self):
        self.assertEqual(note_path("r", "b", Path("x")), Path("x") / "r--b.md")


class ReadNoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_is_none(self):
        self.assertIsNone(read_note(self.dir / "nope.md"))

    def test_blank_is_none(self):
        path = self.dir / "blank.md"
        path.write_text("  \n", encoding="utf-8")
        self.assertIsNone(read_note(path))

    def test_long_notes_keep_the_header_and_the_newest_part(self):
        path = self.dir / "long.md"
        path.write_text("h" * 30 + "m" * 200 + "t" * 30, encoding="utf-8")
        out = read_note(path, limit=60, head=30)
        self.assertTrue(out.startswith("h" * 30))
        self.assertTrue(out.endswith("t" * 30))
        self.assertNotIn("m" * 10, out)
        self.assertIn("200 chars omitted", out)

    def test_a_note_grown_by_appending_injects_its_latest_section(self):
        path = self.dir / "appended.md"
        sections = ["# Objective: ship AB#1\n- owner: me"] + [
            f"## Update {n}\n- status: step {n} done\n- next: step {n + 1}" for n in range(1, 400)
        ]
        path.write_text("\n\n".join(sections), encoding="utf-8")
        out = read_note(path)
        self.assertLessEqual(len(out), 6000 + 200)
        self.assertIn("# Objective: ship AB#1", out)
        self.assertIn("## Update 399\n- status: step 399 done\n- next: step 400", out)
        self.assertNotIn("## Update 200\n", out)
        self.assertIn("chars omitted from the middle", out)
        # Cuts fall on line boundaries: no partial first or last line around the marker.
        before, _, after = out.partition("[...")
        self.assertTrue(before.endswith("\n"))
        self.assertTrue(after.split("]\n", 1)[1].startswith(("#", "-")))

    def test_context_lines_without_note_name_the_path(self):
        lines = context_lines("repo", "branch", self.dir)
        self.assertEqual(len(lines), 1)
        self.assertIn(str(self.dir / "repo--branch.md"), lines[0])
        self.assertIn("none yet", lines[0])

    def test_context_lines_with_note_include_it(self):
        (self.dir / "repo--branch.md").write_text("AB#1 PR !5 next: merge", encoding="utf-8")
        lines = context_lines("repo", "branch", self.dir)
        self.assertEqual(lines[0], f"Task note ({self.dir / 'repo--branch.md'}):")
        self.assertEqual(lines[1], "AB#1 PR !5 next: merge")


class GitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_outside_git_is_silent(self):
        self.assertEqual(task_note_lines(self.dir), [])

    def test_inside_git_keys_by_repo_and_branch(self):
        root = sample_repo(self.dir)
        lines = task_note_lines(root)
        self.assertEqual(len(lines), 1)
        self.assertIn("sample-repo--task-AB1-thing.md", lines[0])

    def test_linked_worktree_uses_primary_repo_name(self):
        root = sample_repo(self.dir)
        worktree = self.dir / "wt-cranky-joliot"
        git(root, "worktree", "add", "-q", "-b", "task/AB2-other", str(worktree))
        self.assertEqual(main_repo_name(worktree), "sample-repo")
        self.assertIn("sample-repo--task-AB2-other.md", task_note_lines(worktree)[0])


if __name__ == "__main__":
    unittest.main()
