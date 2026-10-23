"""Clasificación de patrones de disponibilidad a partir del historial de snapshots."""

from __future__ import annotations

import time
from enum import Enum

from app.db import SnapshotRow
from app.status_parser import HostState

CHRONIC_THRESHOLD_SECONDS = 7 * 86400
RECENT_THRESHOLD_SECONDS = 86400
INTERMITTENT_WINDOW_SECONDS = 7 * 86400
INTERMITTENT_MIN_CHANGES = 3


class Pattern(str, Enum):
    RECOVERED = "recuperado"
    CHRONIC = "cronico"
    INTERMITTENT = "intermitente"
    RECENT = "reciente"
    STABLE = "estable"


def classify(history: list[SnapshotRow], now: int | None = None) -> Pattern:
    """Clasifica el patrón de un host a partir de su historial de snapshots.

    Precedencia: RECOVERED > CHRONIC > INTERMITTENT > RECENT > STABLE.
    Un host recién recuperado se marca como tal aunque también sea
    "intermitente" en lo histórico, porque en ese momento es el dato más
    accionable. Los cambios de estado se cuentan sobre nuestros propios
    snapshots (no el log nativo de Nagios): un flapping más rápido que el
    intervalo de polling puede no reflejarse acá. Es una limitación
    conocida de esta aproximación, no un bug del clasificador.
    """
    if not history:
        return Pattern.STABLE

    reference_now = now if now is not None else int(time.time())
    latest = history[-1]

    if len(history) >= 2:
        previous = history[-2]
        if previous.current_state != HostState.UP and latest.current_state == HostState.UP:
            return Pattern.RECOVERED

    if latest.current_state == HostState.UP:
        if _count_changes(history, reference_now, INTERMITTENT_WINDOW_SECONDS) >= INTERMITTENT_MIN_CHANGES:
            return Pattern.INTERMITTENT
        return Pattern.STABLE

    downtime = reference_now - latest.last_state_change
    if downtime >= CHRONIC_THRESHOLD_SECONDS:
        return Pattern.CHRONIC

    if _count_changes(history, reference_now, INTERMITTENT_WINDOW_SECONDS) >= INTERMITTENT_MIN_CHANGES:
        return Pattern.INTERMITTENT

    if downtime < RECENT_THRESHOLD_SECONDS:
        return Pattern.RECENT

    return Pattern.STABLE


def _count_changes(history: list[SnapshotRow], now: int, window_seconds: int) -> int:
    cutoff = now - window_seconds
    windowed = [s for s in history if s.polled_at >= cutoff]
    return sum(
        1 for prev, curr in zip(windowed, windowed[1:]) if prev.current_state != curr.current_state
    )
