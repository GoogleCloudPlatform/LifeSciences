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

"""OpenFold3 specific prediction analyzer and consolidator."""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import matplotlib
from google import genai
from google.cloud import storage
from google.genai import types

matplotlib.use("Agg")  # Use non-interactive backend for Cloud Run

from .shared_utils import (
    calculate_plddt_stats,
    download_json_from_gcs,
    download_text_from_gcs,
    get_quality_assessment,
    upload_to_gcs,
    parse_cif_chains,
    plot_plddt_distribution,
    plot_error_matrix,
    plot_iptm_matrix,
    wait_for_sibling_tasks,
    calculate_per_chain_plddt,
)

logger = logging.getLogger(__name__)





def generate_gemini_expert_analysis(summary_data: dict) -> dict:
    """Generate expert analysis using Gemini via Google GenAI SDK."""
    try:
        best = summary_data["best_prediction"]
        summary = summary_data["summary"]
        top_preds = summary_data["top_predictions"][:3]

        chain_desc = ""
        for ci in best.get("chain_info", []):
            if ci["molecule_type"] == "ligand":
                comps = ", ".join(ci.get("comp_ids", [])[:3])
                chain_desc += f"- Chain {ci['chain_id']}: {ci['molecule_type']} ({comps}), {ci['atom_count']} atoms\n"
            else:
                chain_desc += f"- Chain {ci['chain_id']}: {ci['molecule_type']}, {ci['residue_count']} residues, {ci['atom_count']} atoms\n"

        gpde_str = (
            f"{best.get('gpde', 0):.4f}" if best.get("gpde") is not None else "N/A"
        )

        prompt = f"""You are an expert structural biologist reviewing OpenFold3 prediction results.

**OpenFold3 Reference (use to interpret results):**
- Diffusion-based structure prediction model (similar to AlphaFold3). Supports proteins, RNA, DNA, and ligands.
- Generates multiple diffusion samples per seed. Seeds = independent weight initializations (different conformations). Samples = different denoising trajectories from same seed (variations on a theme).
- **Metric interpretation thresholds:**
  - sample_ranking_score (0-1): Overall quality. >0.7 = good, >0.8 = very good.
  - pTM (0-1): Global fold accuracy. >0.8 = high confidence fold. >0.9 = very high.
  - ipTM (0-1): Interface quality for complexes. >0.7 = reliable interface. >0.8 = high confidence.
  - pLDDT (0-100): Per-atom confidence. >80 = good, >90 = very high. <50 in a region = likely disordered (real biology, not prediction failure).
  - gpde: Global predicted distance error. Lower = better. Complements pTM.
  - has_clash: Steric clash detected. 0 = clean, 1 = problem (try more seeds).
- **Key interpretation patterns:**
  - ipTM low but pTM high → individual chains fold well but interface/binding pose is uncertain
  - Low ligand pLDDT (e.g. <60) → binding mode is uncertain, try more seeds/samples
  - High variance in ranking_score across samples → conformational flexibility or poorly defined binding site
  - Chain pTM on diagonal of ipTM matrix shows per-chain fold quality independently of interface

**Prediction Summary:**
- Job ID: {summary_data["job_id"]}
- Total Tokens/Residues: {summary["protein_info"]["sequence_length"]}
- Total Samples: {summary["protein_info"]["total_predictions"]}
- Best Sample: {best["sample_name"]} (Rank #{best.get("rank", 1)})

**Chain Composition:**
{chain_desc}
**Quality Metrics (Best Sample):**
- Ranking Score: {best["ranking_score"]:.4f}
- pTM: {best["ptm"]:.4f}
- ipTM: {best["iptm"]:.4f}
- Mean pLDDT: {best["plddt_mean"]:.2f}
- GPDE: {gpde_str}
- Has Clash: {"Yes" if best.get("has_clash") else "No"}
- Disorder: {"Yes" if best.get("disorder") else "No"}
"""

        if best.get("chain_ptm"):
            prompt += "\n**Per-Chain pTM:**\n"
            for chain, score in best["chain_ptm"].items():
                prompt += f"- Chain {chain}: {score:.4f}\n"

        if best.get("chain_pair_iptm"):
            prompt += "\n**Chain-Pair ipTM (interface quality):**\n"
            for pair, score in best["chain_pair_iptm"].items():
                prompt += f"- {pair}: {score:.4f}\n"

        if best.get("per_chain_plddt"):
            prompt += "\n**Per-Chain pLDDT (protein vs ligand breakdown):**\n"
            for cid, stats in best["per_chain_plddt"].items():
                mol_type = stats.get("molecule_type", "unknown")
                comps = ", ".join(stats.get("comp_ids", [])[:3])
                comp_str = f" ({comps})" if mol_type == "ligand" else ""
                prompt += (
                    f"- Chain {cid} ({mol_type}{comp_str}): "
                    f"mean pLDDT={stats['mean']:.1f}, "
                    f"range=[{stats['min']:.1f}, {stats['max']:.1f}], "
                    f"{stats['atom_count']} atoms\n"
                )

        prompt += "\n**Confidence Distribution (Best Sample):**\n"
        if best.get("plddt_distribution"):
            dist = best["plddt_distribution"]
            prompt += (
                f"- Very High (90-100): {dist.get('very_high_confidence', 0)} atoms\n"
            )
            prompt += f"- High (70-90): {dist.get('high_confidence', 0)} atoms\n"
            prompt += f"- Low (50-70): {dist.get('low_confidence', 0)} atoms\n"
            prompt += f"- Very Low (<50): {dist.get('very_low_confidence', 0)} atoms\n"

        all_preds = summary_data.get("all_predictions_summary", [])
        if len(all_preds) > 1:
            all_scores = [p["ranking_score"] for p in all_preds]
            prompt += f"\n**Sample Diversity (all {len(all_preds)} samples):**\n"
            prompt += f"- Ranking score range: {min(all_scores):.4f} - {max(all_scores):.4f} (spread: {max(all_scores) - min(all_scores):.4f})\n"
            prompt += f"- Mean ranking score: {sum(all_scores) / len(all_scores):.4f}\n"

        prompt += "\n**Top 3 Samples Comparison:**\n"
        for i, pred in enumerate(top_preds, 1):
            clash_str = " [CLASH]" if pred.get("has_clash") else ""
            prompt += f"{i}. {pred['sample_name']}: ranking_score {pred['ranking_score']:.4f}, pTM {pred['ptm']:.4f}, ipTM {pred['iptm']:.4f}{clash_str}\n"

        if summary["protein_info"].get("fasta_sequence"):
            fasta_seq = summary["protein_info"]["fasta_sequence"]
            fasta_header = summary["protein_info"].get("fasta_header", "Unknown")
            prompt += f"\n**Input Sequence:**\n>{fasta_header}\n{fasta_seq}\n"

        prompt += """

**Expert Analysis Request:**

Provide a comprehensive assessment covering:

1. **Overall Prediction Quality**: Evaluate the ranking_score, pTM, ipTM, and pLDDT. How reliable is this prediction?
2. **Per-Chain Confidence Analysis**: Compare pLDDT across chains. For protein chains, identify well-folded vs disordered regions. For ligand chains, assess binding pose confidence — low ligand pLDDT suggests uncertain binding mode.
3. **Ligand Binding Assessment** (if ligand present): Evaluate the protein-ligand interface quality using chain_pair_iptm and per-chain pLDDT. Is the ligand binding pose reliable? What does the protein-ligand PDE/PAE tell us about the binding site geometry?
4. **Sample Consistency**: How consistent are the top-ranked diffusion samples across seeds? High variability in ligand poses specifically suggests the binding site is uncertain.
5. **Interface Analysis** (if multi-chain): Interpret the chain_pair_iptm matrix. Which interfaces are confident? Which are uncertain?
6. **Clash Assessment**: If clashes are present, what might cause them and how to address them?
7. **Biological Insights**: What can we infer about binding affinity, specificity, and mechanism from these predictions?
8. **Limitations & Next Steps**: Key caveats and recommended follow-up (experimental validation, docking studies, additional seeds, etc.)

IMPORTANT: Begin your response directly with the analysis. Do NOT include any preamble."""

        logger.info("Generating Gemini expert analysis...")

        content_parts = [prompt]

        if best.get("plots", {}).get("plddt_plot"):
            try:
                img_uri = best["plots"]["plddt_plot"]
                content_parts.append("\n\n**pLDDT Plot (Best Sample):**")
                content_parts.append(
                    types.Part.from_uri(file_uri=img_uri, mime_type="image/png")
                )
            except Exception as e:
                logger.warning(f"Could not include pLDDT plot: {e}")

        if best.get("plots", {}).get("pde_plot"):
            try:
                img_uri = best["plots"]["pde_plot"]
                content_parts.append("\n\n**PDE Plot (Best Sample):**")
                content_parts.append(
                    types.Part.from_uri(file_uri=img_uri, mime_type="image/png")
                )
            except Exception as e:
                logger.warning(f"Could not include PDE plot: {e}")

        if best.get("plots", {}).get("iptm_matrix_plot"):
            try:
                img_uri = best["plots"]["iptm_matrix_plot"]
                content_parts.append("\n\n**ipTM Matrix (Best Sample):**")
                content_parts.append(
                    types.Part.from_uri(file_uri=img_uri, mime_type="image/png")
                )
            except Exception as e:
                logger.warning(f"Could not include ipTM matrix plot: {e}")

        gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
        project_id = os.environ["PROJECT_ID"]
        location = (
            "global"
            if "preview" in gemini_model
            else os.getenv("REGION", "us-central1")
        )

        with genai.Client(
            vertexai=True, project=project_id, location=location
        ) as client:
            response = client.models.generate_content(
                model=gemini_model,
                contents=content_parts,
                config=types.GenerateContentConfig(temperature=1.0),
            )

        disclaimer = (
            "\n\n---\n"
            "*This analysis was generated by AI (Google Gemini) and has not been "
            "peer-reviewed. It is intended as a starting point for interpretation, "
            "not as expert biological advice. Predictions should be validated "
            "experimentally by qualified scientists before use in research or "
            "clinical decisions.*"
        )

        return {
            "status": "success",
            "analysis": response.text + disclaimer,
            "model": gemini_model,
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            + "Z",
            "ai_generated": True,
        }

    except Exception as e:
        logger.error(f"Error generating Gemini analysis: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def consolidate_results(
    job_id: str, analysis_path: str, bucket_name: str, task_count: int
):
    """Consolidate per-sample OF3 analysis results into summary.json."""
    logger.info(f"Consolidating OF3 results from {analysis_path}")
    time.sleep(2)

    parts = analysis_path[5:].split("/", 1)
    prefix = parts[1] if len(parts) > 1 else ""

    if prefix and not prefix.endswith("/"):
        prefix += "/"
    if prefix.startswith("/"):
        prefix = prefix[1:]

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    blobs = bucket.list_blobs(prefix=prefix)
    completed_files = []

    for blob in blobs:
        if "prediction_" in blob.name and blob.name.endswith("_analysis.json"):
            completed_files.append(f"gs://{bucket_name}/{blob.name}")

    logger.info(f"Found {len(completed_files)} analysis files to consolidate")

    all_analyses = []
    for file_path in completed_files:
        try:
            analysis = download_json_from_gcs(file_path)
            all_analyses.append(analysis)
        except Exception as e:
            logger.error(f"Failed to read {file_path}: {e}")

    if not all_analyses:
        logger.error("No analyses found to consolidate")
        return

    all_labels = {}
    job_metadata = {"labels": {}, "parameters": {}}
    try:
        from google.cloud import aiplatform_v1 as vertex_ai

        project_id = os.environ["PROJECT_ID"]
        region = os.getenv("REGION", "us-central1")

        client = vertex_ai.PipelineServiceClient(
            client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
        )

        full_job_id = (
            job_id
            if job_id.startswith("projects/")
            else f"projects/{project_id}/locations/{region}/pipelineJobs/{job_id}"
        )
        request = vertex_ai.GetPipelineJobRequest(name=full_job_id)
        job = client.get_pipeline_job(request=request)

        all_labels = dict(job.labels) if hasattr(job, "labels") and job.labels else {}
        pipeline_parameters = {}
        if hasattr(job, "runtime_config") and hasattr(
            job.runtime_config, "parameter_values"
        ):
            pipeline_parameters = dict(job.runtime_config.parameter_values)

        job_metadata = {
            "display_name": job.display_name if hasattr(job, "display_name") else None,
            "state": job.state.name if hasattr(job, "state") else None,
            "labels": all_labels,
            "parameters": pipeline_parameters,
        }

        if hasattr(job, "create_time") and job.create_time:
            job_metadata["created"] = job.create_time.isoformat()
        if hasattr(job, "start_time") and job.start_time:
            job_metadata["started"] = job.start_time.isoformat()
        if hasattr(job, "end_time") and job.end_time:
            job_metadata["completed"] = job.end_time.isoformat()

        if (
            hasattr(job, "start_time")
            and hasattr(job, "end_time")
            and job.start_time
            and job.end_time
        ):
            duration = job.end_time - job.start_time
            total_seconds = duration.total_seconds()
            job_metadata["duration_seconds"] = total_seconds
            hours = int(total_seconds // 3600)
            minutes = int((total_seconds % 3600) // 60)
            job_metadata["duration_formatted"] = (
                f"{hours}h {minutes}m" if hours > 0 else f"{minutes:.1f}m"
            )
    except Exception as e:
        logger.error(f"Could not get job metadata: {e}", exc_info=True)

    all_analyses.sort(key=lambda x: x.get("ranking_score", 0), reverse=True)

    for i, analysis in enumerate(all_analyses):
        analysis["rank"] = i + 1

    best = all_analyses[0]
    ranking_scores = [a["ranking_score"] for a in all_analyses]
    plddt_means = [a["plddt_mean"] for a in all_analyses]

    fasta_sequence = None
    fasta_header = None
    input_query_json = None
    try:
        if hasattr(job, "runtime_config") and hasattr(
            job.runtime_config, "parameter_values"
        ):
            query_json_path = job.runtime_config.parameter_values.get("query_json_path")
            if query_json_path and isinstance(query_json_path, str):
                query_data = download_json_from_gcs(query_json_path)
                queries = query_data.get("queries", {})
                if queries:
                    query_name = list(queries.keys())[0]
                    fasta_header = query_name
                    chains = queries[query_name].get("chains", [])
                    seqs = [
                        c.get("sequence", "")
                        for c in chains
                        if c.get("molecule_type") == "protein"
                    ]
                    fasta_sequence = "/".join(seqs) if seqs else None

                    clean_queries = {}
                    for qname, qdata in queries.items():
                        clean_chains = []
                        for chain in qdata.get("chains", []):
                            clean_chain = {
                                "molecule_type": chain.get("molecule_type"),
                                "chain_ids": chain.get("chain_ids"),
                            }
                            if chain.get("sequence"):
                                clean_chain["sequence"] = chain["sequence"]
                            if chain.get("smiles"):
                                clean_chain["smiles"] = chain["smiles"]
                            if chain.get("ccd_codes"):
                                clean_chain["ccd_codes"] = chain["ccd_codes"]
                            clean_chains.append(clean_chain)
                        clean_queries[qname] = {"chains": clean_chains}
                    input_query_json = {"queries": clean_queries}
    except Exception as e:
        logger.warning(f"Could not load input sequence: {e}")

    chain_composition = []
    best_chain_info = best.get("chain_info", [])
    total_residues = 0
    for ci in best_chain_info:
        total_residues += ci["residue_count"]
        entry = {
            "chain_id": ci["chain_id"],
            "molecule_type": ci["molecule_type"],
            "residue_count": ci["residue_count"],
            "atom_count": ci["atom_count"],
        }
        if ci["molecule_type"] == "ligand":
            entry["comp_ids"] = ci["comp_ids"]
        chain_composition.append(entry)

    summary = {
        "protein_info": {
            "sequence_length": total_residues
            if total_residues > 0
            else len(best.get("plddt_scores", [])),
            "total_predictions": len(all_analyses),
            "fasta_sequence": fasta_sequence,
            "fasta_header": fasta_header,
            "job_metadata": job_metadata,
            "model_type": "openfold3",
            "chain_composition": chain_composition,
            "input_query_json": input_query_json,
        },
        "quality_metrics": {
            "best_ranking_score": best["ranking_score"],
            "best_ptm": best["ptm"],
            "best_iptm": best["iptm"],
            "best_plddt": best["plddt_mean"],
            "quality_assessment": best["quality_assessment"],
        },
        "statistics": {
            "total_predictions": len(all_analyses),
            "analyzed_successfully": len(all_analyses),
            "ranking_score_stats": {
                "mean": sum(ranking_scores) / len(ranking_scores),
                "min": min(ranking_scores),
                "max": max(ranking_scores),
                "range": max(ranking_scores) - min(ranking_scores),
            },
            "plddt_stats": {
                "mean": sum(plddt_means) / len(plddt_means),
                "min": min(plddt_means),
                "max": max(plddt_means),
                "range": max(plddt_means) - min(plddt_means),
            },
        },
    }

    summary_data = {
        "job_id": job_id,
        "model_type": "openfold3",
        "analyzed_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        + "Z",
        "summary": summary,
        "best_prediction": best,
        "top_predictions": all_analyses[:10],
        "all_predictions_summary": [
            {
                "rank": a["rank"],
                "sample_name": a["sample_name"],
                "ranking_score": a["ranking_score"],
                "ptm": a["ptm"],
                "iptm": a["iptm"],
                "plddt_mean": a["plddt_mean"],
                "plddt_min": a["plddt_min"],
                "quality_assessment": a["quality_assessment"],
                "has_clash": a.get("has_clash", 0),
                "per_chain_plddt": a.get("per_chain_plddt", {}),
                "chain_info": a.get("chain_info", []),
                "cif_uri": a["cif_uri"],
                "plots": a.get("plots", {}),
            }
            for a in all_analyses
        ],
    }

    expert_analysis = generate_gemini_expert_analysis(summary_data)
    summary_data["expert_analysis"] = expert_analysis

    summary_uri = f"{analysis_path}summary.json"
    local_summary = "/tmp/summary.json"

    with open(local_summary, "w") as f:
        json.dump(summary_data, f, indent=2)

    upload_to_gcs(local_summary, summary_uri)
    os.remove(local_summary)

    logger.info(f"Consolidation complete! Summary written to {summary_uri}")


def run_task(
    task_index: int,
    task_count: int,
    task_config: dict,
    bucket_name: str,
    analysis_path: str,
):
    """Execute OpenFold3 predictions analysis task."""
    predictions = task_config.get("predictions", [])
    if task_index >= len(predictions):
        logger.error(f"Task index {task_index} out of range")
        sys.exit(1)

    pred = predictions[task_index]
    job_id = task_config.get("job_id")

    sample_name = pred["sample_name"]
    cif_uri = pred["cif_uri"]
    confidences_uri = pred["confidences_uri"]
    aggregated_uri = pred["aggregated_uri"]
    output_uri = pred["output_uri"]

    logger.info(f"Starting OF3 analysis for sample: {sample_name}")

    try:
        aggregated = download_json_from_gcs(aggregated_uri)
        confidences = download_json_from_gcs(confidences_uri)
        chain_info = []
        try:
            cif_text = download_text_from_gcs(cif_uri)
            chain_info, _ = parse_cif_chains(cif_text, extract_plddt=False)
        except Exception as e:
            logger.warning(f"Could not parse CIF: {e}")

        ptm = aggregated.get("ptm", 0.0)
        iptm = aggregated.get("iptm", 0.0)
        sample_ranking_score = aggregated.get(
            "sample_ranking_score", aggregated.get("ranking_score", 0.0)
        )

        _is_monomer = len(chain_info) <= 1
        if _is_monomer:
            ranking_score = ptm if ptm > 0.0 else sample_ranking_score
        else:
            ranking_score = sample_ranking_score

        avg_plddt = aggregated.get("avg_plddt", 0.0)
        gpde = aggregated.get("gpde", None)
        has_clash = aggregated.get("has_clash", 0)
        disorder = aggregated.get("disorder", 0)
        chain_ptm = aggregated.get("chain_ptm", {})
        chain_pair_iptm = aggregated.get("chain_pair_iptm", {})

        plddt_scores = confidences.get("plddt", [])
        pde_matrix = confidences.get("pde", None)

        if plddt_scores:
            plddt_stats = calculate_plddt_stats(plddt_scores)
        else:
            plddt_stats = {
                "mean": avg_plddt,
                "median": avg_plddt,
                "min": 0.0,
                "max": 100.0,
                "std": 0.0,
                "per_residue": [],
                "distribution": {},
            }

        per_chain_plddt = {}
        if plddt_scores and chain_info:
            per_chain_plddt = calculate_per_chain_plddt(plddt_scores, chain_info)

        quality = get_quality_assessment(plddt_stats["mean"])

        # Generate plots
        plot_files = {}
        output_base = output_uri.rsplit("/", 1)[0]

        if plddt_scores:
            plddt_plot_path = f"/tmp/plddt_plot_{task_index}.png"
            plot_plddt_distribution(plddt_scores, sample_name, plddt_plot_path, chain_info)
            plddt_plot_uri = f"{output_base}/plddt_plot_{task_index}.png"
            upload_to_gcs(plddt_plot_path, plddt_plot_uri)
            plot_files["plddt_plot"] = plddt_plot_uri
            os.remove(plddt_plot_path)

        if pde_matrix:
            pde_plot_path = f"/tmp/pde_plot_{task_index}.png"
            plot_error_matrix(pde_matrix, sample_name, "Predicted Distance Error (Å)", pde_plot_path, chain_info)
            pde_plot_uri = f"{output_base}/pde_plot_{task_index}.png"
            upload_to_gcs(pde_plot_path, pde_plot_uri)
            plot_files["pde_plot"] = pde_plot_uri
            os.remove(pde_plot_path)

        if chain_pair_iptm and len(chain_info) > 1:
            iptm_plot_path = f"/tmp/iptm_matrix_{task_index}.png"
            plot_iptm_matrix(
                chain_pair_iptm,
                sample_name,
                iptm_plot_path,
                chain_ptm,
                chain_info,
            )
            if os.path.exists(iptm_plot_path):
                iptm_plot_uri = f"{output_base}/iptm_matrix_{task_index}.png"
                upload_to_gcs(iptm_plot_path, iptm_plot_uri)
                plot_files["iptm_matrix_plot"] = iptm_plot_uri
                os.remove(iptm_plot_path)

        analysis = {
            "prediction_index": task_index,
            "sample_name": sample_name,
            "rank": task_index + 1,
            "ranking_score": ranking_score,
            "ptm": ptm,
            "iptm": iptm,
            "avg_plddt": avg_plddt,
            "gpde": gpde,
            "has_clash": has_clash,
            "disorder": disorder,
            "chain_ptm": chain_ptm,
            "chain_pair_iptm": chain_pair_iptm,
            "per_chain_plddt": per_chain_plddt,
            "chain_info": chain_info,
            "plddt_mean": plddt_stats["mean"],
            "plddt_median": plddt_stats["median"],
            "plddt_min": plddt_stats["min"],
            "plddt_max": plddt_stats["max"],
            "plddt_std": plddt_stats["std"],
            "plddt_distribution": plddt_stats["distribution"],
            "plddt_scores": plddt_stats["per_residue"],
            "quality_assessment": quality,
            "has_pde": pde_matrix is not None,
            "cif_uri": cif_uri,
            "aggregated_uri": aggregated_uri,
            "confidences_uri": confidences_uri,
            "analyzed_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            + "Z",
            "plots": plot_files,
        }

        local_json = f"/tmp/analysis_{task_index}.json"
        with open(local_json, "w") as f:
            json.dump(analysis, f, indent=2)

        upload_to_gcs(local_json, output_uri)
        os.remove(local_json)

        # Consolidation check
        if task_index == task_count - 1:
            wait_for_sibling_tasks(bucket_name, analysis_path, task_count)
            consolidate_results(job_id, analysis_path, bucket_name, task_count)

    except Exception as e:
        logger.error(f"Error running OF3 analysis task: {e}", exc_info=True)
        raise
