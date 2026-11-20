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
"""KFP component that runs MSA search for BOLTZ2."""

import config as config

from foldrun_app.core.msa_pipeline import create_msa_pipeline_component

msa_pipeline_boltz2 = create_msa_pipeline_component(
    base_image=config.BOLTZ2_COMPONENTS_IMAGE,
    install_kfp_package=False,
)
