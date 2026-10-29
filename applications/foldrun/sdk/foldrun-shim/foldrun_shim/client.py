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

"""HTTP polling client, .foldrun_job.json state manager, and Jupyter Notebook helpers."""

import html
import io
import json
import os
import random
import tarfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from tqdm.auto import tqdm

from .auth import fetch_gateway_headers, invalidate_token_cache, resolve_gateway_url
from .bundler import BundledInput, bundle_local_inputs

_RETRY_BACKOFF_SCHEDULE = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
_DEFAULT_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_NON_500_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_RFD4_FAST_MODE_NOTICE = (
    "[FoldRun] RFD4-Proteina running in fast mode (~metrics; self-consistency metrics disabled "
    "— pass compute_metrics=True or --metrics to compute full metrics)"
)


def _resolve_gpu_type(gpu_type: str | None) -> str:
    if gpu_type is not None:
        return gpu_type
    return os.environ.get("FOLDRUN_GPU_TYPE", "auto").strip() or "auto"


def _resolve_enable_flex_start(enable_flex_start: bool | None) -> bool:
    if enable_flex_start is not None:
        return enable_flex_start
    env_val = os.environ.get("FOLDRUN_ENABLE_FLEX_START")
    if env_val is None or not env_val.strip():
        return True
    return env_val.strip().lower() not in ("0", "false", "no", "off")


def _resolve_fallback_to_ondemand(fallback_to_ondemand: bool | None) -> bool:
    if fallback_to_ondemand is not None:
        return fallback_to_ondemand
    env_val = os.environ.get("FOLDRUN_FALLBACK_TO_ONDEMAND")
    if env_val is None or not env_val.strip():
        return True
    return env_val.strip().lower() not in ("0", "false", "no", "off")


def _extract_bindcraft_design_path(settings_path: str | Path) -> str | None:
    """Inspect a local BindCraft settings JSON/YAML file for `"design_path"` if present."""
    try:
        resolved = Path(settings_path).expanduser().resolve()
        if not resolved.is_file():
            return None
        text = resolved.read_text(encoding="utf-8")
        data = json.loads(text) if text.lstrip().startswith("{") else yaml.safe_load(text)
        if isinstance(data, dict):
            dp = data.get("design_path")
            if isinstance(dp, str) and dp.strip():
                return dp.strip()
    except Exception:
        return None
    return None


class _FlexStartQueueTimeout(Exception):
    """Internal signal indicating a DWS Flex Start task timed out in queue and should fall back to On-Demand."""

    def __init__(self, job_id: str, max_q_wait: float):
        self.job_id = job_id
        self.max_q_wait = max_q_wait
        super().__init__(f"DWS Flex Start queue wait exceeded on job '{job_id}' ({max_q_wait}s)")


@dataclass
class GatewayJobResult:
    """Result object returned by foldrun-shim predictions, with Jupyter HTML rendering."""

    job_id: str
    model: str
    state: str
    out_dir: Path
    viewer_url: str | None = None
    deduplicated: bool = False
    fallback_triggered: bool = False
    extracted_files: list[Path] = field(default_factory=list)

    @property
    def structures(self) -> list[Path]:
        """Materialized 3D structure files (.pdb, .cif, .mmcif) in deterministic order."""
        return [p for p in self.extracted_files if p.suffix.lower() in (".pdb", ".cif", ".mmcif")]

    @property
    def summaries(self) -> list[Path]:
        """Materialized JSON/CSV summary and confidence files."""
        return [p for p in self.extracted_files if p.suffix.lower() in (".json", ".csv")]

    def _repr_html_(self) -> str:
        safe_job_id = html.escape(self.job_id)
        safe_model = html.escape(self.model)
        safe_state = html.escape(self.state)
        safe_out = html.escape(str(self.out_dir))
        viewer_html = ""
        if self.viewer_url:
            safe_url = html.escape(self.viewer_url, quote=True)
            viewer_html = (
                f'<div style="margin-top:6px;">'
                f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">'
                f"Open 3D Structure in FoldRun Viewer &rarr;</a></div>"
            )
        fallback_html = ""
        if self.fallback_triggered:
            fallback_html = (
                '<div style="font-size:12px;color:#137333;margin-top:4px;">'
                "<b>Scheduling:</b> Automatically fell back to On-Demand (STANDARD) due to DWS queue/capacity constraint.</div>"
            )
        return (
            '<div style="border:1px solid #dadce0;border-radius:8px;padding:12px 16px;'
            'font-family:sans-serif;max-width:680px;">'
            f'<div style="font-weight:600;font-size:14px;">FoldRun Cloud Prediction ({safe_model})</div>'
            f'<div style="font-size:13px;margin-top:4px;"><b>Job ID:</b> <code>{safe_job_id}</code></div>'
            f'<div style="font-size:13px;"><b>State:</b> <code>{safe_state}</code></div>'
            f'<div style="font-size:13px;"><b>Local Output Directory:</b> <code>{safe_out}</code> '
            f"({len(self.extracted_files)} files materialized)</div>"
            f"{fallback_html}"
            f"{viewer_html}"
            "</div>"
        )


