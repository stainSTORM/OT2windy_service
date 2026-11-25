"""Opentrons OT-2 as an Arkitekt app.

Uploads a protocol to the OT-2 over its HTTP API (port 31950), lets the robot analyse it, runs it
and waits until it has finished. config.yaml names the robot. run_protocol offers the protocols
config.yaml names and those saved with save_protocol (protocols/ next to config.yaml, on the Pi a
bind mount); run_protocol_source runs pasted Python or Protocol Designer JSON once.

Run:  uv run app.py   then open http://localhost:8080/ to bind it.
OT2_CONFIG picks another config.yaml (default: next to this file).
Without a robot: uv run scripts/ot2_mock_rest.py, and set ot2.ip to 127.0.0.1.
"""

import json
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Annotated

import koil
import yaml
from arkitekt import App, Description, Effects, Task
from arkitekt.widgets import ParagraphWidget, withChoices

from arkitekt_bind import restart_soon, serve
from OT2Driver.ot2_driver import OT2_Config, OT2_Driver, RunStatus

HERE = Path(__file__).resolve().parent
CONFIG = Path(os.getenv("OT2_CONFIG", HERE / "config.yaml"))

if not CONFIG.exists():  # first start: copy the defaults where they can be edited
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "config.example.yaml", CONFIG)
cfg = yaml.safe_load(CONFIG.read_text())
PROTOCOLS = cfg.get("protocols") or {}
SAVED = CONFIG.parent / "protocols"  # extra protocols and those saved with save_protocol
FINISHED = (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.STOPPED)


def protocol_names() -> list[str]:
    """config.yaml's protocols, then the other .py and .json files in protocols/."""
    names = list(PROTOCOLS)
    for path in sorted([*SAVED.glob("*.py"), *SAVED.glob("*.json")]):
        if path.stem not in names and path.name not in PROTOCOLS.values():
            names.append(path.stem)
    return names


PROTOCOL_NAMES = protocol_names()

app = App(cfg.get("app", "ot2windy"), "1.0.0", effects=Effects.IRREVERSIBLE)

Protocol = Annotated[str, Description("Protocol from config.yaml or saved with save_protocol")]
if PROTOCOL_NAMES:
    Protocol = Annotated[Protocol, withChoices(*PROTOCOL_NAMES)]
Source = Annotated[str, ParagraphWidget(), Description(
    "The protocol: Opentrons Python (with run(protocol)) or Protocol Designer JSON")]
ProtocolName = Annotated[str, Description("Letters, digits, - and _; the robot shows it as the "
                                          "file name")]

_driver = None
_driver_lock = threading.Lock()
_running = threading.Lock()  # held during a run; a restart of the app waits for it


def driver() -> OT2_Driver:
    """The connection to the OT-2, opened on first use (it blinks the lights once)."""
    global _driver
    with _driver_lock:
        if _driver is None:
            ot2 = cfg["ot2"]
            _driver = OT2_Driver(OT2_Config(ip=ot2["ip"], port=ot2.get("port", 31950)))
        return _driver


def protocol_file(name: str) -> Path:
    """config.yaml's file in protocols/ or among the protocols shipped with the app, else
    protocols/<name>.py or .json."""
    if name in PROTOCOLS:
        for folder in (SAVED, HERE / "OT2Driver" / "protocols"):
            if (folder / PROTOCOLS[name]).exists():
                return folder / PROTOCOLS[name]
        raise FileNotFoundError(f"{PROTOCOLS[name]} is in neither {SAVED} "
                                f"nor the protocols shipped with the app")
    for suffix in (".py", ".json"):
        if re.fullmatch(r"[A-Za-z0-9_-]+", name) and (SAVED / f"{name}{suffix}").exists():
            return SAVED / f"{name}{suffix}"
    raise ValueError(f"Unknown protocol {name!r}; there are {protocol_names()}")


def check_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
        raise ValueError(f"{name!r} is not a protocol name: use letters, digits, - and _")
    return name


def source_suffix(source: str) -> str:
    """.json for Protocol Designer JSON, .py for Python; refuses either if it does not parse."""
    if source.lstrip().startswith("{"):
        try:
            if isinstance(json.loads(source), dict):
                return ".json"
        except json.JSONDecodeError as e:
            raise ValueError(f"The protocol looks like JSON but is not valid: {e}") from None
    try:
        compile(source, "protocol.py", "exec")
    except SyntaxError as e:
        raise ValueError(f"The protocol is not valid Python: line {e.lineno}: {e.msg}") from None
    return ".py"


