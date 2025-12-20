"""
commit_history_categorizer
===========================

This script connects to a Git repository via its HTTPS URL, iterates over
every commit that touched a specified file, extracts metadata (branch name,
commit SHA, date, committer and message), computes the file's diff for that
commit, passes the diff to a user‑supplied categorisation function and
populates two pandas DataFrames based on the result: one for relevant
commits and one for irrelevant commits.

The script relies on the `git` command‑line tool rather than third‑party
Python libraries. It clones the repository into a temporary directory,
leverages Git log and show commands to gather commit information and diff
patches, and determines which branches contain each commit. The default
categorisation heuristic is simplistic (checking if the word "fix" appears in
the diff) but can be replaced by passing a custom function.

Example usage::

    python commit_history_categorizer.py \
        --repo_url https://github.com/octocat/Hello-World \
        --file_path README \
        --verbose \
        --output relevant.csv irrelevant.csv

This will produce two CSV files listing all commits to the "README" file in
the repository, grouped into relevant and irrelevant categories based on the
default heuristics.
"""

import argparse
import os
import sys
import tempfile
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
import subprocess


def run_git_command(repo_dir: str, args: List[str]) -> str:
    """
    Run a git command in the specified repository directory and return its
    standard output.

    Args:
        repo_dir: Path to the local Git repository.
        args: List of command arguments to pass to git (e.g. ['log', '--oneline']).

    Returns:
        The standard output from the git command as a string. The function
        raises subprocess.CalledProcessError if the command fails.
    """
    result = subprocess.run(
        ["git", "-C", repo_dir] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return result.stdout


def default_categorise_diff(diff_text: str) -> str:
    """
    Default categorisation function used when the caller does not provide one.

    This heuristic returns "relevant" if the diff contains the substring
    "fix" (case insensitive) and "irrelevant" otherwise. Replace this logic
    with your own categorisation criteria.

    Args:
        diff_text: The unified diff of a single file for a given commit.

    Returns:
        Either "relevant" or "irrelevant".
    """
    return "relevant" if "fix" in diff_text.lower() else "irrelevant"


def clone_repo(repo_url: str, token: Optional[str], verbose: bool = False) -> str:
    """
    Clone the Git repository from the provided URL into a temporary directory.

    If a token is supplied, it will be embedded in the URL to allow
    authentication for private repositories.

    Args:
        repo_url: HTTPS URL of the GitHub repository.
        token: Personal access token for authentication (optional).
        verbose: Whether to print cloning progress.

    Returns:
        Path to the cloned repository.

    Raises:
        subprocess.CalledProcessError: If cloning fails.
    """
    # Insert token into URL if provided
    clone_url = repo_url
    if token:
        protocol_split = repo_url.split("://")
        if len(protocol_split) == 2:
            clone_url = f"{protocol_split[0]}://{token}@{protocol_split[1]}"
    tmp_dir = tempfile.mkdtemp()
    if verbose:
        print(f"Cloning repository {repo_url} into {tmp_dir}...")
    try:
        subprocess.run(
            ["git", "clone", "--quiet", clone_url, tmp_dir],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if verbose:
            print("Clone complete.")
    except subprocess.CalledProcessError as e:
        # Clean up directory on failure
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
        raise e
    return tmp_dir


def list_file_commits(repo_dir: str, file_path: str) -> List[Dict[str, str]]:
    """
    Retrieve a list of commits affecting a particular file.

    Args:
        repo_dir: Path to the local Git repository.
        file_path: Path to the file within the repository.

    Returns:
        A list of dictionaries with keys: sha, author, date, and message.

    Each dictionary corresponds to a commit that changed the specified file.
    The date is in ISO 8601 format.
    """
    # Use a custom format string with delimiters that are unlikely to appear in
    # commit metadata. Separate by \0 (NUL) to handle characters safely.
    format_string = "%H%x00%an%x00%ad%x00%s"
    args = [
        "log",
        f"--pretty=format:{format_string}",
        "--date=iso",
        "--",
        file_path,
    ]
    output = run_git_command(repo_dir, args)
    commits = []
    for line in output.strip().split("\n"):
        parts = line.split("\x00")
        if len(parts) >= 4:
            sha, author, date, message = parts[:4]
            commits.append({
                "sha": sha,
                "author": author,
                "date": date,
                "message": message,
            })
    return commits


def branches_containing_commit(repo_dir: str, commit_sha: str) -> List[str]:
    """
    Determine which branches in the repository contain the specified commit.

    Args:
        repo_dir: Path to the local Git repository.
        commit_sha: SHA of the commit to check.

    Returns:
        A list of branch names (local and remote) that contain the commit.

    Notes:
        The output of `git branch --all --contains` includes both local and
        remote branches. Remote branches are prefixed with 'remotes/'. This
        function strips 'remotes/' to return cleaner branch names (e.g.
        'origin/main' becomes 'origin/main').
    """
    try:
        output = run_git_command(repo_dir, ["branch", "--all", "--contains", commit_sha])
    except subprocess.CalledProcessError:
        return []
    branches = []
    for line in output.strip().split("\n"):
        # Lines may have a leading '* ' for the current branch
        clean = line.strip().lstrip("* ").strip()
        if not clean:
            continue
        # Standardise remote branch names by removing 'remotes/' prefix
        if clean.startswith("remotes/"):
            clean = clean[len("remotes/"):]
        branches.append(clean)
    return branches


def diff_for_commit_file(repo_dir: str, commit_sha: str, file_path: str) -> str:
    """
    Get the unified diff for a specific file in a particular commit.

    Args:
        repo_dir: Path to the local Git repository.
        commit_sha: SHA of the commit to show.
        file_path: Path to the file within the repository.

    Returns:
        A string containing the unified diff for the file in the given commit.

    Notes:
        The `git show` command prints the diff relative to the parent commit.
        We suppress the commit header by using `--pretty=format:` and avoid
        coloured output.
    """
    try:
        output = run_git_command(
            repo_dir,
            ["show", "--pretty=format:", "--no-color", commit_sha, "--", file_path],
        )
    except subprocess.CalledProcessError:
        output = ""
    return output


def process_commits(
    repo_url: str,
    file_path: str,
    categorise_fn: Callable[[str], str] = default_categorise_diff,
    token: Optional[str] = None,
    verbose: bool = False,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Clone a repository, iterate through all commits that changed a specific file,
    categorise each commit and return two DataFrames with commit metadata.

    Args:
        repo_url: HTTPS URL of the repository to clone.
        file_path: Path to the file within the repository.
        categorise_fn: Function that takes a diff text and returns 'relevant'
            or 'irrelevant'.
        token: GitHub personal access token for private repositories (optional).
        verbose: If True, prints progress messages to stdout.

    Returns:
        (relevant_df, irrelevant_df): A tuple of pandas DataFrames containing
        commit information for relevant and irrelevant commits respectively.
    """
    # Clone repository
    repo_dir = clone_repo(repo_url, token, verbose=verbose)
    try:
        # Fetch commits for the file
        commits = list_file_commits(repo_dir, file_path)
        if verbose:
            print(f"Found {len(commits)} commits affecting {file_path}.")

        # Prepare empty DataFrames
        relevant_df = pd.DataFrame(columns=["branch", "commit_sha", "commit_date", "committer", "message"])
        irrelevant_df = pd.DataFrame(columns=["branch", "commit_sha", "commit_date", "committer", "message"])

        for idx, commit_info in enumerate(commits, 1):
            sha = commit_info["sha"]
            author = commit_info["author"]
            date_iso = commit_info["date"]
            message = commit_info["message"]

            if verbose:
                print(f"Processing commit {idx}/{len(commits)}: {sha}")

            # Determine branches containing this commit
            branches = branches_containing_commit(repo_dir, sha)
            # Extract diff for this commit and file
            diff_text = diff_for_commit_file(repo_dir, sha, file_path)
            # Categorise diff
            classification = categorise_fn(diff_text)

            # Populate DataFrames
            row_base = {
                "commit_sha": sha,
                "commit_date": date_iso,
                "committer": author,
                "message": message,
            }
            for branch in branches if branches else ["(no branch)"]:
                row = row_base.copy()
                row["branch"] = branch
                if classification.lower() == "relevant":
                    relevant_df = pd.concat([relevant_df, pd.DataFrame([row])], ignore_index=True)
                else:
                    irrelevant_df = pd.concat([irrelevant_df, pd.DataFrame([row])], ignore_index=True)
        return relevant_df, irrelevant_df
    finally:
        # Clean up the cloned repository directory
        try:
            subprocess.run(["rm", "-rf", repo_dir], check=False)
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Categorise commits to a specific file in a GitHub repository.")
    parser.add_argument("--repo_url", required=True, help="HTTPS URL of the GitHub repository (e.g. https://github.com/user/repo).")
    parser.add_argument("--file_path", required=True, help="Path to the file in the repository to analyse.")
    parser.add_argument(
        "--token",
        default=os.getenv("GITHUB_TOKEN"),
        help="Optional personal access token for private repositories. If omitted, the GITHUB_TOKEN environment variable will be used if set.",
    )
    parser.add_argument(
        "--output",
        nargs=2,
        metavar=("RELEVANT_CSV", "IRRELEVANT_CSV"),
        help="Optional names for CSV files to save relevant and irrelevant commit data.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output.")
    args = parser.parse_args()

    # Basic validation
    if not args.repo_url.startswith("http://") and not args.repo_url.startswith("https://"):
        print("Error: --repo_url must start with http:// or https://.", file=sys.stderr)
        sys.exit(1)

    # Process commits
    relevant_df, irrelevant_df = process_commits(
        repo_url=args.repo_url,
        file_path=args.file_path,
        token=args.token,
        verbose=args.verbose,
    )

    # Save DataFrames to CSV if requested
    if args.output:
        relevant_csv, irrelevant_csv = args.output
        relevant_df.to_csv(relevant_csv, index=False)
        irrelevant_df.to_csv(irrelevant_csv, index=False)
        if args.verbose:
            print(f"Data saved to {relevant_csv} and {irrelevant_csv}.")

    # Print summary counts
    print("\nProcessing complete.")
    print(f"Relevant commits: {len(relevant_df)}")
    print(f"Irrelevant commits: {len(irrelevant_df)}")

    if args.verbose:
        print("\nSample relevant commits:")
        print(relevant_df.head())
        print("\nSample irrelevant commits:")
        print(irrelevant_df.head())


if __name__ == "__main__":
    main()