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

// hypex is a CLI for managing the hypothesis-explorer datastore.
//
// Usage:
//
//	hypex <verb> [flags] [args]
//
// Verbs: init-run, add-hypothesis, next-id, validate, list, status, report
//
// See 'hypex --help' for the full command tree.
package main

import "github.com/scion-frontiers/hypex/hypothesis-explorer/tools/hypex/cmd"

func main() {
	cmd.Execute()
}
