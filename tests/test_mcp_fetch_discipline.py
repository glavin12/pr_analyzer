"""Shape guard for AGENTS.md's MCP fetch->parse->summarize recipe (see CLAUDE.md §4.9 /
docs/plans/section-8-mcp-adapter.md §3): the raw CI log body must never enter the agent's
context. This does not parse the recipe, it just checks the documented text still says the
right shape.
"""

import re
from pathlib import Path

AGENTS_MD = Path(__file__).resolve().parent.parent / "AGENTS.md"


def test_recipe_redirects_log_to_a_file():
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert re.search(r"gh api[^\n]*jobs/[^\n]*logs[^\n]*>", text), (
        "AGENTS.md should show the raw job-log API (`gh api .../jobs/<id>/logs`) redirected to a file"
    )


def test_recipe_never_pipes_log_to_stdout():
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert not re.search(r"logs[\"']?\s*\|\s*cat", text)
    assert not re.search(r'^\s*cat\s+["\']?\$TMPDIR', text, re.MULTILINE)


def test_recipe_forbids_reading_the_log_directly():
    text = AGENTS_MD.read_text(encoding="utf-8")
    # a single prohibition sentence naming cat/grep and Read together
    assert re.search(r"[Nn]ever[^\n]*cat[^\n]*grep[^\n]*Read", text)