def analyse(ot2: OT2_Driver, protocol_id: str, task: Task) -> None:
    """Wait for the robot's analysis of an uploaded protocol, and refuse one that fails it:
    the robot simulates the whole protocol, so a wrong labware or volume shows here, not mid-run."""
    timeout = cfg.get("analysis_timeout", 300)
    for _ in range(int(timeout / 0.5) + 1):
        analysis = ot2.get_analysis(protocol_id)
        if analysis is None:
            task.log("The OT-2 did not analyse the protocol (older robot software): running it unchecked")
            return
        if analysis.get("status") == "completed":
            if analysis.get("result") == "ok":
                task.log(f"Protocol {protocol_id}: analysis ok")
                return
            errors = "; ".join(e.get("detail") or e.get("errorType") or str(e)
                               for e in analysis.get("errors") or []) or "no details"
            raise RuntimeError(f"The OT-2's analysis of the protocol ended {analysis.get('result')}: "
                               f"{errors}. Nothing was run.")
        koil.sleep(0.5)
    raise TimeoutError(f"The OT-2 did not finish analysing the protocol in {timeout} s "
                       f"(analysis_timeout in config.yaml). Nothing was run.")


def run(path: Path, label: str, task: Task) -> str:
    """Upload, analyse, run and wait; returns the run id. Cancelling stops the run."""
    with _running:
        ot2 = driver()
        protocol_id = ot2.upload(str(path))
        analyse(ot2, protocol_id, task)
        run_id = ot2.create_run(protocol_id)
        ot2.current_run_id = run_id
        task.log(f"{label}: protocol {protocol_id}, run {run_id}")
        ot2.execute(run_id)
        return wait(ot2, label, run_id, task)


def wait(ot2: OT2_Driver, label: str, run_id: str, task: Task) -> str:
    status = None
    try:
        while status not in FINISHED:
            koil.sleep(cfg.get("poll_interval", 1))
            new = ot2.check_run_status(run_id)
            if new != status:
                task.log(f"Run {run_id}: {new.value}")
                status = new
    except BaseException:
        ot2.cancel(run_id)  # cancelled (or the robot stopped answering): stop the run too
        raise
    if status != RunStatus.SUCCEEDED:
        raise RuntimeError(f"{label} ended {status.value} (run {run_id})")
    return run_id


@app.action(locks=["ot2"])
def run_protocol(protocol: Protocol, *, task: Task) -> str:
    """Run Protocol

    Uploads the protocol, waits for the OT-2's analysis of it, runs it and waits until the robot
    has finished. Returns the run id. A protocol that fails the analysis is not run. Cancelling
    the task stops the run on the robot.
    """
    return run(protocol_file(protocol), protocol, task)


@app.action(locks=["ot2"])
def run_protocol_source(source: Source, name: ProtocolName = "uploaded", *, task: Task) -> str:
    """Run Protocol Source

    Runs a protocol given as text, once, like run_protocol: uploads it, waits for the OT-2's
    analysis, runs it and waits until the robot has finished. Returns the run id. Nothing is
    kept here; save_protocol keeps a protocol for run_protocol.
    """
    suffix = source_suffix(source)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"{check_name(name)}{suffix}"
        path.write_text(source)
        return run(path, name, task)


@app.action(locks=["ot2"], effects=Effects.REPEATABLE)
def save_protocol(name: ProtocolName, source: Source) -> str:
    """Save Protocol

    Keeps a protocol (Python or Protocol Designer JSON) in protocols/ next to config.yaml, where
    run_protocol finds it by name. Replaces a saved protocol of that name; the old file goes to
    protocols/.history/. Does not touch the robot: run_protocol checks it there. A new name
    appears in run_protocol once the app has restarted, which it then does by itself.
    """
    if check_name(name) in PROTOCOLS:
        raise ValueError(f"{name!r} is defined in config.yaml: edit it there, or pick another name")
    suffix = source_suffix(source)
    SAVED.mkdir(parents=True, exist_ok=True)
    for old in (SAVED / f"{name}.py", SAVED / f"{name}.json"):
        if old.exists():
            history = SAVED / ".history"
            history.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            old.replace(history / f"{old.stem}-{stamp}{old.suffix}")
    path = SAVED / f"{name}{suffix}"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(source)
    tmp.replace(path)
    message = f"Saved {name!r} to {path}"
    if protocol_names() != PROTOCOL_NAMES:
        restart_soon(f"new protocol {name!r}")
        message += "; the app restarts so that run_protocol offers it"
    return message


@app.action(effects=Effects.NONE)
def export_protocol(protocol: Protocol) -> str:
    """Export Protocol

    The protocol's text, to check it or to edit and save it again.
    """
    return protocol_file(protocol).read_text()


@app.action
def get_robot_status() -> str:
    """Robot Status

    idle, running, paused, finishing, failed or offline.
    """
    return driver().get_robot_status().value


@app.action
def set_lights(on: bool = True) -> None:
    """Set Lights

    Switches the deck lights: a quick check that the OT-2 is reachable.
    """
    driver().change_lights_status(status=on)


if __name__ == "__main__":
    serve(app, CONFIG, int(os.getenv("PORT", "8080")), busy=_running)
