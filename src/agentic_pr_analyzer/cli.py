import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

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
from .github import GitHubClient, ingest_latest_failure, load_raw_log
from .mcp.adapter import build_summary
from .parsing import parse_log, to_json
from .parsing.model import LogSource


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentic-pr-analyzer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify", help="Confirm the configured GitHub token works (Slice 0).")
    ingest = sub.add_parser(
        "ingest", help="Fetch the latest failed CI run's logs as a fixture (Slice 1)."
    )
    ingest.add_argument("repo", help="owner/repo, e.g. psf/requests")
    parse = sub.add_parser(
        "parse", help="Parse a saved CI log fixture into a FailureReport (Section 1)."
    )
    parse.add_argument("logpath", help="Path to a .log file saved by `ingest` (needs its .json sidecar).")
    analyze = sub.add_parser(
        "analyze", help="Parse a CI log and print a tiered, evidence-backed failure summary (MCP adapter)."
    )
    analyze.add_argument("logpath", help="Path to a log file (local dev -- no allow-listing).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "parse":
            log_path = Path(args.logpath)
            if not log_path.exists():
                print(f"error: log file not found: {log_path}", file=sys.stderr)
                return 4
            raw = load_raw_log(log_path)
            report = parse_log(raw.content, LogSource.from_raw_log(raw))
            print(to_json(report))
            return 0

        if args.command == "analyze":
            log_path = Path(args.logpath)
            if not log_path.exists():
                print(f"error: log file not found: {log_path}", file=sys.stderr)
                return 4
            content = log_path.read_text(encoding="utf-8", errors="surrogatepass")
            report = parse_log(content, source=None)
            print(json.dumps(build_summary(report, content, args.logpath), indent=2))
            return 0

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
        # LEAK-2: never print str(e) or e.request.url/.path_url here.
        # get_job_log (client.py) follows GitHub's 302 to a short-lived
        # SIGNED blob URL, so a ConnectionError/ReadTimeout can be raised
        # against that redirected URL; str(e) and .path_url both embed the
        # full query string, SAS credentials included. Take only the bare
        # path component (no query, no host) for a useful-but-safe hint.
        request = getattr(e, "request", None)
        path = urlsplit(request.url).path if request is not None and request.url else "?"
        print(
            f"error: could not reach GitHub API ({type(e).__name__}) for {path}",
            file=sys.stderr,
        )
        return 5

    return 0
