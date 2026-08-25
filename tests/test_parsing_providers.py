from agentic_pr_analyzer.parsing.model import WorkflowMarker
from agentic_pr_analyzer.parsing.providers import detect_provider
from agentic_pr_analyzer.parsing.providers.generic import GenericProvider
from agentic_pr_analyzer.parsing.providers.github_actions import GitHubActionsProvider


def test_github_actions_provider_detects_timestamped_sample():
    sample = "2026-08-21T10:22:10.5332965Z Current runner version: '2.336.0'\n"
    assert GitHubActionsProvider().detect(sample) is True


def test_generic_provider_is_terminal_fallback():
    assert GenericProvider().detect("anything, no timestamps here") is True


def test_detect_provider_picks_github_actions_for_timestamped_log():
    sample = "2026-08-21T10:22:10.5332965Z Current runner version: '2.336.0'\n"
    assert detect_provider(sample).name == "github_actions"


def test_detect_provider_falls_back_to_generic():
    provider = detect_provider("plain text log with no ci markers\nsecond line\n")
    assert provider.name == "generic"


def test_github_actions_split_line_extracts_timestamp_and_payload():
    provider = GitHubActionsProvider()
    ts, payload = provider.split_line(
        "2026-08-21T10:22:10.5332965Z Current runner version: '2.336.0'"
    )
    assert ts == "2026-08-21T10:22:10.5332965Z"
    assert payload == "Current runner version: '2.336.0'"


def test_github_actions_split_line_handles_missing_timestamp():
    provider = GitHubActionsProvider()
    ts, payload = provider.split_line("**/*requirements*.in")
    assert ts is None
    assert payload == "**/*requirements*.in"


def test_github_actions_marker_of_group_and_endgroup():
    provider = GitHubActionsProvider()
    marker, body = provider.marker_of("##[group]Runner Image Provisioner")
    assert marker == WorkflowMarker.GROUP
    assert body == "Runner Image Provisioner"
    marker, body = provider.marker_of("##[endgroup]")
    assert marker == WorkflowMarker.ENDGROUP
    assert body is None


def test_github_actions_marker_of_error_with_exit_code():
    provider = GitHubActionsProvider()
    marker, body = provider.marker_of("##[error]Process completed with exit code 1.")
    assert marker == WorkflowMarker.ERROR
    assert body == "Process completed with exit code 1."


def test_github_actions_marker_of_command_echo():
    provider = GitHubActionsProvider()
    marker, body = provider.marker_of(
        '[command]"C:\\Program Files\\Git\\bin\\git.exe" version'
    )
    assert marker == WorkflowMarker.COMMAND
    assert body == '"C:\\Program Files\\Git\\bin\\git.exe" version'


def test_github_actions_marker_of_none_for_plain_text():
    provider = GitHubActionsProvider()
    marker, body = provider.marker_of("Successfully set up CPython (3.14.7)")
    assert marker is None
    assert body is None


def test_generic_provider_split_line_is_identity():
    provider = GenericProvider()
    assert provider.split_line("some raw line") == (None, "some raw line")


def test_generic_provider_marker_of_always_none():
    assert GenericProvider().marker_of("##[group]x") == (None, None)
