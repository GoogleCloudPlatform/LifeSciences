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

"""Utility functions that encapsulate AlphaFold inference components.

This is the AUTHORITATIVE copy that gets baked into the Docker container image
(via Dockerfile: ADD src/components/alphafold_utils.py). It runs inside Vertex AI
Pipeline tasks at execution time.

A duplicate copy exists at:
  foldrun-agent/foldrun_app/models/af2/pipeline/components/alphafold_utils.py

That copy is a non-executing reference used only for IDE support and type checking
in the agent codebase. It is never imported at runtime.

TODO: Eliminate the duplicate by having the agent-side code reference this file
directly (e.g. via symlink or shared package). Maintaining two copies leads to
drift — fixes applied to one copy may be missed in the other.
"""

import glob
import logging
import os
import pickle
import shutil
import subprocess
import tempfile
import time
from typing import Dict, List, Mapping, Sequence, Tuple

from alphafold.common import protein
from alphafold.common import residue_constants
from alphafold.data import parsers
from alphafold.data import pipeline
from alphafold.data import pipeline_multimer
from alphafold.data import templates
from alphafold.data.pipeline import make_msa_features
from alphafold.data.pipeline import make_sequence_features
from alphafold.data.tools import hhblits
from alphafold.data.tools import hhsearch
from alphafold.data.tools import hmmsearch
from alphafold.data.tools import jackhmmer
from alphafold.model import config
from alphafold.model import data
from alphafold.model import model
from alphafold.relax import relax


import numpy as np


JACKHMMER_BINARY_PATH = shutil.which('jackhmmer')
HHBLITS_BINARY_PATH = shutil.which('hhblits')
HHSEARCH_BINARY_PATH = shutil.which('hhsearch')
HMMSEARCH_BINARY_PATH = shutil.which('hmmsearch')
KALIGN_BINARY_PATH = shutil.which('kalign')
HMMBUILD_BINARY_PATH = shutil.which('hmmbuild')
MMSEQS2_BINARY_PATH = shutil.which('mmseqs')

MAX_TEMPLATE_HITS = 20


def _load_features(features_path: str) -> Dict[str, str]:
    """Loads pickeled features."""
    with open(features_path, 'rb') as f:
        features = pickle.load(f)
    return features


def _read_msa(msa_path: str, msa_format: str) -> str:
    """Reads and parses an MSA file."""
    if os.path.exists(msa_path):
        with open(msa_path) as f:
            msa = f.read()
        if msa_format == 'sto':
            msa = parsers.parse_stockholm(msa)
        elif msa_format == 'a3m':
            msa = parsers.parse_a3m(msa)
        else:
            raise RuntimeError(f'Unsupported MSA format: {msa_format}')
    return msa


def _read_sequence(sequence_path: str) -> Tuple[str, str, int]:
    """Reads and parses a FASTA sequence file."""
    with open(sequence_path) as f:
        sequence_str = f.read()
    sequences, sequence_descs = parsers.parse_fasta(sequence_str)
    if len(sequences) != 1:
        raise ValueError(
            f'More than one input sequence found in {sequence_path}.')
    return sequences[0], sequence_descs[0], len(sequences[0])


def _read_template_features(template_features_path) -> Dict[str, str]:
    """Reads and unpickles a pdb structure."""
    with open(template_features_path, 'rb') as f:
        template_features = pickle.load(f)
    return template_features


