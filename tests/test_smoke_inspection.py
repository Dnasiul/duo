"""Smoke reporting distinguishes explicit denials from ordinary tool failures."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SmokeDenialTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('smoke_inspection', ROOT / 'scripts/smoke_inspection.py')
        self.smoke = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.smoke)

    def test_live_denials_correlate_and_deduplicate(self):
        path = ROOT / 'tests/fixtures/claude-2.1.330/events-noweb.jsonl'
        denied = self.smoke.claude_denials(path)
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]['tool_name'], 'Read')
        self.assertEqual(denied[0]['tool_input'], {'file_path': '/etc/hostname'})

    def test_partial_stream_retains_denial_but_does_not_infer_from_error(self):
        source = ROOT / 'tests/fixtures/claude-2.1.330/events-noweb.jsonl'
        lines = source.read_text().splitlines()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'partial.jsonl'
            path.write_text('\n'.join(lines[:-1]) + '\n{"type":')
            denied = self.smoke.claude_denials(path)
            self.assertEqual(len(denied), 1)
            self.assertEqual(denied[0]['tool_input'], {'file_path': '/etc/hostname'})
            path.write_text('\n'.join(line for line in lines[:-1]
                                      if json.loads(line).get('subtype') != 'permission_denied'))
            self.assertEqual(self.smoke.claude_denials(path), [])
