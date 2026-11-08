import asyncio
import logging
import sys
import threading
import types
from types import SimpleNamespace

import pytest

from someip_ros2_bridge.config import load_config
from someip_ros2_bridge.node import run

from conftest import TankingCommands

pytest.importorskip("someipy")

CONFIG = """
network:
  local_ip: {ip}
  sd_port: 30690
services:
  FcRadioService:
    instance_id: 1
    client_port: 10519
    events:
      GSEOxidizerTankPressEvent:
        topic: tanking/sensors
        type: fake_msgs/msg/TankingSensors
        fields: {{pressure_bar: {{scale: 0.01}}}}
    methods:
      SetGSEOxidizerFillValve:
        topic: tanking/commands
        type: fake_msgs/msg/TankingCommands
        field: valve_feed_oxidizer
"""


class FakeRclpy:
    def __init__(self):
        self.running = True
        self.node = None
        self.stopped = threading.Event()
        rclpy = types.ModuleType("rclpy")
        rclpy.init = lambda args: None
        rclpy.ok = lambda: self.running
        rclpy.try_shutdown = self.stopped.set
        node_module = types.ModuleType("rclpy.node")
        node_module.Node = self._node
        executors = types.ModuleType("rclpy.executors")
        executors.SingleThreadedExecutor = lambda: SimpleNamespace(
            add_node=lambda node: None, spin=self.stopped.wait, shutdown=lambda: None
        )
        self.modules = {"rclpy": rclpy, "rclpy.node": node_module, "rclpy.executors": executors}

    def _node(self, name):
        published, subscriptions = {}, {}

        def create_publisher(cls, topic, depth):
            return SimpleNamespace(msg_type=cls, publish=published.setdefault(topic, []).append)

        self.node = SimpleNamespace(
            published=published,
            subscriptions=subscriptions,
            create_publisher=create_publisher,
            create_subscription=lambda cls, topic, cb, depth: subscriptions.setdefault(topic, cb),
            get_clock=lambda: SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: 0)),
            destroy_node=lambda: None,
        )
        return self.node


async def wait_for(condition, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


async def test_run_bridges_both_directions(write_config, fake_msgs, monkeypatch, caplog):
    from fake_board import simulate

    caplog.set_level(logging.INFO)
    rclpy = FakeRclpy()
    for name, module in rclpy.modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    board = asyncio.create_task(
        simulate(load_config(write_config(CONFIG.format(ip="127.0.0.2"))), 30701, 0.05)
    )
    bridge = asyncio.create_task(run(load_config(write_config(CONFIG.format(ip="127.0.0.1")))))
    try:
        await wait_for(lambda: rclpy.node and rclpy.node.published.get("tanking/sensors"))
        message = rclpy.node.published["tanking/sensors"][-1]
        assert isinstance(message.pressure_bar, float)
        assert message.header.frame_id == "someip/FcRadioService/GSEOxidizerTankPressEvent"

        await wait_for(lambda: "FcRadioService found at 127.0.0.2:30701" in caplog.text)
        callback = rclpy.node.subscriptions["tanking/commands"]
        for value in (0, 1):
            command = TankingCommands()
            command.valve_feed_oxidizer = value
            thread = threading.Thread(target=callback, args=(command,))
            thread.start()
            thread.join()
        await wait_for(lambda: "SetGSEOxidizerFillValve(1) -> True" in caplog.text)
    finally:
        rclpy.running = False
        await asyncio.wait_for(bridge, 3)
        board.cancel()
        await asyncio.gather(board, return_exceptions=True)
    assert rclpy.stopped.is_set()
