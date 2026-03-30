"""Patch AF2 parsers.py to skip unparseable template descriptions instead of crashing.

Some PDB entries (e.g. 5apu) have description formats that don't match the
expected regex in _parse_hmmsearch_description, causing ValueError and
pipeline failure. This patch wraps the call in try/except so the parser
logs a warning and skips the bad entry instead of crashing.

Applied at container build time via Dockerfile.
"""

import re
import sys

PARSERS_PATH = "/app/alphafold/alphafold/data/parsers.py"

with open(PARSERS_PATH, "r") as f:
    content = f.read()

# Wrap the _parse_hmmsearch_description call in try/except ValueError.
# Original (4-space indent):
#     metadata = _parse_hmmsearch_description(hit_description)
# Patched:
#     try:
#       metadata = _parse_hmmsearch_description(hit_description)
#     except ValueError:
#       logging.warning(f'Skipping unparseable template: {hit_description}')
#       continue
OLD = "    metadata = _parse_hmmsearch_description(hit_description)"
NEW = """\
    try:
      metadata = _parse_hmmsearch_description(hit_description)
    except ValueError:
      logging.warning('Skipping unparseable hmmsearch template description: %s', hit_description)
      continue"""

if OLD not in content:
    print("ERROR: could not find expected line in parsers.py", file=sys.stderr)
    sys.exit(1)

# Ensure logging is imported
if "import logging" not in content:
    content = "import logging\n" + content

content = content.replace(OLD, NEW, 1)

with open(PARSERS_PATH, "w") as f:
    f.write(content)

print(f"Patched {PARSERS_PATH}: unparseable template descriptions are now skipped")
