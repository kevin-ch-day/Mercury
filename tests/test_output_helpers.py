"""Tests for shared CLI output helpers."""

from mercury.core import output


def test_status_tags() -> None:
    assert output.tag_ok("done").startswith("[ok]")
    assert output.tag_warn("missing").startswith("[--]")
    assert output.tag_fail("error").startswith("[!!]")


def test_action_banner(capsys) -> None:
    output.action_banner("Environment Check")
    out = capsys.readouterr().out
    assert "Environment Check" in out
    assert "---" in out
