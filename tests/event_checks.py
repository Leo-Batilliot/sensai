"""Contract checks on the events of one turn (docs/contracts/events.md §4.3)."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from sensai.core import events

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "docs/contracts/schemas/events"
VALIDATORS = {
    path.stem: Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))
    for path in SCHEMAS_DIR.glob("*.json")
}


def check_turn(emitted: list[events.Event]) -> None:
    """Every event matches its JSON schema, and the turn follows the sequence rules."""
    for event in emitted:
        data = json.loads(json.dumps(event.to_dict()))  # what actually goes over the wire
        problems = [error.message for error in VALIDATORS[event.type].iter_errors(data)]
        assert problems == [], f"{event.type}: {problems}"

    assert emitted[0].type == "turn_started"
    assert len({event.turn_id for event in emitted}) == 1
    ends = [event for event in emitted if event.type in ("message_done", "error")]
    assert len(ends) == 1 and ends[0] is emitted[-1]

    # Tool steps: tool_call -> sql_generated (if any) -> tool_result, one result per call
    position = {id(event): index for index, event in enumerate(emitted)}
    calls = [event for event in emitted if isinstance(event, events.ToolCall)]
    results = [event for event in emitted if isinstance(event, events.ToolResult)]
    assert [r.call_id for r in results] == [c.call_id for c in calls][: len(results)]
    for result in results:
        call = next(c for c in calls if c.call_id == result.call_id)
        assert position[id(call)] < position[id(result)]
        for sql in emitted:
            if isinstance(sql, events.SqlGenerated) and sql.call_id == call.call_id:
                assert position[id(call)] < position[id(sql)] < position[id(result)]

    if isinstance(ends[0], events.MessageDone):
        assert len(results) == len(calls)
        tokens = "".join(event.delta for event in emitted if isinstance(event, events.Token))
        assert tokens == ends[0].content
        assert (ends[0].usage is None) == ends[0].interrupted
