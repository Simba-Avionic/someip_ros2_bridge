import asyncio

import pytest

from someip_ros2_bridge.config import load_config

from conftest import ROOT

EXAMPLES = ROOT / "examples"


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.yaml")), ids=lambda p: p.name)
def test_example_configs_load(path):
    config = load_config(path, local_ip="127.0.0.1")
    assert config.services


TALK_CONFIG = """
network: {{local_ip: {ip}, sd_port: 30790}}
services:
  FcRadioService:
    instance_id: 1
    client_port: 10619
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
  PrimerService:
    instance_id: 1
    client_port: 10620
    methods:
      StartPrime: {{topic: primer/start, type: std_msgs/msg/Empty}}
"""


async def test_talk_to_board_against_the_simulator(write_config, config):
    bridge_config = config
    pytest.importorskip("someipy")
    from fake_board import simulate

    talk = __import__("talk_to_board").talk
    board = load_config(write_config(TALK_CONFIG.format(ip="127.0.0.2")))
    board_task = asyncio.create_task(simulate(board, first_port=30801, period=0.05))
    try:
        config = load_config(write_config(TALK_CONFIG.format(ip="127.0.0.1")))
        read_only = await talk(config, seconds=0.3)
        calls = [("SetGSEOxidizerFillValve", 1), ("StartPrime", None)]
        lines = await talk(config, seconds=0.3, calls=calls)
    finally:
        board_task.cancel()
        await asyncio.gather(board_task, return_exceptions=True)
    assert not any(line.startswith("method") for line in read_only)
    assert "found  FcRadioService at 127.0.0.2:30801" in lines
    assert any(line.startswith("found  PrimerService at 127.0.0.2:") for line in lines)
    event = "event  FcRadioService.GSEOxidizerTankPressEvent -> tanking/sensors"
    assert any(line.startswith(event) for line in lines)
    assert lines[-2:] == [
        "method SetGSEOxidizerFillValve(1) -> True",
        "method StartPrime() -> True",
    ]
    for checked, calls, error in (
        (config, [("Nope", 1)], "Nope is not in the config"),
        (config, [("SetGSEOxidizerFillValve", 300)], "Cannot encode 300"),
        (bridge_config, [("SetGSEOxidizerFillValve", 7)], "is not allowed"),
    ):
        with pytest.raises(SystemExit, match=error):
            await talk(checked, calls=calls)
