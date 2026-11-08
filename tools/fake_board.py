from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import math
import sys
import time
from pathlib import Path
from typing import Any

from someip_ros2_bridge.codec import INTEGER_TYPES, Codec, CodecError
from someip_ros2_bridge.config import BridgeConfig, ConfigError, load_config
from someip_ros2_bridge.definitions import DefinitionError, Method
from someip_ros2_bridge.node import default_config_path
from someip_ros2_bridge.someip import (
    SUBSCRIPTION_TTL_S,
    SomeIpError,
    open_service_discovery,
    quiet_someipy,
    someip_service,
)

PEER_TIMEOUT_S = 10.0

_LOG = logging.getLogger("fake_board")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fake board offering the configured services.")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--board-ip", default="127.0.0.2", help="IP (or interface) to serve on")
    parser.add_argument("--first-port", type=int, default=30501, help="UDP port of 1st service")
    parser.add_argument("--period", type=float, default=1.0, help="seconds between events")
    parser.add_argument("--definitions", type=Path, help="system_definition/someip folder")
    parser.add_argument("--advertised-ip", help="public IP to offer, when behind NAT")
    parser.add_argument("--no-multicast", action="store_true", help="unicast discovery only")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = load_config(
            args.config,
            local_ip=args.board_ip,
            advertised_ip=args.advertised_ip,
            definitions=args.definitions,
        )
        asyncio.run(simulate(config, args.first_port, args.period, not args.no_multicast))
    except (ConfigError, DefinitionError, OSError, SomeIpError) as error:
        sys.exit(f"error: {error}")
    except KeyboardInterrupt:
        pass


async def simulate(
    config: BridgeConfig,
    first_port: int = 30501,
    period: float = 1.0,
    multicast: bool = True,
) -> None:
    from someipy import TransportLayerProtocol, construct_server_service_instance
    from someipy.service import Method as SomeIpMethod

    quiet_someipy()
    net = config.network
    codec = config.codec()
    sd = await open_service_discovery(net, multicast=multicast)
    offer_to_finders(sd)
    servers = []
    try:
        for port, service in enumerate(config.services, start=first_port):
            definition = service.definition
            methods = [
                SomeIpMethod(id=m.id, method_handler=_method_handler(definition.name, m, codec))
                for m in definition.methods.values()
            ]
            event_ids = [event.id for event in definition.events.values()]
            server = await construct_server_service_instance(
                someip_service(definition, service.event_group_id, event_ids, methods),
                service.instance_id,
                (ipaddress.IPv4Address(net.local_ip), port),
                SUBSCRIPTION_TTL_S,
                sd,
                cyclic_offer_delay_ms=1000,
                protocol=TransportLayerProtocol.UDP,
            )
            if net.advertised_ip:
                server._endpoint = (ipaddress.IPv4Address(net.advertised_ip), port)
            sd.attach(server)
            server.start_offer()
            servers.append((service, server))
            _LOG.info(
                "Board offers %s at %s:%d", definition.name, net.advertised_ip or net.local_ip, port
            )

        tick = 0
        while True:
            for service, server in servers:
                for event in service.definition.events.values():
                    value = fake_value(event.type, tick, codec)
                    server.send_event(
                        service.event_group_id, event.id, codec.encode(event.type, value)
                    )
            tick += 1
            await asyncio.sleep(period)
    finally:
        for _, server in servers:
            await server.stop_offer()
        sd.close()


def offer_to_finders(sd: Any) -> None:
    from someipy._internal.someip_sd_header import SdEntryType, SomeIpSdHeader

    finders: dict[str, float] = {}
    receive, send_multicast = sd.datagram_received, sd.send_multicast

    def received(data: bytes, addr: tuple[str, int]) -> None:
        try:
            entries = SomeIpSdHeader.from_buffer(data).service_entries
        except Exception:
            entries = []
        if any(entry.sd_entry.type == SdEntryType.FIND_SERVICE for entry in entries):
            if addr[0] not in finders:
                _LOG.info("Unicast SD peer %s", addr[0])
            finders[addr[0]] = time.monotonic()
        receive(data, (addr[0], sd.sd_port))

    def send_offer(buffer: bytes) -> None:
        if sd.mcast_transport is not None:
            send_multicast(buffer)
        for ip, seen in finders.items():
            if time.monotonic() - seen < PEER_TIMEOUT_S:
                sd.unicast_transport.sendto(buffer, (ip, sd.sd_port))

    sd.datagram_received = received
    sd.send_multicast = send_offer


def fake_value(type_name: str, tick: int, codec: Codec) -> Any:
    fields = codec.fields(type_name)
    if fields is not None:
        return {name: fake_value(t, tick + i, codec) for i, (name, t) in enumerate(fields.items())}
    if type_name == "void":
        return None
    if type_name == "bool":
        return tick % 2 == 0
    if type_name in INTEGER_TYPES:
        span = 100 if type_name.endswith("8") else 5000
        return (tick * 37) % span
    return round(10 + 5 * math.sin(tick / 5), 3)


def _method_handler(service: str, method: Method, codec: Codec) -> Any:
    from someipy import MethodResult, ReturnCode

    async def handle(payload: bytes, address: tuple[str, int]) -> MethodResult:
        result = MethodResult()
        try:
            argument = codec.decode(method.in_type, payload)
        except CodecError as error:
            _LOG.warning("Board: bad %s.%s request: %s", service, method.name, error)
            result.return_code = ReturnCode.E_MALFORMED_MESSAGE
            return result
        _LOG.info("Board: %s.%s(%r) called from %s", service, method.name, argument, address[0])
        reply = True if method.out_type == "bool" else fake_value(method.out_type, 0, codec)
        result.payload = codec.encode(method.out_type, reply)
        return result

    return handle


if __name__ == "__main__":
    main()
