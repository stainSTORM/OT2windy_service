# OT2windy

**The Opentrons OT-2 as an [Arkitekt](https://arkitekt.live) app.** It uploads a protocol to the
OT-2 over the robot's HTTP API (port 31950), runs it and waits until it has finished, so that a
workflow such as [protocolapp](https://github.com/stainSTORM/protocolapp) can call it.

It runs as a container on the openUC2 OS Raspberry Pi image (see [Raspberry Pi](#raspberry-pi)), or
with `uv` on any computer that can reach the OT-2.

## Actions

| Action | Arguments | What it does |
|---|---|---|
| `run_protocol` | `protocol` | Uploads, waits for the robot's analysis, runs and waits; returns the run id. Fails if the analysis or the run fails, or the run is stopped |
| `run_protocol_source` | `source`, `name` | The same for a protocol pasted as text, run once |
| `save_protocol` | `name`, `source` | Keeps a pasted protocol for `run_protocol` |
| `export_protocol` | `protocol` | The protocol's text |
| `get_robot_status` | | `idle`, `running`, `paused`, `finishing` or `failed` |
| `set_lights` | `on` | Deck lights: a quick check that the OT-2 is reachable |

`protocol` is a dropdown of the protocols in `config.yaml` and those saved with `save_protocol`.
`run_protocol`, `run_protocol_source` and `save_protocol` hold an `ot2` lock, so they do not
overlap. Cancelling the task in Arkitekt stops the run on the robot.

### Protocols from Arkitekt

`source` is Opentrons Python (a file with `run(protocol)`) or a Protocol Designer `.json`, pasted
into the text field. The app checks that it parses, uploads it and waits for the OT-2's own
analysis, which simulates the whole protocol. If the analysis fails (unknown labware, a volume the
pipette cannot take, …), the error is reported and nothing runs. Robot software without analyses
runs the protocol unchecked, with a note in the task log.

`save_protocol` writes `protocols/<name>.py` (or `.json`) next to `config.yaml`; on the Pi that is
`/home/pi/arkitekt/opentrons/protocols/`, a bind mount, so saved protocols survive container
updates. Saving under an existing name replaces the protocol and keeps the old file in
`protocols/.history/`. A **new** name shows up in the `run_protocol` dropdown after a restart, which
the app then does by itself (after the current run, if one is running). Names from `config.yaml`
are not overwritten.

YAML protocols are not supported: the OT-2 takes Python and Protocol Designer JSON.
`OT2Driver/ot2_driver.py`'s `ProtoPiler` is a stub that compiles nothing.

## Configuration

`config.yaml` holds the OT-2's address and the protocols `run_protocol` offers. On the first start
it is copied from [config.example.yaml](config.example.yaml).

```yaml
ot2:
  ip: 169.254.254.239     # USB/direct cable: link-local 169.254.x.x
  port: 31950
protocols:                # name -> file
  staining: ccIHC_cycle1_Pan-CK_testing.py
  dummy: dummy.py
```

A protocol file is looked up in `protocols/` next to `config.yaml` first, then among the ones
shipped in [OT2Driver/protocols](OT2Driver/protocols). Every other `.py` and `.json` file in
`protocols/` is offered under its file name, as `save_protocol` stores them.
`analysis_timeout` (default 300 s) bounds the wait for the robot's analysis. Custom labware
(`OT2Driver/labware/`) must be installed on the OT-2 itself, with the Opentrons App.

## Running locally (uv)

```bash
uv sync
uv run app.py                 # config.yaml next to app.py; OT2_CONFIG=... for another one
```

Open <http://localhost:8080/> to bind it (see below). Without a robot, start the stand-in for its
HTTP API and set `ot2.ip: 127.0.0.1`. A run there finishes after `RUN_SECONDS` (default 5); its
analysis fails for a protocol that contains `MOCK_ANALYSIS_FAIL`:

```bash
uv run scripts/ot2_mock_rest.py
```

`uv run opentrons_simulate OT2Driver/protocols/dummy.py` tries a protocol before it goes to the
robot.

## Binding to an Arkitekt server

The page on port 8080 (on the Pi: `http://<pi>/opentrons/`):

1. Enter the server (e.g. `https://go.arkitekt.live`, or a local one) and press **Bind**.
2. The page shows a code and an approval link. Open the link in any browser, sign in and check that
   the code matches.
3. The app registers its actions and stays connected. The login is stored in `.state/` next to
   `config.yaml`, so the app reconnects by itself after a restart.

**Disconnect** keeps the stored login; **Unbind** forgets it. Tick *allow plain http* for a server
without TLS (e.g. a NAS); the session token then travels unencrypted. The page also edits
`config.yaml`: **Save and restart** checks the YAML, writes it and restarts the app.

`arkitekt_bind.py` is the same file in OT2windy_service and fairinogale. Keep the copies identical.

## Docker

```bash
docker build -t ot2windy .
docker run -p 8080:8080 -v ~/ot2:/config ot2windy
```

GitHub Actions ([.github/workflows/docker.yml](.github/workflows/docker.yml)) builds
`ghcr.io/stainstorm/ot2windy` for arm64 and amd64 on every push, tagged `sha-<commit>` and with the
branch name.

## Raspberry Pi

The Arkitekt robot pallet (branch
[`arkitekt-service` of openUC2/os-rpi](https://github.com/openUC2/os-rpi/tree/arkitekt-service))
deploys this container as `opentrons`, switched on, in its `os-rpi-arkitekt` SD card image. The
microscope pallet on `edge` does not have it. The page is linked from the machine's home page.

On the Pi, the config and extra protocols live in `/home/pi/arkitekt/opentrons/`. To ship a new version,
push here, then set the new `sha-<commit>` tag in that branch's
`deployments/opentrons.pkg/deployment.compose.yml`.

## File structure

```
OT2windy/
├── app.py                    # the Arkitekt app: actions, config
├── arkitekt_bind.py          # binding page and connection (same file in fairinogale)
├── config.example.yaml       # copied to config.yaml on the first start
├── OT2Driver/
│   ├── ot2_driver.py         # HTTP driver: upload, run, status, lights
│   ├── protocols/            # protocols shipped with the app
│   └── labware/              # custom labware definitions (install on the OT-2)
├── scripts/ot2_mock_rest.py  # stand-in for the OT-2's HTTP API
└── Dockerfile
```

## License

MIT.
