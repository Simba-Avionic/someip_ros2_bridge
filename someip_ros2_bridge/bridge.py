from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .codec import INTEGER_TYPES, CodecError
from .config import EventRoute, MethodRoute
from .someip import BoardError, NotDelivered

_LOG = logging.getLogger(__name__)

Publish = Callable[[EventRoute, dict[str, Any]], None]
Call = Callable[[MethodRoute, Any], Awaitable[Any]]
_NOTHING = object()


class TranslationError(ValueError):
    pass


def event_to_ros(route: EventRoute, payload: Any) -> dict[str, Any]:
    fields = {}
    for field in route.fields:
        value = payload if field.someip_field is None else payload[field.someip_field]
        fields[field.ros_field] = _linear(value, field.scale, field.offset)
    return fields


def ros_to_argument(route: MethodRoute, message: Any) -> Any:
    if route.field is None:
        return True
    value = read_field(message, route.field)
    if route.allowed is not None and value not in route.allowed:
        choices = ", ".join(f"{key} ({label})" for key, label in route.allowed.items())
        raise TranslationError(f"{route.field}={value!r} is not allowed, expected {choices}")
    if route.method.in_type == "void":
        return bool(value)
    value = _linear(value, route.scale, route.offset)
    if route.method.in_type in INTEGER_TYPES and isinstance(value, float):
        if not math.isfinite(value):
            raise TranslationError(f"{route.field}={value!r} is not a finite number")
        value = round(value)
    return value


def read_field(message: Any, path: str) -> Any:
    value = message
    for name in path.split("."):
        try:
            value = value[name] if isinstance(value, Mapping) else getattr(value, name)
        except (KeyError, AttributeError) as error:
            raise TranslationError(f"message has no field {path!r}") from error
    return value


def _linear(value: Any, scale: float, offset: float) -> Any:
    if scale == 1 and offset == 0:
        return value
    return value * scale + offset


class Bridge:
    def __init__(self, publish: Publish, call: Call):
        self._publish = publish
        self._call = call
        self._panel: dict[tuple[str, str], tuple[MethodRoute, Any]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    def on_someip_event(self, route: EventRoute, payload: Any) -> None:
        try:
            self._publish(route, event_to_ros(route, payload))
        except Exception:
            _LOG.exception("%s.%s -> %s failed", route.service, route.event.name, route.topic)

    def on_ros_message(self, route: MethodRoute, message: Any) -> None:
        key = (route.service, route.method.name)
        try:
            value = ros_to_argument(route, message)
        except TranslationError as error:
            _LOG.error("%s -> %s.%s ignored: %s", route.topic, *key, error)
            return
        previous = self._panel.get(key, (route, _NOTHING))[1]
        self._panel[key] = (route, value)
        if route.only_on_change and previous is _NOTHING:
            _LOG.info(
                "%s.%s: panel says %s, not sent until the switch moves",
                *key,
                _position(route, value),
            )
            return
        if route.only_on_change and previous == value:
            return
        if route.method.in_type == "void" and not value:
            return
        if self._closed:
            _log(logging.WARNING, route, value, "not sent: shutting down")
            return
        task = asyncio.get_running_loop().create_task(self._send(route, value))
        self._tasks.add(task)
        task.add_done_callback(lambda task: self._done(task, route, value))

    def _done(self, task: asyncio.Task[None], route: MethodRoute, value: Any) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            _log(logging.WARNING, route, value, "cancelled by shutdown, board state unknown")

    def board_found(self, service: str) -> None:
        for (name, method), (route, value) in self._panel.items():
            if name == service:
                _LOG.warning(
                    "%s found: %s state unknown, panel says %s; move the switch to send it",
                    name,
                    method,
                    _position(route, value),
                )

    async def _send(self, route: MethodRoute, value: Any) -> None:
        _log(logging.INFO, route, value, "sending")
        try:
            reply = await self._call(route, None if route.method.in_type == "void" else value)
        except NotDelivered as error:
            _log(logging.ERROR, route, value, f"{error}. Not resent")
        except (BoardError, CodecError) as error:
            _log(logging.ERROR, route, value, f"failed: {error}")
        except Exception:
            _LOG.exception(
                "%s.%s failed unexpectedly, board state unknown", route.service, route.method.name
            )
        else:
            if reply is False:
                _log(logging.WARNING, route, value, "rejected by the board (replied false)")
            else:
                _log(logging.INFO, route, value, f"-> {reply!r}")

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _log(level: int, route: MethodRoute, value: Any, text: str) -> None:
    _LOG.log(level, "%s.%s(%s) %s", route.service, route.method.name, _label(route, value), text)


def _label(route: MethodRoute, value: Any) -> str:
    if route.method.in_type == "void":
        return ""
    if route.allowed and value in route.allowed and route.allowed[value] != str(value):
        return f"{value} = {route.allowed[value]}"
    return repr(value)


def _position(route: MethodRoute, value: Any) -> str:
    return repr(value) if route.method.in_type == "void" else _label(route, value)
