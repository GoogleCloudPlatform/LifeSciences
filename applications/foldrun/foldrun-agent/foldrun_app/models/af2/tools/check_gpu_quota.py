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

"""Tool for checking GPU quota and current usage."""

import logging
from typing import Any, Dict

from foldrun_app.core.hardware import check_gpu_quota

from ..base import AF2Tool

logger = logging.getLogger(__name__)


class AF2CheckGPUQuotaTool(AF2Tool):
    """Tool for checking GPU quota limits and current usage in a region."""

    def run(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check GPU quota and current usage for the configured region.

        Args:
            arguments: {
                'region': Optional region to check (default: config.region)
            }

        Returns:
            Dict with quota information for each GPU type
        """
        region = arguments.get("region", self.config.region)

        logger.info(f"Checking GPU quotas for region {region}")

        try:
            quota_result = check_gpu_quota(self.config.project_id, region)
            on_demand = quota_result["on_demand_gpus"]
            preemptible = quota_result["preemptible_gpus"]

            summary = self._generate_summary(on_demand, preemptible)

            return {
                "region": region,
                "project_id": self.config.project_id,
                "on_demand_gpus": on_demand,
                "preemptible_gpus": preemptible,
                "summary": summary,
                "recommendation": self._generate_recommendation(on_demand, preemptible),
            }

        except Exception as e:
            logger.error(f"Failed to check GPU quotas: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"Failed to retrieve GPU quota information: {str(e)}",
                "region": region,
            }

    def _generate_summary(self, on_demand: Dict, preemptible: Dict) -> str:
        """Generate human-readable summary of GPU availability."""
        lines = []

        if on_demand:
            lines.append("**On-Demand GPUs:**")
            for quota_name, info in on_demand.items():
                lines.append(
                    f"  {info['status_emoji']} {info['friendly_name']}: "
                    f"{info['available']}/{info['limit']} available "
                    f"({info['usage_percentage']}% used)"
                )

        if preemptible:
            lines.append("\n**Preemptible/Spot GPUs (for FLEX_START):**")
            for quota_name, info in preemptible.items():
                lines.append(
                    f"  {info['status_emoji']} {info['friendly_name']}: "
                    f"{info['available']}/{info['limit']} available "
                    f"({info['usage_percentage']}% used)"
                )

        return "\n".join(lines)

    def _generate_recommendation(self, on_demand: Dict, preemptible: Dict) -> str:
        """Generate recommendations based on quota availability."""
        recommendations = []

        exhausted = []
        high_usage = []

        for quota_name, info in {**on_demand, **preemptible}.items():
            if info["status"] == "EXHAUSTED":
                exhausted.append(info["friendly_name"])
            elif info["status"] == "HIGH_USAGE":
                high_usage.append(info["friendly_name"])

        if exhausted:
            recommendations.append(
                f"⚠️ **Quota Exhausted**: {', '.join(exhausted)} - "
                "Jobs requiring these GPUs will fail or queue indefinitely. "
                "Consider requesting quota increase or using alternative GPU types."
            )

        if high_usage:
            recommendations.append(
                f"⚠️ **High Usage**: {', '.join(high_usage)} - "
                "GPU availability is limited. Enable FLEX_START to queue jobs when capacity is full."
            )

        if preemptible:
            preempt_available = all(info["available"] > 0 for info in preemptible.values())
            if preempt_available:
                recommendations.append(
                    "✅ **FLEX_START Recommended**: Preemptible GPU quota available. "
                    "Jobs with enable_flex_start=true will use cost-effective spot instances."
                )

        if not recommendations:
            recommendations.append(
                "✅ **Good Availability**: Sufficient GPU quota available for job submission."
            )

        return "\n\n".join(recommendations)
