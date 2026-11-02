"""A validation failure must fail the test rather than wait for a human dialog."""
import pytest


@pytest.fixture(autouse=True)
def no_blocking_error_report(monkeypatch):
    from pa_agent.gui import validation_debug_dialog
    def unexpected_dialog(*args, **kwargs):
        raise AssertionError(f"Unexpected validation dialog: {args[1:3]}")
    monkeypatch.setattr(validation_debug_dialog, "show_validation_debug_dialog", unexpected_dialog)
