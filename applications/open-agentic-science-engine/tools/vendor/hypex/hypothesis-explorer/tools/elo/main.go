// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// elo is a CLI for computing Elo ratings and generating tournament pairings
// in the hypothesis-explorer system.
//
// Usage:
//
//	elo <verb> [flags] [args]
//
// Verbs: recompute, pair, standings
//
// See 'elo --help' for the full command tree.
package main

import "github.com/scion-frontiers/hypex/hypothesis-explorer/tools/elo/cmd"

func main() {
	cmd.Execute()
}
