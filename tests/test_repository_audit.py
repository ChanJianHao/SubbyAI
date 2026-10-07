"""Release gates inspect filenames, metadata, and frozen code paths."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "privacy_audit", Path(__file__).resolve().parents[1] / "scripts" / "audit_privacy.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.mark.parametrize(
    "name", [".env.local", "config/client.pfx", ".codex/state.json", "logs/app.log"]
)
def test_private_files_are_rejected_even_without_recognizable_secrets(name):
    assert audit.repository_path_findings(name, "worktree:test")


def test_example_configuration_and_source_are_allowed():
    assert not audit.repository_path_findings(".env.example", "worktree:test")
    assert not audit.repository_path_findings("src/subbyai/core/secrets.py", "worktree:test")


def test_frozen_code_paths_reject_private_accounts_and_allow_generic_ci():
    private = "/".join(("C:", "Users", "private-account", "source.py")).encode()
    generic = "/".join(("", "Users", "runner", "source.py")).encode()
    assert audit.scan(private, "binary:application:module", set())
    assert not audit.scan(generic, "binary:application:module", set())


def test_identity_is_checked_in_history_metadata():
    assert audit.scan(b"tagger Private-account", "tag:test", {b"private-account"})


def test_windows_resource_strings_are_scanned_for_private_paths_and_credentials():
    path = "/".join(("C:", "Users", "private-account", "source.py"))
    assert any(
        finding["rule"] == "private-home-path"
        for finding in audit.scan(path.encode("utf-16-le"), "binary:app:resource", set())
    )
    token = "ghp_" + "synthetic" * 5
    assert any(
        finding["rule"] == "service-token"
        for finding in audit.scan(token.encode("utf-16-le"), "artifact:resource", set())
    )


def test_additional_private_names_are_redacted_in_findings():
    findings = audit.scan(b"author Private Person", "commit:example", {b"private person"})
    assert findings == [{"location": "commit:example", "rule": "current-machine-identity"}]