@dataclass
class GatewayGPUStatus:
    """Live GPU status and queue depth across FoldRun accelerator tiers."""

    project_id: str
    region: str
    tiers: list[dict[str, Any]]
    active_pipeline_jobs: int = 0
    recommended_gpu_type: str = "L4"
    recommended_enable_flex_start: bool = False
    recommendation_reason: str = ""

    def _repr_html_(self) -> str:
        safe_proj = html.escape(self.project_id)
        safe_region = html.escape(self.region)
        safe_rec_type = html.escape(self.recommended_gpu_type)
        safe_rec_flex = str(self.recommended_enable_flex_start).lower()
        safe_rec_reason = html.escape(self.recommendation_reason)

        rows_html = []
        for t in self.tiers:
            gtype = html.escape(str(t.get("gpu_type", "")))
            mtype = html.escape(str(t.get("machine_type", "")))
            vram = t.get("vram_gb", 0)
            od_q = t.get("on_demand_quota")
            fl_q = t.get("flex_start_quota")
            od_q_str = str(od_q) if od_q is not None else "-"
            fl_q_str = str(fl_q) if fl_q is not None else "-"
            r_od = t.get("running_on_demand", 0)
            r_fl = t.get("running_flex_start", 0)
            q_od = t.get("queued_on_demand", 0)
            q_fl = t.get("queued_flex_start", 0)
            status = str(t.get("status", "AVAILABLE")).upper()
            typical = html.escape(str(t.get("typical_use", "")))

            if status == "AVAILABLE":
                badge_style = "background:#e6f4ea;color:#137333;font-weight:600;padding:2px 8px;border-radius:4px;border:1px solid #ceead6;"
            elif status == "QUEUED":
                badge_style = "background:#fef7e0;color:#b06000;font-weight:600;padding:2px 8px;border-radius:4px;border:1px solid #fce8b2;"
            elif status == "BUSY":
                badge_style = "background:#fce8e6;color:#c5221f;font-weight:600;padding:2px 8px;border-radius:4px;border:1px solid #fad2cf;"
            else:
                badge_style = "background:#f1f3f4;color:#5f6368;font-weight:600;padding:2px 8px;border-radius:4px;border:1px solid #dadce0;"

            row = (
                f"<tr>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #dadce0;font-weight:600;'>{gtype}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #dadce0;'><code>{mtype}</code> ({vram} GB)</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #dadce0;text-align:center;'>{od_q_str} OD / {fl_q_str} DWS</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #dadce0;text-align:center;'>{r_od} OD / {r_fl} DWS</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #dadce0;text-align:center;'>{q_od} OD / {q_fl} DWS</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #dadce0;text-align:center;'><span style='{badge_style}'>{status}</span></td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #dadce0;font-size:12px;color:#5f6368;'>{typical}</td>"
                f"</tr>"
            )
            rows_html.append(row)

        table_body = "\n".join(rows_html)

        return (
            '<div style="border:1px solid #dadce0;border-radius:8px;padding:14px 18px;'
            'font-family:sans-serif;max-width:850px;background:#ffffff;margin:8px 0;">'
            f'<div style="font-weight:600;font-size:15px;color:#202124;">FoldRun Live GPU Availability & Quotas</div>'
            f'<div style="font-size:13px;color:#5f6368;margin-top:2px;margin-bottom:12px;">'
            f"<b>Project:</b> <code>{safe_proj}</code> &nbsp;|&nbsp; "
            f"<b>Region:</b> <code>{safe_region}</code> &nbsp;|&nbsp; "
            f"<b>Active Pipelines:</b> {self.active_pipeline_jobs}</div>"
            '<table style="width:100%;border-collapse:collapse;font-size:13px;text-align:left;">'
            "<thead>"
            '<tr style="background:#f8f9fa;color:#3c4043;font-size:12px;">'
            '<th style="padding:6px 10px;border-bottom:2px solid #dadce0;">GPU Tier</th>'
            '<th style="padding:6px 10px;border-bottom:2px solid #dadce0;">Machine (VRAM)</th>'
            '<th style="padding:6px 10px;border-bottom:2px solid #dadce0;text-align:center;">Quota</th>'
            '<th style="padding:6px 10px;border-bottom:2px solid #dadce0;text-align:center;">Running</th>'
            '<th style="padding:6px 10px;border-bottom:2px solid #dadce0;text-align:center;">Queued</th>'
            '<th style="padding:6px 10px;border-bottom:2px solid #dadce0;text-align:center;">Status</th>'
            '<th style="padding:6px 10px;border-bottom:2px solid #dadce0;">Typical Use</th>'
            "</tr>"
            "</thead>"
            f"<tbody>{table_body}</tbody>"
            "</table>"
            '<div style="margin-top:12px;padding:10px 12px;background:#e8f0fe;border-radius:6px;border:1px solid #d2e3fc;font-size:13px;color:#174ea6;">'
            f"<b>Recommendation:</b> {safe_rec_reason}<br/>"
            '<div style="margin-top:6px;font-family:monospace;font-size:12px;color:#202124;background:#ffffff;padding:6px 8px;border-radius:4px;border:1px solid #d2e3fc;">'
            f'os.environ["FOLDRUN_GPU_TYPE"] = "{safe_rec_type}"<br/>'
            f'os.environ["FOLDRUN_ENABLE_FLEX_START"] = "{safe_rec_flex}"'
            "</div>"
            "</div>"
            "</div>"
        )

    def __str__(self) -> str:
        lines = [
            "=" * 80,
            "FoldRun GPU Availability & Regional Quotas",
            f"Project: {self.project_id} | Region: {self.region} | Active Pipelines: {self.active_pipeline_jobs}",
            "-" * 80,
            f"{'GPU Tier':<10} {'Machine (VRAM)':<22} {'Quota':<14} {'Running':<12} {'Queued':<12} {'Status':<10}",
            "-" * 80,
        ]
        for t in self.tiers:
            gtype = str(t.get("gpu_type", ""))
            mtype = f"{t.get('machine_type', '')} ({t.get('vram_gb', 0)}GB)"
            od_q = t.get("on_demand_quota")
            fl_q = t.get("flex_start_quota")
            od_str = str(od_q) if od_q is not None else "-"
            fl_str = str(fl_q) if fl_q is not None else "-"
            q_str = f"{od_str} OD/{fl_str} DWS"
            r_str = f"{t.get('running_on_demand', 0)} OD/{t.get('running_flex_start', 0)} DWS"
            qu_str = f"{t.get('queued_on_demand', 0)} OD/{t.get('queued_flex_start', 0)} DWS"
            status = str(t.get("status", "AVAILABLE"))
            lines.append(
                f"{gtype:<10} {mtype:<22} {q_str:<14} {r_str:<12} {qu_str:<12} {status:<10}"
            )

        lines.extend(
            [
                "-" * 80,
                f"Recommendation: {self.recommendation_reason}",
                "Recommended configuration:",
                f'  os.environ["FOLDRUN_GPU_TYPE"] = "{self.recommended_gpu_type}"',
                f'  os.environ["FOLDRUN_ENABLE_FLEX_START"] = "{str(self.recommended_enable_flex_start).lower()}"',
                "=" * 80,
            ]
        )
        return "\n".join(lines)


