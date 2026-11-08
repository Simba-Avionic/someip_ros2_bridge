from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Callable, Iterable
from typing import Any

from .codec import CodecError
from .config import BridgeConfig, EventRoute, MethodRoute, Network, ServiceConfig
from .definitions import Service

_LOG = logging.getLogger(__name__)
SUBSCRIPTION_TTL_S = 5
FIND_PERIOD_S = 2.0


class SomeIpError(RuntimeError):
    pass


class NotDelivered(SomeIpError):
    pass


class BoardError(SomeIpError):
    pass


def quiet_someipy() -> None:
    from someipy.logging import set_someipy_log_level

    set_someipy_log_level(logging.DEBUG if _LOG.isEnabledFor(logging.DEBUG) else logging.WARNING)
    logging.getLogger("someipy").propagate = False


async def open_service_discovery(net: Network, *, multicast: bool = True) -> Any:
    from someipy._internal.utils import (
        DatagramAdapter,
        create_rcv_multicast_socket,
        create_udp_socket,
    )
    from someipy.service_discovery import ServiceDiscoveryProtocol

    loop = asyncio.get_running_loop()
    sd = ServiceDiscoveryProtocol(net.sd_multicast_ip, net.local_ip, net.sd_port)
    try:
        sd.unicast_transport, _ = await loop.create_datagram_endpoint(
            lambda: DatagramAdapter(target=sd), sock=create_udp_socket(net.local_ip, net.sd_port)
        )
        if multicast:
            sd.mcast_transport, _ = await loop.create_datagram_endpoint(
                lambda: DatagramAdapter(target=sd),
                sock=create_rcv_multicast_socket(net.sd_multicast_ip, net.sd_port, net.local_ip),
            )
            sock = sd.unicast_transport.get_extra_info("socket")
            sock.setsockopt(
                socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(net.local_ip)
            )
    except OSError as error:
        sd.close()
        group = f" (multicast {net.sd_multicast_ip})" if multicast else ""
        raise SomeIpError(
            f"Cannot open Service Discovery on {net.local_ip}:{net.sd_port}{group}: {error}"
        ) from error
    return sd


def accept_sd_only_from(sd: Any, board_ip: str) -> None:
    receive = sd.datagram_received
    warned: set[str] = set()

    def received(data: bytes, addr: tuple[str, int]) -> None:
        if addr[0] == board_ip:
            receive(data, (addr[0], sd.sd_port))
        else:
            _warn_once(warned, addr[0], "Ignoring SOME/IP SD from %s: not network.board_ip")

    sd.datagram_received = received


def find_service_message(sd: Any, service: Service, instance_id: int) -> bytes:
    from someipy._internal.someip_sd_builder import build_find_service_sd_header

    session_id, reboot_flag = sd.get_unicast_session_handler().update_session()
    header = build_find_service_sd_header(
        service.id, instance_id, service.major_version, 0xFFFFFFFF, session_id, reboot_flag
    )
    header.length_options = 0
    header.someip_header.length = len(header.to_buffer()) - 8
    return bytes(header.to_buffer())


def _warn_once(warned: set[str], ip: str, message: str, *args: Any) -> None:
    if ip not in warned:
        warned.add(ip)
        _LOG.warning(message, ip, *args)


def someip_service(
    definition: Service, event_group_id: int, event_ids: list[int], methods: Iterable[Any] = ()
) -> Any:
    from someipy import EventGroup, ServiceBuilder

    builder = (
        ServiceBuilder()
        .with_service_id(definition.id)
        .with_major_version(definition.major_version)
        .with_minor_version(definition.minor_version)
    )
    for method in methods:
        builder = builder.with_method(method)
    if event_ids:
        builder = builder.with_eventgroup(EventGroup(event_group_id, event_ids))
    return builder.build()


