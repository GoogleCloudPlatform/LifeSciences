# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""State machine definitions for work-order and run lifecycles.

Two state machines govern the control plane:

**Work-order state machine** tracks a work order from initial proposal
through specialist execution, mechanical validation, and scientific
review to a terminal state.  The ``validation_failed → in_progress``
transition is the recovery path for contract failures (§9 of
orchestration-design-guidance.md: "contract failure → return for
correction").  Without it, every validation failure — a missing sidecar,
a wrong path — would require creating an entirely new work-order
revision, which is too heavyweight for mechanical issues.

The ``validation_failed → mechanically_validated`` transition is the
override path for hand-verified work orders (#178).  When specific
checks fail but the underlying property has been verified by alternative
means, an operator can override named checks (from a hardcoded
allow-list) with a required justification and evidence link.  The
override is recorded in the event log and as append-only history on the
work-order record; the original validation record is never modified.

**Run state machine** tracks a single execution attempt against a
committed work-order revision, from queuing through completion or
failure.

Both machines live in one module because they are interleaved: run
creation validates that the referenced work order is in a state that
permits queuing, and run completion may trigger work-order transitions.
Splitting definitions across command modules would create cross-module
imports for the common case.

Illegal transitions raise ``Refusal`` (exit 9) — the same code used
elsewhere in the CLI for "change the input; retrying cannot help."
"""

from __future__ import annotations

from .concepts import CONCEPT_TRANSITIONS
from .errors import Refusal

# ---------------------------------------------------------------------------
# Work-order state machine
# ---------------------------------------------------------------------------

WORK_ORDER_TRANSITIONS: dict[str | None, set[str]] = {
    None: {"proposed"},
    "proposed": {"committed"},
    "committed": {"queued"},
    "queued": {"in_progress", "blocked", "cancelled"},
    "in_progress": {"submitted", "blocked", "cancelled"},
    "submitted": {"validation_failed", "mechanically_validated"},
    "validation_failed": {"in_progress", "mechanically_validated"},
    "mechanically_validated": {
        "scientifically_accepted",
        "under_scientific_review",
        "revision_requested",
        "scientifically_rejected",
    },
    "under_scientific_review": {
        "scientifically_accepted",
        "revision_requested",
        "scientifically_rejected",
    },
    # Terminal states — no outgoing transitions.
    "scientifically_accepted": set(),
    "revision_requested": set(),
    "scientifically_rejected": set(),
    "blocked": set(),
    "cancelled": set(),
}

# ---------------------------------------------------------------------------
# Run state machine
# ---------------------------------------------------------------------------

RUN_TRANSITIONS: dict[str | None, set[str]] = {
    None: {"queued"},
    "queued": {"starting"},
    "starting": {"running"},
    "running": {"succeeded", "failed", "blocked", "cancelled"},
    # Terminal states.
    "succeeded": set(),
    "failed": set(),
    "blocked": set(),
    "cancelled": set(),
}

# ---------------------------------------------------------------------------
# Derived sets
# ---------------------------------------------------------------------------

WORK_ORDER_STATES: set[str] = set()
for _src, _targets in WORK_ORDER_TRANSITIONS.items():
    if _src is not None:
        WORK_ORDER_STATES.add(_src)
    WORK_ORDER_STATES.update(_targets)

RUN_STATES: set[str] = set()
for _src, _targets in RUN_TRANSITIONS.items():
    if _src is not None:
        RUN_STATES.add(_src)
    RUN_STATES.update(_targets)

TERMINAL_WO_STATES: set[str] = {
    state
    for state, targets in WORK_ORDER_TRANSITIONS.items()
    if state is not None and not targets
}

TERMINAL_RUN_STATES: set[str] = {
    state
    for state, targets in RUN_TRANSITIONS.items()
    if state is not None and not targets
}

# ---------------------------------------------------------------------------
# Transition validation
# ---------------------------------------------------------------------------

_MACHINES: dict[str, dict[str | None, set[str]]] = {
    "workorder": WORK_ORDER_TRANSITIONS,
    "run": RUN_TRANSITIONS,
    "concept": CONCEPT_TRANSITIONS,
}


def validate_transition(machine: str, current: str | None, target: str) -> None:
    """Validate that *current* → *target* is legal in *machine*.

    Parameters
    ----------
    machine:
        ``"workorder"`` or ``"run"``, selecting the transitions dict.
    current:
        The current state, or ``None`` for the initial creation transition.
    target:
        The requested target state.

    Raises
    ------
    Refusal
        If the transition is illegal.  The message names the current
        state, the requested target, and the set of legal targets so the
        caller knows what is permitted.
    """
    transitions = _MACHINES.get(machine)
    if transitions is None:
        raise Refusal(
            f"unknown state machine {machine!r}",
            detail=f"known machines: {', '.join(sorted(_MACHINES))}",
        )

    if current not in transitions:
        raise Refusal(
            f"{machine} has no state {current!r}",
            detail=f"legal states: {', '.join(sorted(s for s in transitions if s is not None))}",
        )

    legal = transitions[current]
    if target not in legal:
        current_label = current if current is not None else "(initial)"
        if legal:
            legal_str = ", ".join(sorted(legal))
            raise Refusal(
                f"cannot transition {machine} from {current_label!r} to {target!r}",
                detail=f"legal targets from {current_label!r}: {legal_str}",
                remedy=f"transition to one of: {legal_str}",
            )
        else:
            raise Refusal(
                f"cannot transition {machine} from {current_label!r} to {target!r}",
                detail=f"{current_label!r} is a terminal state with no outgoing transitions",
            )
