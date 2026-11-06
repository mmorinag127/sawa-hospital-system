# Old production image reconstruction

## Scope

Local reconstruction only. No production deployment, traffic change, database
change, registry upload, or GitHub Actions run is authorized by this preparation.

## Worker provenance

- Running revision: `worker-prod-se39911618242n1`.
- Original image digest: `sha256:cce530e11a2095e74d7fec90af8db215b027821110f76982ca571aa7e1fc03a3`.
- Source: `9c16aa0711f1864271f83050c2e889b00fddd12e`.
- Original Actions run: `39911618242`; its build log confirms the source-export
  build recipe and normal (not recovery-disabled candidate) deployment.
- Original base: `sha256:42e9e7536384881ecd0bde2d075573f963a36f2d86a78e5b812603e1dfe815a0`.
- Registry reports both original final image and original base unavailable.
- Source is exported with `git archive`, without changing the worktree HEAD.
- Context is prepared using that commit's
  `scripts/prepare_hakodate_backend_deploy_source.sh`.

## Reconstruction limitations

The historical `Dockerfile.base` uses floating `python:3.11-slim`, apt packages,
several lower-bounded Python dependencies, and model downloads without an explicit
model revision. Rebuilding that recipe does NOT recover the original digest or
prove production equivalence. Record the newly resolved packages and image ID.
Do not insert this replacement into evidence that requires the original digest.
Local runtime and database compatibility remain required before treating a
reconstructed candidate as a usable rollback image.

## OCR provenance blocker

- Revision: `ocr-pipeline-prod-00034-9gm`, created `2026-04-13T07:28:23Z`.
- Image: `sha256:62507a385554248287c2f67abddcf3beea9d491bdc018a170f6a60c788572434`.
- Cloud Run deployment audit confirms this digest but contains no source SHA.
- GitHub runs for 2026-04-13 and regional/global Cloud Build history searched so
  far do not establish a source mapping.
- The nearby `e4539e48` snapshot is not proof of the April 13 deployed source.
  Do not silently rebuild it and label it the original OCR image.

## Local artifacts

All generated source/context/logs reside in
`tmp/old-image-reconstruction-20261106/` in the preparation worktree.
Original Actions log: `/private/tmp/sawa-old-worker-39911618242.log`.
## Actual local result

- Base build: exit 0, `sawa-old-worker-base-rebuilt:9c16aa07`.
- Worker build: exit 0, `sawa-old-worker-rebuilt:9c16aa07`.
- Local image ID: `sha256:3ad03660a1524ecdf78ffd578849e9be0ba1e53cfa773fd643da722b05743251`.
- Architecture: amd64. Uncompressed size: 2,738,809,916 bytes.
- Network-disabled, 1 CPU / 3 GB import check: exit 0, `IMPORT_OK 194`.
  This imports the actual old `src.main` and discovers 194 routes; it does NOT
  run the PostgreSQL-dependent startup event or prove business compatibility.
- Resolved Python dependencies saved to `resolved-python.txt` in the artifact
  directory. Base/worker build logs and `import-check.log` are retained there.
- No running test containers remain. No image was uploaded or deployed.
- OCR image reconstruction remains blocked on the original source mapping.
- A rebuild candidate exists, but safe production rollback is NOT yet proven.