class GatewayClient:
    """Thin-permission client for submitting, polling, and downloading FoldRun Gateway jobs."""

    def __init__(
        self,
        gateway_url: str | None = None,
        http_client: httpx.Client | None = None,
        poll_interval_sec: float = 15.0,
        stream_outputs: bool = False,
    ) -> None:
        self.gateway_url = resolve_gateway_url(gateway_url)
        self._http_client = http_client
        self.poll_interval_sec = poll_interval_sec
        self.stream_outputs = stream_outputs

    def _headers(self) -> dict[str, str]:
        return fetch_gateway_headers(self.gateway_url)

    def _request(
        self,
        method: str,
        path: str,
        *,
        max_retries: int = 6,
        retry_statuses: frozenset[int] | set[int] = _DEFAULT_RETRY_STATUSES,
        **kwargs: Any,
    ) -> httpx.Response:
        caller_headers = dict(kwargs.pop("headers", {}) or {})
        auth_refreshed = False

        for attempt in range(max_retries + 1):
            headers = {**self._headers(), **caller_headers}
            try:
                if self._http_client is not None:
                    resp = self._http_client.request(method, path, headers=headers, **kwargs)
                else:
                    url = f"{self.gateway_url}{path}"
                    with httpx.Client(timeout=300.0, follow_redirects=True) as client:
                        resp = client.request(method, url, headers=headers, **kwargs)
            except httpx.TransportError:
                if attempt >= max_retries:
                    raise
                delay = _RETRY_BACKOFF_SCHEDULE[
                    min(attempt, len(_RETRY_BACKOFF_SCHEDULE) - 1)
                ] * random.uniform(0.85, 1.15)
                time.sleep(delay)
                continue

            if resp.status_code == 401 and not auth_refreshed:
                auth_refreshed = True
                invalidate_token_cache(self.gateway_url)
                continue

            if resp.status_code in retry_statuses and attempt < max_retries:
                delay = _RETRY_BACKOFF_SCHEDULE[
                    min(attempt, len(_RETRY_BACKOFF_SCHEDULE) - 1)
                ] * random.uniform(0.85, 1.15)
                time.sleep(delay)
                continue

            return resp

        raise RuntimeError(f"Exhausted {max_retries} retries on {method} {path}")

    def _download_signed_url(self, download_url: str) -> bytes:
        if self._http_client is not None and download_url.startswith(("http://testserver", "/")):
            rel_path = download_url.replace("http://testserver", "")
            resp = self._http_client.get(rel_path, headers=self._headers())
            resp.raise_for_status()
            return resp.content
        with httpx.Client(timeout=600.0, follow_redirects=True) as client:
            resp = client.get(download_url)
            resp.raise_for_status()
            return resp.content

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        """Cancel a running or queued FoldRun Gateway job."""
        resp = self._request("POST", f"/v1/jobs/{job_id}/cancel")
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to cancel job '{job_id}' (HTTP {resp.status_code}): {resp.text}"
            )
        return resp.json()

    def check_gpus(self) -> GatewayGPUStatus:
        """Inspect live GPU availability, active/queued CustomJob counts, and regional GPU quotas."""
        resp = self._request("GET", "/v1/gpus")
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to query GPU availability (HTTP {resp.status_code}): {resp.text}"
            )
        data = resp.json()
        return GatewayGPUStatus(
            project_id=data.get("project_id", ""),
            region=data.get("region", ""),
            tiers=data.get("tiers", []),
            active_pipeline_jobs=data.get("active_pipeline_jobs", 0),
            recommended_gpu_type=data.get("recommended_gpu_type", "L4"),
            recommended_enable_flex_start=data.get("recommended_enable_flex_start", False),
            recommendation_reason=data.get("recommendation_reason", ""),
        )

    def run_prediction(
        self,
        model: str,
        input_path: str | Path,
        out_dir: str | Path,
        cli_args: list[str] | None = None,
        runner_yaml_path: str | Path | None = None,
        override: bool = False,
        gpu_type: str | None = None,
        enable_flex_start: bool | None = None,
        fallback_to_ondemand: bool | None = None,
        queue_timeout_sec: float | None = None,
        state_filename: str = ".foldrun_job.json",
    ) -> GatewayJobResult:
        """Submit or re-attach to a FoldRun Gateway job, poll until completion, and extract outputs."""
        env_gpu_raw = os.environ.get("FOLDRUN_GPU_TYPE", "").strip()
        explicit_gpu_requested = (gpu_type is not None and gpu_type.strip().lower() != "auto") or (
            bool(env_gpu_raw) and env_gpu_raw.lower() != "auto"
        )

        env_flex_raw = os.environ.get("FOLDRUN_ENABLE_FLEX_START")
        explicit_flex_requested = (enable_flex_start is not None) or (
            env_flex_raw is not None and env_flex_raw.strip() != ""
        )

        resolved_gpu_type = _resolve_gpu_type(gpu_type)
        resolved_flex_start = _resolve_enable_flex_start(enable_flex_start)
        resolved_fallback = _resolve_fallback_to_ondemand(fallback_to_ondemand)
        fallback_triggered = False

        bundled = bundle_local_inputs(
            model=model,
            input_path=input_path,
            cli_args=cli_args,
            runner_yaml_path=runner_yaml_path,
        )
        out_path = Path(out_dir).expanduser().resolve()
        out_path.mkdir(parents=True, exist_ok=True)
        state_file = out_path / state_filename

        job_id: str | None = None
        viewer_url: str | None = None
        deduplicated = False

        # 1. Inspect existing .foldrun_job.json FIRST (before pre-flight check_gpus())
        #    so re-attaching after a Colab disconnect NEVER cancels a live RUNNING GPU job.
        if not override and state_file.exists():
            saved: dict[str, Any] | None = None
            try:
                loaded_state = json.loads(state_file.read_text(encoding="utf-8"))
                if isinstance(loaded_state, dict):
                    saved = loaded_state
                else:
                    state_file.unlink(missing_ok=True)
            except Exception:
                state_file.unlink(missing_ok=True)

            if saved is not None:
                saved_gpu = saved.get("gpu_type")
                saved_flex = saved.get("enable_flex_start")

                hw_changed = bool(
                    explicit_gpu_requested
                    and saved_gpu is not None
                    and str(saved_gpu).upper() != resolved_gpu_type.upper()
                )
                sched_changed = bool(
                    explicit_flex_requested
                    and saved_flex is not None
                    and bool(saved_flex) != bool(resolved_flex_start)
                )

                if hw_changed or sched_changed:
                    state_file.unlink(missing_ok=True)
                elif (
                    saved.get("model") == model
                    and saved.get("input_sha256") == bundled.client_input_sha256
                    and saved.get("job_id")
                ):
                    candidate_id = str(saved["job_id"])
                    status_resp = self._request("GET", f"/v1/jobs/{candidate_id}")
                    if status_resp.status_code == 200:
                        status_data = status_resp.json()
                        state_str = status_data.get("state", "")
                        if state_str not in (
                            "PIPELINE_STATE_FAILED",
                            "PIPELINE_STATE_CANCELLED",
                            "PIPELINE_STATE_CANCELLING",
                            "FAILED",
                            "CANCELLED",
                            "CANCELLING",
                        ):
                            job_id = candidate_id
                            viewer_url = status_data.get("viewer_url") or saved.get("viewer_url")
                            deduplicated = True
                            if saved_gpu is not None:
                                resolved_gpu_type = str(saved_gpu)
                            if saved_flex is not None:
                                resolved_flex_start = bool(saved_flex)
                            if saved.get("fallback_triggered"):
                                fallback_triggered = True
                        else:
                            state_file.unlink(missing_ok=True)
                    elif status_resp.status_code == 404:
                        state_file.unlink(missing_ok=True)
                else:
                    state_file.unlink(missing_ok=True)

        # 2. Submit new job ONLY if not re-attached
        if job_id is None:
            # Proactive pre-flight check only for brand-new submissions when scheduling wasn't forced
            if resolved_fallback and not explicit_flex_requested and resolved_flex_start:
                try:
                    gpu_status = self.check_gpus()
                    target_tier = None
                    if resolved_gpu_type != "auto":
                        target_tier = next(
                            (
                                t
                                for t in gpu_status.tiers
                                if str(t.get("gpu_type", "")).upper() == resolved_gpu_type.upper()
                            ),
                            None,
                        )

                    should_proactively_fallback = False
                    fallback_reason = ""
                    if target_tier:
                        queued_fl = target_tier.get("queued_flex_start", 0)
                        tier_status = target_tier.get("status", "")
                        fl_quota = target_tier.get("flex_start_quota")
                        if queued_fl > 0 or tier_status == "QUEUED":
                            should_proactively_fallback = True
                            fallback_reason = f"DWS Flex Start on {target_tier.get('gpu_type')} currently has {queued_fl} queued task(s)"
                        elif fl_quota is not None and fl_quota == 0:
                            should_proactively_fallback = True
                            fallback_reason = (
                                f"DWS Flex Start quota is 0 on {target_tier.get('gpu_type')}"
                            )
                    elif not gpu_status.recommended_enable_flex_start:
                        should_proactively_fallback = True
                        fallback_reason = (
                            gpu_status.recommendation_reason
                            or "DWS queues detected across GPU tiers"
                        )

                    if should_proactively_fallback:
                        resolved_flex_start = False
                        fallback_triggered = True
                        tqdm.write(
                            f"[FoldRun] {fallback_reason}. "
                            f"Proactively falling back to On-Demand (STANDARD) scheduling for immediate execution."
                        )
                except Exception:
                    pass

            job_id, viewer_url, deduplicated, server_sha256 = self._submit_bundled_job(
                bundled=bundled,
                override=override,
                gpu_type=resolved_gpu_type,
                enable_flex_start=resolved_flex_start,
                fallback_to_ondemand=resolved_fallback,
            )
            state_payload = {
                "job_id": job_id,
                "model": model,
                "input_sha256": bundled.client_input_sha256,
                "server_input_sha256": server_sha256,
                "viewer_url": viewer_url,
                "gpu_type": resolved_gpu_type,
                "enable_flex_start": resolved_flex_start,
                "fallback_triggered": fallback_triggered,
                "submitted_at": datetime.now(UTC).isoformat(),
            }
            state_file.write_text(json.dumps(state_payload, indent=2), encoding="utf-8")

        # 3. Poll until completion
        try:
            final_status = self._poll_until_complete(
                job_id=job_id,
                state_file=state_file,
                queue_timeout_sec=queue_timeout_sec,
                enable_flex_start=resolved_flex_start,
                fallback_to_ondemand=resolved_fallback,
            )
        except _FlexStartQueueTimeout as timeout_exc:
            tqdm.write(
                f"\n[FoldRun] Job '{job_id}' queued on DWS Flex Start for {int(timeout_exc.max_q_wait)}s without GPU allocation."
            )
            tqdm.write(
                f"[FoldRun] Cancelling queued DWS job and automatically falling back to On-Demand (STANDARD) scheduling on {resolved_gpu_type}..."
            )
            try:
                self.cancel_job(job_id)
            except Exception as cancel_exc:
                tqdm.write(f"[FoldRun] Notice during cancellation of queued job: {cancel_exc}")

            resolved_flex_start = False
            fallback_triggered = True
            job_id, viewer_url, deduplicated, server_sha256 = self._submit_bundled_job(
                bundled=bundled,
                override=True,
                gpu_type=resolved_gpu_type,
                enable_flex_start=False,
                fallback_to_ondemand=False,
            )
            state_payload = {
                "job_id": job_id,
                "model": model,
                "input_sha256": bundled.client_input_sha256,
                "server_input_sha256": server_sha256,
                "viewer_url": viewer_url,
                "gpu_type": resolved_gpu_type,
                "enable_flex_start": False,
                "fallback_triggered": True,
                "submitted_at": datetime.now(UTC).isoformat(),
            }
            state_file.write_text(json.dumps(state_payload, indent=2), encoding="utf-8")

            final_status = self._poll_until_complete(
                job_id=job_id,
                state_file=state_file,
                queue_timeout_sec=queue_timeout_sec,
                enable_flex_start=False,
                fallback_to_ondemand=False,
            )

        viewer_url = final_status.get("viewer_url") or viewer_url

        # 4. Download and extract native output directory into out_path
        extracted_files = self._download_and_extract_outputs(job_id=job_id, out_path=out_path)

        return GatewayJobResult(
            job_id=job_id,
            model=model,
            state=final_status.get("state", "PIPELINE_STATE_SUCCEEDED"),
            out_dir=out_path,
            viewer_url=viewer_url,
            deduplicated=deduplicated,
            fallback_triggered=fallback_triggered,
            extracted_files=extracted_files,
        )

    def _submit_bundled_job(
        self,
        bundled: BundledInput,
        override: bool,
        gpu_type: str,
        enable_flex_start: bool,
        fallback_to_ondemand: bool = True,
    ) -> tuple[str, str | None, bool, str]:
        params_dict: dict[str, Any] = {
            "cli_args": bundled.cli_args,
            "runner_yaml_overrides": bundled.runner_yaml_overrides,
            "gpu_type": gpu_type,
            "enable_flex_start": enable_flex_start,
            "fallback_to_ondemand": fallback_to_ondemand,
            "deduplicate": not override,
        }
        if override:
            params_dict["override"] = True
            params_dict["deduplicate"] = False

        files: dict[str, Any] = {
            "input_file": (
                bundled.input_filename,
                bundled.primary_bytes,
                "application/octet-stream",
            )
        }
        if bundled.assets_tar_bytes is not None:
            files["assets_bundle"] = (
                "assets.tar.gz",
                bundled.assets_tar_bytes,
                "application/gzip",
            )
        data = {
            "input_filename": bundled.input_filename,
            "params": json.dumps(params_dict),
        }

        resp = self._request(
            "POST",
            f"/v1/{bundled.model}/jobs",
            data=data,
            files=files,
            retry_statuses=_NON_500_RETRY_STATUSES,
        )
        if resp.status_code not in (200, 202):
            detail = resp.text
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            raise RuntimeError(
                f"FoldRun Gateway rejected {bundled.model} job submission "
                f"(HTTP {resp.status_code}): {detail}"
            )
        payload = resp.json()
        return (
            str(payload["job_id"]),
            payload.get("viewer_url"),
            bool(payload.get("deduplicated", False)),
            str(payload.get("input_sha256", "")),
        )

    def _poll_until_complete(
        self,
        job_id: str,
        state_file: Path,
        queue_timeout_sec: float | None = None,
        enable_flex_start: bool = True,
        fallback_to_ondemand: bool = False,
    ) -> dict[str, Any]:
        bar = tqdm(total=100, desc=f"FoldRun [{job_id}]", leave=True)
        notice_printed = False
        start_poll = time.time()
        queue_notice_delay = float(os.environ.get("FOLDRUN_QUEUE_NOTICE_SEC", "30.0"))
        if queue_timeout_sec is None:
            env_timeout = os.environ.get("FOLDRUN_QUEUE_TIMEOUT_SEC")
            if env_timeout:
                try:
                    queue_timeout_sec = float(env_timeout)
                except ValueError:
                    queue_timeout_sec = None
            elif fallback_to_ondemand and enable_flex_start:
                queue_timeout_sec = 180.0

        seen_task_states: dict[str, str] = {}
        try:
            while True:
                resp = self._request("GET", f"/v1/jobs/{job_id}")
                if resp.status_code == 404:
                    state_file.unlink(missing_ok=True)
                    raise RuntimeError(f"Vertex AI PipelineJob '{job_id}' was not found (HTTP 404)")
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Error polling job '{job_id}' (HTTP {resp.status_code}): {resp.text}"
                    )

                status_data = resp.json()
                state = status_data.get("state", "")
                step_desc = status_data.get("current_step") or state
                bar.set_description(f"FoldRun [{job_id}] {step_desc}")

                # Update progress percentage
                prog_pct = status_data.get("progress_percent")
                if prog_pct is not None:
                    bar.n = max(bar.n, min(int(prog_pct), 100))
                    bar.refresh()

                # Report task state transitions
                tasks = status_data.get("tasks") or []
                for t in tasks:
                    tname = str(t.get("task_name") or t.get("name") or "")
                    tstate = str(t.get("state") or "")
                    if tname:
                        prev = seen_task_states.get(tname)
                        if prev != tstate:
                            seen_task_states[tname] = tstate
                            if prev is not None:
                                tqdm.write(f"[FoldRun] Task '{tname}': {prev} -> {tstate}")

                # Print actionable queue notice once if queued
                exec_phase = status_data.get("execution_phase")
                queue_notice = status_data.get("queue_notice")
                max_q_wait = status_data.get("max_queue_wait_sec") or 0.0

                if (
                    not notice_printed
                    and queue_notice
                    and (
                        (time.time() - start_poll >= queue_notice_delay)
                        or max_q_wait >= queue_notice_delay
                    )
                ):
                    tqdm.write(f"\n[FoldRun Queue Notice] {queue_notice}\n")
                    notice_printed = True

                # Check queue timeout
                if (
                    queue_timeout_sec is not None
                    and queue_timeout_sec > 0
                    and (exec_phase == "QUEUED_GPU" or status_data.get("queued_gpu_tasks", 0) > 0)
                    and max_q_wait >= queue_timeout_sec
                ):
                    if fallback_to_ondemand and enable_flex_start:
                        raise _FlexStartQueueTimeout(job_id=job_id, max_q_wait=max_q_wait)

                    tqdm.write(
                        f"\n[FoldRun] Queue timeout exceeded ({int(max_q_wait)}s >= {int(queue_timeout_sec)}s). "
                        f"Cancelling job '{job_id}'..."
                    )
                    try:
                        self.cancel_job(job_id)
                    except Exception as cancel_exc:
                        tqdm.write(f"[FoldRun] Notice during cancellation: {cancel_exc}")
                    state_file.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"FoldRun job '{job_id}' cancelled: queue wait time exceeded timeout of {int(queue_timeout_sec)}s. "
                        f"Retry with --no-flex-start (standard on-demand) or --gpu_type L4."
                    )

                if state in ("PIPELINE_STATE_SUCCEEDED", "SUCCEEDED"):
                    bar.n = 100
                    bar.refresh()
                    return status_data

                if state in (
                    "PIPELINE_STATE_FAILED",
                    "PIPELINE_STATE_CANCELLED",
                    "FAILED",
                    "CANCELLED",
                ):
                    state_file.unlink(missing_ok=True)
                    err = status_data.get("error_message") or f"Job terminated in state {state}"
                    raise RuntimeError(f"FoldRun job '{job_id}' failed ({state}): {err}")

                time.sleep(self.poll_interval_sec)
        finally:
            bar.close()

    def _download_and_extract_outputs(self, job_id: str, out_path: Path) -> list[Path]:
        archive_bytes: bytes | None = None
        if not self.stream_outputs:
            try:
                resp = self._request(
                    "GET",
                    f"/v1/jobs/{job_id}/outputs",
                    retry_statuses=_NON_500_RETRY_STATUSES,
                )
            except httpx.TransportError:
                resp = None

            if resp is not None:
                if resp.status_code == 200:
                    out_info = resp.json()
                    download_url = out_info.get("download_url")
                    if download_url:
                        try:
                            archive_bytes = self._download_signed_url(download_url)
                        except Exception:
                            # Fallback to direct gateway streaming for private VPC environments
                            archive_bytes = None
                elif resp.status_code in (403, 500, 501, 502, 503, 504):
                    # V4 signed URL generation failed (e.g. missing signBlob or private VPC);
                    # fall back automatically to direct gateway tar.gz streaming.
                    archive_bytes = None
                else:
                    raise RuntimeError(
                        f"Failed to retrieve output archive URL for '{job_id}' "
                        f"(HTTP {resp.status_code}): {resp.text}"
                    )

        if archive_bytes is None:
            stream_resp = self._request("GET", f"/v1/jobs/{job_id}/outputs?stream=true")
            if stream_resp.status_code != 200:
                raise RuntimeError(
                    f"Failed to stream output archive for '{job_id}' "
                    f"(HTTP {stream_resp.status_code}): {stream_resp.text}"
                )
            archive_bytes = stream_resp.content

        extracted: list[Path] = []
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                target_path = (out_path / member.name).resolve()
                if not target_path.is_relative_to(out_path):
                    raise RuntimeError(
                        f"Archive member '{member.name}' attempted path traversal outside {out_path}"
                    )
                target_path.parent.mkdir(parents=True, exist_ok=True)
                fobj = tf.extractfile(member)
                if fobj is not None:
                    target_path.write_bytes(fobj.read())
                    extracted.append(target_path)
        return extracted