class SomeIpClient:
    def __init__(self, config: BridgeConfig):
        self._config = config
        self._codec = config.codec()
        self._instances: dict[str, Any] = {}
        self._sd: Any = None
        self._find_task: asyncio.Task[None] | None = None
        self._warned: set[str] = set()

    async def start(self, on_event: Callable[[EventRoute, Any], None]) -> None:
        try:
            from someipy import TransportLayerProtocol, construct_client_service_instance
        except ImportError as error:
            raise SomeIpError(
                "someipy is not installed: pip install -e <this repo> (README)"
            ) from error

        quiet_someipy()
        net = self._config.network
        self._sd = await open_service_discovery(net, multicast=not net.board_ip)
        if net.board_ip:
            accept_sd_only_from(self._sd, net.board_ip)

        for service in self._config.services:
            definition = service.definition
            event_ids = [route.event.id for route in service.events]
            try:
                instance = await construct_client_service_instance(
                    service=someip_service(definition, service.event_group_id, event_ids),
                    instance_id=service.instance_id,
                    endpoint=(ipaddress.IPv4Address(net.local_ip), service.client_port),
                    ttl=SUBSCRIPTION_TTL_S,
                    sd_sender=self._sd,
                    protocol=TransportLayerProtocol.UDP,
                )
            except OSError as error:
                raise SomeIpError(
                    f"{definition.name}: cannot open UDP {net.local_ip}:{service.client_port}: "
                    f"{error} (is another program using this client_port?)"
                ) from error
            if net.advertised_ip:
                instance._endpoint = (ipaddress.IPv4Address(net.advertised_ip), service.client_port)
            self._accept_only_from_board(definition.name, instance)
            if service.events:
                instance.register_callback(self._event_callback(service, on_event))
                instance.subscribe_eventgroup(service.event_group_id)
            self._sd.attach(instance)
            self._instances[definition.name] = instance
            address = f"{net.advertised_ip or net.local_ip}:{service.client_port}"
            _LOG.info(
                f"Looking for {definition.name} (service id {definition.id}, instance "
                f"{service.instance_id}) from {address}"
            )
        if net.board_ip:
            self._find_task = asyncio.get_running_loop().create_task(self._find_loop())
            _LOG.info("Unicast discovery: asking %s every %.0f s", net.board_ip, FIND_PERIOD_S)

    async def _find_loop(self) -> None:
        net = self._config.network
        assert net.board_ip is not None
        while True:
            for service in self._config.services:
                message = find_service_message(self._sd, service.definition, service.instance_id)
                self._sd.unicast_transport.sendto(message, (net.board_ip, net.sd_port))
            await asyncio.sleep(FIND_PERIOD_S)

    def _accept_only_from_board(self, name: str, instance: Any) -> None:
        board_ip = self._config.network.board_ip

        def receive(message: Any, addr: tuple[str, int]) -> None:
            if addr[0] == board_ip or addr[0] in (str(o.endpoint[0]) for o in _offers(instance)):
                instance.someip_message_received(message, addr)
            else:
                _warn_once(
                    self._warned,
                    addr[0],
                    "Ignoring SOME/IP from %s: not the %s board (or not found yet)",
                    name,
                )

        instance._someip_endpoint.set_someip_callback(receive)

    def board_address(self, service: str) -> str | None:
        instance = self._instances.get(service)
        offers = _offers(instance) if instance is not None and instance.service_found() else []
        return f"{offers[0].endpoint[0]}:{offers[0].endpoint[1]}" if offers else None

    async def call(self, route: MethodRoute, argument: Any) -> Any:
        instance = self._instances[route.service]
        if not instance.service_found():
            raise NotDelivered(f"NOT SENT: {route.service} is not found on the network")
        payload = self._codec.encode(route.method.in_type, argument)
        try:
            result = await instance.call_method(route.method.id, payload)
        except asyncio.TimeoutError as error:
            raise NotDelivered(
                "NO REPLY within 10 s: the board may or may not have done it"
            ) from error
        except RuntimeError as error:
            raise NotDelivered(f"NOT SENT: {error}") from error

        if result.message_type.name != "RESPONSE" or result.return_code.name != "E_OK":
            raise BoardError(f"board replied {result.message_type.name}/{result.return_code.name}")
        try:
            return self._codec.decode(route.method.out_type, result.payload)
        except CodecError as error:
            raise BoardError(f"unreadable reply: {error}") from error

    async def close(self) -> None:
        if self._find_task is not None:
            self._find_task.cancel()
            await asyncio.gather(self._find_task, return_exceptions=True)
            self._find_task = None
        for name, instance in self._instances.items():
            try:
                await instance.close()
                instance._someip_endpoint.shutdown()
            except Exception:
                _LOG.exception("Failed to close SOME/IP client for %s", name)
        self._instances.clear()
        if self._sd is not None:
            self._sd.close()
            self._sd = None

    def _event_callback(
        self, service: ServiceConfig, on_event: Callable[[EventRoute, Any], None]
    ) -> Callable[[Any], None]:
        routes = {route.event.id: route for route in service.events}

        def callback(message: Any) -> None:
            route = routes.get(message.header.method_id)
            if route is None:
                return
            try:
                value = self._codec.decode(route.event.type, message.payload)
            except CodecError as error:
                _LOG.warning("Malformed %s.%s event: %s", route.service, route.event.name, error)
                return
            on_event(route, value)

        return callback


def _offers(instance: Any) -> list[Any]:
    return [wrapper.value for wrapper in instance._offered_services.values]
