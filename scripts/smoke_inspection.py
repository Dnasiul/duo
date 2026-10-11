#!/usr/bin/env python3
"""Opt-in real-CLI inspection smoke test; performs two authenticated model calls.

Run from an environment with duo-plan installed. No user settings are modified.
Temporary project hooks and MCP launchers create harmless sentinel files if run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from duo.adapters import CLIAdapter
from duo.models import Config, validate_review
from duo.prompts import build_prompt
from duo.storage import RunStore
from duo.workflow import initial_state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-model', required=True)
    parser.add_argument('--claude-model', required=True)
    parser.add_argument('--codex-bin', default='codex')
    parser.add_argument('--claude-bin', default='claude')
    parser.add_argument('--codex-backend', choices=['native', 'copilot'], default='native')
    parser.add_argument('--claude-backend', choices=['native', 'copilot'], default='native')
    parser.add_argument('--copilot-bin', default='copilot')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='duo-inspection-smoke-') as directory:
        root = Path(directory)
        repo = root / 'repo'
        repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        sentinel = root / 'sentinel.py'
        sentinel.write_text('import pathlib, sys\npathlib.Path(sys.argv[1]).touch()\n')
        marker = root / 'unexpected-execution'
        command = shlex.join([sys.executable, str(sentinel), str(marker)])
        # Prove the sentinel works independently of CLI enforcement.
        subprocess.run([sys.executable, str(sentinel), str(marker)], check=True)
        assert marker.exists()
        marker.unlink()
        hook = {'hooks': {'SessionStart': [{'hooks': [{'type': 'command', 'command': command}]}]}}
        (repo / '.claude').mkdir()
        uses_copilot = 'copilot' in (args.codex_backend, args.claude_backend)
        (repo / '.claude/settings.json').write_text(json.dumps({**hook, 'disableAllHooks': uses_copilot}))
        if uses_copilot:
            (repo / '.github/hooks').mkdir(parents=True)
            (repo / '.github/hooks/sentinel.json').write_text(json.dumps({
                'version': 1, 'hooks': {'sessionStart': [{'type': 'command', 'bash': command}]},
            }))
        (repo / '.mcp.json').write_text(json.dumps({'mcpServers': {'sentinel': {
            'command': sys.executable, 'args': [str(sentinel), str(marker)]}}}))
        (repo / '.codex').mkdir()
        (repo / '.codex/hooks.json').write_text(json.dumps(hook))
        (repo / '.codex/config.toml').write_text(
            '[features]\nhooks = true\napps = true\nplugins = true\n'
            '[mcp_servers.sentinel]\n'
            f'command = {json.dumps(sys.executable)}\n'
            f'args = {json.dumps([str(sentinel), str(marker)])}\n'
        )
        plan = '# Plan\nRead smoke-input.txt and report its verification phrase.\n'
        (repo / 'plan.md').write_text(plan)
        phrase = 'inspection-sentinel-verified'
        (repo / 'smoke-input.txt').write_text(phrase + '\n')
        config = Config(repo, args.codex_model, args.claude_model,
                        codex_bin=args.codex_bin, claude_bin=args.claude_bin,
                        codex_backend=args.codex_backend, claude_backend=args.claude_backend,
                        copilot_bin=args.copilot_bin)
        store = RunStore(root / 'audit')
        adapter = CLIAdapter(config, store)
        versions = adapter.preflight()
        state = initial_state('Review the plan; read smoke-input.txt and include its exact phrase in reason.',
                              'Approve if the plan is feasible. Do not execute hooks or MCP launchers.', plan, config)
        for agent in ('codex', 'claude'):
            result = validate_review(adapter(agent, build_prompt(state), 120), plan)
            if marker.exists():
                raise RuntimeError(f'{agent}: hook or MCP sentinel executed')
            if phrase not in result['reason']:
                raise RuntimeError(f'{agent}: did not demonstrate repository inspection')
            if (repo / 'plan.md').read_text() != plan:
                raise RuntimeError(f'{agent}: modified the plan')
            backend = getattr(config, f'{agent}_backend')
            version = versions['copilot' if backend == 'copilot' else agent]
            print(f'{agent} ({backend}): inspected repository; no hook/MCP sentinel executed ({version})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
