---
name: storage-management
description: Audits Cloud Storage buckets for orphaned pipeline outputs and deletes obsolete job artifacts using a two-step preview-and-confirm workflow. Use when the user asks to check storage usage, find orphaned files, or clean up GCS buckets.
metadata:
  adk_additional_tools:
    - find_orphaned_gcs_files
    - cleanup_gcs_files
---

# Storage Management

## Find Orphaned Files (`find_orphaned_gcs_files`)
- Scans the GCS bucket against active Agent Platform jobs and returns `orphaned_pipeline_dirs` and `orphaned_fasta_files` with sizes.
- **FASTA Retention Rule**: NEVER suggest deleting `orphaned_fasta_files` unless the user explicitly asks to delete FASTA files (FASTA inputs are small and often reused). Only recommend cleaning up `orphaned_pipeline_dirs` by default.

## Two-Step Cleanup Workflow (`cleanup_gcs_files`) — HITL Confirmation Required
**CRITICAL**: Always execute a two-step **Preview (`search_only=True`) → User Confirmation → Delete (`confirm_delete=True`)** workflow. Warn that GCS deletion is permanent.

```
Orphan Cleanup Checklist:
- [ ] Step 1: Call `find_orphaned_gcs_files()` to list `orphaned_pipeline_dirs` and sizes
- [ ] Step 2: Call `cleanup_gcs_files(gcs_paths=[...], search_only=True, include_fasta=False)` to preview exact files and total size
- [ ] Step 3: Present the preview list/size to the user and wait for explicit confirmation
- [ ] Step 4: Call `cleanup_gcs_files(gcs_paths=[...], search_only=False, confirm_delete=True, include_fasta=False)`
```

- **Job-Based Cleanup Mode**: Pass `job_id=...` to `cleanup_gcs_files` (with `search_only=True` first, then `confirm_delete=True`, keeping `include_fasta=False` unless requested) to remove GCS outputs for a specific deleted job.