def run_data_pipeline(
    fasta_path: str,
    run_multimer_system: bool,
    uniref90_database_path: str,
    mgnify_database_path: str,
    bfd_database_path: str,
    small_bfd_database_path: str,
    uniref30_database_path: str,
    uniprot_database_path: str,
    pdb70_database_path: str,
    obsolete_pdbs_path: str,
    seqres_database_path: str,
    mmcif_path: str,
    max_template_date: str,
    msa_output_path: str,
    features_output_path: str,
    use_small_bfd: bool,
) -> Dict[str, str]:
    """Runs AlphaFold data pipeline."""
    if run_multimer_system:
        template_searcher = hmmsearch.Hmmsearch(
            binary_path=HMMSEARCH_BINARY_PATH,
            hmmbuild_binary_path=HMMBUILD_BINARY_PATH,
            database_path=seqres_database_path)
        template_featurizer = templates.HmmsearchHitFeaturizer(
            mmcif_dir=mmcif_path,
            max_template_date=max_template_date,
            max_hits=MAX_TEMPLATE_HITS,
            kalign_binary_path=KALIGN_BINARY_PATH,
            release_dates_path=None,
            obsolete_pdbs_path=obsolete_pdbs_path)
    else:
        template_searcher = hhsearch.HHSearch(
            binary_path=HHSEARCH_BINARY_PATH,
            databases=[pdb70_database_path])
        template_featurizer = templates.HhsearchHitFeaturizer(
            mmcif_dir=mmcif_path,
            max_template_date=max_template_date,
            max_hits=MAX_TEMPLATE_HITS,
            kalign_binary_path=KALIGN_BINARY_PATH,
            release_dates_path=None,
            obsolete_pdbs_path=obsolete_pdbs_path)

    monomer_data_pipeline = pipeline.DataPipeline(
        jackhmmer_binary_path=JACKHMMER_BINARY_PATH,
        hhblits_binary_path=HHBLITS_BINARY_PATH,
        uniref90_database_path=uniref90_database_path,
        mgnify_database_path=mgnify_database_path,
        bfd_database_path=bfd_database_path,
        uniref30_database_path=uniref30_database_path,
        small_bfd_database_path=small_bfd_database_path,
        template_searcher=template_searcher,
        template_featurizer=template_featurizer,
        use_small_bfd=use_small_bfd)

    if run_multimer_system:
        data_pipeline = pipeline_multimer.DataPipeline(
            monomer_data_pipeline=monomer_data_pipeline,
            jackhmmer_binary_path=JACKHMMER_BINARY_PATH,
            uniprot_database_path=uniprot_database_path)
    else:
        data_pipeline = monomer_data_pipeline

    feature_dict = data_pipeline.process(
        input_fasta_path=fasta_path,
        msa_output_dir=msa_output_path
    )

    with open(features_output_path, 'wb') as f:
        pickle.dump(feature_dict, f, protocol=4)

    msas_metadata = {}
    paths = glob.glob(os.path.join(msa_output_path, '**'), recursive=True)
    paths = [path for path in paths if os.path.isfile(path)]

    if run_multimer_system:
        folders = [os.path.join(msa_output_path, folder)
                   for folder in os.listdir(msa_output_path)
                   if os.path.isdir(os.path.join(msa_output_path, folder))]
        paths = []
        for folder in folders:
            paths += [os.path.join(folder, file)
                      for file in os.listdir(folder)]
    else:
        paths = [os.path.join(msa_output_path, file)
                 for file in os.listdir(msa_output_path)]
    for file in paths:
        with open(file, 'r') as f:
            artifact = f.read()
        file_format = file.split('.')[-1]
        if file_format == 'sto':
            artifact = parsers.parse_stockholm(artifact)
        elif file_format == 'a3m':
            artifact = parsers.parse_a3m(artifact)
        elif file_format == 'hhr':
            artifact = parsers.parse_hhr(artifact)
        else:
            raise ValueError('Unknown artifact type')
        msas_metadata[os.path.join(
            file.split(os.sep)[-2], file.split(os.sep)[-1])] = len(artifact)

    return feature_dict, msas_metadata


def predict(
    model_features_path: str,
    model_params_path: str,
    model_name: str,
    num_ensemble: int,
    run_multimer_system: bool,
    random_seed: int,
    raw_prediction_path: str,
    unrelaxed_protein_path: str,
) -> Mapping[str, str]:
    """Runs inference on an AlphaFold model."""

    # Ensure random_seed is a plain Python int, not a JAX traced value
    # This fixes: TypeError: PRNG key seed must be an integer; got Traced<ShapedArray(float32[], weak_type=True)>
    random_seed = int(random_seed)

    model_config = config.model_config(model_name)
    if run_multimer_system:
        model_config.model.num_ensemble_eval = num_ensemble
    else:
        model_config.data.eval.num_ensemble_eval = num_ensemble

    model_params = data.get_model_haiku_params(
        model_name=model_name, data_dir=model_params_path)
    model_runner = model.RunModel(model_config, model_params)

    features = _load_features(model_features_path)
    processed_feature_dict = model_runner.process_features(
        raw_features=features,
        random_seed=random_seed)

    prediction_result = model_runner.predict(
        feat=processed_feature_dict,
        random_seed=random_seed)

    with open(raw_prediction_path, 'wb') as f:
        pickle.dump(prediction_result, f, protocol=4)

    plddt = prediction_result['plddt']
    plddt_b_factors = np.repeat(
        plddt[:, None], residue_constants.atom_type_num, axis=-1)
    unrelaxed_structure = protein.from_prediction(
        features=processed_feature_dict,
        result=prediction_result,
        b_factors=plddt_b_factors,
        remove_leading_feature_dimension=not model_runner.multimer_mode)
    unrelaxed_pdbs = protein.to_pdb(unrelaxed_structure)
    with open(unrelaxed_protein_path, 'w') as f:
        f.write(unrelaxed_pdbs)

    return prediction_result


