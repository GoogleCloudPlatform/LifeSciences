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

"""Atomic file-write helpers.

All writes to the datastore must be atomic (temp + fsync + rename) to
prevent partial reads by concurrent agents.  See datastore-conventions.md.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import scipy.sparse


def atomic_write_json(path: Path, data: Any) -> None:
    """Write *data* as JSON to *path* atomically.

    1. Write to a temporary file in the same directory.
    2. ``fsync`` to ensure data reaches disk.
    3. ``rename`` (atomic on POSIX) to the final path.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.rename(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_npz(path: Path, matrix: scipy.sparse.spmatrix) -> None:
    """Write a scipy sparse matrix to *path* atomically in ``.npz`` format.

    Same temp + fsync + rename pattern as :func:`atomic_write_json`.

    ``scipy.sparse.save_npz`` appends ``.npz`` when the filename doesn't
    already end with that suffix, so we use a ``.npz`` temp suffix and
    clean up accordingly.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".npz")
    os.close(fd)  # save_npz opens the path itself
    try:
        scipy.sparse.save_npz(tmp, matrix)
        # fsync the written file
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.rename(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