def _kwargs_to_cli_args(kwargs: dict[str, Any]) -> list[str]:
    cli_args: list[str] = []
    for key, val in kwargs.items():
        if val is None:
            continue
        flag = "--no-pyrosetta" if key == "no_pyrosetta" else f"--{key}"
        if isinstance(val, bool):
            if key == "no_pyrosetta":
                cli_args.append(f"{flag}={'true' if val else 'false'}")
            elif val:
                cli_args.append(flag)
        elif isinstance(val, (list, tuple)):
            cli_args.extend([flag, ",".join(str(x) for x in val)])
        else:
            cli_args.extend([flag, str(val)])
    return cli_args


class BoltzNotebookHelper:
    """Notebook helper: `from foldrun_shim import boltz; boltz.predict(...)`."""

    def predict(
        self,
        data: str | Path,
        out_dir: str | Path = "./boltz_results",
        override: bool = False,
        gpu_type: str | None = None,
        enable_flex_start: bool | None = None,
        fallback_to_ondemand: bool | None = None,
        gateway_url: str | None = None,
        http_client: httpx.Client | None = None,
        poll_interval_sec: float = 15.0,
        **kwargs: Any,
    ) -> GatewayJobResult:
        cli_args = _kwargs_to_cli_args(kwargs)
        client = GatewayClient(
            gateway_url=gateway_url,
            http_client=http_client,
            poll_interval_sec=poll_interval_sec,
        )
        return client.run_prediction(
            model="boltz",
            input_path=data,
            out_dir=out_dir,
            cli_args=cli_args,
            override=override,
            gpu_type=gpu_type,
            enable_flex_start=enable_flex_start,
            fallback_to_ondemand=fallback_to_ondemand,
        )


