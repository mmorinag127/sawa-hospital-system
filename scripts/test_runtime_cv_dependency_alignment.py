"""Offline dependency contract; does not import the application or open a DB."""

from pathlib import Path
import tomllib
import unittest
from urllib.parse import urlparse

from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[1] / "backend"
CV_VERSION = "5.0.0.93"


class RuntimeCvDependencyAlignment(unittest.TestCase):
    def test_serving_and_frozen_dependencies_pin_the_same_headless_release(self):
        requirements = [Requirement(line) for line in (ROOT / "requirements.txt").read_text().splitlines()
                        if line.strip() and not line.lstrip().startswith(("#", "--"))]
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        dependencies = [Requirement(value) for value in project["project"]["dependencies"]]
        for name, entries in (("opencv-python-headless", requirements),
                              ("opencv-python", requirements),
                              ("opencv-python-headless", dependencies)):
            matching = [item for item in entries if item.name == name]
            self.assertEqual(len(matching), 1)
            self.assertEqual(str(matching[0].specifier), "==" + CV_VERSION)
        self.assertNotIn("opencv-python", {item.name for item in dependencies})
        lock = tomllib.loads((ROOT / "uv.lock").read_text())
        packages = [item for item in lock["package"] if item["name"] == "opencv-python-headless"]
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0]["version"], CV_VERSION)
        application = next(item for item in lock["package"] if item["name"] == project["project"]["name"])
        declared = [item for item in application["metadata"]["requires-dist"]
                    if item["name"] == "opencv-python-headless"]
        self.assertEqual(declared, [{"name": "opencv-python-headless", "specifier": "==" + CV_VERSION}])

    def test_locked_release_artifacts_have_versioned_urls_and_sha256_digests(self):
        lock = tomllib.loads((ROOT / "uv.lock").read_text())
        package = next(item for item in lock["package"] if item["name"] == "opencv-python-headless")
        self.assertTrue(package["wheels"])
        for artifact in [package["sdist"], *package["wheels"]]:
            url = urlparse(artifact["url"])
            self.assertEqual(url.scheme, "https")
            self.assertEqual(url.hostname, "files.pythonhosted.org")
            self.assertTrue(Path(url.path).name.startswith("opencv_python_headless-" + CV_VERSION))
            algorithm, digest = artifact["hash"].split(":", 1)
            self.assertEqual(algorithm, "sha256")
            self.assertEqual(len(digest), 64)
            self.assertEqual(len(bytes.fromhex(digest)), 32)
            self.assertGreater(artifact["size"], 0)


if __name__ == "__main__":
    unittest.main()
