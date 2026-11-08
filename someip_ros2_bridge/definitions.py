from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class Method:
    name: str
    id: int
    in_type: str
    out_type: str


@dataclass(frozen=True)
class Event:
    name: str
    id: int
    type: str


@dataclass(frozen=True)
class Service:
    name: str
    id: int
    major_version: int
    minor_version: int
    methods: dict[str, Method]
    events: dict[str, Event]


@dataclass(frozen=True)
class Definitions:
    services: dict[str, Service]
    structs: dict[str, dict[str, str]]


def short_type(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _payload_type(data_structure: dict[str, Any], direction: str) -> str:
    entry = data_structure.get(direction)
    return short_type(entry["type"]) if entry else "void"


def load_definitions(root: Path | str) -> Definitions:
    root = Path(root)
    files = sorted(root.rglob("*.json"))
    if not files:
        raise DefinitionError(f"No SOME/IP definition files found under {root}")

    services: dict[str, Service] = {}
    structs: dict[str, dict[str, str]] = {}
    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            for name, fields in document.get("data_structure", {}).items():
                fields = {field: short_type(type_) for field, type_ in fields.items()}
                if structs.setdefault(name, fields) != fields:
                    raise DefinitionError(f"{path}: struct {name} is defined differently elsewhere")
            for name, body in document.get("someip", {}).items():
                if name in services:
                    raise DefinitionError(f"{path}: service {name} is already defined elsewhere")
                services[name] = _service(name, body)
        except (OSError, json.JSONDecodeError) as error:
            raise DefinitionError(f"Cannot read {path}: {error}") from error
        except (KeyError, TypeError, AttributeError) as error:
            raise DefinitionError(f"{path}: missing or malformed entry {error}") from error
    return Definitions(services=services, structs=structs)


def _service(name: str, body: dict[str, Any]) -> Service:
    return Service(
        name=name,
        id=body["service_id"],
        major_version=body.get("major_version", 1),
        minor_version=body.get("minor_version", 0),
        methods={
            method_name: Method(
                name=method_name,
                id=method["id"],
                in_type=_payload_type(method.get("data_structure", {}), "in"),
                out_type=_payload_type(method.get("data_structure", {}), "out"),
            )
            for method_name, method in body.get("methods", {}).items()
        },
        events={
            event_name: Event(
                name=event_name,
                id=event["id"],
                type=_payload_type(event.get("data_structure", {}), "out"),
            )
            for event_name, event in body.get("events", {}).items()
        },
    )