class OpenFold3NotebookHelper:
    """Notebook helper: `from foldrun_shim import openfold3; openfold3.predict(...)`."""

    def predict(
        self,
        query_json: str | Path,
        output_dir: str | Path = "./of3_results",
        runner_yaml: str | Path | None = None,
        override: bool = False,
        gpu_type: str | None = None,
        enable_flex_start: bool | None = None,
        fallback_to_ondemand: bool | None = None,
        gateway_url: str | None = None,
        http_client: httpx.Client | None = None,
        poll_interval_sec: float = 15.0,
        **kwargs: Any,
    ) -> GatewayJobResult:
        cli_args: list[str] = []
        for k, v in kwargs.items():
            if v is None:
                continue
            val_str = str(v).lower() if isinstance(v, bool) else str(v)
            cli_args.append(f"--{k}={val_str}")
        client = GatewayClient(
            gateway_url=gateway_url,
            http_client=http_client,
            poll_interval_sec=poll_interval_sec,
        )
        return client.run_prediction(
            model="openfold3",
            input_path=query_json,
            out_dir=output_dir,
            cli_args=cli_args,
            runner_yaml_path=runner_yaml,
            override=override,
            gpu_type=gpu_type,
            enable_flex_start=enable_flex_start,
            fallback_to_ondemand=fallback_to_ondemand,
        )


