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
"""KFP component that generates seed configs for BOLTZ2 ParallelFor.

Lightweight CPU component — generates seed values matching BOLTZ2's internal
seed generation logic (random.seed(42), then N random ints).
"""

from typing import NamedTuple

from kfp import dsl


@dsl.component(
    base_image="python:3.12-slim",
)
def configure_seeds_boltz2(
    num_model_seeds: int,
) -> NamedTuple(
    "ConfigureSeedsOutputs",
    [
        ("seed_configs", list),
    ],
):
    """Generate seed configs for ParallelFor.

    Matches BOLTZ2's internal seed generation: random.seed(42), then
    N random ints from [0, 2^32).
    """
    import random
    from collections import namedtuple

    # Match BOLTZ2's seed generation logic from experiment_runner.py
    random.seed(42)
    seeds = [random.randint(0, 2**32 - 1) for _ in range(num_model_seeds)]

    seed_configs = [{"seed_value": s} for s in seeds]

    output = namedtuple("ConfigureSeedsOutputs", ["seed_configs"])
    return output(seed_configs)
