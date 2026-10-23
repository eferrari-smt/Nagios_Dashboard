from app.db import SnapshotRow
from app.pattern_classifier import Pattern, classify
from app.status_parser import HostState

DAY = 86400


def row(state, polled_at, last_state_change=None):
    return SnapshotRow(
        host_name="HOST1",
        current_state=state,
        last_check=polled_at,
        last_state_change=last_state_change if last_state_change is not None else polled_at,
        plugin_output="",
        polled_at=polled_at,
    )


def test_empty_history_is_stable():
    assert classify([], now=1000) == Pattern.STABLE


def test_recovered_when_previous_down_and_latest_up():
    history = [
        row(HostState.DOWN, polled_at=1000),
        row(HostState.UP, polled_at=2000, last_state_change=2000),
    ]
    assert classify(history, now=2000) == Pattern.RECOVERED


def test_chronic_when_down_over_seven_days():
    now = 10 * DAY
    history = [row(HostState.DOWN, polled_at=now, last_state_change=now - 8 * DAY)]
    assert classify(history, now=now) == Pattern.CHRONIC


def test_recent_when_down_under_24h():
    now = 10 * DAY
    history = [row(HostState.DOWN, polled_at=now, last_state_change=now - 3600)]
    assert classify(history, now=now) == Pattern.RECENT


def test_stable_when_down_between_recent_and_chronic_thresholds_and_no_flapping():
    now = 10 * DAY
    history = [row(HostState.DOWN, polled_at=now, last_state_change=now - 3 * DAY)]
    assert classify(history, now=now) == Pattern.STABLE


def test_chronic_takes_priority_over_intermittent():
    now = 10 * DAY
    history = [
        row(HostState.DOWN, polled_at=now - 6 * DAY),
        row(HostState.UP, polled_at=now - 5 * DAY),
        row(HostState.DOWN, polled_at=now - 4 * DAY),
        row(HostState.UP, polled_at=now - 3 * DAY),
        row(HostState.DOWN, polled_at=now, last_state_change=now - 8 * DAY),
    ]
    assert classify(history, now=now) == Pattern.CHRONIC


def test_intermittent_when_currently_down_but_not_chronic_or_recent():
    now = 10 * DAY
    history = [
        row(HostState.DOWN, polled_at=now - 6 * DAY),
        row(HostState.UP, polled_at=now - 5 * DAY),
        row(HostState.DOWN, polled_at=now - 4 * DAY),
        row(HostState.UP, polled_at=now - 3 * DAY),
        row(HostState.DOWN, polled_at=now, last_state_change=now - 2 * DAY),
    ]
    assert classify(history, now=now) == Pattern.INTERMITTENT


def test_intermittent_when_currently_up_but_flapped_recently():
    # La penúltima snapshot también está UP para no disparar RECOVERED
    # (que compara solo el último par y tiene prioridad más alta).
    now = 10 * DAY
    history = [
        row(HostState.UP, polled_at=now - 6 * DAY),
        row(HostState.DOWN, polled_at=now - 5 * DAY),
        row(HostState.UP, polled_at=now - 4 * DAY),
        row(HostState.DOWN, polled_at=now - 3 * DAY),
        row(HostState.UP, polled_at=now - 2 * DAY),
        row(HostState.UP, polled_at=now, last_state_change=now - 2 * DAY),
    ]
    assert classify(history, now=now) == Pattern.INTERMITTENT


def test_stable_when_up_and_no_recent_flapping():
    history = [row(HostState.UP, polled_at=1000)]
    assert classify(history, now=1000) == Pattern.STABLE


def test_changes_outside_window_are_not_counted():
    now = 20 * DAY
    history = [
        row(HostState.DOWN, polled_at=now - 15 * DAY),
        row(HostState.UP, polled_at=now - 14 * DAY),
        row(HostState.DOWN, polled_at=now - 13 * DAY),
        row(HostState.UP, polled_at=now - 12 * DAY),
        row(HostState.DOWN, polled_at=now, last_state_change=now - 2 * DAY),
    ]
    # Los 4 cambios ocurrieron hace más de 7 días: no cuentan como intermitente.
    assert classify(history, now=now) == Pattern.STABLE
