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

// Package schema provides JSON Schema validation for hypothesis-explorer
// artifacts. It uses the santhosh-tekuri/jsonschema library for draft 2020-12
// compliant validation.
package schema

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

// ArtifactSchemaMap maps filename prefixes to their schema filenames.
var ArtifactSchemaMap = map[string]string{
	"H-":     "hypothesis.schema.json",
	"M-":     "match.schema.json",
	"epoch-": "ratings.schema.json",
}

// reviewPattern identifies review files by the H-XXXX.R-NN pattern.
const reviewPattern = ".R-"

// SchemaForFile returns the schema filename for a given artifact file.
func SchemaForFile(filename string) (string, error) {
	cleaned := filepath.Clean(filename)
	base := filepath.Base(cleaned)
	parentDir := filepath.Base(filepath.Dir(cleaned))

	// Check for citation verification manifests: either stored in a directory
	// named "citations" (e.g. <run-dir>/citations/H-0001.json) or named
	// "citations.json" (e.g. <run-dir>/report/citations.json).
	if parentDir == "citations" || base == "citations.json" {
		return "citation-manifest.schema.json", nil
	}

	// Check for review pattern first (H-XXXX.R-NN.json).
	if strings.Contains(base, reviewPattern) {
		return "review.schema.json", nil
	}

	for prefix, schema := range ArtifactSchemaMap {
		if strings.HasPrefix(base, prefix) {
			return schema, nil
		}
	}

	return "", fmt.Errorf("cannot determine schema for file %q", base)
}

// ValidateFile validates a JSON file against its matching schema.
func ValidateFile(schemaDir, filePath string) error {
	schemaFile, err := SchemaForFile(filePath)
	if err != nil {
		return err
	}

	return ValidateFileAgainstSchema(schemaDir, schemaFile, filePath)
}

// ValidateFileAgainstSchema validates a JSON file against a specific schema.
func ValidateFileAgainstSchema(schemaDir, schemaFile, filePath string) error {
	schemaPath := filepath.Join(schemaDir, schemaFile)

	c := jsonschema.NewCompiler()
	sch, err := c.Compile(schemaPath)
	if err != nil {
		return fmt.Errorf("compiling schema %s: %w", schemaPath, err)
	}

	data, err := os.ReadFile(filePath)
	if err != nil {
		return fmt.Errorf("reading file %s: %w", filePath, err)
	}

	var v any
	if err := json.Unmarshal(data, &v); err != nil {
		return fmt.Errorf("parsing JSON from %s: %w", filePath, err)
	}

	if err := sch.Validate(v); err != nil {
		return fmt.Errorf("validation failed for %s against %s:\n%s", filePath, schemaFile, err)
	}

	return nil
}

// ValidateBytes validates raw JSON bytes against a specific schema.
func ValidateBytes(schemaDir, schemaFile string, data []byte) error {
	schemaPath := filepath.Join(schemaDir, schemaFile)

	c := jsonschema.NewCompiler()
	sch, err := c.Compile(schemaPath)
	if err != nil {
		return fmt.Errorf("compiling schema %s: %w", schemaPath, err)
	}

	var v any
	if err := json.Unmarshal(data, &v); err != nil {
		return fmt.Errorf("parsing JSON: %w", err)
	}

	if err := sch.Validate(v); err != nil {
		return fmt.Errorf("validation failed against %s:\n%s", schemaFile, err)
	}

	return nil
}
