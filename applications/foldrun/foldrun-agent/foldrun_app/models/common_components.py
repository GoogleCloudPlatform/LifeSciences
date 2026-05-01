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

"""Shared Kubeflow Pipeline components across models."""

from typing import NamedTuple
from kfp import dsl

@dsl.component(
    base_image="python:3.14-slim",
    packages_to_install=["google-cloud-pubsub"]
)
def publish_completion_message(
    project: str,
    topic_id: str,
    model_name: str,
    job_id: str,
    input_path: str,
    status: dsl.PipelineTaskFinalStatus,
):
    from google.cloud import pubsub_v1
    import json
    
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project, topic_id)
    
    message_dict = {
        "model_name": model_name,
        "job_id": job_id,
        "input_path": input_path,
        "state": status.state,
        "error_code": getattr(status, "error_code", None),
        "error_message": getattr(status, "error_message", None),
    }
    
    data = json.dumps(message_dict).encode("utf-8")
    future = publisher.publish(topic_path, data)
    future.result()
    print(f"Published message to {topic_path}: {message_dict}")


@dsl.component(base_image="python:3.14-slim")
def configure_seeds(
    num_model_seeds: int,
    base_seed: int,
) -> NamedTuple(
    "ConfigureSeedsOutputs",
    [
        ("seed_configs", list),
    ],
):
    """Generate seed configs for ParallelFor.

    Uses random.seed(base_seed), then N random ints from [0, 2^32).
    """
    import random
    from collections import namedtuple

    random.seed(base_seed)
    seeds = [random.randint(0, 2**32 - 1) for _ in range(num_model_seeds)]

    seed_configs = [{"seed_value": s} for s in seeds]

    output = namedtuple("ConfigureSeedsOutputs", ["seed_configs"])
    return output(seed_configs)
