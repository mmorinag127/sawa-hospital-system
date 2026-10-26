# Workflow revision readiness

## Root cause

On staging source7446e07d, ORD581f55d6 reproduced the reported error by selecting
its existing facility FAC00015 while inspection was still loading, then pressing
the context save button. Facility options arrived before the canonical workflow.
No HTTP428 was logged: the client rejected the missing revision before sending.

`beginWorkflowEditorEdit` marked an editor dirty with a null base revision before
the request guard rejected it. `adoptReadWorkflow` correctly preserves dirty
editor bases, so ordinary reload could not repair this poisoned input lease.
The preservation rule itself must not be weakened or replaced with auto-rebase.

## Shared invariant

Editing begins only after the matching order has a canonical workflow token.
The explicit not-initialized projection may use its authored bootstrap token0.
Missing or invalid revisions cannot create dirty editors or begin commands.
Navigation, read-only state and reload remain accessible while loading.

The shared edit/action guards reject before side effects; all step editors stay
unmounted until the matching read token is valid. Original quantity text,
operator edits, saved history, ownership and If-Match checks remain unchanged.

## Verification scope

Delayed inspection with fast facility retrieval; missing-token direct entry and
command entry; recovery after a valid read; invalid revision0 rejection; explicit
bootstrap0 acceptance; existing dirty-editor/stale-reply/CAS regressions.
The real staging order must be checked after the normal manual deployment.
Pre-fix screenshot: /private/tmp/sawa-workflow-revision-before-20261026.png.
