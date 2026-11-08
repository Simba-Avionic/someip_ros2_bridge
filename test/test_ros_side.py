from types import SimpleNamespace

import pytest

from someip_ros2_bridge.config import ConfigError
from someip_ros2_bridge.node import RosPublisher, describe, field_type, import_message, set_field

from conftest import LoadCells

pytestmark = pytest.mark.usefixtures("fake_msgs")


def test_import_and_field_types():
    assert import_message("fake_msgs/msg/LoadCells") is LoadCells
    assert field_type(LoadCells, "combined_raw_mass_kg", "t") == "float"
    assert field_type(LoadCells, "tenso_l.raw", "t") == "float"
    with pytest.raises(ConfigError, match="has no field 'mass'"):
        field_type(LoadCells, "mass", "t")
    with pytest.raises(ConfigError, match="source install/setup"):
        import_message("gs_interfaces_missing/msg/X")


def test_set_field_converts_to_the_ros_type():
    message = LoadCells()
    set_field(message, "combined_raw_mass_kg", 12, "float")
    set_field(message, "tenso_l.raw", 3, "float")
    set_field(message, "count", 2.6, "uint8")
    assert type(message.combined_raw_mass_kg) is float and message.tenso_l.raw == 3.0
    assert message.count == 3


def test_publisher_fills_fields_and_header(config):
    published = []
    publisher = SimpleNamespace(msg_type=LoadCells, publish=published.append)
    node = SimpleNamespace(
        create_publisher=lambda cls, topic, depth: publisher,
        get_clock=lambda: SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: 42)),
    )
    route = next(r for r in config.event_routes if r.event.name == "GSERocketMassEvent")
    route = type(route)(**{**route.__dict__, "ros_type": "fake_msgs/msg/LoadCells"})
    RosPublisher(node, [route]).publish(route, {"combined_raw_mass_kg": 5})
    (message,) = published
    assert message.combined_raw_mass_kg == 5.0 and type(message.combined_raw_mass_kg) is float
    assert (message.header.stamp, message.header.frame_id) == (42, "someip_gse_load_cells")

    fields = (type(route.fields[0])("header.frame_id", None),)
    text_route = type(route)(**{**route.__dict__, "fields": fields})
    with pytest.raises(ConfigError, match="only number and bool fields"):
        RosPublisher(node, [text_route])


def test_describe_lists_every_route(config):
    text = describe(config)
    assert "FcRadioService (id 545" in text and "UDP port 10319" in text
    assert "SetGSEPFSFillValve [uint8 -> bool] <- tanking/commands.valve_feed_pressurizer" in text
