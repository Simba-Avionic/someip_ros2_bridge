from __future__ import annotations

import ipaddress
import socket
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .codec import Codec, CodecError
from .definitions import Definitions, Event, Method, Service, load_definitions


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Network:
    local_ip: str
    sd_multicast_ip: str = "224.224.224.245"
    sd_port: int = 30490
    payload_byte_order: str = "little"
    board_ip: str | None = None
    advertised_ip: str | None = None


@dataclass(frozen=True)
class FieldMap:
    ros_field: str
    someip_field: str | None
    scale: float = 1
    offset: float = 0


@dataclass(frozen=True)
class EventRoute:
    service: str
    event: Event
    topic: str
    ros_type: str
    frame_id: str
    fields: tuple[FieldMap, ...]


@dataclass(frozen=True)
class MethodRoute:
    service: str
    method: Method
    topic: str
    ros_type: str
    field: str | None
    allowed: dict[Any, str] | None
    scale: float = 1
    offset: float = 0
    only_on_change: bool = True


@dataclass(frozen=True)
class ServiceConfig:
    definition: Service
    instance_id: int
    client_port: int
    event_group_id: int
    events: tuple[EventRoute, ...]
    methods: tuple[MethodRoute, ...]


@dataclass(frozen=True)
class BridgeConfig:
    network: Network
    services: tuple[ServiceConfig, ...]
    definitions: Definitions

    def codec(self) -> Codec:
        return Codec(self.definitions.structs, self.network.payload_byte_order)

    @property
    def event_routes(self) -> list[EventRoute]:
        return [route for service in self.services for route in service.events]

    @property
    def method_routes(self) -> list[MethodRoute]:
        return [route for service in self.services for route in service.methods]


def load_config(
    path: Path | str,
    *,
    local_ip: str | None = None,
    board_ip: str | None = None,
    advertised_ip: str | None = None,
    definitions: Path | str | None = None,
) -> BridgeConfig:
    path = Path(path).resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"Cannot read config {path}: {error}") from error
    raw = _keys(raw, "config", required={"definitions", "network", "services"})

    defs = load_definitions(definitions or path.parent / _text(raw["definitions"], "definitions"))
    overrides = {"local_ip": local_ip, "board_ip": board_ip, "advertised_ip": advertised_ip}
    network = _network(raw["network"], overrides)
    codec = Codec(defs.structs)
    services = tuple(
        _service(name, body, defs, codec)
        for name, body in _mapping(raw["services"], "services").items()
    )

    ports = [service.client_port for service in services] + [network.sd_port]
    if len(ports) != len(set(ports)):
        raise ConfigError(f"Every service needs its own client_port (and not sd_port): {ports}")
    config = BridgeConfig(network=network, services=services, definitions=defs)
    _check_topic_types(config)
    return config


def resolve_ip(value: str, where: str = "local_ip") -> str:
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError:
        pass
    try:
        import fcntl

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            SIOCGIFADDR = 0x8915
            info = fcntl.ioctl(sock.fileno(), SIOCGIFADDR, struct.pack("256s", value[:15].encode()))
        return socket.inet_ntoa(info[20:24])
    except (ImportError, OSError):
        pass
    try:
        return socket.gethostbyname(value)
    except OSError as error:
        raise ConfigError(
            f"{where} {value!r} is neither an IPv4 address, a network interface with an IPv4 "
            f"address nor a known host ({error})."
        ) from error


