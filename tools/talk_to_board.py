from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml

from someip_ros2_bridge import event_to_ros, load_config
from someip_ros2_bridge.codec import CodecError
from someip_ros2_bridge.config import BridgeConfig, ConfigError
from someip_ros2_bridge.definitions import DefinitionError
from someip_ros2_bridge.someip import SomeIpClient


def parse_call(text: str) -> tuple[str, Any]:
    name, _, value = text.partition("=")
    return name, yaml.safe_load(value) if value else None


async def talk(
    config: BridgeConfig,
    seconds: float = 3.0,
    timeout: float = 10.0,
    calls: list[tuple[str, Any]] | None = None,
) -> list[str]:
    lines: list[str] = []

    def show(line: str) -> None:
        print(line)
        lines.append(line)

    routes = {route.method.name: route for route in config.method_routes}
    for name, value in calls or []:
        route = routes.get(name)
        if route is None:
            raise SystemExit(
                f"{name} is not in the config (methods: {', '.join(routes) or 'none'})"
            )
        if route.allowed and value not in route.allowed:
            raise SystemExit(f"{name}={value!r} is not allowed: {route.allowed}")
        try:
            config.codec().encode(route.method.in_type, value)
        except CodecError as error:
            raise SystemExit(f"{name}: {error}") from error

    client = SomeIpClient(config)

    def on_event(route: Any, value: Any) -> None:
        name = f"{route.service}.{route.event.name}"
        show(f"event  {name} -> {route.topic} {event_to_ros(route, value)}")

    await client.start(on_event)
    try:
        missing = [service.definition.name for service in config.services]
        for _ in range(int(timeout / 0.1)):
            for name in [name for name in missing if client.board_address(name)]:
                show(f"found  {name} at {client.board_address(name)}")
                missing.remove(name)
            if not missing:
                break
            await asyncio.sleep(0.1)
        else:
            raise SystemExit(f"Not found within {timeout:.0f} s: {', '.join(missing)}")

        await asyncio.sleep(seconds)

        for name, value in calls or []:
            reply = await client.call(routes[name], value)
            show(f"method {name}({'' if value is None else value}) -> {reply!r}")
    finally:
        await client.close()
    return lines


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find the board and print its events, no ROS.")
    parser.add_argument("--config", default=Path(__file__).parents[1] / "config/bridge.yaml")
    parser.add_argument("--local-ip", help="this computer's IP")
    parser.add_argument("--board-ip", help="board on another network")
    parser.add_argument("--seconds", type=float, default=3.0, help="how long to print events")
    parser.add_argument(
        "--call",
        action="append",
        type=parse_call,
        metavar="METHOD[=VALUE]",
        help="then call a configured method with this SOME/IP value, no scale (repeatable)",
    )
    args = parser.parse_args()
    try:
        config = load_config(args.config, local_ip=args.local_ip, board_ip=args.board_ip)
        asyncio.run(talk(config, args.seconds, calls=args.call))
    except (ConfigError, DefinitionError, RuntimeError) as error:
        sys.exit(f"error: {error}")
    except KeyboardInterrupt:
        pass
