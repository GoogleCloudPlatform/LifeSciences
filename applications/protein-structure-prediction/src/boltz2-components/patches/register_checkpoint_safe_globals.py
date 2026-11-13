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

"""Register OmegaConf safe globals for PyTorch `weights_only=True` checkpoint loading.

Upstream `boltz==2.2.1` loads `boltz2_conf.ckpt` and `boltz2_aff.ckpt` via
`load_from_checkpoint(...)` without passing `weights_only=False` or registering
the OmegaConf configuration classes stored in the checkpoint hyperparameters.
With PyTorch >=2.6 and PyTorch Lightning >=2.6 (`weights_only=True` by default),
unpickling `DictConfig` / `ListConfig` (which store `ContainerMetadata` class
references such as `dict` and `list`) raises `WeightsUnpickler` errors unless
those benign configuration types are allowlisted via
`torch.serialization.add_safe_globals`.

Applied at container build time via Dockerfile so `weights_only=True` remains
strictly enforced while allowing Boltz-2 checkpoints to load cleanly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MARKER = "# FoldRun: allowlist OmegaConf config nodes for torch weights_only=True"

SNIPPET = f"""
{MARKER}
def _foldrun_register_checkpoint_safe_globals() -> None:
    import collections
    import pathlib
    import typing
    import torch.serialization
    from omegaconf.base import Box, Container, ContainerMetadata, Metadata, Node, SCMode
    from omegaconf.dictconfig import DictConfig
    from omegaconf.listconfig import ListConfig
    from omegaconf.nodes import (
        AnyNode,
        BooleanNode,
        BytesNode,
        EnumNode,
        FloatNode,
        IntegerNode,
        PathNode,
        StringNode,
        ValueNode,
    )

    torch.serialization.add_safe_globals(
        [
            dict,
            list,
            tuple,
            set,
            int,
            float,
            bool,
            str,
            bytes,
            collections.OrderedDict,
            collections.defaultdict,
            pathlib.PosixPath,
            typing.Any,
            typing.Union,
            Box,
            Container,
            ContainerMetadata,
            Metadata,
            Node,
            SCMode,
            DictConfig,
            ListConfig,
            AnyNode,
            BooleanNode,
            BytesNode,
            EnumNode,
            FloatNode,
            IntegerNode,
            PathNode,
            StringNode,
            ValueNode,
        ]
    )


_foldrun_register_checkpoint_safe_globals()
"""


def main() -> int:
    spec = importlib.util.find_spec("boltz")
    if spec is None or spec.origin is None:
        print("ERROR: could not locate installed 'boltz' package", file=sys.stderr)
        return 1

    init_path = Path(spec.origin)
    content = init_path.read_text(encoding="utf-8")
    if MARKER not in content:
        init_path.write_text(content.rstrip() + "\n" + SNIPPET, encoding="utf-8")

    # Verify import and that OmegaConf nodes are registered in PyTorch safe globals
    import boltz  # noqa: F401
    import torch.serialization
    from omegaconf.base import ContainerMetadata
    from omegaconf.dictconfig import DictConfig
    from omegaconf.listconfig import ListConfig

    safe_globals = set(torch.serialization.get_safe_globals())
    for expected_cls in (DictConfig, ListConfig, ContainerMetadata):
        assert expected_cls in safe_globals, f"{expected_cls} not in safe globals"

    print(f"Patched and verified {init_path} for weights_only=True checkpoint loading")
    return 0


if __name__ == "__main__":
    sys.exit(main())
