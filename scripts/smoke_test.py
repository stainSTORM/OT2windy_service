"""Smoke test without a robot or a server: the app against the mock HTTP API, in Arkitekt's local
runtime.

    uv run scripts/smoke_test.py
"""

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]
os.environ.setdefault("RUN_SECONDS", "1")

import uvicorn  # noqa: E402

import ot2_mock_rest  # noqa: E402  (scripts/ot2_mock_rest.py)

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(ot2_mock_rest.app, host="127.0.0.1", port=port,
                                       log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)

config = Path(tempfile.mkdtemp()) / "config.yaml"
config.write_text(f"ot2: {{ip: 127.0.0.1, port: {port}}}\n"
                  "protocols: {dummy: dummy.py}\npoll_interval: 0.2\n")
os.environ["OT2_CONFIG"] = str(config)

import app as ot2  # noqa: E402  (reads OT2_CONFIG)
from arkitekt.testing import local_app  # noqa: E402


@ot2.app.action
def peek(state: ot2.RunState) -> str:
    """Peek"""
    return f"{state.available_protocols} {state.runs_completed} {state.current_protocol!r}"


with local_app(ot2.app) as run:
    assert run.call_local(peek) == "['dummy'] 0 ''", run.call_local(peek)
    assert run.call_local(ot2.run_protocol, protocol="dummy") is None
    assert run.call_local(peek) == "['dummy'] 1 ''", run.call_local(peek)

    dummy = (HERE.parent / "OT2Driver" / "protocols" / "dummy.py").read_text()
    run.call_local(ot2.save_protocol, name="extra", source=dummy)
    assert run.call_local(peek) == "['dummy', 'extra'] 1 ''", run.call_local(peek)

    # dropped into protocols/ by hand: offered within RESCAN_S, a helper without run() is not
    (config.parent / "protocols" / "dropped.py").write_text(dummy)
    (config.parent / "protocols" / "make_more.py").write_text("print('a helper')\n")
    deadline = time.monotonic() + ot2.RESCAN_S + 5
    while "dropped" not in run.call_local(peek) and time.monotonic() < deadline:
        time.sleep(0.5)
    assert run.call_local(peek) == "['dummy', 'dropped', 'extra'] 1 ''", run.call_local(peek)

    try:
        run.call_local(ot2.run_protocol_source, name="bad",
                       source="def run(p):\n    pass  # MOCK_ANALYSIS_FAIL\n")
    except Exception as e:  # noqa: BLE001
        assert "analysis" in str(e), e
    else:
        raise AssertionError("a protocol that failed the analysis was run")

print("smoke test passed")
