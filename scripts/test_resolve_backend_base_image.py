import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import resolve_backend_base_image as resolver


class BackendBaseResolutionTest(unittest.TestCase):
    fingerprint = 'a' * 64
    digest = 'sha256:' + 'b' * 64

    def inventory(self):
        return json.dumps([dict(package=resolver.REPOSITORY, version=self.digest,
            tags=['stg-backend-base-yomitoku-cpu-' + self.fingerprint[:12] + '-20261026-212804'])])

    def config(self):
        return {'Os': 'linux', 'Architecture': 'amd64',
            'Config': {'Labels': {'sawa.backend_base_fingerprint': self.fingerprint}},
            'RepoDigests': [resolver.REPOSITORY + '@' + self.digest]}

    def test_complete_fingerprint_and_digest_are_verified_before_reuse(self):
        for prefix in ('stg', 'prod'):
            with self.subTest(prefix=prefix), \
                 patch.object(resolver.subprocess, 'check_output', side_effect=[
                     self.inventory().replace('stg-backend', prefix + '-backend'), json.dumps(self.config())]) as read, \
                 patch.object(resolver.subprocess, 'run') as pull:
                image = resolver.resolve(self.fingerprint)
                self.assertEqual(image, resolver.REPOSITORY + '@' + self.digest)
                self.assertEqual(read.call_args_list[1].args[0][-1], image)
                self.assertEqual(pull.call_args.args[0], ['docker', 'pull', '--platform', 'linux/amd64', image])
                self.assertTrue(pull.call_args.kwargs['check'])

    def test_missing_fingerprint_returns_empty_without_docker_or_build(self):
        with patch.object(resolver.subprocess, 'check_output', return_value='[]'), \
             patch.object(resolver.subprocess, 'run') as run:
            self.assertEqual(resolver.resolve(self.fingerprint), '')
            run.assert_not_called()

    def test_wrong_full_fingerprint_platform_digest_or_config_is_rejected(self):
        invalid = []
        for key, value in [('Os', 'windows'), ('Architecture', 'arm64'), ('RepoDigests', []), ('Config', None)]:
            config = self.config()
            config[key] = value
            invalid.append(config)
        config = self.config()
        config['Config']['Labels']['sawa.backend_base_fingerprint'] = 'a' * 63 + 'b'
        invalid.append(config)
        invalid.append([])
        for config in invalid:
            with self.subTest(config=config), \
                 patch.object(resolver.subprocess, 'check_output', side_effect=[self.inventory(), json.dumps(config)]), \
                 patch.object(resolver.subprocess, 'run'):
                with self.assertRaises(ValueError):
                    resolver.resolve(self.fingerprint)

    def test_invalid_inventory_and_identity_are_rejected_before_pull(self):
        row = json.loads(self.inventory())[0]
        variants = [{}, [None], [{**row, 'tags': [None]}], [{**row, 'package': 'foreign/repository'}],
                    [{**row, 'version': '../untrusted'}]]
        for value in variants:
            with self.subTest(value=value), patch.object(resolver.subprocess, 'check_output', return_value=json.dumps(value)), \
                 patch.object(resolver.subprocess, 'run') as pull:
                with self.assertRaises(ValueError):
                    resolver.resolve(self.fingerprint)
                pull.assert_not_called()

    def test_invalid_fingerprint_does_not_start_cloud_command(self):
        with patch.object(resolver.subprocess, 'check_output') as read:
            with self.assertRaises(ValueError):
                resolver.resolve('a' * 12)
            read.assert_not_called()

    def test_cloud_or_pull_failure_is_not_reinterpreted_as_cache_miss(self):
        with patch.object(resolver.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, 'gcloud')):
            with self.assertRaises(subprocess.CalledProcessError):
                resolver.resolve(self.fingerprint)
        with patch.object(resolver.subprocess, 'check_output', return_value=self.inventory()), \
             patch.object(resolver.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'docker')):
            with self.assertRaises(subprocess.CalledProcessError):
                resolver.resolve(self.fingerprint)

    def test_both_workflows_resolve_before_build_and_preserve_deploy_gates(self):
        root = Path(__file__).resolve().parents[1]
        for environment in ('stg', 'prod'):
            source = (root / f'.github/workflows/deploy-{environment}.yml').read_text()
            start = source.index('name: Ensure backend base image')
            end = source.index('- id: prepare', start)
            step = source[start:end]
            self.assertLess(step.index('resolve_backend_base_image.py'), step.index('docker build'))
            self.assertIn('if [ -n "$existing_image" ]; then', step)
            self.assertIn('echo "image=$existing_image"', step)
            self.assertIn('sawa.backend_base_fingerprint=${current_base_fingerprint}', step)
            self.assertNotIn('BACKEND_BASE_FINGERPRINT', source)
            self.assertIn('workflow_dispatch:', source)
            if environment == 'prod':
                self.assertIn('release/prod-', source)


if __name__ == '__main__':
    unittest.main()
