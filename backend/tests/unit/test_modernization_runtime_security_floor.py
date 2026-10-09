"""Dependency source contracts and small real-API component probes only.

Not hospital business-flow, OCR parity, native/live acceptance, exploitability,
or vulnerability-elimination proof. No network, model load or service doubles.
Installed-version assertions require the parent's later isolated installation.
The preexisting grpcio/grpcio-status 1.78.1 yanked risk remains outside this batch.
"""

from hashlib import sha256
from importlib.metadata import version
from io import StringIO
from pathlib import Path
import tomllib

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version
import pytest


BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
FLOORS = {
    "anyio": ">=4.14.2,<5",
    "mako": ">=1.3.12,<2",
    "pyasn1": ">=0.6.4,<0.7",
    "python-dotenv": ">=1.2.2,<2",
    "python-multipart": ">=0.0.31,<0.1",
    "urllib3": ">=2.8.0,<3",
    "pytest": ">=9.0.3,<10",
}
FIXED = {
    "anyio": "4.14.2", "mako": "1.3.12", "pyasn1": "0.6.4",
    "python-dotenv": "1.2.2", "python-multipart": "0.0.31",
    "urllib3": "2.8.0", "pytest": "9.0.3",
}


def requirements(path):
    return [Requirement(line.strip()) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "--"))]


def named(items, name):
    return [item for item in items if canonicalize_name(item.name) == name]


def one(items):
    assert len(items) == 1, items
    return items[0]


@pytest.fixture
def sources():
    with (BACKEND / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]
    with (BACKEND / "uv.lock").open("rb") as stream:
        lock = tomllib.load(stream)
    return {
        "requirements": requirements(BACKEND / "requirements.txt"),
        "pipeline": requirements(ROOT / "ocr_pipeline" / "requirements.txt"),
        "runtime": [Requirement(value) for value in project["dependencies"]],
        "dev": [Requirement(value) for value in project["optional-dependencies"]["dev"]],
        "lock": lock,
        "root": one([package for package in lock["package"]
                     if package["name"] == project["name"]]),
    }


@pytest.mark.parametrize("name", FLOORS)
def test_actual_installer_and_lock_floor_parity(sources, name):
    expected = SpecifierSet(FLOORS[name])
    root = sources["root"]
    if name == "pytest":
        assert named(sources["requirements"], name) == []
        assert named(sources["runtime"], name) == []
        declared = one(named(sources["dev"], name))
        assert name not in {item["name"] for item in root["dependencies"]}
        assert name in {item["name"] for item in root["optional-dependencies"]["dev"]}
    else:
        declared = one(named(sources["runtime"], name))
        direct = one(named(sources["requirements"], name))
        assert direct.specifier == expected
        assert direct.marker is None
        assert named(sources["dev"], name) == []
        assert name in {item["name"] for item in root["dependencies"]}
    assert declared.specifier == expected
    assert declared.marker is None
    assert not declared.extras and declared.url is None
    metadata = one([item for item in root["metadata"]["requires-dist"] if item["name"] == name])
    assert SpecifierSet(metadata["specifier"]) == expected
    if name == "pytest":
        marker = Requirement(f"pytest; {metadata['marker']}").marker
        assert marker.evaluate({"extra": "dev"}) is True
        assert marker.evaluate({"extra": ""}) is False
    else:
        assert metadata.get("marker") is None
    locked = one([package for package in sources["lock"]["package"] if package["name"] == name])
    assert locked["version"] == FIXED[name]
    assert Version(locked["version"]) in expected


@pytest.mark.parametrize("name", ["pyasn1", "urllib3"])
def test_independent_pipeline_sibling_floors(sources, name):
    requirement = one(named(sources["pipeline"], name))
    assert requirement.specifier == SpecifierSet(FLOORS[name])
    assert requirement.marker is None
    assert not requirement.extras and requirement.url is None


def test_pipeline_does_not_install_unrelated_security_batch_packages(sources):
    assert {canonicalize_name(item.name) for item in sources["pipeline"]} & set(FLOORS) == {"pyasn1", "urllib3"}


@pytest.mark.parametrize("name", FLOORS)
def test_actual_installed_distribution_meets_fixed_security_range(name):
    installed = Version(version(name))
    assert installed in SpecifierSet(FLOORS[name]), (name, str(installed), FLOORS[name])


@pytest.mark.parametrize("path,baseline", [
    (BACKEND / "requirements.txt", {
        "fastapi": "0.136.1", "pillow": "12.1.1", "pymupdf": "1.24.14",
        "pdfplumber": "0.11.9", "pdfminer-six": "20251230", "pypdfium2": "5.5.0",
        "yomitoku": "0.9.5", "huggingface-hub": "0.36.2",
    }),
    (ROOT / "ocr_pipeline" / "requirements.txt", {
        "pillow": "10.4.0", "opencv-python-headless": "4.10.0.84", "pypdfium2": "4.30.0",
        "yomitoku": "0.9.5", "huggingface-hub": "0.36.2",
    }),
])
def test_explicit_image_and_ocr_source_pins_remain_unchanged(path, baseline):
    parsed = requirements(path)
    for name, pinned in baseline.items():
        requirement = one(named(parsed, name))
        assert requirement.specifier == SpecifierSet(f"=={pinned}")
        assert requirement.marker is None