class AlphaFold2NotebookHelper:
    """Notebook helper: `from foldrun_shim import alphafold2; alphafold2.predict(...)`."""

    def predict(
        self,
        fasta_paths: str | Path | list[str | Path],
        output_dir: str | Path = "./af2_results",
        model_preset: str = "monomer",
        max_template_date: str = "2030-01-01",
        override: bool = False,
        gpu_type: str | None = None,
        enable_flex_start: bool | None = None,
        fallback_to_ondemand: bool | None = None,
        gateway_url: str | None = None,
        http_client: httpx.Client | None = None,
        poll_interval_sec: float = 15.0,
        **kwargs: Any,
    ) -> GatewayJobResult | list[GatewayJobResult]:
        if isinstance(fasta_paths, (str, Path)):
            path_list = [p.strip() for p in str(fasta_paths).split(",") if p.strip()]
        else:
            path_list = [str(p) for p in fasta_paths]

        cli_args = [
            f"--model_preset={model_preset}",
            f"--max_template_date={max_template_date}",
        ]
        kw = dict(kwargs)
        kw.pop("use_gpu_relax", None)
        kw.pop("use_precomputed_msas", None)
        run_relax_kw = kw.pop("run_relax", None)
        if run_relax_kw is not None and "models_to_relax" not in kw:
            kw["models_to_relax"] = "best" if bool(run_relax_kw) else "none"
        for k, v in kw.items():
            if v is not None:
                val_str = str(v).lower() if isinstance(v, bool) else str(v)
                cli_args.append(f"--{k}={val_str}")

        client = GatewayClient(
            gateway_url=gateway_url,
            http_client=http_client,
            poll_interval_sec=poll_interval_sec,
        )
        results: list[GatewayJobResult] = []
        for idx, fasta_path in enumerate(path_list):
            stem = Path(fasta_path).stem
            state_filename = (
                ".foldrun_job.json" if len(path_list) == 1 else f".foldrun_job_{stem}_{idx}.json"
            )
            res = client.run_prediction(
                model="alphafold2",
                input_path=fasta_path,
                out_dir=output_dir,
                cli_args=cli_args,
                override=override,
                gpu_type=gpu_type,
                enable_flex_start=enable_flex_start,
                fallback_to_ondemand=fallback_to_ondemand,
                state_filename=state_filename,
            )
            results.append(res)
        return results[0] if len(results) == 1 else results


