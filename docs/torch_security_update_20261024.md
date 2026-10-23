# Torch security runtime update

The failed f50d8118 diagnostic audit reports 24 entries against torch2.6.0.
No advisory is ignored and no successful audit or recognition parity is claimed.

## Source selection

- [Official PyTorch2.13 GA](https://dev-discuss.pytorch.org/t/pytorch-2-13-0-general-availability/3412)
  pairs torch2.13.0 with torchvision0.28.0.
- [Official torchvision compatibility table](https://github.com/pytorch/vision/blob/main/README.md)
  includes Python3.11 for this pair.
- [PyTorch checkpoint advisory](https://github.com/pytorch/pytorch/security/advisories/GHSA-63cw-57p8-fm3p)
  affects versions through2.9.1 and identifies2.10.0 as patched.
- [PYSEC-2026-139 source](https://github.com/pypa/advisory-database/blob/main/vulns/torch/PYSEC-2026-139.yaml)
  lists2.10.0 as the last affected release, despite the diagnostic table having
  no explicit fix-version entry. This does not justify ignoring the finding.

## Consistent pins

Hospital backend and OCR requirements and CPU constraints move together from
torch2.6.0/torchvision0.21.0 to2.13.0/0.28.0. Linux x86_64 keeps explicit+cpu
selection; other supported platforms keep regular wheels. The native school
worker must be synchronized from its actual updated upstream source, not given
an untrue source_revision. Agent Hospital inherits the same rebuilt base image.

The Mac Python3.11.15 candidate environment was installed at
`/private/tmp/sawa-hospital-secure-20261024`. Its 133-package input preserves
the diagnostic environment's pins except torch2.13.0, torchvision0.28.0 and
their required sympy1.14.0. The old environment is retained separately for
comparison. Installation exited zero; application imports and inference have
not yet been verified.

The official CPU index plus PyPI resolved 106 Linux x86_64 Python3.11 packages
with the actual requirements and CPU constraints, including torch2.13.0+cpu,
torchvision0.28.0+cpu and yomitoku0.9.5. The resolver exited zero and saved
`/private/tmp/sawa-hospital-linux-cpu-resolved-20261024.txt`. Resolution is not
a Linux installation, image, audit, inference or application test.

The school source dependency commit is
`33b30f85703967083cf68b3537f485e605570a25`, based on actual upstream
`234b57bd7b30f003458d9f3b2596fb8a3b83b2e1`. Neither this local source commit
nor the candidate environment proves deployment or OCR equivalence.

## Acceptance still outstanding

1. Review and integrate the school source, its recorded native delta and hashes.
2. Resolve/install Linux CPU and Mac environments; record actual versions.
3. Compare old/new checkpoint hashes and run the same authoritative input/model
   through both runtimes. Preserve recognized text, cell identities, quantities,
   selected results, output and failure behavior. Do not normalize away drift.
4. Run the complete integrated-source local batch serially, including audit,
   actual images, native workflows, PostgreSQL, frontend and output tests.
5. Only then perform minimal explicit staging deployment and live verification.

No model files, inference algorithms, source FAX, quantities, production,
IAM, budget or deployment were changed by these source-pin edits. Dependency
edits alone are not evidence of OCR equivalence or overall completion.
