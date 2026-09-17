import os
import pytest
from src.config import get_settings
from src.pipeline import _pipeline_instance


@pytest.fixture(autouse=True)
def configure_test_environment(monkeypatch, request):
    """Ensure normal unit tests run in offline mock mode without polluting live CRM data."""
    if "test_live_frappe" not in request.node.nodeid:
        monkeypatch.setenv("MOCK_MODE", "true")
        get_settings.cache_clear()
        import src.pipeline

        src.pipeline._pipeline_instance = None
        yield
        get_settings.cache_clear()
        src.pipeline._pipeline_instance = None
    else:
        yield
