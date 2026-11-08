# SOME/IP ↔ ROS 2 bridge

Board events become ROS topics; ROS messages become board method calls. One YAML file names the
services, events and methods as in `system_definition/someip`; IDs and types come from there.

```text
board  <-- SOME/IP over UDP -->  bridge  <-- ROS 2 topics -->  ground station
```

The package (`someip_ros2_bridge/`) is only the translation, run as `someip-ros2-bridge`
(`--check`: validate the config and print it). Helpers in `tools/`:

| Tool | Purpose |
| --- | --- |
| `fake_board.py` | Fake board for testing without hardware |
| `talk_to_board.py` | Find the board and print its events, without ROS |
| `make_config.py` | Generate a config from `system_definition` |

## Try it without ROS

Needs Python 3.10+ and git. Work in the folder that contains this repo:

```bash
git clone https://github.com/Simba-Avionic/system_definition.git
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -U pip
pip install -e someip_ros2_bridge
```

On Windows use `python` and `.venv\Scripts\activate`. Then, each in its own terminal with `.venv`
active:

```bash
python someip_ros2_bridge/tools/fake_board.py
python someip_ros2_bridge/tools/talk_to_board.py
```

The second prints `found  FcRadioService at 127.0.0.2:30501` and the board's events.

## Run with ROS 2

Needs ROS 2 (Humble or newer) and access to the private `gs_interfaces` repo. In the same folder,
with ROS sourced and `.venv` active:

```bash
git clone https://github.com/Simba-Avionic/gs_interfaces.git
python -m colcon build --symlink-install --packages-select gs_interfaces someip_ros2_bridge
```

In every terminal: source ROS, activate `.venv`, source `install/setup` (`.bash`, `.zsh`, `.ps1`).

```bash
python someip_ros2_bridge/tools/fake_board.py
someip-ros2-bridge
ros2 topic echo /tanking/sensors
ros2 topic pub --once /tanking/commands gs_interfaces/msg/TankingCommands "{valve_feed_oxidizer: 0, valve_feed_pressurizer: 0}"
ros2 topic pub --once /tanking/commands gs_interfaces/msg/TankingCommands "{valve_feed_oxidizer: 1, valve_feed_pressurizer: 0}"
```

The first message only records the panel position; the second moves the valve, and the bridge logs
`FcRadioService.SetGSEOxidizerFillValve(1 = open) -> True`. `python -m colcon`
makes the installed command (also `ros2 run someip_ros2_bridge someip-ros2-bridge`) use `.venv`;
`--symlink-install` lets the installed config find `system_definition`. Upstream `someipy`
is not compatible; see [docs/someipy.md](docs/someipy.md).

## Connect to a board

Same network: give this computer's IP (or interface name on Linux); the board is found
automatically.

```bash
someip-ros2-bridge --local-ip 192.168.10.49
```

Other network (router, VPN, internet): also give the board's address.

```bash
someip-ros2-bridge --local-ip 100.64.0.2 --board-ip 100.64.0.5
```

Behind NAT see [docs/network.md](docs/network.md).

> ⚠ SOME/IP and ROS 2 have no authentication. Use a VPN or a firewall that allows only the other
> side, and never expose ROS 2 traffic.

`tools/talk_to_board.py` takes the same `--config`, `--local-ip` and `--board-ip`. With
`--call METHOD[=VALUE]` it also calls a configured method with that SOME/IP value (checked against
`allowed`, no `scale`): real hardware moves, so use it only when that is safe.

## Config

[`config/bridge.yaml`](config/bridge.yaml) shows events and methods,
[`examples/buttons.yaml`](examples/buttons.yaml) methods without arguments. Or generate one:

```bash
python someip_ros2_bridge/tools/make_config.py                     # list services
python someip_ros2_bridge/tools/make_config.py EnvAppFc -o my.yaml  # structs: fill in the template
someip-ros2-bridge --config my.yaml --check
```

