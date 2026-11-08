# Architecture

| Module | Does |
| --- | --- |
| `definitions.py` | Reads `system_definition/someip` JSON: services, IDs, payload types |
| `codec.py` | Payload bytes ↔ Python values; little-endian, flat structs of primitives |
| `config.py` | Loads and validates the YAML; names are resolved to IDs here |
| `bridge.py` | Translation and command rules; no ROS, no network ([commands](commands.md)) |
| `someip.py` | Discovery, events, method calls via `someipy` ([network](network.md), [someipy](someipy.md)) |
| `node.py` | ROS publishers and subscriptions; the `someip-ros2-bridge` command |

`tools/` is outside the package and not installed: `fake_board.py` (also used by the tests),
`talk_to_board.py`, `make_config.py`.

## Data flow

```text
board event  -> codec.decode -> event_to_ros: ros = payload[field] * scale + offset -> publish
ROS message  -> ros_to_argument: arg = msg.field * scale + offset -> codec.encode -> method call
```

- Config is checked fully before any socket or ROS node exists. ROS types and fields are checked
  when publishers and subscriptions are created, before the network starts.
- A struct event maps fields by name. Payloads longer than expected are accepted (newer firmware
  may append fields); shorter ones are dropped with a warning.
- `system_definition` has no event groups: like srp-app, the group ID is the service's lowest event
  ID unless `event_group_id` is set.
- ROS integer fields get rounded values; float fields get floats; header stamp and `frame_id` are
  filled when the message has a header.

## Threads

ROS callbacks run in an executor thread. SOME/IP runs in the asyncio loop of the main thread. ROS
messages cross to the loop with `loop.call_soon_threadsafe`; publishing from the loop is
thread-safe in rclpy. The main loop checks every second whether each board is found or lost.
