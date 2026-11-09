# Automatic Observed Ruling Junctions

## Contract

Fix shared automatic junction detection without tuning a dropout threshold,
fitting to expected row counts, painting missing lines or changing quantities.
Keep operator correction and all saved evidence, edits and history.
Finish implementation before serial local verification; deploy staging only.
Verify the same original without a saved geometry override, then OCR, generate,
save and reload a sheet. Leave a separate unprocessed FAX for user testing.

## Failure Class

Horizontal morphological opening removes short arms connected to vertical ink.
The existing recovery compares horizontal dropout length with vertical stroke
thickness. These are different physical quantities: a three-pixel scan dropout
can split a genuine two-pixel-wide ruling, leaving a real endpoint undetected.
The shared restoration also rejects entirely continuous short arms after opening.

## Implementation

- Restore only original ink attached to an observed vertical ruling and aligned
  with an observed long horizontal ruling within the existing feature window.
- For a disconnected arm, require a real attached branch plus observed ink runs
  on both sides of every dropout. Neither supporting run may be shorter than
  the dropout. No gap pixels are filled, and no row/template count is consulted.
- Continuous observed arms do not require a fabricated interruption to qualify.
- Unsupported or ambiguous geometry remains operator review, not guessed output.

## Work

- Shared detector implementation: implemented, not yet verified.
- Regression cases and exact-original automatic proof: pending.
- Staging operator flow with no pre-existing override: pending.
- Fresh unprocessed FAX for user: pending.
