"""[fork] The dashboard can be restarted right after it stops (no ~30 s TIME_WAIT wait on macOS/Linux),
and a second instance on the same port is still refused."""
import os
import threading
import urllib.request

import pytest

import dashboard_server
from dashboard_server import DashboardServer, DashboardAlreadyRunning


@pytest.mark.skipif(os.name == "nt", reason="Windows keeps SO_REUSEADDR off (exclusive bind)")
def test_restart_right_after_stop_and_no_duplicate():
    first = DashboardServer(("127.0.0.1", 0), dashboard_server.DashboardHandler)
    port = first.server_address[1]
    threading.Thread(target=first.serve_forever, daemon=True).start()
    with pytest.raises(DashboardAlreadyRunning):
        DashboardServer(("127.0.0.1", port), dashboard_server.DashboardHandler)
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/ops/state", timeout=3).read()   # leaves a TIME_WAIT
    except Exception:
        pass
    first.shutdown(); first.server_close()
    again = DashboardServer(("127.0.0.1", port), dashboard_server.DashboardHandler)      # must not raise
    again.server_close()