@pytest.mark.parametrize("path", [BACKEND / "requirements.txt", ROOT / "ocr_pipeline" / "requirements.txt"])
@pytest.mark.parametrize("system,machine,suffix", [("Linux", "x86_64", "+cpu"), ("Darwin", "arm64", "")])
def test_explicit_model_platform_source_pins_remain_unchanged(path, system, machine, suffix):
    environment = {**default_environment(), "platform_system": system, "platform_machine": machine}
    parsed = requirements(path)
    for name, pinned in (("torch", "2.6.0"), ("torchvision", "0.21.0")):
        variants = named(parsed, name)
        assert len(variants) == 2
        active = one([item for item in variants if item.marker is not None and item.marker.evaluate(environment)])
        assert active.specifier == SpecifierSet(f"=={pinned}{suffix}")


def test_nonbatch_locked_pins_and_known_grpc_risk_not_silently_changed(sources):
    baseline = {"fastapi": "0.136.1", "starlette": "0.52.1", "cryptography": "46.0.5",
                "pillow": "12.1.1", "grpcio": "1.78.1", "grpcio-status": "1.78.1"}
    for name, pinned in baseline.items():
        assert one([package for package in sources["lock"]["package"] if package["name"] == name])["version"] == pinned


def test_independent_fastapi_multipart_component_contract():
    from fastapi import FastAPI, File, Form, UploadFile
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/component-upload")
    async def upload(attachment: UploadFile = File(...), label: str = Form(...)):
        data = await attachment.read()
        return {"filename": attachment.filename, "content_type": attachment.content_type,
                "size": len(data), "sha256": sha256(data).hexdigest(), "label": label}

    data = b"\x00synthetic multipart\r\n\xff"
    with TestClient(app) as client:
        response = client.post("/component-upload", data={"label": "component only"},
                               files={"attachment": ("synthetic.bin", data, "application/octet-stream")})
        assert response.status_code == 200
        assert response.json() == {"filename": "synthetic.bin", "content_type": "application/octet-stream",
                                   "size": len(data), "sha256": sha256(data).hexdigest(), "label": "component only"}
        missing = client.post("/component-upload", data={"label": "component only"})
        assert missing.status_code == 422
        assert any(error["loc"] == ["body", "attachment"] for error in missing.json()["detail"])


def test_anyio_actual_task_group_and_cancellation_component():
    import anyio

    async def probe():
        completed = []

        async def work(value):
            await anyio.sleep(0)
            completed.append(value)

        async with anyio.create_task_group() as group:
            group.start_soon(work, 1)
            group.start_soon(work, 2)
        assert sorted(completed) == [1, 2]
        with anyio.CancelScope() as scope:
            scope.cancel()
            await anyio.sleep(0)
            pytest.fail("cancelled task must not cross its checkpoint")
        assert scope.cancel_called is True
        assert scope.cancelled_caught is True

    anyio.run(probe, backend="asyncio")


def test_dotenv_actual_stream_parse_component():
    from dotenv import dotenv_values

    parsed = dotenv_values(stream=StringIO('NAME="literal value"\nCOUNT=7\nEMPTY=\n'), interpolate=False)
    assert parsed == {"NAME": "literal value", "COUNT": "7", "EMPTY": ""}


def test_mako_actual_escaped_data_render_component():
    from mako.template import Template

    assert Template("${value | h}").render(value='<tag>&"quoted"</tag>') == "&lt;tag&gt;&amp;&#34;quoted&#34;&lt;/tag&gt;"


def test_pyasn1_actual_der_encode_decode_component():
    from pyasn1.codec.der.decoder import decode
    from pyasn1.codec.der.encoder import encode
    from pyasn1.type import univ

    integer = encode(univ.Integer(7))
    assert integer == b"\x02\x01\x07"
    value, remainder = decode(integer, asn1Spec=univ.Integer())
    assert int(value) == 7 and remainder == b""
    octets, remainder = decode(encode(univ.OctetString(b"\x00unit\xff")), asn1Spec=univ.OctetString())
    assert octets.asOctets() == b"\x00unit\xff" and remainder == b""


def test_urllib3_actual_public_header_api_without_network():
    from urllib3 import HTTPHeaderDict, make_headers

    headers = HTTPHeaderDict()
    headers.add("X-Component", "first")
    headers.add("x-component", "second")
    assert headers.getlist("X-COMPONENT") == ["first", "second"]
    assert headers["x-component"] == "first, second"
    generated = make_headers(basic_auth="unit:secret", user_agent="Component/1.0", keep_alive=True)
    assert generated["authorization"] == "Basic dW5pdDpzZWNyZXQ="
    assert generated["user-agent"] == "Component/1.0"
    assert generated["connection"] == "keep-alive"
    assert generated["authorization"].isascii()
