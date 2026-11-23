# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Lightweight KFP component that validates FASTA inputs and configures AF2 model runners."""

from typing import NamedTuple

from kfp import dsl
from kfp.dsl import Artifact, Input


@dsl.component(base_image="python:3.13-slim")
def configure_run(
    sequence: Input[Artifact],
    model_preset: str,
    random_seed: int = None,  # noqa: RUF013  # ty: ignore[invalid-parameter-default]
    num_multimer_predictions_per_model: int = 5,
) -> NamedTuple(  # ty: ignore[invalid-type-form]
    "ConfigureRunOutputs",
    [
        ("model_runners", list),
        ("run_multimer_system", bool),
        ("num_ensemble", int),
    ],
):
    """Configures an AlphaFold2 pipeline run without pulling the CUDA container image."""

    import random
    from collections import namedtuple

    model_presets = {
        "monomer": (
            "model_1",
            "model_2",
            "model_3",
            "model_4",
            "model_5",
        ),
        "monomer_ptm": (
            "model_1_ptm",
            "model_2_ptm",
            "model_3_ptm",
            "model_4_ptm",
            "model_5_ptm",
        ),
        "monomer_casp14": (
            "model_1",
            "model_2",
            "model_3",
            "model_4",
            "model_5",
        ),
        "multimer": (
            "model_1_multimer_v3",
            "model_2_multimer_v3",
            "model_3_multimer_v3",
            "model_4_multimer_v3",
            "model_5_multimer_v3",
        ),
    }

    if model_preset not in model_presets:
        raise ValueError(f"Incorrect model preset: {model_preset}")

    run_multimer_system = model_preset == "multimer"
    num_ensemble = 8 if model_preset == "monomer_casp14" else 1
    num_predictions_per_model = (
        num_multimer_predictions_per_model if model_preset == "multimer" else 1
    )
    if num_predictions_per_model < 1:
        raise ValueError(
            "num_multimer_predictions_per_model must be >= 1, "
            f"got {num_multimer_predictions_per_model}"
        )

    with open(sequence.path, encoding="utf-8") as f:
        sequence_str = f.read()

    seqs: list[str] = []
    cur_seq: list[str] = []
    in_record = False
    for raw_line in sequence_str.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if in_record:
                if not cur_seq:
                    raise ValueError(f"Empty FASTA record found in {sequence.path}.")
                seqs.append("".join(cur_seq))
            cur_seq = []
            in_record = True
        else:
            if not in_record:
                raise ValueError(f"FASTA input in {sequence.path} must start with '>' header.")
            cur_seq.append(line)
    if in_record:
        if not cur_seq:
            raise ValueError(f"Empty FASTA record found in {sequence.path}.")
        seqs.append("".join(cur_seq))

    if not seqs:
        raise ValueError(f"No valid sequences found in {sequence.path}.")
    if len(seqs) != 1 and model_preset != "multimer":
        raise ValueError(
            f"More than one sequence found in {sequence.path}. Unsupported for monomer predictions."
        )

    models = model_presets[model_preset]
    total_runs = len(models) * num_predictions_per_model
    if random_seed is None:
        # Keep seeds within signed 32-bit int range so JSON/protobuf float64 serialization
        # preserves exact distinct integer seeds for every model/seed runner.
        random_seed = random.randrange(2**31 - max(1, total_runs))

    model_runners = []
    for model_name in models:
        for i in range(num_predictions_per_model):
            model_runners.append(
                {"prediction_index": i, "model_name": model_name, "random_seed": random_seed}
            )
            random_seed += 1

    ConfigureRunOutputs = namedtuple(
        "ConfigureRunOutputs", ["model_runners", "run_multimer_system", "num_ensemble"]
    )

    return ConfigureRunOutputs(model_runners, run_multimer_system, num_ensemble)
