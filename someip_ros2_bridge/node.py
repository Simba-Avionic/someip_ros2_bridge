from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import sys
import threading
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .bridge import Bridge
from .config import BridgeConfig, ConfigError, EventRoute, MethodRoute, load_config
from .definitions import DefinitionError
from .someip import SomeIpClient

_LOG = logging.getLogger("someip_ros2_bridge")
_ROS_INTEGERS = {f"{sign}int{bits}" for sign in ("", "u") for bits in (8, 16, 32, 64)}
_ROS_NUMBERS = _ROS_INTEGERS | {"float", "double", "boolean"}


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    ros_args = argv[argv.index("--ros-args") :] if "--ros-args" in argv else []
    args = _parser().parse_args(argv[: len(argv) - len(ros_args)])
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = load_config(
            args.config,
            local_ip=args.local_ip,
            board_ip=args.board_ip,
            advertised_ip=args.advertised_ip,
            definitions=args.definitions,
        )
        if args.check:
            print(describe(config))
            return
        asyncio.run(run(config, ros_args))
    except (ConfigError, DefinitionError, RuntimeError) as error:
        sys.exit(f"error: {error}")
    except KeyboardInterrupt:
        pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="someip-ros2-bridge",
        description="Translate between a board's SOME/IP services and ROS 2 topics.",
    )
    parser.add_argument("--config", type=Path, default=default_config_path(), help="bridge YAML")
    parser.add_argument("--local-ip", help="network.local_ip: this PC's IP or interface (eth0)")
    parser.add_argument("--board-ip", help="network.board_ip: board on another network")
    parser.add_argument("--advertised-ip", help="network.advertised_ip: public IP behind NAT")
    parser.add_argument("--definitions", type=Path, help="system_definition/someip folder")
    parser.add_argument("--check", action="store_true", help="validate, print routes, exit")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    return parser


def default_config_path() -> Path:
    try:
        from ament_index_python.packages import get_package_share_directory

        return Path(get_package_share_directory("someip_ros2_bridge")) / "config/bridge.yaml"
    except (ImportError, LookupError):
        return Path(__file__).resolve().parents[1] / "config/bridge.yaml"


def describe(config: BridgeConfig) -> str:
    net = config.network
    lines = [
        f"local ip {net.local_ip}, service discovery {net.sd_multicast_ip}:{net.sd_port}",
    ]
    if net.board_ip:
        lines.append(f"unicast discovery: board {net.board_ip}, SOME/IP accepted only from there")
    if net.advertised_ip:
        lines.append(f"board sends events to {net.advertised_ip} (forward the client ports here)")
    for service in config.services:
        d = service.definition
        lines.append(
            f"\n{d.name} (id {d.id}, v{d.major_version}.{d.minor_version}, instance "
            f"{service.instance_id}) <- UDP port {service.client_port}"
        )
        for event in service.events:
            fields = ", ".join(f.ros_field for f in event.fields)
            lines.append(
                f"  event  {event.event.name} [{event.event.type}] -> {event.topic} ({fields})"
            )
        for method in service.methods:
            source = f"{method.topic}.{method.field}" if method.field else method.topic
            lines.append(
                f"  method {method.method.name} [{method.method.in_type} -> "
                f"{method.method.out_type}] <- {source}"
            )
    return "\n".join(lines)


async def run(config: BridgeConfig, ros_args: list[str] | None = None) -> None:
    try:
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
    except ImportError as error:
        raise RuntimeError(
            "ROS 2 not found: source your ROS 2 setup and the workspace's install/setup first"
        ) from error

    rclpy.init(args=["someip_ros2_bridge", *(ros_args or [])])
    node = Node("someip_ros2_bridge")
    executor = SingleThreadedExecutor()
    someip = SomeIpClient(config)
    try:
        bridge = Bridge(publish=RosPublisher(node, config.event_routes).publish, call=someip.call)
        _subscribe(node, config.method_routes, asyncio.get_running_loop(), bridge)
        try:
            await someip.start(on_event=bridge.on_someip_event)
            executor.add_node(node)
            spin = threading.Thread(target=executor.spin, daemon=True)
            spin.start()
            _LOG.info("Bridge running (Ctrl+C to stop)")
            await _watch_boards(config, someip, bridge, lambda: rclpy.ok() and spin.is_alive())
            if rclpy.ok():
                raise RuntimeError("the ROS executor stopped; commands are no longer received")
        finally:
            executor.shutdown()
            await bridge.close()
            await someip.close()
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


