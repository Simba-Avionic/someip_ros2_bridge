import asyncio
import inspect
import sys
import types
from pathlib import Path

import pytest

from someip_ros2_bridge.config import BridgeConfig, load_config
from someip_ros2_bridge.definitions import Definitions, load_definitions
from someip_ros2_bridge.node import import_message

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/bridge.yaml"
DEFINITIONS = ROOT.parent / "system_definition/someip"
sys.path.insert(0, str(ROOT / "tools"))


@pytest.fixture(scope="session")
def definitions() -> Definitions:
    return load_definitions(DEFINITIONS)


@pytest.fixture
def config() -> BridgeConfig:
    return load_config(CONFIG)


@pytest.fixture
def write_config(tmp_path):

    def write(text: str) -> Path:
        path = tmp_path / "bridge.yaml"
        path.write_text(f"definitions: {DEFINITIONS}\n{text}", encoding="utf-8")
        return path

    return write


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    arguments = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
    asyncio.run(pyfuncitem.obj(**arguments))
    return True


class FakeMessage:
    FIELDS: dict[str, str] = {}

    def __init__(self):
        for name, type_name in self.FIELDS.items():
            nested = "/" in type_name
            setattr(self, name, import_message(type_name)() if nested else 0)

    @classmethod
    def get_fields_and_field_types(cls):
        return dict(cls.FIELDS)


class Header(FakeMessage):
    FIELDS = {"stamp": "int32", "frame_id": "string"}


class LoadCell(FakeMessage):
    FIELDS = {"raw": "float"}


class LoadCells(FakeMessage):
    FIELDS = {
        "header": "fake_msgs/Header",
        "tenso_l": "fake_msgs/LoadCell",
        "combined_raw_mass_kg": "float",
        "count": "uint8",
    }


class TankingSensors(FakeMessage):
    FIELDS = {"header": "fake_msgs/Header", "pressure_bar": "float"}


class TankingCommands(FakeMessage):
    FIELDS = {"header": "fake_msgs/Header", "valve_feed_oxidizer": "uint8"}


@pytest.fixture
def fake_msgs(monkeypatch):
    module = types.ModuleType("fake_msgs.msg")
    for cls in (Header, LoadCell, LoadCells, TankingSensors, TankingCommands):
        setattr(module, cls.__name__, cls)
    monkeypatch.setitem(sys.modules, "fake_msgs", types.ModuleType("fake_msgs"))
    monkeypatch.setitem(sys.modules, "fake_msgs.msg", module)
