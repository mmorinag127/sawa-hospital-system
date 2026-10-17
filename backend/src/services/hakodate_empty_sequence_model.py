"""Frozen PARSeq with a learned initial EOS output row, before native decoding."""
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
from threading import Lock

from huggingface_hub import hf_hub_download
from safetensors import safe_open
from safetensors.torch import load_file, load_model
import torch
from torch import nn


BASE_SHA256 = "0fbc62e8654c1ed3d565cce0471b998e42a3bcb40b3dcc8d936a59b480a8b139"
LEARNED_SHA256 = "42e759e442f9e75ae3b31a49714461fcc9abb19f67dc5c5bf35a9ae83a9745ff"
TRAINING_SOURCE_SHA256 = "5893063043188ef952366536824e190bd2cea4fb0ab99fcd3ebfff46669bf4d4"
ADAPTER_SHA256 = "e465f15f1bab9addd0b6ac3fae520abf232a2eae71c0bd740c5c4ea7a85a6280"
PROVENANCE_SHA256 = "34fab691c43a9ecd0c38e045f1000cfb3a56acd953fa09d926a8240c97f4811e"
MODEL_REPO = "KotaroKinoshita/yomitoku-text-recognizer-parseq-middle-v2"
MODEL_REVISION = "1bcaac6ff8eb37edd80b20536e497391f234c038"
ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "data/ocr_models/hakodate_empty_sequence_v1"
CONTRACT = "hakodate_initial_eos_parseq_v1"


def file_sha256(path):
    digest = sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_adapter(directory):
    directory = Path(directory)
    artifact, provenance_path = directory / "adapter.safetensors", directory / "provenance.json"
    if file_sha256(artifact) != ADAPTER_SHA256 or file_sha256(provenance_path) != PROVENANCE_SHA256:
        raise ValueError("hakodate_empty_sequence_adapter_integrity")
    provenance = json.loads(provenance_path.read_text())
    if (provenance["contract"] != CONTRACT or provenance["base_sha256"] != BASE_SHA256
            or provenance["learned_source_sha256"] != LEARNED_SHA256
            or provenance["adapter_sha256"] != ADAPTER_SHA256):
        raise ValueError("hakodate_empty_sequence_provenance_invalid")
    tensors = load_file(str(artifact), device="cpu")
    if (set(tensors) != {"eos_weight", "eos_bias"}
            or tensors["eos_weight"].shape != (512,) or tensors["eos_bias"].shape != ()
            or any(t.dtype != torch.float32 or not torch.isfinite(t).all() for t in tensors.values())):
        raise ValueError("hakodate_empty_sequence_adapter_shape")
    return tensors, provenance


def _validate_native(recognizer, weights_path):
    from yomitoku.models import PARSeq

    base = recognizer.model
    if (version("yomitoku") != "0.9.5" or type(base) is not PARSeq
            or recognizer.infer_onnx or recognizer._cfg.hf_hub_repo != MODEL_REPO
            or base.refine_iters != 1 or base.decode_ar != 1 or base.export_onnx
            or base.max_label_length != 100 or recognizer.tokenizer.eos_id != 0
            or tuple(recognizer._cfg.data.img_size) != (32, 800)
            or recognizer._cfg.num_tokens != 7312
            or type(base.head) is not nn.Linear or base.head.weight.shape != (7310, 512)
            or base.head.bias is None or base.head.bias.shape != (7310,)):
        raise ValueError("hakodate_empty_sequence_native_architecture")
    if file_sha256(weights_path) != BASE_SHA256:
        raise ValueError("hakodate_empty_sequence_base_integrity")
    state = base.state_dict()
    with safe_open(str(weights_path), framework="pt", device="cpu") as saved:
        if set(saved.keys()) != set(state):
            raise ValueError("hakodate_empty_sequence_native_weights")
        for key, tensor in state.items():
            reference = saved.get_tensor(key)
            if tensor.dtype != reference.dtype or not torch.equal(tensor.detach().cpu(), reference):
                raise ValueError("hakodate_empty_sequence_native_weights")


class InitialEosPARSeq(nn.Module):
    """Serialize hook lifetime; retain the complete native AR/refinement forward."""

    def __init__(self, base, weight, bias, provenance):
        super().__init__()
        self.base = base
        self.register_buffer("eos_weight", weight.to(base.head.weight).clone())
        self.register_buffer("eos_bias", bias.to(base.head.bias).clone())
        self.provenance = dict(provenance)
        self.tokenizer = base.tokenizer
        self._forward_lock = Lock()
        self.eval()
        self.requires_grad_(False)
        self._versions = self._parameter_versions()
        object.__setattr__(self, "_head", base.head)
        self._architecture = tuple(getattr(base, key, None) for key in ("decode_ar", "export_onnx", "max_label_length"))

    def _parameter_versions(self):
        return tuple((name, id(tensor), tensor._version) for name, tensor in self.named_parameters()) + tuple(
            (name, id(tensor), tensor._version) for name, tensor in self.named_buffers())

    def forward(self, images, max_length=None):
        with self._forward_lock:
            if (self.training or self.base.training or self.base.refine_iters != 1
                    or tuple(getattr(self.base, key, None) for key in ("decode_ar", "export_onnx", "max_label_length")) != self._architecture
                    or self.base.head is not self._head or self._parameter_versions() != self._versions):
                raise ValueError("hakodate_empty_sequence_model_changed")
            captured = []

            def capture(_module, args):
                captured[:] = [args[0]]

            hook = self.base.head.register_forward_pre_hook(capture)
            try:
                logits = self.base(images, max_length=max_length)
            finally:
                hook.remove()
            if (len(captured) != 1 or logits.ndim != 3 or logits.shape[1] < 1
                    or captured[0].shape[:2] != logits.shape[:2]
                    or captured[0].shape[-1] != self.eos_weight.shape[0]):
                raise ValueError("hakodate_empty_sequence_final_head_missing")
            output = logits.clone()
            output[:, 0, 0] = captured[0][:, 0] @ self.eos_weight + self.eos_bias
            return output


def load_empty_sequence_recognizer(device):
    """Download only the pinned source, verify it, then construct native PARSeq."""
    from yomitoku import TextRecognizer

    weights_path = hf_hub_download(MODEL_REPO, "model.safetensors", revision=MODEL_REVISION)
    if file_sha256(weights_path) != BASE_SHA256:
        raise ValueError("hakodate_empty_sequence_base_integrity")
    recognizer = TextRecognizer(device=device, visualize=False, from_pretrained=False)
    missing, unexpected = load_model(recognizer.model, str(weights_path))
    if missing or unexpected:
        raise ValueError("hakodate_empty_sequence_native_weights")
    recognizer.model.to(recognizer.device).eval()
    return install_empty_sequence_model(recognizer, weights_path=weights_path)


def install_empty_sequence_model(recognizer, *, weights_path, artifact_dir=ARTIFACT_DIR):
    """Validate exact frozen inputs before replacing the recognizer's model only."""
    tensors, provenance = _load_adapter(artifact_dir)
    _validate_native(recognizer, weights_path)
    recognizer.model = InitialEosPARSeq(recognizer.model, tensors["eos_weight"], tensors["eos_bias"], provenance)
    recognizer.recognition_model_metadata = dict(provenance)
    return recognizer
