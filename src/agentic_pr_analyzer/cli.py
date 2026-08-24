import argparse
import sys
from pathlib import Path

import requests

from .config import load_settings
from .exceptions import (
    ConfigError,
    GitHubAuthError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    NoFailedJobsFoundError,
    NoFailedRunsFoundError,
)
from .github import GitHubClient, ingest_latest_failure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentic-pr-analyzer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify", help="Confirm the configured GitHub token works (Slice 0).")
    ingest = sub.add_parser(
        "ingest", help="Fetch the latest failed CI run's logs as a fixture (Slice 1)."
    )
    ingest.add_argument("repo", help="owner/repo, e.g. psf/requests")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = GitHubClient(token=load_settings().github_token)

        if args.command == "verify":
            data = client.verify_access()["resources"]["core"]
            print(f"OK: token valid. {data['remaining']}/{data['limit']} requests remaining.")
            return 0

        if args.command == "ingest":
            parts = args.repo.split("/")
            if len(parts) != 2 or not all(parts):
                print(
                    f"error: repo must be exactly 'owner/repo', got {args.repo!r} "
                    "(strip any https://github.com/ prefix)",
                    file=sys.stderr,
                )
                return 2
            owner, repo = parts
            paths = ingest_latest_failure(client, owner, repo, Path("tests/fixtures/raw_logs"))
            for p in paths:
                print(f"saved: {p}")
            return 0

    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except GitHubAuthError as e:
        print(f"error: GitHub rejected the token (401): {e}", file=sys.stderr)
        return 3
    except GitHubForbiddenError as e:
        print(
            f"error: GitHub denied the request (403), possibly rate-limited "
            f"(reset={e.rate_limit_reset}, retry_after={e.retry_after}): {e}",
            file=sys.stderr,
        )
        return 3
    except GitHubNotFoundError as e:
        print(f"error: not found (404): {e}", file=sys.stderr)
        return 4
    except (NoFailedRunsFoundError, NoFailedJobsFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except requests.exceptions.RequestException as e:
        print(f"error: could not reach GitHub API: {e}", file=sys.stderr)
        return 5

    return 0
