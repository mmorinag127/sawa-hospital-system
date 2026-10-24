"""Local, isolated SAWA agent trial. Never imported by production runtime."""
import os

os.environ["ORT_DISABLE_TELEMETRY"] = "1"
