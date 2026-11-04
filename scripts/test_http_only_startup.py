"""HTTP-only production launch must not initialize unused WebSocket protocols."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

import profile_stg_startup as profile


class HttpOnlyStartupTests(unittest.TestCase):
    def test_normal_and_diagnostic_launches_disable_websockets(self):
        root = Path(__file__).resolve().parents[1]
        command, = [json.loads(line.removeprefix('CMD '))
                    for line in (root / 'backend/Dockerfile').read_text().splitlines()
                    if line.startswith('CMD ')]
        self.assertEqual(command[:2], ['uvicorn', 'src.http_entrypoint:app'])
        self.assertEqual(command[command.index('--ws') + 1], 'none')
        self.assertEqual(command[2:], profile.TARGETS['worker-stg'][1:])
        for target in profile.TARGETS.values():
            self.assertEqual(target[target.index('--ws') + 1], 'none')

    def test_uvicorn_keeps_http_without_loading_websocket_packages(self):
        result = subprocess.run([sys.executable, '-c', '\n'.join([
            'import sys',
            'from uvicorn import Config',
            'async def app(scope, receive, send): pass',
            'config = Config(app, ws="none", log_config=None)',
            'config.load()',
            'assert config.http_protocol_class is not None',
            'assert config.ws_protocol_class is None',
            'assert not any(name == "websockets" or name.startswith("websockets.") for name in sys.modules)',
        ])], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
