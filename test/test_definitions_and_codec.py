import pytest

from someip_ros2_bridge.codec import Codec, CodecError
from someip_ros2_bridge.definitions import DefinitionError, load_definitions


def test_radio_service_contract(definitions):
    service = definitions.services["FcRadioService"]
    assert (service.id, service.major_version, service.minor_version) == (545, 1, 0)
    valve = service.methods["SetGSEOxidizerFillValve"]
    assert (valve.id, valve.in_type, valve.out_type) == (1, "uint8", "bool")
    assert service.events["GSERocketMassEvent"].id == 32771
    assert service.events["GSERocketMassEvent"].type == "int16"


def test_void_inputs_and_qualified_struct_names(definitions):
    assert any(
        m.in_type == "void" for s in definitions.services.values() for m in s.methods.values()
    )
    event = definitions.services["FcSysStatService"].events
    assert all("." not in e.type for e in event.values())
    assert definitions.structs["RadioDataType"]["rxerrors"] == "uint16"


def test_missing_definitions_folder(tmp_path):
    with pytest.raises(DefinitionError, match="No SOME/IP definition"):
        load_definitions(tmp_path)


def test_malformed_definition_names_the_file(tmp_path):
    (tmp_path / "service.json").write_text('{"someip": {"X": {"methods": {}}}}')
    with pytest.raises(
        DefinitionError, match="service.json: missing or malformed entry 'service_id'"
    ):
        load_definitions(tmp_path)


def test_primitives_are_little_endian(definitions):
    codec = Codec(definitions.structs)
    assert codec.encode("uint16", 1234) == b"\xd2\x04"
    assert codec.decode("int16", b"\x2e\xfb") == -1234
    assert codec.decode("bool", b"\x01") is True
    assert codec.encode("void", None) == b"" and codec.decode("void", b"") is None
    assert Codec(definitions.structs, "big").encode("uint16", 1234) == b"\x04\xd2"


def test_struct_round_trip_keeps_definition_order(definitions):
    codec = Codec(definitions.structs)
    value = dict(rxerrors=1, fixed=2, rssi=3, remrssi=4, txbuf=5, noise=6, remnoise=7)
    payload = codec.encode("RadioDataType", value)
    assert payload == b"\x01\x00\x02\x00\x03\x04\x05\x06\x07"
    assert codec.decode("RadioDataType", payload) == value


@pytest.mark.parametrize(
    ("type_name", "value"), [("uint8", 256), ("uint8", -1), ("uint8", 1.5), ("int16", 40000)]
)
def test_encode_rejects_out_of_range(definitions, type_name, value):
    with pytest.raises(CodecError):
        Codec(definitions.structs).encode(type_name, value)


@pytest.mark.parametrize("payload", [b"", b"\x01"])
def test_decode_rejects_short_payloads(definitions, payload):
    with pytest.raises(CodecError, match="needs 2 bytes"):
        Codec(definitions.structs).decode("uint16", payload)


def test_decode_ignores_appended_bytes(definitions):
    assert Codec(definitions.structs).decode("uint16", b"\x01\x02\x03") == 0x0201


def test_unknown_type(definitions):
    with pytest.raises(CodecError, match="Unknown SOME/IP type"):
        Codec(definitions.structs).fields("NoSuchType")
