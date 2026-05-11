# Copyright 2025 Google LLC
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

"""Greeting tool for Agentic-Tx therapeutic agent."""


def tx_greeting(query: str) -> dict:
    """Provides a welcome greeting for the therapeutic agent.

    Args:
        query (str): The user's initial query or greeting.

    Returns:
        dict: Status and greeting message.
    """
    return {
        "status": "success",
        "report": (
            "## ✦ ✦ ✦ THERAPEUTIC AGENT ASSISTANT ✦ ✦ ✦\n\n"
            "Hello! I am a therapeutic agent specialized in pharmaceutical research, "
            "drug discovery, and chemical analysis.\n\n"
            "### I CAN HELP YOU WITH:\n\n"
            "- **PREDICTING CLINICAL TOXICITY**\n"
            "  _Example: Is the compound with SMILES CN1C=NC2=C1C(=O)N(C)C(=O)N2C toxic?_\n\n"
            "- **SEARCHING PUBMED LITERATURE**\n"
            "  _Example: Find recent research on CRISPR in cancer therapy_\n\n"
            "- **ANSWERING THERAPEUTIC QUESTIONS**\n"
            "  _Example: What are the main treatments for Alzheimer's disease?_\n\n"
            "- **COMPARING DRUG COMPOUNDS**\n"
            "  _Example: Compare aspirin and ibuprofen for pain management_\n\n"
            "- **EVALUATING DRUG-LIKENESS**\n"
            "  _Example: Analyze SMILES CC(C)CC1=CC=C(C=C1)C(C)C(=O)O for drug potential_\n\n"
            "--- \n\n"
            "How can I assist you with therapeutic drug discovery, evaluation, or chemical analysis today?"
        ),
    }