def relax_protein(
    unrelaxed_protein_path: str,
    relaxed_protein_path: str,
    max_iterations: int = 0,
    tolerance: float = 2.39,
    stiffness: float = 10.0,
    exclude_residues: List[str] = [],
    max_outer_iterations: int = 3,
    use_gpu=False
) -> Mapping[str, str]:
    """Runs AMBER relaxation."""

    with open(unrelaxed_protein_path, 'r') as f:
        unrelaxed_protein_pdb = f.read()

    unrelaxed_structure = protein.from_pdb_string(unrelaxed_protein_pdb)
    amber_relaxer = relax.AmberRelaxation(
        max_iterations=max_iterations,
        tolerance=tolerance,
        stiffness=stiffness,
        exclude_residues=exclude_residues,
        max_outer_iterations=max_outer_iterations,
        use_gpu=use_gpu)
    relaxed_protein_pdb, _, _ = amber_relaxer.process(prot=unrelaxed_structure)

    logging.info(f'Saving relaxed protein to {relaxed_protein_path}')
    with open(relaxed_protein_path, 'w') as f:
        f.write(relaxed_protein_pdb)

    return relaxed_protein_pdb


def predict_relax(
    model_features_path: str,
    model_params_path: str,
    prediction_runners: List[Dict],
    num_ensemble: int,
    run_multimer_system: bool,
    raw_prediction_path: str,
    unrelaxed_protein_path: str,
    relaxed_protein_path: str,
    run_relax: bool,
    max_iterations: int = 0,
    tolerance: float = 2.39,
    stiffness: float = 10.0,
    exclude_residues: List[str] = [],
    max_outer_iterations: int = 3,
    use_gpu=True
) -> Mapping[str, str]:
    """Runs predictions and relaxations sequentially on all specified models."""

    model_names = set([runner['model_name'] for runner in prediction_runners])
    runners = {}
    for model_name in model_names:
        model_config = config.model_config(model_name)
        if run_multimer_system:
            model_config.model.num_ensemble_eval = num_ensemble
        else:
            model_config.data.eval.num_ensemble = num_ensemble
        model_params = data.get_model_haiku_params(
            model_name=model_name, data_dir=model_params_path)
        model_runner = model.RunModel(model_config, model_params)
        runners[model_name] = model_runner

    model_runners = {}
    for runner in prediction_runners:
        prediction_name = f'{runner["model_name"]}_pred_{runner["prediction_index"]}'
        model_runners[prediction_name] = (
            runners[runner['model_name']], runner['random_seed'])

    logging.info('Have %d models: %s', len(model_runners),
                 list(model_runners.keys()))

    if run_relax:
        amber_relaxer = relax.AmberRelaxation(
            max_iterations=max_iterations,
            tolerance=tolerance,
            stiffness=stiffness,
            exclude_residues=exclude_residues,
            max_outer_iterations=max_outer_iterations,
            use_gpu=use_gpu)
    else:
        amber_relaxer = None

    # Run the predictions
    feature_dict = _load_features(model_features_path)
    timings = {}
    unrelaxed_pdbs = {}
    relaxed_pdbs = {}
    ranking_confidences = {}
    for model_name, prediction_runner in model_runners.items():
        logging.info('Running prediction %s', model_name)
        t_0 = time.time()
        model_random_seed = prediction_runner[1]
        model_runner = prediction_runner[0]
        processed_feature_dict = model_runner.process_features(
            feature_dict, random_seed=model_random_seed)
        timings[f'process_features_{model_name}'] = time.time() - t_0

        t_0 = time.time()
        prediction_result = model_runner.predict(processed_feature_dict,
                                                 random_seed=model_random_seed)
        t_diff = time.time() - t_0
        timings[f'predict_and_compile_{model_name}'] = t_diff
        logging.info(
            'Total JAX model %s predict time (includes compilation time, see --benchmark): %.1fs',
            model_name, t_diff)

        plddt = prediction_result['plddt']
        ranking_confidences[model_name] = prediction_result['ranking_confidence']

        # Save the model outputs.
        result_output_path = os.path.join(
            raw_prediction_path, f'result_{model_name}.pkl')
        with open(result_output_path, 'wb') as f:
            pickle.dump(prediction_result, f, protocol=4)

        # Add the predicted LDDT in the b-factor column.
        # Note that higher predicted LDDT value means higher model confidence.
        plddt_b_factors = np.repeat(
            plddt[:, None], residue_constants.atom_type_num, axis=-1)
        unrelaxed_protein = protein.from_prediction(
            features=processed_feature_dict,
            result=prediction_result,
            b_factors=plddt_b_factors,
            remove_leading_feature_dimension=not model_runner.multimer_mode)

        unrelaxed_pdbs[model_name] = protein.to_pdb(unrelaxed_protein)
        unrelaxed_pdb_path = os.path.join(
            unrelaxed_protein_path, f'unrelaxed_{model_name}.pdb')
        with open(unrelaxed_pdb_path, 'w') as f:
            f.write(unrelaxed_pdbs[model_name])

        if amber_relaxer:
            # Relax the prediction.
            t_0 = time.time()
            relaxed_pdb_str, _, _ = amber_relaxer.process(
                prot=unrelaxed_protein)
            timings[f'relax_{model_name}'] = time.time() - t_0

            relaxed_pdbs[model_name] = relaxed_pdb_str

            # Save the relaxed PDB.
            relaxed_output_path = os.path.join(
                relaxed_protein_path, f'relaxed_{model_name}.pdb')
            with open(relaxed_output_path, 'w') as f:
                f.write(relaxed_pdb_str)

    logging.info('Final timings  %s ',  timings)

    return ranking_confidences


