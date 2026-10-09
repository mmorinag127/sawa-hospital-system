"""Dependency/offline-cache contract only; not native recognition or live proof."""

import ast
from importlib import metadata
import inspect
from pathlib import Path
import textwrap

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import pytest


ROOT = Path(__file__).resolve().parents[3]
INSTALLERS = (
    "backend/requirements.txt",
    "backend/constraints-yomitoku-cpu.txt",
    "ocr_pipeline/requirements.txt",
    "ocr_pipeline/constraints-yomitoku-cpu.txt",
)


@pytest.mark.parametrize("relative", INSTALLERS)
def test_each_independent_installer_has_one_unconditional_exact_compatible_pin(relative):
    requirements = []
    for raw in (ROOT / relative).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "--extra-index-url https://download.pytorch.org/whl/cpu":
            continue
        requirements.append(Requirement(line))
    for package, expected in (("huggingface-hub", "==0.36.2"), ("yomitoku", "==0.9.5")):
        matches = [item for item in requirements if canonicalize_name(item.name) == package]
        assert len(matches) == 1, (relative, package, matches)
        requirement = matches[0]
        assert str(requirement.specifier) == expected
        assert requirement.marker is None
        assert requirement.url is None
        assert requirement.extras == set()


def test_installed_versions_match_the_yomitoku_hub_compatibility_contract():
    assert metadata.version("yomitoku") == "0.9.5"
    assert metadata.version("huggingface-hub") == "0.36.2"


def test_real_local_cache_error_is_the_http_error_caught_by_yomitoku_inherited_mixin():
    from huggingface_hub import hub_mixin, PyTorchModelHubMixin
    from huggingface_hub.errors import HfHubHTTPError, LocalEntryNotFoundError
    from yomitoku.models.dbnet_plus import DBNet

    # Yomitoku DBNet actually inherits PyTorchModelHubMixin; do not fabricate a
    # local "YomitokuModelHubMixin" or replace its real exception handler.
    assert issubclass(DBNet, PyTorchModelHubMixin)
    inherited = inspect.unwrap(DBNet.from_pretrained.__func__)
    assert inherited is inspect.unwrap(hub_mixin.ModelHubMixin.from_pretrained.__func__)
    assert inherited.__globals__["HfHubHTTPError"] is HfHubHTTPError
    assert issubclass(LocalEntryNotFoundError, HfHubHTTPError)
    handlers = [node for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(inherited))))
                if isinstance(node, ast.ExceptHandler)]
    assert any(isinstance(handler.type, ast.Name) and handler.type.id == "HfHubHTTPError"
               for handler in handlers)


@pytest.mark.parametrize("filename", ["config.json", "model.safetensors", "pytorch_model.bin"])
def test_real_offline_download_with_no_cached_config_or_weights_stops_without_output(tmp_path, filename):
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import HfHubHTTPError, LocalEntryNotFoundError

    cache = tmp_path / "empty-owned-hub-cache"
    cache.mkdir()
    assert list(cache.iterdir()) == []
    returned_paths = []
    with pytest.raises(LocalEntryNotFoundError) as caught:
        returned_paths.append(hf_hub_download(
            repo_id="KotaroKinoshita/yomitoku-text-detector-dbnet-open-beta",
            filename=filename, cache_dir=cache, local_files_only=True, token=False,
        ))
    assert isinstance(caught.value, HfHubHTTPError)
    assert returned_paths == []
    assert not any(path.is_file() for path in cache.rglob("*"))
    assert not list(tmp_path.rglob("*.safetensors"))
    assert not list(tmp_path.rglob("*.bin"))
