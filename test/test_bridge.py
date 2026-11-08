import asyncio
import logging
from types import SimpleNamespace

import pytest

from someip_ros2_bridge.bridge import Bridge, TranslationError, event_to_ros, ros_to_argument
from someip_ros2_bridge.codec import CodecError
from someip_ros2_bridge.config import load_config
from someip_ros2_bridge.someip import BoardError, NotDelivered


def route(config, name):
    events = {r.event.name: r for r in config.event_routes}
    return {**events, **{r.method.name: r for r in config.method_routes}}[name]


def valve(value=0, pfs=0):
    return SimpleNamespace(valve_feed_oxidizer=value, valve_feed_pressurizer=pfs)


class FakeBoard:
    def __init__(self, *results):
        self.calls = []
        self.results = list(results)
        self.release = asyncio.Event()
        self.release.set()

    async def call(self, route, argument):
        self.calls.append((route.method.name, argument))
        await self.release.wait()
        result = self.results.pop(0) if self.results else True
        if isinstance(result, Exception):
            raise result
        return result


async def settle():
    for _ in range(10):
        await asyncio.sleep(0)


def test_scalar_event_is_scaled(config):
    assert event_to_ros(route(config, "GSEOxidizerTankPressEvent"), 1234) == {
        "pressure_bar": pytest.approx(12.34)
    }
    assert event_to_ros(route(config, "GSERocketMassEvent"), -250) == {
        "combined_raw_mass_kg": pytest.approx(-2.5)
    }


def test_struct_event_maps_fields_and_keeps_ints(config):
    payload = dict(rxerrors=1, fixed=2, rssi=3, remrssi=4, txbuf=5, noise=6, remnoise=7)
    fields = event_to_ros(route(config, "RadioStatusEvent"), payload)
    assert fields == payload and all(type(v) is int for v in fields.values())


def test_event_publish_errors_are_contained(config, caplog):
    def broken_publish(route, fields):
        raise AssertionError("bad ROS value")

    Bridge(broken_publish, FakeBoard().call).on_someip_event(route(config, "GSERocketMassEvent"), 1)
    assert "GSERocketMassEvent -> tanking/load_cells failed" in caplog.text


def test_command_conversion(config):
    oxidizer = route(config, "SetGSEOxidizerFillValve")
    assert ros_to_argument(oxidizer, valve(2)) == 2
    with pytest.raises(TranslationError, match="not allowed, expected 0 \\(closed\\)"):
        ros_to_argument(oxidizer, valve(7))
    with pytest.raises(TranslationError, match="no field"):
        ros_to_argument(oxidizer, SimpleNamespace())


def test_scaled_float_is_rounded_for_integer_argument(config, write_config):
    path = write_config(
        "network: {local_ip: 127.0.0.1}\nservices:\n  FcRadioService:\n"
        "    instance_id: 1\n    client_port: 10400\n    methods:\n      SetGSEPFSFillValve:\n"
        "        {topic: t, type: p/msg/T, field: bar, scale: 100}\n"
    )
    (r,) = load_config(path).method_routes
    assert ros_to_argument(r, {"bar": 1.234}) == 123
    with pytest.raises(TranslationError, match="finite"):
        ros_to_argument(r, {"bar": float("nan")})


def button_route(write_config, extra=""):
    path = write_config(
        "network: {local_ip: 127.0.0.1}\nservices:\n  PrimerService:\n"
        "    instance_id: 1\n    client_port: 10400\n"
        f"    methods: {{StartPrime: {{topic: t, type: p/msg/T, field: pressed{extra}}}}}\n"
    )
    return load_config(path).method_routes[0]


async def send(bridge, route, *messages):
    for message in messages:
        bridge.on_ros_message(route, message)
        await settle()


def arguments(board):
    return [argument for _, argument in board.calls]


async def test_only_a_moving_switch_fires(config, caplog):
    caplog.set_level(logging.INFO)
    board = FakeBoard()
    bridge = Bridge(lambda *_: None, board.call)
    oxidizer = route(config, "SetGSEOxidizerFillValve")
    await send(bridge, oxidizer, valve(1), valve(1), valve(1))
    assert board.calls == []
    assert "SetGSEOxidizerFillValve: panel says 1 = open, not sent until the switch moves" in (
        caplog.text
    )
    await send(bridge, oxidizer, valve(0), valve(0), valve(2), valve(2))
    assert arguments(board) == [0, 2]
    assert "SetGSEOxidizerFillValve(2 = half_open) -> True" in caplog.text


