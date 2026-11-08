# Network

## Same LAN (default)

1. The board multicasts offers to `sd_multicast_ip:sd_port`. The bridge learns its IP and ports.
2. The bridge subscribes to the event group; events go to `local_ip:client_port`.
3. Method requests go to the offered endpoint; replies come back to `local_ip:client_port`.

## Other network: router, VPN, internet (`board_ip`)

Multicast does not cross routers. With `board_ip` set the bridge opens no multicast socket, sends
a SOME/IP-SD "find service" for each service to `board_ip:sd_port` every 2 s, and accepts SD only
from `board_ip`. `tools/fake_board.py` answers: a host that sent a find in the last 10 s gets
the cyclic offers by unicast. Real board firmware may not answer finds.

## NAT

SOME/IP carries IP addresses inside its messages, so behind NAT each side must offer its public
address (`advertised_ip`) and forward its ports unchanged:

- bridge side: UDP `sd_port` and every `client_port`;
- board side: UDP `sd_port` and the service ports.

## Security

SOME/IP and ROS 2 have no authentication.

- Events and replies are accepted only from the board: `board_ip`, or else the address the service
  was offered from. Others are dropped and logged once per address.
- With `board_ip`, SD from anyone else is dropped too. Without it, a forged offer is still
  possible: use a firewall or VPN on untrusted networks.
