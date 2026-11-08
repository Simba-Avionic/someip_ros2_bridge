# someipy fork

`setup.py` pins the SimBa fork: little-endian payloads, and a lifecycle API that differs
from upstream 2.x. The bridge works around these fork behaviours:

| Behaviour | Workaround (`someip.py`) |
| --- | --- |
| Outgoing multicast uses the routing table's interface | `IP_MULTICAST_IF` set to `local_ip` |
| Multicast socket is always opened | SD sockets are opened by the bridge; multicast is optional |
| SD whose source port is not `sd_port` is dropped | source port rewritten for the board's SD |
| `build_find_service_sd_header` declares an option it does not include | lengths fixed |
| `ClientServiceInstance.close()` leaves the UDP socket open | endpoint shut down explicitly |
| Events/replies are accepted from any sender | endpoint callback filters by sender |
| Logs every datagram with its own handler | level WARNING unless `--log-level DEBUG`; no propagation |
| SubscribeAck is not matched to its service | not fixed; with several services it logs `unexpected subscribe ACK` warnings |

Internals used (re-check them when the pin changes):

- `someipy._internal`: `utils` (socket helpers, `DatagramAdapter`), `someip_sd_builder`
  (find message), `someip_sd_header` (find detection in the fake board).
- `ServiceDiscoveryProtocol`: built directly; `datagram_received` and `send_multicast` are
  replaced; `unicast_transport`, `mcast_transport`, `sd_port`, `get_unicast_session_handler()`.
- `ClientServiceInstance`: `_offered_services` (board address, sender filter),
  `_someip_endpoint` (filter, close), `_endpoint` (advertised IP).
- `ServerServiceInstance._endpoint` (fake board, advertised IP).
