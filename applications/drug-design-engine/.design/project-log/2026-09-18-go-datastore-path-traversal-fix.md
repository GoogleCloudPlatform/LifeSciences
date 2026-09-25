# Fix Path Traversal in Go Datastore (#234)

**Date:** 2026-09-18
**Issue:** #234
**Branch:** scion/dev-go-datastore

## Summary

Fixed a path traversal vulnerability in the `datastore` package where an
unsanitized `runID` parameter could escape the base directory via `../`
sequences or absolute paths.

## Problem

`RunPath()` in `datastore.go` directly joined `baseDir` and `runID` using
`filepath.Join()` without any validation. A malicious `runID` containing `../`
could escape the intended base directory. `InitRun()` then used the escaped
path to create directories and write files, allowing arbitrary filesystem
writes.

## Fix

1. **Added `ConfinePath` function** — validates that the joined path stays
   within the base directory. Rejects absolute untrusted paths outright, then
   uses `filepath.Clean` + `strings.HasPrefix` with path-separator suffixing
   to prevent prefix false positives (e.g., `/tmp/runs` vs `/tmp/runs-evil`).

2. **Updated `RunPath` signature** from `(string)` to `(string, error)` to
   use `ConfinePath` internally.

3. **Updated all callers** of `RunPath` to handle the error return:
   - `InitRun` (internal)
   - `cmd/add_review.go`
   - `cmd/add_hypothesis.go`
   - `cmd/set_status.go`
   - `cmd/next_id.go`
   - `cmd/add_match.go`

4. **Added tests** for `ConfinePath` covering valid paths, `../` traversal,
   and absolute path injection. Added `TestInitRun_TraversalBlocked` to verify
   end-to-end rejection. Updated existing `TestRunPath` for the new signature.

## Verification

- All 20 tests pass (`go test -v ./...`)
- Full package builds cleanly (`go build ./...`)

## Notes

- `filepath.Clean` normalizes `..` but does NOT resolve symlinks; acceptable
  here since we're validating a string ID, not following filesystem symlinks.
- Go's `filepath.Join` treats an absolute second argument differently than
  some languages (it concatenates rather than replacing the base), so an
  explicit `filepath.IsAbs` check was added for defense in depth.