async def test_snapshot_fires_only_the_changed_valve(config):
    board = FakeBoard()
    bridge = Bridge(lambda *_: None, board.call)
    oxidizer, pfs = config.method_routes
    for message in (valve(0, 0), valve(1, 0), valve(1, 0)):
        await send(bridge, oxidizer, message)
        await send(bridge, pfs, message)
    assert board.calls == [("SetGSEOxidizerFillValve", 1)]


@pytest.mark.parametrize(
    ("failure", "logged"),
    [
        (NotDelivered("NO REPLY within 10 s"), "NO REPLY within 10 s. Not resent"),
        (BoardError("board replied ERROR/E_NOT_OK"), "failed: board replied ERROR"),
        (CodecError("x"), "failed: x"),
        (OSError("boom"), "failed unexpectedly, board state unknown"),
        (False, "rejected by the board (replied false)"),
    ],
)
async def test_every_failure_is_logged_and_nothing_is_resent(config, caplog, failure, logged):
    board = FakeBoard(failure)
    bridge = Bridge(lambda *_: None, board.call)
    oxidizer = route(config, "SetGSEOxidizerFillValve")
    await send(bridge, oxidizer, valve(0), valve(1), valve(1), valve(1))
    assert arguments(board) == [1]
    assert logged in caplog.text


async def test_quick_toggles_are_sent_in_order_without_waiting(config):
    board = FakeBoard()
    board.release.clear()
    bridge = Bridge(lambda *_: None, board.call)
    await send(bridge, route(config, "SetGSEOxidizerFillValve"), *map(valve, (0, 1, 0, 1)))
    assert arguments(board) == [1, 0, 1]
    board.release.set()
    await settle()


async def test_board_found_only_warns(config, caplog):
    board = FakeBoard()
    bridge = Bridge(lambda *_: None, board.call)
    oxidizer = route(config, "SetGSEOxidizerFillValve")
    await send(bridge, oxidizer, valve(0), valve(1))
    bridge.board_found("FcRadioService")
    await send(bridge, oxidizer, valve(1), valve(1))
    assert arguments(board) == [1]
    assert (
        "FcRadioService found: SetGSEOxidizerFillValve state unknown, panel says 1 = open;"
        " move the switch to send it" in caplog.text
    )


async def test_button_fires_when_pressed(write_config):
    board = FakeBoard()
    bridge = Bridge(lambda *_: None, board.call)
    r = button_route(write_config)
    pressed = [{"pressed": p} for p in (True, False, True, True, False, False, True)]
    await send(bridge, r, *pressed)
    assert board.calls == [("StartPrime", None)] * 2


async def test_without_only_on_change_every_message_fires(write_config):
    board = FakeBoard()
    bridge = Bridge(lambda *_: None, board.call)
    r = button_route(write_config, ", only_on_change: false")
    await send(bridge, r, *[{"pressed": p} for p in (True, True, False, True)])
    assert len(board.calls) == 3


async def test_close_cancels_running_calls_and_refuses_new_ones(config, caplog):
    board = FakeBoard()
    board.release.clear()
    bridge = Bridge(lambda *_: None, board.call)
    oxidizer = route(config, "SetGSEOxidizerFillValve")
    await send(bridge, oxidizer, valve(0), valve(1))
    await asyncio.wait_for(bridge.close(), 1)
    await send(bridge, oxidizer, valve(2))
    assert arguments(board) == [1]
    assert "(1 = open) cancelled by shutdown, board state unknown" in caplog.text
    assert "(2 = half_open) not sent: shutting down" in caplog.text


async def test_a_call_cancelled_before_it_started_is_logged(config, caplog):
    board = FakeBoard()
    bridge = Bridge(lambda *_: None, board.call)
    oxidizer = route(config, "SetGSEOxidizerFillValve")
    await send(bridge, oxidizer, valve(0))
    bridge.on_ros_message(oxidizer, valve(1))
    await bridge.close()
    assert board.calls == []
    assert "(1 = open) cancelled by shutdown" in caplog.text