def aggregate(
    sequence_path: str,
    msa_paths: List[Tuple[str, str]],
    template_features_path: str,
    output_features_path: str
) -> Dict[str, str]:
    """Aggregates MSAs and template features to create model features."""

    # Create sequence features
    seq, seq_desc, num_res = _read_sequence(sequence_path)
    sequence_features = make_sequence_features(
        sequence=seq,
        description=seq_desc,
        num_res=num_res
    )
    # Create MSA features
    msas = []
    for msa_path, msa_format in msa_paths:
        msas.append(_read_msa(msa_path, msa_format))
    if not msas:
        raise RuntimeError('No MSAs passed to the component')
    msa_features = make_msa_features(msas=msas)
    # Create template features
    template_features = _read_template_features(template_features_path)

    model_features = {
        **sequence_features,
        **msa_features,
        **template_features
    }
    with open(output_features_path, 'wb') as f:
        pickle.dump(model_features, f, protocol=4)

    return model_features


def run_jackhmmer(
    input_path: str,
    msa_path: str,
    database_path: str,
    maxseq: int,
    n_cpu: int = 8
):
    """Runs jackhmeer and saves results to files."""

    runner = jackhmmer.Jackhmmer(
        binary_path=JACKHMMER_BINARY_PATH,
        database_path=database_path,
        n_cpu=n_cpu,
    )

    results = runner.query(input_path, maxseq)[0]
    with open(msa_path, 'w') as f:
        f.write(results['sto'])

    return parsers.parse_stockholm(results['sto']), 'sto'


def run_hhblits(
    input_path: str,
    msa_path: str,
    database_paths: List[str],
    n_cpu: int,
    maxseq: int
):
    """Runs hhblits and saves results to a file."""

    runner = hhblits.HHBlits(
        binary_path=HHBLITS_BINARY_PATH,
        databases=database_paths,
        n_cpu=n_cpu,
        maxseq=maxseq,
    )

    results = runner.query(input_path)[0]
    with open(msa_path, 'w') as f:
        f.write(results['a3m'])

    return parsers.parse_a3m(results['a3m']), 'a3m'