async def _watch_boards(
    config: BridgeConfig, someip: SomeIpClient, bridge: Bridge, running: Callable[[], bool]
) -> None:
    found: dict[str, bool] = {}
    while running():
        for service in config.services:
            name = service.definition.name
            address = someip.board_address(name)
            if found.get(name, False) != (address is not None):
                if address:
                    _LOG.info("%s found at %s", name, address)
                    bridge.board_found(name)
                else:
                    _LOG.warning("%s lost - check cable/wifi, IPs and ports", name)
                found[name] = address is not None
        await asyncio.sleep(1.0)


class RosPublisher:
    def __init__(self, node: Any, routes: list[EventRoute]):
        self._node = node
        self._publishers: dict[str, Any] = {}
        self._field_types: dict[tuple[str, str], dict[str, str]] = {}
        for route in routes:
            message_class = import_message(route.ros_type)
            types = {
                f.ros_field: field_type(message_class, f.ros_field, route.topic)
                for f in route.fields
            }
            wrong = [
                f"{name}: {type_}" for name, type_ in types.items() if type_ not in _ROS_NUMBERS
            ]
            if wrong:
                raise ConfigError(
                    f"{route.topic}: only number and bool fields ({', '.join(wrong)})"
                )
            self._field_types[(route.service, route.event.name)] = types
            if route.topic not in self._publishers:
                self._publishers[route.topic] = node.create_publisher(
                    message_class, route.topic, 10
                )
            _LOG.info("%s.%s -> ROS %s", route.service, route.event.name, route.topic)

    def publish(self, route: EventRoute, fields: dict[str, Any]) -> None:
        publisher = self._publishers[route.topic]
        message = publisher.msg_type()
        types = self._field_types[(route.service, route.event.name)]
        for path, value in fields.items():
            set_field(message, path, value, types[path])
        if hasattr(message, "header"):
            message.header.stamp = self._node.get_clock().now().to_msg()
            message.header.frame_id = route.frame_id
        publisher.publish(message)


def _subscribe(
    node: Any, routes: list[MethodRoute], loop: asyncio.AbstractEventLoop, bridge: Bridge
) -> None:
    by_topic: dict[str, list[MethodRoute]] = defaultdict(list)
    for route in routes:
        by_topic[route.topic].append(route)
    for topic, topic_routes in by_topic.items():
        message_class = import_message(topic_routes[0].ros_type)
        for route in topic_routes:
            if route.field:
                field_type(message_class, route.field, topic)
            _LOG.info("ROS %s -> %s.%s", topic, route.service, route.method.name)

        def callback(message: Any, topic_routes: list[MethodRoute] = topic_routes) -> None:
            for route in topic_routes:
                loop.call_soon_threadsafe(bridge.on_ros_message, route, message)

        node.create_subscription(message_class, topic, callback, 10)


def import_message(ros_type: str) -> Any:
    package, *_, name = ros_type.split("/")
    try:
        return getattr(importlib.import_module(f"{package}.msg"), name)
    except (ImportError, AttributeError) as error:
        raise ConfigError(
            f"ROS message {ros_type} not found - build and source the workspace "
            "(source install/setup)"
        ) from error


def field_type(message_class: Any, path: str, topic: str) -> str:
    ros_type = ""
    for name in path.split("."):
        if ros_type:
            message_class = import_message(ros_type)
        types = message_class.get_fields_and_field_types()
        if name not in types:
            raise ConfigError(
                f"{topic}: {message_class.__name__} has no field {name!r} in {path!r} "
                f"(fields: {', '.join(types)})"
            )
        ros_type = str(types[name])
    return ros_type


def set_field(message: Any, path: str, value: Any, ros_type: str) -> None:
    *parents, name = path.split(".")
    for parent in parents:
        message = getattr(message, parent)
    if ros_type in ("float", "double"):
        value = float(value)
    elif ros_type == "boolean":
        value = bool(value)
    elif ros_type in _ROS_INTEGERS and isinstance(value, float):
        value = round(value)
    setattr(message, name, value)


if __name__ == "__main__":
    main()
