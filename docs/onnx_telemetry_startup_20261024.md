# Native telemetry startup failure class

## Observed failure

The controlled Pillow13 comparison produced a complete recognition result but
exited139. Its raw analysis matched the Pillow12 control; that is not a passing
comparison because process termination differed. Crash report:
/Users/mmorinag/Library/Logs/DiagnosticReports/Python-2026-10-24-063822.ips,
PID77871, EXC_BAD_ACCESS/SIGSEGV, triggered thread2 in
onnxruntime_pybind11_state.so (arm64). The stack is inside Microsoft's 1DS
ILogConfiguration/Packager/TransmissionPolicyManager uploader, after model
recognition. It does not establish a Pillow decoder or model-computation fault.

## Common decision and invariant

Official ONNX Runtime1.31 enables 1DS telemetry by default. Its documentation
specifies ORT_DISABLE_TELEMETRY=1 before initialization to prevent uploader,
events and persistent device-ID creation for the process lifetime. Calling a
later API to suppress nonessential events is not the same startup guarantee.

https://github.com/microsoft/onnxruntime/blob/v1.31.0/docs/Privacy.md#disabling-telemetry

Invariant: OCR must not create this optional uploader, and inference/process
success must not depend on diagnostic transmission or identifier persistence.
The correction removes that provider from the application startup path; it
does not swallow crashes, bypass recognition or force a successful exit code.

## Boundaries covered

- Hospital and producer image environment, before prewarm or application import.
- Hospital src, producer app and agent package initialization.
- Direct hospital/producer model-prewarm scripts.
- Local serial regression runner before creating child commands.
- Every managed native child environment, independent of inherited profile flags.
- School's actual upstream worker/app/prewarm and both image entrypoints.

Native source synchronization now queries ocr_pipeline as well as backend/worker
and publishes new OCR application modules. Omitting that directory could leave
a corrected upstream bootstrap out of the bundle. The real three-way merge,
extension preservation and hash sealing are unchanged.

Fresh-process bootstrap, child-environment and OCR-upstream-query regressions
were added. Actual image audits also refuse images missing the early setting.
All are unexecuted until the next fixed-source local batch. The old failed
capture and its139 status are retained. Recognition equality, clean process
termination, new Linux images and live acceptance remain separate requirements.
No model, quantity, selected evidence, database or workflow result was edited.
