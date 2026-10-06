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

"""Tests for verb aliases (issue #92).

Every retrieval command group accepts both ``fetch`` and ``search`` as
verbs for discoverability.  The primary verb depends on the data source:

  * ``search`` is primary when the command queries and returns a result
    set (dice, allen, trials, patent).
  * ``fetch`` is primary when the command retrieves the record for a
    known identifier (genetics, expression, alphafold).

The alternative verb is registered as a Click alias via
``group.add_command(cmd, "alias")``.  These tests verify that:

  1. Each alias resolves to a command (not "no such command").
  2. The alias points to the same underlying callback as the primary.
  3. ``--help`` works on each alias.
"""

from __future__ import annotations

import unittest

from click.testing import CliRunner

from dde.commands.allen import allen
from dde.commands.allen import search_cmd as allen_search
from dde.commands.alphafold import alphafold
from dde.commands.alphafold import fetch as alphafold_fetch
from dde.commands.dice import dice
from dde.commands.dice import search_cmd as dice_search
from dde.commands.expression import expression
from dde.commands.expression import fetch_cmd as expression_fetch
from dde.commands.genetics import fetch_cmd as genetics_fetch
from dde.commands.genetics import genetics
from dde.commands.patent import patent
from dde.commands.patent import search_cmd as patent_search
from dde.commands.trials import search_cmd as trials_search
from dde.commands.trials import trials

# (group, primary_name, alias_name, primary_function)
ALIAS_TABLE = [
    # search-primary groups: ``fetch`` is the alias
    (dice, "search", "fetch", dice_search),
    (allen, "search", "fetch", allen_search),
    (trials, "search", "fetch", trials_search),
    (patent, "search", "fetch", patent_search),
    # fetch-primary groups: ``search`` is the alias
    (genetics, "fetch", "search", genetics_fetch),
    (expression, "fetch", "search", expression_fetch),
    (alphafold, "fetch", "search", alphafold_fetch),
]


class TestAliasResolution(unittest.TestCase):
    """Each alias resolves to a valid command in its group."""

    def test_alias_registered(self):
        for group, _primary, alias, _ in ALIAS_TABLE:
            with self.subTest(group=group.name, alias=alias):
                commands = group.list_commands(ctx=None)
                self.assertIn(
                    alias,
                    commands,
                    f"{group.name} does not list '{alias}' as a command",
                )

    def test_primary_still_registered(self):
        for group, primary, _alias, _ in ALIAS_TABLE:
            with self.subTest(group=group.name, primary=primary):
                commands = group.list_commands(ctx=None)
                self.assertIn(
                    primary,
                    commands,
                    f"{group.name} lost its primary verb '{primary}'",
                )


class TestAliasCallback(unittest.TestCase):
    """The alias points to the same Click callback as the primary."""

    def test_same_callback(self):
        for group, primary, alias, _primary_fn in ALIAS_TABLE:
            with self.subTest(group=group.name, alias=alias):
                primary_cmd = group.get_command(ctx=None, cmd_name=primary)
                alias_cmd = group.get_command(ctx=None, cmd_name=alias)
                self.assertIsNotNone(primary_cmd)
                self.assertIsNotNone(alias_cmd)
                self.assertIs(
                    primary_cmd.callback,
                    alias_cmd.callback,
                    f"{group.name}: '{alias}' callback differs from '{primary}'",
                )


class TestAliasHelp(unittest.TestCase):
    """``--help`` works on each alias without error."""

    def test_help_exits_zero(self):
        runner = CliRunner()
        for group, _, alias, _ in ALIAS_TABLE:
            with self.subTest(group=group.name, alias=alias):
                result = runner.invoke(group, [alias, "--help"])
                self.assertEqual(
                    result.exit_code,
                    0,
                    f"{group.name} {alias} --help failed:\n{result.output}",
                )

    def test_help_shows_usage(self):
        runner = CliRunner()
        for group, _, alias, _ in ALIAS_TABLE:
            with self.subTest(group=group.name, alias=alias):
                result = runner.invoke(group, [alias, "--help"])
                self.assertIn("Usage:", result.output)


if __name__ == "__main__":
    unittest.main()
