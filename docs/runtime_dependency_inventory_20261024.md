# Isolated runtime dependency inventory and source repair

Failure class: auditing only the hospital interpreter did not cover the OCR
producer or four isolated agent interpreters. Native pins faithfully reproduced
old feature locks, so a clean hospital audit was not whole-image evidence.

## Actual inventory findings

| Inventory | Result | Source correction |
| --- | --- | --- |
| Prepared hospital Mac candidate | No known findings | No claim about other environments |
| Prepared gateway | No known findings | Keep its real locked dependency contract |
| Prepared native shift |14 records across AnyIO, cryptography, pyasn1, urllib3;10 distinct IDs | Feature floors, actual lock/export and native sync |
| Prepared native school |70 Pillow12.1.1 records, including duplicate aliases | Both feature requirements select Pillow13.0.0 |
| Actual f5a08888 Linux producer image |6 records across Flask3.0.3, protobuf4.25.9 and setuptools79.0.1 | Flask3.1.3, Firestore2.34.1/protobuf6.33.6, explicit image bootstrap84.2.1 |

These are inventory findings, not proofs that a particular attack is exploitable.
Duplicate aliases are not counted as distinct vulnerabilities. None are ignored.
The image-inherited setuptools version is updated in all hospital/producer/
agent Python environments and both standalone feature image pairs.

Evidence files in /private/tmp:

- sawa-hospital-dependency-inventory-20261024.json
- sawa-agent-dependency-inventory-20261024.json
- sawa-shift-dependency-inventory-before-20261024.json
- sawa-school-dependency-inventory-20261024.json
- sawa-image-dependency-inventory-20261024/producer.audit.json

## Actual source synchronization

School dependency checkpoint d0f29ec7cef8a455604ee2a8134c63ce8f0de747 includes
the torch pair, Pillow and both image bootstraps. Latest school source
0e9a8aaf1f2b72b424f97a9ef73e6dcbfe0b814e also disables the optional native
telemetry provider before model imports; see onnx_telemetry_startup_20261024.md.
Shift source
01a3869c748f1023847328b1b5f053a2311478c9 includes its five lock upgrades and
both image bootstraps. Native sources are merged with the existing three-way
utility, not blindly replaced. Existing native extensions are retained.
The shift sync also incorporates the four published staging-copy/runtime files
between d516f8e9 and official9ef957ee. Its main.py gains only the upstream
internal migration-router registration; the agent transport extension remains.

Reports:

- /private/tmp/sawa-native-shift-sync-20261024.json
- /private/tmp/sawa-native-shift-security-followup-20261024.json
- /private/tmp/sawa-native-school-security-followup-20261024.json

## Repeatable completion evidence

The local manifest adds six actual-image environment audits, bound to the fixed
source label and immutable Docker image ID. Inventories are read with networking
disabled and without application startup. The host auditor queries public
advisories without installing packages into the image. Nonzero results, skipped
packages and incomplete inventory coverage fail the command. Failures in one
environment do not hide the others.

For official torch/torchvision +cpu builds, the actual installed local version
is retained in inventory evidence, while its public upstream version is used
for advisory lookup and explicitly recorded as an alias. This avoids PyPI404
skips; it neither replaces runtime packages nor normalizes OCR/business data.
Other unknown distributions are not silently substituted or ignored.

The updated definition has30 independent serial commands. Source/manifest
contracts were added but have not run. New image builds, installed patched
inventory audits, full integrated regression, live save/reload, OCR job separation
and test-database return remain outstanding. The interrupted f5a agent build is
not successful evidence. Earlier backend/producer builds are not final-source
images and cannot satisfy the later same-source release receipt.

No GitHub Actions, source push, deployment, budget, IAM or production writes
were performed for this source repair. The prior same-contact Mac Torch
comparison is recorded separately in
/private/tmp/sawa-ocr-runtime-parity-20261024/comparison.md; it does not prove
the new Pillow or complete production/live workflow equivalence.
