import sys

import pytest

from someip_ros2_bridge.config import ConfigError, load_config, resolve_ip

NETWORK = "network:\n  local_ip: 127.0.0.1\n"


def test_shipped_config_resolves_names_to_ids(config):
    (service,) = config.services
    assert (service.definition.id, service.instance_id, service.client_port) == (545, 1, 10319)
    assert service.event_group_id == 32769
    events = {route.event.name: route for route in config.event_routes}
    pressure = events["GSEOxidizerTankPressEvent"]
    assert (pressure.event.id, pressure.event.type, pressure.topic) == (
        32770,
        "uint16",
        "tanking/sensors",
    )
    assert pressure.fields[0].someip_field is None and pressure.fields[0].scale == 0.01
    assert events["RadioStatusEvent"].fields[0].someip_field == "rxerrors"
    valve = config.method_routes[0]
    assert (valve.method.id, valve.field, valve.only_on_change) == (1, "valve_feed_oxidizer", True)
    assert valve.allowed == {0: "closed", 1: "open", 2: "half_open"}


def test_local_ip_accepts_interface_name_and_override(config, write_config):
    assert resolve_ip("10.0.0.7") == "10.0.0.7"
    path = write_config(NETWORK + "services: {}\n")
    assert load_config(path, local_ip="10.0.0.7").network.local_ip == "10.0.0.7"
    if sys.platform == "linux":
        assert resolve_ip("lo") == "127.0.0.1"
    with pytest.raises(ConfigError, match="neither an IPv4 address, a network interface"):
        resolve_ip("no_such_if0")


@pytest.mark.parametrize(
    ("services", "error"),
    [
        ("NoSuchService: {instance_id: 1, client_port: 1}", "no such service"),
        ("FcRadioService: {instance_id: 1, clientport: 1}", "unknown key clientport"),
        ("FcRadioService: {instance_id: 1, client_port: 70000}", "client_port must be .* 1..65535"),
        (
            "FcRadioService: {instance_id: one, client_port: 1}",
            "instance_id must be a whole number",
        ),
        ("[FcRadioService]", "services must be a mapping"),
        (
            "FcRadioService: {instance_id: 1, client_port: 1, events: [X]}",
            "events must be a mapping",
        ),
        (
            "FcRadioService: {instance_id: 1, client_port: 1, events: {GSERocketMassEvent: "
            "{topic: t, type: p/msg/T, fields: [m]}}}",
            "fields must be a mapping",
        ),
        (
            "FcRadioService: {instance_id: 1, client_port: 1, events: {GSERocketMassEvent: "
            "{topic: t, type: p/msg/T, fields: {m: {scale: '0.01'}}}}}",
            "scale must be a number",
        ),
        (
            "FcRadioService: {instance_id: 1, client_port: 1, events: {GSERocketMassEvent: "
            "{topic: 5, type: p/msg/T, fields: {m: {}}}}}",
            "topic must be a non-empty text",
        ),
        (
            "FcRadioService: {instance_id: 1, client_port: 1, methods: {SetGSEPFSFillValve: "
            "{topic: t, type: p/msg/T, field: f, only_on_change: 'no'}}}",
            "only_on_change must be true or false",
        ),
        ("FcRadioService: {instance_id: 1, client_port: 30490}", "own client_port"),
        (
            "FcRadioService: {instance_id: 1, client_port: 1, events: {Nope: "
            "{topic: t, type: p/msg/T, fields: {x: {}}}}}",
            "has no such event",
        ),
        (
            "FcRadioService: {instance_id: 1, client_port: 1, events: {RadioStatusEvent: "
            "{topic: t, type: p/msg/T, fields: {rssi: wrong}}}}",
            "'from' must be one of rxerrors",
        ),
        (
            "FcRadioService: {instance_id: 1, client_port: 1, events: {GSERocketMassEvent: "
            "{topic: t, type: p/msg/T, fields: {m: rssi}}}}",
            "remove 'from'",
        ),
        (
            "FcRadioService: {instance_id: 1, client_port: 1, methods: {SetGSEPFSFillValve: "
            "{topic: t, type: p/msg/T}}}",
            "'field' is required",
        ),
        (
            "FcRadioService: {instance_id: 1, client_port: 1, methods: {SetGSEPFSFillValve: "
            "{topic: t, type: TankingCommands, field: f}}}",
            "package/msg/Type",
        ),
        (
            "FcRadioService: {instance_id: 1, client_port: 1, events: {GSERocketMassEvent: "
            "{topic: t, type: p/msg/A, fields: {m: {}}}}, methods: {SetGSEPFSFillValve: "
            "{topic: t, type: p/msg/B, field: f}}}",
            "used with two types",
        ),
        (
            "FcRadioService: {instance_id: 1, client_port: 1, events: {GSERocketMassEvent: "
            "{topic: t, type: p/msg/A, fields: {m: {}}}}, methods: {SetGSEPFSFillValve: "
            "{topic: t, type: p/msg/A, field: f}}}",
            "both published and commanded",
        ),
    ],
)
def test_mistakes_give_clear_errors(write_config, services, error):
    with pytest.raises(ConfigError, match=error):
        load_config(write_config(f"{NETWORK}services:\n  {services}\n"))


def test_void_method_without_field_is_called_every_time(write_config):
    path = write_config(
        NETWORK + "services:\n  PrimerService:\n    instance_id: 1\n    client_port: 10400\n"
        "    methods: {StartPrime: {topic: arm, type: std_msgs/msg/Empty}}\n"
    )
    (route,) = load_config(path).method_routes
    assert route.method.in_type == "void" and route.field is None
    assert route.only_on_change is False


def test_unicast_settings(write_config):
    path = write_config(
        "network:\n  local_ip: 127.0.0.1\n  board_ip: localhost\n"
        "  advertised_ip: 203.0.113.7\nservices: {}\n"
    )
    net = load_config(path).network
    assert (net.board_ip, net.advertised_ip) == ("127.0.0.1", "203.0.113.7")
    assert load_config(path, board_ip="10.0.0.9").network.board_ip == "10.0.0.9"
    defaults = load_config(write_config(NETWORK + "services: {}\n")).network
    assert (defaults.board_ip, defaults.advertised_ip) == (None, None)
    bad = write_config(
        "network:\n  local_ip: 127.0.0.1\n  board_ip: no.such.host.invalid\nservices: {}\n"
    )
    with pytest.raises(ConfigError, match="network.board_ip 'no.such.host.invalid' is neither"):
        load_config(bad)
