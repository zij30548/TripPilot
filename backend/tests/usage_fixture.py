"""Independent real counters for offline business-handler tests, never a bypass."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import weakref

from app.business_usage import DailyLimit, SERVICES, UsageSettings, UsageStore
from app.usage_runtime import bind_usage_context, reset_usage_context


class UsageFixture:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="trippilot-test-usage-")
        self._cleanup = weakref.finalize(self, self.directory.cleanup)
        self.store = UsageStore(UsageSettings(_env_file=None,
            business_usage_path=str(Path(self.directory.name) / "usage.sqlite3"),
            business_rate_account_limit=100_000, business_rate_global_limit=100_000,
            business_daily_limits={service: DailyLimit(account=100_000, **{"global": 100_000}) for service in SERVICES}))
        self.store.initialize()

    @contextmanager
    def bind(self, path="/media/landmark", owner_id="a" * 32):
        token = bind_usage_context(owner_id, path, self.store)
        try:
            yield self.store
        finally:
            reset_usage_context(token)

    def close(self):
        self._cleanup()