| Key | Example | Meaning |
| --- | --- | --- |
| `definitions` | `../../system_definition/someip` | Relative to the YAML |
| `network.local_ip` | `192.168.10.49`, `eth0` | This computer (`--local-ip`) |
| `network.board_ip` | `100.64.0.5` | Board on another network (`--board-ip`) |
| `network.advertised_ip` | `203.0.113.7` | This computer's public IP behind NAT (`--advertised-ip`) |
| `network.sd_multicast_ip`, `sd_port` | `224.224.224.245`, `30490` | Must match the board |
| `network.payload_byte_order` | `little` | `big` only for non-SimBa peers |
| `services.<S>.instance_id` | `1` | Instance the board offers |
| `services.<S>.client_port` | `10319` | Local UDP port, one per service |
| `services.<S>.event_group_id` | `32769` | Default: lowest event ID |
| `events.<E>.topic`, `type`, `frame_id` | `tanking/sensors`, `gs_interfaces/msg/TankingSensors` | Where to publish |
| `events.<E>.fields.<ros_field>` | `rssi` or `{from: rssi, scale: 0.01, offset: 0}` | `ros = payload * scale + offset` |
| `methods.<M>.topic`, `type`, `field` | `tanking/commands`, …, `valve_feed_oxidizer` | What triggers the call |
| `methods.<M>.allowed` | `{0: closed, 1: open}` or `[0, 1]` | Accepted values |
| `methods.<M>.scale`, `offset` | `100`, `0` | `argument = value * scale + offset` |
| `methods.<M>.only_on_change` | `true` | `false`: every message fires, not only changes |

Payloads: `void`, `bool`, `(u)int8…64`, `float32/64` and flat structs; method arguments are plain.

## Commands

Fire and forget: the board moves only when a panel switch moves. Nothing is retried or resent, not
even after a board reboot. Rules and log messages: [docs/commands.md](docs/commands.md).

> ⚠ An unset ROS field is `0`, which means **closed**: always fill in every valve field.

## Troubleshooting

| You see | Do |
| --- | --- |
| `local_ip … is neither …` | Use one of this computer's IPv4 addresses. |
| `<Service>: cannot open UDP …` | `client_port` is used by another program; change it. |
| `Cannot open Service Discovery on …:30490` | Another bridge or simulator uses that IP; stop it. |
| `Cannot open … 127.0.0.2` on macOS | Add the address: `sudo ifconfig lo0 alias 127.0.0.2`. |
| No `found at …` | Same subnet? Firewall open for 30490 and `client_port`? Wifi often blocks multicast. `instance_id` and minor version must match. Other network: use `--board-ip`. |
| `found`, but no ROS messages | Wrong `event_group_id` or blocked `client_port`; `--log-level DEBUG`. |
| `NOT SENT: … not found` | The board is not found yet; see the rows above. |
| `NO REPLY within 10 s` | Reply blocked: open `client_port`. The board may have done it. |
| `Ignoring SOME/IP from …` | Not the board, or not found yet. Behind NAT, `board_ip` must be its public IP. |
| `E_UNKNOWN_METHOD`, `Malformed … event` | Firmware and `system_definition` differ. |
| `ROS 2 not found`, `ROS message … not found` | Source ROS and `install/setup`. |
| `No SOME/IP definition files found` | Clone `system_definition` next to this repo. |

## Development

```bash
pip install -e "someip_ros2_bridge[test]"
cd someip_ros2_bridge
python -m pytest     # no ROS or hardware needed
make check           # also ruff, mypy and the config check, as CI does
```

On macOS add the loopback addresses the tests use first:
`sudo ifconfig lo0 alias 127.0.0.2; sudo ifconfig lo0 alias 127.0.0.3`. Design notes are in
[docs/](docs).

## Open questions

1. `system_definition` says pressures are in "hekto Bar"; boards and `gs_web_app` use 1/100 bar.
2. Pressure-feed tank pressure goes to `tanking/pressurizer/sensors`, not shown by `gs_web_app`.
3. `gs_interfaces` has no acknowledgement message; command results are only logged.
4. A plain event fills one field of a message; the other fields stay `0`.
5. `TankingCommands` vent and decoupler fields, and `gs_web_app`'s abort and tare, have no method in
   `system_definition`, so the board never gets them.
