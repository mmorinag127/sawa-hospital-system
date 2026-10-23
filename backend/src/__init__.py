import os

# This must precede ONNX Runtime initialization, including indirect imports.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