def run_hhsearch(
    sequence_path: str,
    msa_path: str,
    msa_data_format: str,
    template_hits_path: str,
    template_features_path: str,
    template_dbs_paths: List[str],
    mmcif_path: str,
    obsolete_path: str,
    max_template_date: str,
    max_template_hits: int,
    maxseq: int
):
    """Runs hhsearch and saves results to a file."""

    if msa_data_format != 'sto' and msa_data_format != 'a3m':
        raise ValueError(f'Unsupported MSA format: {msa_data_format}')

    sequence, _, _ = _read_sequence(sequence_path)

    template_searcher = hhsearch.HHSearch(
        binary_path=HHSEARCH_BINARY_PATH,
        databases=template_dbs_paths,
        maxseq=maxseq
    )

    template_featurizer = templates.HhsearchHitFeaturizer(
        mmcif_dir=mmcif_path,
        max_template_date=max_template_date,
        max_hits=max_template_hits,
        kalign_binary_path=KALIGN_BINARY_PATH,
        obsolete_pdbs_path=obsolete_path,
        release_dates_path=None,
    )

    with open(msa_path) as f:
        msa_str = f.read()

    if msa_data_format == 'sto':
        msa_for_templates = parsers.deduplicate_stockholm_msa(msa_str)
        msa_for_templates = parsers.remove_empty_columns_from_stockholm_msa(
            msa_for_templates)
        msa_for_templates = parsers.convert_stockholm_to_a3m(msa_for_templates)

    hhr_str = template_searcher.query(msa_for_templates)
    with open(template_hits_path, 'w') as f:
        f.write(hhr_str)

    template_hits = template_searcher.get_template_hits(
        output_string=hhr_str, input_sequence=sequence)
    templates_result = template_featurizer.get_templates(
        query_sequence=sequence,
        hits=template_hits)
    with open(template_features_path, 'wb') as f:
        pickle.dump(templates_result.features, f, protocol=4)

    return parsers.parse_hhr(hhr_str), templates_result.features


def run_hmmsearch(
    sequence_path: str,
    msa_path: str,
    msa_data_format: str,
    template_hits_path: str,
    template_features_path: str,
    template_db_path: str,
    mmcif_path: str,
    obsolete_path: str,
    max_template_date,
    max_template_hits
):
    """Runs hhsearch and saves results to a file."""

    if msa_data_format != 'sto':
        raise ValueError(f'Unsupported MSA format: {msa_data_format}')

    sequence, _, _ = _read_sequence(sequence_path)

    template_searcher = hmmsearch.Hmmsearch(
        binary_path=HMMSEARCH_BINARY_PATH,
        hmmbuild_binary_path=HMMBUILD_BINARY_PATH,
        database_path=template_db_path
    )

    template_featurizer = templates.HmmsearchHitFeaturizer(
        mmcif_dir=mmcif_path,
        max_template_date=max_template_date,
        max_hits=max_template_hits,
        kalign_binary_path=KALIGN_BINARY_PATH,
        obsolete_pdbs_path=obsolete_path,
        release_dates_path=None
    )

    with open(msa_path) as f:
        msa_str = f.read()

    msa_for_templates = parsers.deduplicate_stockholm_msa(msa_str)
    msa_for_templates = parsers.remove_empty_columns_from_stockholm_msa(
        msa_for_templates)

    sto_str = template_searcher.query(msa_for_templates)
    with open(template_hits_path, 'w') as f:
        f.write(sto_str)

    template_hits = template_searcher.get_template_hits(
        output_string=sto_str, input_sequence=sequence)
    templates_result = template_featurizer.get_templates(
        query_sequence=sequence,
        hits=template_hits)

    with open(template_features_path, 'wb') as f:
        pickle.dump(templates_result.features, f, protocol=4)

    return parsers.parse_stockholm(template_hits), templates_result.features


def run_mmseqs2_search(
    query_fasta: str,
    target_db: str,
    output_a3m: str,
    tmp_dir: str,
    gpu: bool = True,
    max_seqs: int = 10000,
    split: int = 0,
) -> int:
    """Run MMseqs2 search against a single database and produce A3M output.

    Args:
        query_fasta: Path to query FASTA file.
        target_db: Path to MMseqs2 target database (created by mmseqs createdb).
        output_a3m: Path to write the output A3M alignment.
        tmp_dir: Temporary directory for intermediate files.
        gpu: Whether to enable GPU acceleration.
        max_seqs: Maximum number of sequences to return.
        split: Number of splits for search-time memory management. Controls how
            many chunks the index is divided into so each chunk fits in GPU VRAM.
            0 means auto (MMseqs2 decides based on available memory).

    Returns:
        Number of sequences in the output alignment.
    """
    query_db = os.path.join(tmp_dir, 'queryDB')
    result_db = os.path.join(tmp_dir, 'resultDB')

    # Create query database from FASTA
    subprocess.run(
        [MMSEQS2_BINARY_PATH, 'createdb', query_fasta, query_db],
        check=True,
    )

    # Search target database
    search_cmd = [
        MMSEQS2_BINARY_PATH, 'search',
        query_db, target_db, result_db, tmp_dir,
        '--max-seqs', str(max_seqs),
        '-s', '7.5',
    ]
    if gpu:
        search_cmd.extend(['--gpu', '1'])
    if split > 0:
        search_cmd.extend(['--split', str(split)])
    subprocess.run(search_cmd, check=True)

    # Convert results to A3M
    subprocess.run(
        [MMSEQS2_BINARY_PATH, 'result2msa',
         query_db, target_db, result_db, output_a3m,
         '--msa-format-mode', '6'],
        check=True,
    )

    # Count sequences in output
    n_seqs = 0
    with open(output_a3m) as f:
        for line in f:
            if line.startswith('>'):
                n_seqs += 1
    return n_seqs


