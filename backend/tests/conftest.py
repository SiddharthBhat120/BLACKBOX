import os
import sys
import tempfile
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="blackbox-test-")
os.environ["LLM_API_KEY"] = ""
os.environ["DEFAULT_TZ"] = "UTC"
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "demo"))

import pytest  # noqa: E402

from app.store import Store  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture(scope="session")
def demo_runs(tmp_path_factory):
    """Simulate a few incidents once per test session."""
    from app.importer import import_bundle
    from simulate import simulate

    st = Store(":memory:")
    runs = {}
    for scenario in ("bad_deploy", "slow_dependency", "disk_full"):
        out = tmp_path_factory.mktemp(scenario)
        truth = simulate(scenario, 1, out)
        inc = st.create_incident(scenario, scenario=scenario, ground_truth=truth)
        import_bundle(st, inc.id, out)
        runs[scenario] = (st, inc.id, truth)
    return runs
