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

"""Shared GCS retry policy for the OF3 analysis Cloud Run Job.

Pass ``GCS_RETRY`` as ``retry=`` on Cloud Storage operations so transient
network failures back off and retry instead of failing the analysis job.
"""

from google.api_core import exceptions as api_exceptions
from google.api_core import retry as api_retry
from google.auth import exceptions as auth_exceptions

_RETRYABLE = (
    api_exceptions.TooManyRequests,
    api_exceptions.InternalServerError,
    api_exceptions.BadGateway,
    api_exceptions.ServiceUnavailable,
    api_exceptions.GatewayTimeout,
    api_exceptions.RetryError,
    ConnectionError,
    TimeoutError,
    auth_exceptions.TransportError,
)


def _if_transient(exc: BaseException) -> bool:
    return isinstance(exc, _RETRYABLE)


GCS_RETRY = api_retry.Retry(
    predicate=_if_transient,
    initial=1.0,
    maximum=30.0,
    multiplier=2.0,
    timeout=300.0,
)
