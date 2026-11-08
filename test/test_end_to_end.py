import asyncio
import logging

import pytest

from someip_ros2_bridge.bridge import Bridge
from someip_ros2_bridge.config import load_config
from someip_ros2_bridge.someip import NotDelivered, SomeIpClient

pytest.importorskip("someipy")

NETWORK = """
network:
  local_ip: {ip}
  sd_port: 30590
services:
  FcRadioService:
    instance_id: 1
    client_port: 10419
    events:
      GSEOxidizerTankPressEvent:
        topic: tanking/sensors
        type: gs_interfaces/msg/TankingSensors
        fields: {{pressure_bar: {{scale: 0.01}}}}
      RadioStatusEvent:
        topic: mavlink/radio_status
        type: gs_interfaces/msg/RadioStatus
        fields: {{rssi: rssi, noise: noise}}
    methods:
      SetGSEOxidizerFillValve:
        topic: tanking/commands
        type: gs_interfaces/msg/TankingCommands
        field: valve_feed_oxidizer
        allowed: [0, 1, 2]
"""


async def wait_for(condition, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


async def test_events_and_method_calls_over_the_network(write_config, caplog):
    from fake_board import simulate

    caplog.set_level(logging.INFO)
    board_config = load_config(write_config(NETWORK.format(ip="127.0.0.2")))
    config = load_config(write_config(NETWORK.format(ip="127.0.0.1")))
    published = []
    client = SomeIpClient(config)
    bridge = Bridge(lambda route, fields: published.append((route.topic, fields)), client.call)
    await client.start(on_event=bridge.on_someip_event)
    try:
        route = config.method_routes[0]
        with pytest.raises(NotDelivered, match="not found"):
            await client.call(route, 1)

        board = asyncio.create_task(simulate(board_config, first_port=30601, period=0.05))
        try:
            await wait_for(lambda: client.board_address("FcRadioService") == "127.0.0.2:30601")
            wanted = {"tanking/sensors", "mavlink/radio_status"}
            await wait_for(lambda: wanted <= {topic for topic, _ in published})
            pressure = next(f for t, f in published if t == "tanking/sensors")
            assert isinstance(pressure["pressure_bar"], float)
            assert set(next(f for t, f in published if t == "mavlink/radio_status")) == {
                "rssi",
                "noise",
            }

            assert await client.call(route, 2) is True
            assert "Board: FcRadioService.SetGSEOxidizerFillValve(2) called" in caplog.text

            bridge.on_ros_message(route, {"valve_feed_oxidizer": 0})
            bridge.on_ros_message(route, {"valve_feed_oxidizer": 1})
            await wait_for(lambda: "SetGSEOxidizerFillValve(1) -> True" in caplog.text)

            count = len(published)
            spoofed_event(545, 0x8002, b"\xff\xff", "127.0.0.3", ("127.0.0.1", 10419))
            await wait_for(lambda: "Ignoring SOME/IP from 127.0.0.3" in caplog.text)
            assert all(f.get("pressure_bar", 0) < 600 for _, f in published[count:])
        finally:
            board.cancel()
            await asyncio.gather(board, return_exceptions=True)
    finally:
        await bridge.close()
        await client.close()


UNICAST = """
network:
  local_ip: {ip}
  sd_port: 30890
  board_ip: 127.0.0.2
services:
  FcRadioService:
    instance_id: 1
    client_port: 10819
    events:
      GSEOxidizerTankPressEvent:
        topic: tanking/sensors
        type: gs_interfaces/msg/TankingSensors
        fields: {{pressure_bar: {{scale: 0.01}}}}
    methods:
      SetGSEOxidizerFillValve:
        topic: tanking/commands
        type: gs_interfaces/msg/TankingCommands
        field: valve_feed_oxidizer
"""


def spoofed_event(service_id, event_id, payload, source, target):
    import socket
    import struct

    header = struct.pack(">HHIHHBBBB", service_id, event_id, 8 + len(payload), 0, 1, 1, 1, 2, 0)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((source, 0))
        sock.sendto(header + payload, target)


async def test_unicast_discovery_without_multicast_and_spoofing_is_ignored(write_config, caplog):
    from fake_board import simulate

    caplog.set_level(logging.INFO)
    board_config = load_config(write_config(UNICAST.format(ip="127.0.0.2")))
    config = load_config(write_config(UNICAST.format(ip="127.0.0.1")))
    assert config.network.board_ip == "127.0.0.2"
    published = []
    client = SomeIpClient(config)
    bridge = Bridge(lambda route, fields: published.append(fields), client.call)
    board = asyncio.create_task(simulate(board_config, 30901, 0.05, multicast=False))
    await client.start(on_event=bridge.on_someip_event)
    try:
        await wait_for(lambda: client.board_address("FcRadioService") == "127.0.0.2:30901")
        assert "Unicast SD peer 127.0.0.1" in caplog.text
        await wait_for(lambda: len(published) > 2)
        assert await client.call(config.method_routes[0], 1) is True

        spoofed_event(545, 0x8002, b"\xff\xff", "127.0.0.3", ("127.0.0.1", 10819))
        await wait_for(lambda: "Ignoring SOME/IP from 127.0.0.3" in caplog.text)
        await asyncio.sleep(0.2)
        assert all(fields["pressure_bar"] < 600 for fields in published)
    finally:
        board.cancel()
        await asyncio.gather(board, return_exceptions=True)
        await bridge.close()
        await client.close()