def _network(raw: Any, overrides: dict[str, str | None]) -> Network:
    net = _keys(
        raw,
        "network",
        required={"local_ip"},
        optional={
            "sd_multicast_ip",
            "sd_port",
            "payload_byte_order",
            "board_ip",
            "advertised_ip",
        },
    ) | {key: value for key, value in overrides.items() if value}
    multicast = str(net.get("sd_multicast_ip", Network.sd_multicast_ip))
    try:
        is_multicast = ipaddress.IPv4Address(multicast).is_multicast
    except ValueError:
        is_multicast = False
    if not is_multicast:
        raise ConfigError(f"network.sd_multicast_ip {multicast!r} is not a multicast address")
    byte_order = net.get("payload_byte_order", Network.payload_byte_order)
    if byte_order not in ("little", "big"):
        raise ConfigError("network.payload_byte_order must be 'little' or 'big'")
    board_ip, advertised_ip = net.get("board_ip"), net.get("advertised_ip")
    return Network(
        local_ip=resolve_ip(str(net["local_ip"])),
        sd_multicast_ip=multicast,
        sd_port=_int(net.get("sd_port", Network.sd_port), "network.sd_port", 1, 65535),
        payload_byte_order=byte_order,
        board_ip=board_ip and resolve_ip(str(board_ip), "network.board_ip"),
        advertised_ip=advertised_ip and resolve_ip(str(advertised_ip), "network.advertised_ip"),
    )


def _service(name: str, raw: Any, defs: Definitions, codec: Codec) -> ServiceConfig:
    where = f"services.{name}"
    body = _keys(
        raw,
        where,
        required={"instance_id", "client_port"},
        optional={"event_group_id", "events", "methods"},
    )
    service = defs.services.get(name)
    if service is None:
        raise ConfigError(
            f"{where}: no such service in system_definition ({_names(defs.services)})"
        )
    default_group = min((event.id for event in service.events.values()), default=0)
    return ServiceConfig(
        definition=service,
        instance_id=_int(body["instance_id"], f"{where}.instance_id", 0, 0xFFFF),
        client_port=_int(body["client_port"], f"{where}.client_port", 1, 65535),
        event_group_id=_int(
            body.get("event_group_id", default_group), f"{where}.event_group_id", 0, 0xFFFF
        ),
        events=tuple(
            _event_route(service, event, route, codec)
            for event, route in _mapping(body.get("events"), f"{where}.events").items()
        ),
        methods=tuple(
            _method_route(service, method, route, codec)
            for method, route in _mapping(body.get("methods"), f"{where}.methods").items()
        ),
    )


def _event_route(service: Service, name: str, raw: Any, codec: Codec) -> EventRoute:
    where = f"services.{service.name}.events.{name}"
    body = _keys(raw, where, required={"topic", "type", "fields"}, optional={"frame_id"})
    event = service.events.get(name)
    if event is None:
        raise ConfigError(f"{where}: {service.name} has no such event ({_names(service.events)})")
    struct_fields = _payload_fields(codec, event.type, where)

    fields = []
    for ros_field, spec in _mapping(body["fields"], f"{where}.fields").items():
        at = f"{where}.fields.{ros_field}"
        spec = _keys(
            {"from": spec} if isinstance(spec, str) else spec,
            at,
            optional={"from", "scale", "offset"},
        )
        source = spec.get("from")
        if struct_fields is None and source is not None:
            raise ConfigError(f"{at}: the payload is a plain {event.type}, remove 'from'")
        if struct_fields is not None and source not in struct_fields:
            raise ConfigError(f"{at}: 'from' must be one of {', '.join(struct_fields)}")
        fields.append(
            FieldMap(
                ros_field=ros_field,
                someip_field=source,
                scale=_number(spec.get("scale", 1), f"{at}.scale"),
                offset=_number(spec.get("offset", 0), f"{at}.offset"),
            )
        )
    if not fields:
        raise ConfigError(f"{where}.fields: map at least one ROS field")

    return EventRoute(
        service=service.name,
        event=event,
        topic=_text(body["topic"], f"{where}.topic"),
        ros_type=_ros_type(body["type"], f"{where}.type"),
        frame_id=_text(body.get("frame_id", f"someip/{service.name}/{name}"), f"{where}.frame_id"),
        fields=tuple(fields),
    )


