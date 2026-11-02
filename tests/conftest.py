"""Keep ordinary pytest runs away from the developer's saved app settings."""
import os
import tempfile

_test_runtime = tempfile.TemporaryDirectory(prefix="pa-agent-tests-", ignore_cleanup_errors=True)
os.environ.setdefault("PA_AGENT_DATA_DIR", _test_runtime.name)
