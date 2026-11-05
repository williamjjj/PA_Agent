"""The web distribution deliberately excludes desktop-only UI and fallback routes."""
collect_ignore = [
    "unit/test_ai_model_dialog_glm.py",
    "unit/test_confidence_gate_display.py",
    "unit/test_confidence_threshold_default.py",
    # These exercise desktop-specific provider switching; web requests must
    # stay with their own account's credential and bounded HTTP ModelClient.
    "unit/test_provider_fallback_chain.py",
    "unit/test_free_chat_spare_channel.py",
    "unit/test_sensenova_fallback.py",
    "unit/test_sensenova_fallback_review.py",
    "unit/test_lite_fallback.py",
    "unit/test_glm_backup.py",
]


def pytest_collection_modifyitems(items):
    import pytest
    desktop_switching = {"test_midcall_primary_failure_changes_contract", "test_whole_pipeline_switch_at_stage2"}
    for item in items:
        if item.originalname in desktop_switching:
            item.add_marker(pytest.mark.skip(reason="Desktop provider switching is not enabled for web tenants"))
