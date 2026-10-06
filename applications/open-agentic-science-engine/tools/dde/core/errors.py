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

"""Error hierarchy and exit codes for the dde CLI.

Every failure path in the CLI raises one of these. The top-level entry
point renders them to stderr and exits with the code carried by the
exception. Nothing in the CLI is permitted to return a plausible value
when the underlying computation did not happen — see
docs/tool-design-guidance.md §8.
"""

from __future__ import annotations


class DDEError(Exception):
    """Base class for all dde CLI failures.

    `exit_code` is what the process returns. `remedy` is optional
    operator-facing guidance rendered under the message.
    """

    exit_code = 1

    def __init__(
        self, message: str, *, remedy: str | None = None, detail: str | None = None
    ):
        super().__init__(message)
        self.message = message
        self.remedy = remedy
        self.detail = detail

    def render(self) -> str:
        parts = [f"error: {self.message}"]
        if self.detail:
            parts.append(f"  detail: {self.detail}")
        if self.remedy:
            parts.append(f"  remedy: {self.remedy}")
        return "\n".join(parts)


class ProjectRootError(DDEError):
    """The dde project root could not be resolved, or is not writable."""

    exit_code = 2


class UsageError(DDEError):
    """Caller supplied bad or insufficient arguments."""

    exit_code = 2


class ArtifactError(DDEError):
    """An expected Layer 0 artifact is missing, unreadable, or malformed."""

    exit_code = 3


class SchemaError(DDEError):
    """Input parsed as JSON but did not match the structure we require.

    Raised rather than guessing. A silently-skipped section is the
    failure mode this class exists to prevent.
    """

    exit_code = 3


class ThresholdError(DDEError):
    """A required threshold is unresolved or a threshold file is invalid."""

    exit_code = 4


class CredentialError(DDEError):
    """A required credential is absent. Never reports the value."""

    exit_code = 5


class EndpointError(DDEError):
    """A remote endpoint returned a non-transient error."""

    exit_code = 6


class EndpointUnavailable(DDEError):
    """A remote endpoint is known-unhealthy or exhausted its retry budget."""

    exit_code = 7


class DependencyError(DDEError):
    """A required binary or Python package is not installed."""

    exit_code = 8


class PhaseContractError(DDEError):
    """A phase-2 command attempted the network. This is a dde bug.

    Not a caller error, and there is nothing an operator can do about it,
    which is why it stays on the generic code rather than taking one of
    the actionable ones. It exists to make the two-phase split
    (tool-design-guidance §3) enforced rather than observed.

    The rule that `analyze` never touches the network was true because
    every author had so far written it that way, and it was verified once,
    by hand, by monkeypatching the client. A rule an agent cannot check is
    not a rule: the day a phase-2 command grows a lookup, re-analysis
    stops being free and offline, the reviewer's fabrication check starts
    depending on an endpoint being up, and nothing reports it.
    """

    exit_code = 1


class Refusal(DDEError):
    """The tool declined to answer. Retrying this input cannot help.

    A refusal is neither an answer nor a failure, and conflating it with
    either loses the remedy. Three cases separated by what the caller
    should do next (tool-design-guidance §8, "Refusal is its own exit
    code"):

      answer   exit 0    the analysis completed; act on it
      refusal  exit 9    change the input; the same query will always
                         be declined
      failure  exit 6/7  keep the input; the fault is elsewhere and may
                         clear

    Every tool that resolves a name will have one of these, because
    never-rank-and-pick guarantees it: the ambiguous case is a refusal
    by construction. It must not depend on which tool was asked, which
    is why this lives here rather than as a per-command exit constant.
    Before this existed, `litref analyze` on an ambiguous name and
    `expression fetch` on an ambiguous symbol — the same outcome —
    exited 7 and 6 respectively, and 7 already meant "retry later",
    the opposite remedy.

    Raising this does not suppress output. A refusal that has produced
    an analysis artifact still prints its path first; the artifact
    records what was refused and why.
    """

    exit_code = 9
