import pytest

from someip_ros2_bridge.config import ConfigError, load_config

from conftest import DEFINITIONS
from make_config import generate, list_services, snake_case


def test_every_service_generates_a_valid_config(definitions, tmp_path):
    names = sorted(definitions.services)
    path = tmp_path / "all.yaml"
    path.write_text(generate(definitions, names, definitions_path=str(DEFINITIONS)))
    config = load_config(path)
    assert [s.definition.name for s in config.services] == names
    assert sorted({s.client_port for s in config.services}) == list(
        range(10319, 10319 + len(names))
    )


def test_plain_types_use_std_msgs_and_structs_are_templates(definitions, tmp_path):
    text = generate(definitions, ["FcRadioService", "PrimerService"], definitions_path="x")
    assert "# RadioStatusEvent: struct RadioDataType" in text
    assert "#     <ros_field>: rxerrors  # uint16" in text
    path = tmp_path / "c.yaml"
    path.write_text(text.replace("definitions: x", f"definitions: {DEFINITIONS}"))
    config = load_config(path)
    events = {r.event.name: r for r in config.event_routes}
    assert "RadioStatusEvent" not in events
    pressure = events["GSEOxidizerTankPressEvent"]
    assert (pressure.topic, pressure.ros_type) == (
        "someip/fc_radio_service/gse_oxidizer_tank_press_event",
        "std_msgs/msg/UInt16",
    )
    methods = {r.method.name: r for r in config.method_routes}
    assert (
        methods["SetGSEOxidizerFillValve"].ros_type,
        methods["SetGSEOxidizerFillValve"].field,
    ) == (
        "std_msgs/msg/UInt8",
        "data",
    )
    assert methods["StartPrime"].ros_type == "std_msgs/msg/Empty"
    assert methods["StartPrime"].field is None


def test_unknown_service_and_listing(definitions):
    with pytest.raises(ConfigError, match="Unknown service Nope"):
        generate(definitions, ["Nope"], definitions_path="x")
    assert "FcRadioService" in list_services(definitions)


@pytest.mark.parametrize(
    ("name", "snake"),
    [
        ("FcRadioService", "fc_radio_service"),
        ("newBME280Event", "new_bme280_event"),
        ("SetGSEPFSFillValve", "set_gsepfs_fill_valve"),
        ("newBoardTempEvent_1", "new_board_temp_event_1"),
    ],
)
def test_snake_case(name, snake):
    assert snake_case(name) == snake