class BoltzGenNotebookHelper:
    """Notebook helper: `from foldrun_shim import boltzgen; boltzgen.predict(...)`."""

    def predict(
        self,
        spec_path: str | Path,
        out_dir: str | Path = "./boltzgen_results",
        override: bool = False,
        gpu_type: str | None = None,
        enable_flex_start: bool | None = None,
        fallback_to_ondemand: bool | None = None,
        gateway_url: str | None = None,
        http_client: httpx.Client | None = None,
        poll_interval_sec: float = 15.0,
        **kwargs: Any,
    ) -> GatewayJobResult:
        out_dir = kwargs.pop("output_dir", kwargs.pop("output", out_dir))
        cli_args = _kwargs_to_cli_args(kwargs)
        client = GatewayClient(
            gateway_url=gateway_url,
            http_client=http_client,
            poll_interval_sec=poll_interval_sec,
        )
        return client.run_prediction(
            model="boltzgen",
            input_path=spec_path,
            out_dir=out_dir,
            cli_args=cli_args,
            override=override,
            gpu_type=gpu_type,
            enable_flex_start=enable_flex_start,
            fallback_to_ondemand=fallback_to_ondemand,
        )


class RFD4ProteinaNotebookHelper:
    """Notebook helper: `from foldrun_shim import rfd4_proteina; rfd4_proteina.predict(...)`."""

    def predict(
        self,
        spec_path: str | Path,
        out_dir: str | Path = "./rfd4_results",
        compute_metrics: bool = False,
        override: bool = False,
        gpu_type: str | None = None,
        enable_flex_start: bool | None = None,
        fallback_to_ondemand: bool | None = None,
        gateway_url: str | None = None,
        http_client: httpx.Client | None = None,
        poll_interval_sec: float = 15.0,
        **kwargs: Any,
    ) -> GatewayJobResult:
        out_dir = kwargs.pop("output_dir", kwargs.pop("output", out_dir))
        metrics_alias = kwargs.pop("metrics", None)
        if metrics_alias is not None:
            compute_metrics = bool(metrics_alias)

        cli_args = _kwargs_to_cli_args(kwargs)
        if compute_metrics:
            cli_args.append("compute_metrics=true")
        else:
            tqdm.write(_RFD4_FAST_MODE_NOTICE)
            cli_args.append("compute_metrics=false")

        client = GatewayClient(
            gateway_url=gateway_url,
            http_client=http_client,
            poll_interval_sec=poll_interval_sec,
        )
        return client.run_prediction(
            model="rfd4_proteina",
            input_path=spec_path,
            out_dir=out_dir,
            cli_args=cli_args,
            override=override,
            gpu_type=gpu_type,
            enable_flex_start=enable_flex_start,
            fallback_to_ondemand=fallback_to_ondemand,
        )


