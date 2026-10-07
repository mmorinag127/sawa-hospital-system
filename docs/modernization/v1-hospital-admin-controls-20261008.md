# Admin ordinary controls: source-only packet

Base: `ab0af71e03e146afc96f43f783d35c278887c7f5`.
Branch: `codex/modernization-v1-hospital-admin-controls-20261008`.

## Scope

- `admin/users.tsx`: 9 original opening elements (5 buttons, 1 email input, 1 checkbox, 2 selects).
- `system-status.tsx`: 5 (4 buttons, 1 text input).
- `system-process-logs.tsx`: 2 buttons, including the mapped row control.
- Total: 16 source opening elements, not rendered instance counts; 10 explicit button types and 1 submit type. No textarea/file input or omitted button type.

MUI controls carry `admin-ordinary-control`. ThemeProvider/sawaTheme wraps normal returns only; loading/denied branches remain unchanged. No new query/session provider. Non-rendered logic, callback expressions, options and existing form submission remain unchanged in source; test definitions compare these, including early returns and conditional branches. Tests have NOT been executed.

Original scoped style declarations share paired marked selectors where applicable. Admin global native field styling is countered locally: one outer Input border and reset inner input. Installed MUI internal/SwitchBase.js defines a transparent absolute native checkbox input with width/height 100%. Global sawa-template.css instead targets descendant inputs at 18px. The marker-scoped checkbox input override restores 100% coverage; its icon remains 18px. Original checked/onChange and label behavior are not edited. Actual geometry, pointer/label clicking and keyboard verification remain pending. Global CSS is unchanged. Log-row paired selector spacing uses `.log-row,` and `) {`.

## Source Counts

Total/nonblank lines before -> after:

- admin/users: 281/260 -> 354/333.
- system-status: 810/786 -> 829/805.
- system-process-logs: 436/420 -> 454/438.

## Evidence And Limits

Executed only read-only source inspection, git HEAD/status/diff inspection, whitespace checking, SHA256 hashing and staging. No tests, builds, TypeScript/lint, application imports, servers, browsers, API/DB/network calls or installs were executed. No users or maintenance operations were performed. No commit/integration/deploy.

`frontend/tests/admin-controls-source.test.cjs` defines source-only non-rendered parity, branch/form attributes, exact control counts, mapped native props/options/callbacks and marker scope. The CSS assertion condition now matches the loop key `admin/users`; both native-input coverage and 18px icon assertions are retained. The prior 95b314d packet was withdrawn NOT OK. No production source or page counts changed in this correction. All technical, runtime, geometry/pointer, auth/security and live verification remains pending, not waived. Source freeze is not runtime/business/security/root or whole-modernization completion. Parent fresh normal/literal monitor and integrated-source verification remain required.

## Source SHA256

- frontend/src/pages/admin/users.tsx: `fe18906ce92b6730536acc6c149d3cc28a5a835ad5f25bec3d41c90b31e90514`
- frontend/src/pages/system-status.tsx: `8ad81a62346ec978376e5546a19ee6b89461f2052d1ca3e56174ebb0cf8d4aca`
- frontend/src/pages/system-process-logs.tsx: `e9c9415f39f8b24782c8797bb180a98270ea32181e3fc8574da19bf544132ec8`
- frontend/tests/admin-controls-source.test.cjs: `46387fb25a6427d6dc499f25ffeaebd35bcc9ef77472176e86d3e70f6dab3804`

The cached binary patch hash is reported separately to avoid self-reference.
