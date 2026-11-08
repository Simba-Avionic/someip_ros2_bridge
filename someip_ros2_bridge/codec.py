from __future__ import annotations

import struct
from typing import Any

PRIMITIVES = {
    "bool": "?",
    "uint8": "B",
    "uint16": "H",
    "uint32": "I",
    "uint64": "Q",
    "int8": "b",
    "int16": "h",
    "int32": "i",
    "int64": "q",
    "float32": "f",
    "float64": "d",
}
INTEGER_TYPES = frozenset(name for name, fmt in PRIMITIVES.items() if fmt in "BHIQbhiq")


class CodecError(ValueError):
    pass


class Codec:
    def __init__(self, structs: dict[str, dict[str, str]], byte_order: str = "little"):
        self._structs = structs
        self._prefix = "<" if byte_order == "little" else ">"

    def fields(self, type_name: str) -> dict[str, str] | None:
        return self._layout(type_name)[0]

    def encode(self, type_name: str, value: Any) -> bytes:
        fields, layout = self._layout(type_name)
        try:
            if type_name == "void":
                return b""
            if fields is None:
                return layout.pack(value)
            return layout.pack(*(value[name] for name in fields))
        except (struct.error, KeyError, TypeError) as error:
            raise CodecError(f"Cannot encode {value!r} as {type_name}: {error}") from error

    def decode(self, type_name: str, payload: bytes) -> Any:
        fields, layout = self._layout(type_name)
        try:
            values = layout.unpack_from(payload)
        except struct.error as error:
            raise CodecError(
                f"{type_name} needs {layout.size} bytes, got {len(payload)}: {payload.hex()}"
            ) from error
        if type_name == "void":
            return None
        if fields is None:
            return values[0]
        return dict(zip(fields, values, strict=True))

    def _layout(self, type_name: str) -> tuple[dict[str, str] | None, struct.Struct]:
        if type_name == "void":
            return None, struct.Struct("")
        if type_name in PRIMITIVES:
            return None, struct.Struct(self._prefix + PRIMITIVES[type_name])
        fields = self._structs.get(type_name)
        if fields is None:
            raise CodecError(f"Unknown SOME/IP type {type_name!r}")
        unsupported = [f"{name}: {t}" for name, t in fields.items() if t not in PRIMITIVES]
        if unsupported:
            raise CodecError(f"{type_name} has non-primitive fields: {', '.join(unsupported)}")
        return fields, struct.Struct(self._prefix + "".join(PRIMITIVES[t] for t in fields.values()))