class BindCraftNotebookHelper:
    """Notebook helper: `from foldrun_shim import bindcraft; bindcraft.predict(...)`."""

    def predict(
        self,
        settings_path: str | Path,
        out_dir: str | Path | None = None,
        override: bool = False,
        gpu_type: str | None = None,
        enable_flex_start: bool | None = None,
        fallback_to_ondemand: bool | None = None,
        gateway_url: str | None = None,
        http_client: httpx.Client | None = None,
        poll_interval_sec: float = 15.0,
        **kwargs: Any,
    ) -> GatewayJobResult:
        resolved_out_dir = kwargs.pop("output_dir", kwargs.pop("output", out_dir))
        if resolved_out_dir is None:
            resolved_out_dir = (
                _extract_bindcraft_design_path(settings_path) or "./bindcraft_results"
            )
        cli_args = _kwargs_to_cli_args(kwargs)
        client = GatewayClient(
            gateway_url=gateway_url,
            http_client=http_client,
            poll_interval_sec=poll_interval_sec,
        )
        return client.run_prediction(
            model="bindcraft",
            input_path=settings_path,
            out_dir=resolved_out_dir,
            cli_args=cli_args,
            override=override,
            gpu_type=gpu_type,
            enable_flex_start=enable_flex_start,
            fallback_to_ondemand=fallback_to_ondemand,
        )


class AlphaFold3NotebookHelper:
    """Notebook helper: `from foldrun_shim import alphafold3; alphafold3.predict(...)`."""

    def predict(
        self,
        input_path: str | Path | None = None,
        json_path: str | Path | None = None,
        fasta_paths: str | Path | None = None,
        sequence: str | None = None,
        output_dir: str | Path = "./af3_results",
        num_diffusion_samples: int = 5,
        num_recycles: int = 10,
        num_seeds: int = 1,
        model_seeds: list[int] | tuple[int, ...] | str | None = None,
        max_template_date: str = "2021-09-30",
        run_data_pipeline: bool = True,
        override: bool = False,
        gpu_type: str | None = None,
        enable_flex_start: bool | None = None,
        fallback_to_ondemand: bool | None = None,
        gateway_url: str | None = None,
        http_client: httpx.Client | None = None,
        poll_interval_sec: float = 15.0,
        **kwargs: Any,
    ) -> GatewayJobResult:
        target_path = input_path or json_path or fasta_paths
        temp_file = None

        if target_path is None and sequence is not None:
            import tempfile

            temp_file = tempfile.NamedTemporaryFile(suffix=".fasta", mode="w", delete=False)
            temp_file.write(f">query\n{sequence.strip()}\n")
            temp_file.close()
            target_path = temp_file.name

        if target_path is None:
            raise ValueError("predict() requires input_path, json_path, fasta_paths, or sequence.")

        cli_args = [
            f"--num_diffusion_samples={num_diffusion_samples}",
            f"--num_recycles={num_recycles}",
            f"--num_seeds={num_seeds}",
            f"--max_template_date={max_template_date}",
            f"--run_data_pipeline={'true' if run_data_pipeline else 'false'}",
        ]
        if model_seeds is not None:
            seeds_str = (
                ",".join(str(s) for s in model_seeds)
                if isinstance(model_seeds, (list, tuple))
                else str(model_seeds)
            )
            cli_args.append(f"--model_seeds={seeds_str}")
        for k, v in kwargs.items():
            if v is not None:
                if k == "run_inference" and bool(v) is True:
                    continue
                if isinstance(v, bool):
                    val_str = str(v).lower()
                elif isinstance(v, (list, tuple)):
                    val_str = ",".join(str(x) for x in v)
                else:
                    val_str = str(v)
                cli_args.append(f"--{k}={val_str}")

        client = GatewayClient(
            gateway_url=gateway_url,
            http_client=http_client,
            poll_interval_sec=poll_interval_sec,
        )
        try:
            return client.run_prediction(
                model="alphafold3",
                input_path=target_path,
                out_dir=output_dir,
                cli_args=cli_args,
                override=override,
                gpu_type=gpu_type,
                enable_flex_start=enable_flex_start,
                fallback_to_ondemand=fallback_to_ondemand,
            )
        finally:
            if temp_file and os.path.exists(temp_file.name):
                try:
                    os.unlink(temp_file.name)
                except Exception:
                    pass


boltz = BoltzNotebookHelper()
openfold3 = OpenFold3NotebookHelper()
alphafold2 = AlphaFold2NotebookHelper()
boltzgen = BoltzGenNotebookHelper()
rfd4_proteina = RFD4ProteinaNotebookHelper()
bindcraft = BindCraftNotebookHelper()
alphafold3 = AlphaFold3NotebookHelper()
af3 = alphafold3


def check_gpus(
    gateway_url: str | None = None,
    http_client: httpx.Client | None = None,
) -> GatewayGPUStatus:
    """Check live GPU availability, queue counts, and regional quotas via the FoldRun Gateway."""
    client = GatewayClient(gateway_url=gateway_url, http_client=http_client)
    return client.check_gpus()
