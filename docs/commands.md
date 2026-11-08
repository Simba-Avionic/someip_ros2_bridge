# Commands (ROS → SOME/IP)

Fire and forget: **the board moves only when a panel switch moves.** The rules are in `bridge.py`,
made for full-state messages such as `TankingCommands`, which `gs_web_app` publishes every 0.5 s
and on every switch change.

1. **Fire:** a call happens only when a field differs from the previous message. A valve fires on
   any change; a button (void method with a `field`) only when it turns true. Repeats never fire.
2. **Forget:** each call is sent once and its result is logged. Nothing is queued, retried or
   resent: not after a timeout, not after a board reboot, not when the bridge starts.
3. **Start-up:** the first message only records the panel position, e.g.
   `FcRadioService.SetGSEOxidizerFillValve: panel says 1 = open, not sent until the switch moves`.
4. **Board found** (at start or after it was lost): a warning for each method with a recorded panel
   position, e.g. `FcRadioService found: SetGSEOxidizerFillValve state unknown, panel says 1 = open;
   move the switch to send it`. A board counts as lost only once its offer expires (TTL, 5 s for the
   simulators): a faster reboot is not noticed.
5. **Quick toggles** are each sent at once, in order, without waiting for the previous reply.

Why: after a board restart nothing may start on its own; an operator has to act again.

Cost: after a lost packet or a board reboot the valve can differ from the panel until the switch
moves. The board sends no valve state, so only the log shows it.

**One publisher per command topic.** The bridge compares each message with the previous one on the
topic, whoever sent it. A second node publishing `TankingCommands` next to `gs_web_app` would be
undone by the panel's next message 0.5 s later.

## Log

| Log | Level | Meaning |
| --- | --- | --- |
| `…(1 = open) sending` | INFO | Call sent |
| `…(1 = open) -> True` | INFO | Board replied |
| `rejected by the board (replied false)` | WARNING | Board refused it |
| `NOT SENT: … Not resent` | ERROR | Board not found; nothing was sent |
| `NO REPLY within 10 s: … Not resent` | ERROR | Sent, but the board may or may not have done it |
| `failed: …` | ERROR | Error code or unreadable reply, or the value does not fit the type |
| `failed unexpectedly, board state unknown` | ERROR | Bug or OS error, with traceback |
| `cancelled by shutdown, board state unknown` | WARNING | Ctrl+C during a call |
| `not sent: shutting down` | WARNING | Message arrived during Ctrl+C |
| `… ignored: …` | ERROR | Value not in `allowed`, or not a number |

## Options

- `only_on_change: false`: every message fires, not only changes (for topics without a panel);
  a button still fires only for true. A method without a `field` fires on every message.
- Integer arguments are rounded after `scale` and `offset`.