def run_mmseqs2_data_pipeline(
    fasta_path: str,
    run_multimer_system: bool,
    uniref90_mmseqs_path: str,
    mgnify_mmseqs_path: str,
    small_bfd_mmseqs_path: str,
    uniref90_database_path: str,
    mgnify_database_path: str,
    uniprot_database_path: str,
    pdb70_database_path: str,
    obsolete_pdbs_path: str,
    seqres_database_path: str,
    mmcif_path: str,
    max_template_date: str,
    msa_output_path: str,
    features_output_path: str,
) -> Tuple[Dict, Dict[str, int]]:
    """MSA generation using MMseqs2-GPU, then standard AF2 feature generation.

    Replaces JackHMMER/HHblits MSA search with GPU-accelerated MMseqs2 for
    the three FASTA sequence databases (uniref90, mgnify, small_bfd).
    Template search still uses HHsearch/hmmsearch (CPU, fast).

    Only valid when use_small_bfd=True (all databases are FASTA format).
    """
    logging.info('Starting MMseqs2-GPU data pipeline')
    os.makedirs(msa_output_path, exist_ok=True)

    # Detect GPU availability
    gpu_available = _check_gpu_available()
    logging.info(f'GPU available for MMseqs2: {gpu_available}')

    # Parse input sequences (handles both monomer and multimer FASTA)
    with open(fasta_path) as f:
        fasta_str = f.read()
    sequences, descriptions = parsers.parse_fasta(fasta_str)

    if run_multimer_system:
        # For multimer: process each chain separately, then combine
        chain_features = []
        for chain_idx, (seq, desc) in enumerate(zip(sequences, descriptions)):
            chain_fasta = os.path.join(msa_output_path, f'chain_{chain_idx}.fasta')
            with open(chain_fasta, 'w') as f:
                f.write(f'>{desc}\n{seq}\n')

            chain_msa_dir = os.path.join(msa_output_path, desc)
            os.makedirs(chain_msa_dir, exist_ok=True)

            chain_feat = _run_mmseqs2_single_chain(
                fasta_path=chain_fasta,
                sequence=seq,
                description=desc,
                msa_output_dir=chain_msa_dir,
                uniref90_mmseqs_path=uniref90_mmseqs_path,
                mgnify_mmseqs_path=mgnify_mmseqs_path,
                small_bfd_mmseqs_path=small_bfd_mmseqs_path,
                pdb70_database_path=pdb70_database_path,
                seqres_database_path=seqres_database_path,
                mmcif_path=mmcif_path,
                obsolete_pdbs_path=obsolete_pdbs_path,
                max_template_date=max_template_date,
                run_multimer_system=True,
                gpu=gpu_available,
            )
            chain_features.append(chain_feat)

        # Run uniprot search for multimer pairing
        all_chain_features = {}
        for chain_idx, (seq, desc) in enumerate(zip(sequences, descriptions)):
            chain_fasta = os.path.join(msa_output_path, f'chain_{chain_idx}.fasta')
            uniprot_runner = jackhmmer.Jackhmmer(
                binary_path=JACKHMMER_BINARY_PATH,
                database_path=uniprot_database_path,
            )
            uniprot_results = uniprot_runner.query(chain_fasta)[0]
            uniprot_msa = parsers.parse_stockholm(uniprot_results['sto'])
            chain_features[chain_idx].update(
                pipeline_multimer.DataPipeline._all_seq_msa_features(
                    chain_fasta, uniprot_msa))
            all_chain_features[desc] = chain_features[chain_idx]

        # Pair and merge features
        feature_dict = pipeline_multimer.DataPipeline._pair_and_merge(
            all_chain_features=all_chain_features)
        feature_dict = pipeline_multimer.DataPipeline._pad_features(
            feature_dict)
    else:
        # Monomer: single chain
        if len(sequences) != 1:
            raise ValueError(
                f'Expected 1 sequence for monomer, got {len(sequences)}')

        feature_dict = _run_mmseqs2_single_chain(
            fasta_path=fasta_path,
            sequence=sequences[0],
            description=descriptions[0],
            msa_output_dir=msa_output_path,
            uniref90_mmseqs_path=uniref90_mmseqs_path,
            mgnify_mmseqs_path=mgnify_mmseqs_path,
            small_bfd_mmseqs_path=small_bfd_mmseqs_path,
            pdb70_database_path=pdb70_database_path,
            seqres_database_path=seqres_database_path,
            mmcif_path=mmcif_path,
            obsolete_pdbs_path=obsolete_pdbs_path,
            max_template_date=max_template_date,
            run_multimer_system=False,
            gpu=gpu_available,
        )

    # Save features
    with open(features_output_path, 'wb') as f:
        pickle.dump(feature_dict, f, protocol=4)

    # Collect MSA metadata
    msas_metadata = {}
    if run_multimer_system:
        folders = [os.path.join(msa_output_path, d)
                   for d in os.listdir(msa_output_path)
                   if os.path.isdir(os.path.join(msa_output_path, d))]
        paths = []
        for folder in folders:
            paths += [os.path.join(folder, f)
                      for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
    else:
        paths = [os.path.join(msa_output_path, f)
                 for f in os.listdir(msa_output_path)
                 if os.path.isfile(os.path.join(msa_output_path, f))]

    for filepath in paths:
        ext = filepath.split('.')[-1]
        try:
            with open(filepath, 'r') as f:
                content = f.read()
            if ext == 'sto':
                parsed = parsers.parse_stockholm(content)
            elif ext == 'a3m':
                parsed = parsers.parse_a3m(content)
            elif ext == 'hhr':
                parsed = parsers.parse_hhr(content)
            else:
                continue
            key = os.path.join(
                filepath.split(os.sep)[-2], filepath.split(os.sep)[-1])
            msas_metadata[key] = len(parsed)
        except Exception:
            continue

    return feature_dict, msas_metadata


def _check_gpu_available() -> bool:
    """Check if a CUDA GPU is available for MMseqs2."""
    try:
        result = subprocess.run(
            ['nvidia-smi'], capture_output=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_mmseqs2_single_chain(
    fasta_path: str,
    sequence: str,
    description: str,
    msa_output_dir: str,
    uniref90_mmseqs_path: str,
    mgnify_mmseqs_path: str,
    small_bfd_mmseqs_path: str,
    pdb70_database_path: str,
    seqres_database_path: str,
    mmcif_path: str,
    obsolete_pdbs_path: str,
    max_template_date: str,
    run_multimer_system: bool,
    gpu: bool = True,
) -> Dict:
    """Run MMseqs2 MSA search + template search for a single chain.

    Returns AlphaFold feature dict for this chain.
    """
    num_res = len(sequence)
    t0 = time.time()

    # 1. Run MMseqs2 search on each database
    # Split values sized so each chunk fits in L4 24 GB VRAM:
    #   uniref90: ~165 GB index / 8 splits = ~21 GB/chunk
    #   mgnify:   ~259 GB index / 12 splits = ~22 GB/chunk
    #   small_bfd: ~36 GB index / 2 splits = ~18 GB/chunk
    db_searches = [
        ('uniref90', uniref90_mmseqs_path, 10000, 8),
        ('mgnify', mgnify_mmseqs_path, 501, 12),
        ('small_bfd', small_bfd_mmseqs_path, 10000, 2),
    ]

    msas = []
    for db_name, db_path, max_seqs, split in db_searches:
        a3m_path = os.path.join(msa_output_dir, f'{db_name}.a3m')
        tmp_dir = tempfile.mkdtemp(prefix=f'mmseqs2_{db_name}_')
        try:
            logging.info(f'Running MMseqs2 search against {db_name} (gpu={gpu}, split={split})')
            t_search = time.time()
            n_seqs = run_mmseqs2_search(
                query_fasta=fasta_path,
                target_db=db_path,
                output_a3m=a3m_path,
                tmp_dir=tmp_dir,
                gpu=gpu,
                max_seqs=max_seqs,
                split=split,
            )
            logging.info(
                f'MMseqs2 {db_name}: {n_seqs} sequences in {time.time() - t_search:.1f}s')
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Parse the A3M output, stripping null bytes that MMseqs2
        # result2msa can emit for padded sequences.
        with open(a3m_path) as f:
            a3m_str = f.read().replace('\x00', '')
        msa = parsers.parse_a3m(a3m_str)
        msas.append(msa)

    logging.info(f'All MMseqs2 searches done in {time.time() - t0:.1f}s')

    # 2. Build sequence features
    sequence_features = make_sequence_features(
        sequence=sequence,
        description=description,
        num_res=num_res,
    )

    # 3. Build MSA features from all MSAs
    msa_features = make_msa_features(msas=msas)

    # 4. Template search (still CPU — HHsearch for monomer, hmmsearch for multimer)
    # Use the uniref90 A3M as input for template search
    uniref90_a3m_path = os.path.join(msa_output_dir, 'uniref90.a3m')

    if run_multimer_system:
        template_searcher = hmmsearch.Hmmsearch(
            binary_path=HMMSEARCH_BINARY_PATH,
            hmmbuild_binary_path=HMMBUILD_BINARY_PATH,
            database_path=seqres_database_path,
        )
        template_featurizer = templates.HmmsearchHitFeaturizer(
            mmcif_dir=mmcif_path,
            max_template_date=max_template_date,
            max_hits=MAX_TEMPLATE_HITS,
            kalign_binary_path=KALIGN_BINARY_PATH,
            release_dates_path=None,
            obsolete_pdbs_path=obsolete_pdbs_path,
        )
        # hmmsearch needs Stockholm format — convert A3M to STO
        with open(uniref90_a3m_path) as f:
            a3m_str = f.read()
        # Write as STO for template search compatibility
        sto_path = os.path.join(msa_output_dir, 'uniref90.sto')
        _write_a3m_as_sto(a3m_str, sto_path)
        with open(sto_path) as f:
            sto_str = f.read()
        msa_for_templates = parsers.deduplicate_stockholm_msa(sto_str)
        msa_for_templates = parsers.remove_empty_columns_from_stockholm_msa(
            msa_for_templates)
        hits_str = template_searcher.query(msa_for_templates)
    else:
        template_searcher = hhsearch.HHSearch(
            binary_path=HHSEARCH_BINARY_PATH,
            databases=[pdb70_database_path],
        )
        template_featurizer = templates.HhsearchHitFeaturizer(
            mmcif_dir=mmcif_path,
            max_template_date=max_template_date,
            max_hits=MAX_TEMPLATE_HITS,
            kalign_binary_path=KALIGN_BINARY_PATH,
            release_dates_path=None,
            obsolete_pdbs_path=obsolete_pdbs_path,
        )
        # HHsearch uses A3M format directly
        with open(uniref90_a3m_path) as f:
            msa_for_templates = f.read()
        hits_str = template_searcher.query(msa_for_templates)

    # Save template hits
    hits_ext = 'sto' if run_multimer_system else 'hhr'
    hits_path = os.path.join(msa_output_dir, f'pdb_hits.{hits_ext}')
    with open(hits_path, 'w') as f:
        f.write(hits_str)

    template_hits = template_searcher.get_template_hits(
        output_string=hits_str, input_sequence=sequence)
    templates_result = template_featurizer.get_templates(
        query_sequence=sequence, hits=template_hits)

    logging.info(f'Template search found {len(template_hits)} hits')

    # 5. Combine all features
    feature_dict = {
        **sequence_features,
        **msa_features,
        **templates_result.features,
    }

    return feature_dict


def _write_a3m_as_sto(a3m_str: str, sto_path: str):
    """Convert A3M alignment to minimal Stockholm format for template search."""
    lines = a3m_str.strip().split('\n')
    names = []
    seqs = []
    current_name = None
    current_seq = []
    for line in lines:
        if line.startswith('>'):
            if current_name is not None:
                names.append(current_name)
                seqs.append(''.join(current_seq))
            current_name = line[1:].split()[0]
            current_seq = []
        else:
            # Remove lowercase insertions for Stockholm format
            current_seq.append(''.join(c for c in line if not c.islower()))
    if current_name is not None:
        names.append(current_name)
        seqs.append(''.join(current_seq))

    with open(sto_path, 'w') as f:
        f.write('# STOCKHOLM 1.0\n')
        for name, seq in zip(names, seqs):
            f.write(f'{name}\t{seq}\n')
        f.write('//\n')