def _method_route(service: Service, name: str, raw: Any, codec: Codec) -> MethodRoute:
    where = f"services.{service.name}.methods.{name}"
    body = _keys(
        raw,
        where,
        required={"topic", "type"},
        optional={"field", "allowed", "scale", "offset", "only_on_change"},
    )
    method = service.methods.get(name)
    if method is None:
        raise ConfigError(f"{where}: {service.name} has no such method ({_names(service.methods)})")
    _payload_fields(codec, method.out_type, where)
    if _payload_fields(codec, method.in_type, where) is not None:
        raise ConfigError(f"{where}: struct method arguments are not supported yet")
    if method.in_type != "void" and "field" not in body:
        raise ConfigError(f"{where}: 'field' is required, the method takes a {method.in_type}")

    allowed = body.get("allowed")
    if isinstance(allowed, list):
        allowed = {value: str(value) for value in allowed}
    if allowed is not None and (not isinstance(allowed, dict) or not allowed):
        raise ConfigError(f"{where}.allowed must be a non-empty list or mapping")

    field = body.get("field")
    return MethodRoute(
        service=service.name,
        method=method,
        topic=_text(body["topic"], f"{where}.topic"),
        ros_type=_ros_type(body["type"], f"{where}.type"),
        field=None if field is None else _text(field, f"{where}.field"),
        allowed=allowed,
        scale=_number(body.get("scale", 1), f"{where}.scale"),
        offset=_number(body.get("offset", 0), f"{where}.offset"),
        only_on_change=_bool(body.get("only_on_change", True), f"{where}.only_on_change")
        and field is not None,
    )


def _check_topic_types(config: BridgeConfig) -> None:
    seen: dict[str, str] = {}
    routes = [(r.topic, r.ros_type) for r in config.event_routes]
    routes += [(r.topic, r.ros_type) for r in config.method_routes]
    for topic, ros_type in routes:
        if seen.setdefault(topic, ros_type) != ros_type:
            raise ConfigError(f"Topic {topic} is used with two types: {seen[topic]}, {ros_type}")
    loops = {r.topic for r in config.event_routes} & {r.topic for r in config.method_routes}
    if loops:
        raise ConfigError(f"Topic {', '.join(sorted(loops))} is both published and commanded")


def _payload_fields(codec: Codec, type_name: str, where: str) -> dict[str, str] | None:
    try:
        return codec.fields(type_name)
    except CodecError as error:
        raise ConfigError(f"{where}: {error}") from error


def _ros_type(value: Any, where: str) -> str:
    parts = _text(value, where).split("/")
    if len(parts) == 2:
        parts.insert(1, "msg")
    if len(parts) != 3 or parts[1] != "msg" or not all(parts):
        raise ConfigError(f"{where} must look like 'package/msg/Type', got {value!r}")
    return "/".join(parts)


def _keys(
    value: Any, where: str, required: Iterable[str] = (), optional: Iterable[str] = ()
) -> dict[str, Any]:
    value = _mapping(value, where)
    required, optional = set(required), set(optional)
    unknown = value.keys() - required - optional
    if unknown:
        allowed = ", ".join(sorted(required | optional))
        raise ConfigError(f"{where}: unknown key {', '.join(sorted(unknown))} (allowed: {allowed})")
    missing = required - value.keys()
    if missing:
        raise ConfigError(f"{where}: missing {', '.join(sorted(missing))}")
    return value


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a mapping (`name: ...` lines), got {value!r}")
    return value


def _check(value: Any, ok: bool, where: str, expected: str) -> Any:
    if not ok:
        raise ConfigError(f"{where} must be {expected}, got {value!r}")
    return value


def _int(value: Any, where: str, low: int, high: int) -> int:
    ok = type(value) is int and low <= value <= high
    return _check(value, ok, where, f"a whole number {low}..{high}")


def _number(value: Any, where: str) -> float:
    return _check(value, type(value) in (int, float), where, "a number")


def _bool(value: Any, where: str) -> bool:
    return _check(value, type(value) is bool, where, "true or false")


def _text(value: Any, where: str) -> str:
    return _check(value, isinstance(value, str) and value != "", where, "a non-empty text")


def _names(items: dict[str, Any]) -> str:
    return "known: " + ", ".join(sorted(items)) if items else "none defined"
