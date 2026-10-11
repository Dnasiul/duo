from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from duo.adapters import CLIAdapter, classify_failure, run_process
from duo.cli import main
from duo.models import AgentError, Config, RunError, validate_review
from duo.policy import CODEX_DISABLED
from duo.prompts import build_prompt, revision_diff
from duo.storage import PlanFile, RunStore, atomic_write, run_lock
from duo.workflow import Reviewer, build_graph, initial_state


def response(decision, plan="", reason="Review complete"):
    return {"decision": decision, "plan": plan, "reason": reason}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.audit = self.root / "audit"
        self.store = RunStore(self.audit)
        self.config = Config(self.repo, "codex-test", "claude-test", skip_git_repo_check=True)
        # Policy parsing/enforcement has its own tests. Keep fake agent tests
        # independent of any host administrator configuration.
        for target in ("duo.adapters.check_claude_managed_files",
                       "duo.policy.check_claude_managed_files", "duo.adapters.verify_version"):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def node(self, replies, plan="", **changes):
        if plan:
            (self.repo / "plan.md").write_text(plan)
        config = replace(self.config, **changes)
        file = PlanFile(self.repo)
        adapter = Mock(side_effect=replies)
        node = Reviewer(config, adapter, file, self.store, sleep=lambda _: None)
        return node, initial_state("Task", "Criteria", file.initial, config), adapter

    def run_turns(self, node, state):
        while state["status"] == "running":
            state = node(state)
        return state

    def executable(self, name, body):
        path = self.root / name
        probes = f'''import sys
if '--version' in sys.argv:
    print('test CLI 1.0'); sys.exit(0)
if '--help' in sys.argv:
    print('--sandbox --ephemeral --output-schema --ignore-rules --ignore-user-config --strict-config --safe-mode --restricted --setting-sources --settings --strict-mcp-config --no-chrome --disable-slash-commands --tools --allowedTools'); sys.exit(0)
if 'features' in sys.argv:
    print({chr(10).join(name + ' stable false' for name in CODEX_DISABLED)!r}); sys.exit(0)
if 'mcp' in sys.argv:
    print('[]'); sys.exit(0)
if 'doctor' in sys.argv:
    print('Managed settings (remote): none configured for this organization'); sys.exit(0)
'''
        path.write_text(f"#!{sys.executable}\n" + probes + body, encoding="utf-8")
        path.chmod(0o755)
        self.config = replace(self.config, codex_bin=str(path), claude_bin=str(path))
        return str(path)


class WorkflowTests(Fixture):
    def test_draft_then_two_explicit_approvals(self):
        node, state, adapter = self.node([
            response("revise", "# Plan\n"), response("approve"), response("approve"),
        ])
        result = self.run_turns(node, state)
        self.assertEqual(result["status"], "agreed")
        self.assertEqual(result["turns"], 3)
        self.assertEqual([c.args[0] for c in adapter.call_args_list], ["codex", "claude", "codex"])
        self.assertEqual((self.repo / "plan.md").read_text(), "# Plan\n")
        self.assertEqual(json.loads((self.store.path / "state.json").read_text())["status"], "agreed")

    def test_revision_invalidates_earlier_approval(self):
        node, state, _ = self.node([
            response("approve"), response("revise", "New\n"), response("approve"), response("approve"),
        ], plan="Old\n")
        state = node(state)
        self.assertEqual(state["approved"], ["codex"])
        state = node(state)
        self.assertEqual(state["approved"], [])
        state = node(state)
        self.assertEqual(state["status"], "running")
        self.assertEqual(node(state)["status"], "agreed")

    def test_existing_plan_and_claude_first(self):
        node, state, adapter = self.node([response("approve"), response("approve")],
                                        plan="Existing", start="claude")
        result = self.run_turns(node, state)
        self.assertEqual(result["turns"], 2)
        self.assertEqual(adapter.call_args_list[0].args[0], "claude")

    def test_blocked_preserves_plan_and_question(self):
        node, state, _ = self.node([response("blocked", reason="Which database?")], plan="Existing")
        result = node(state)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["history"][-1]["reason"], "Which database?")
        self.assertEqual((self.repo / "plan.md").read_text(), "Existing")

    def test_turn_limit_and_agreement_on_last_turn(self):
        node, state, _ = self.node([response("approve")], plan="Existing", max_turns=1)
        self.assertEqual(node(state)["status"], "limit_reached")
        node, state, _ = self.node([response("approve"), response("approve")], plan="Existing", max_turns=2)
        self.assertEqual(self.run_turns(node, state)["status"], "agreed")

    def test_invalid_approval_retried_with_feedback(self):
        node, state, adapter = self.node([response("approve"), response("revise", "Plan")])
        result = node(state)
        self.assertEqual(result["turns"], 1)
        self.assertEqual(result["attempts"], 2)
        self.assertIn("Cannot approve an empty plan", adapter.call_args.args[1])

    def test_exhausted_validation_preserves_approvals_and_file(self):
        node, state, adapter = self.node([response("revise", "Old\r\n")] * 3, plan="Old\n")
        state["approved"] = ["claude"]
        result = node(state)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["approved"], ["claude"])
        self.assertEqual(result["turns"], 0)
        self.assertEqual(adapter.call_count, 3)
        self.assertEqual((self.repo / "plan.md").read_text(), "Old\n")

    def test_permanent_failure_is_not_retried(self):
        node, state, adapter = self.node([AgentError("Login required", retryable=False)])
        self.assertEqual(node(state)["status"], "error")
        self.assertEqual(adapter.call_count, 1)

    def test_save_failure_does_not_accept_revision(self):
        node, state, _ = self.node([response("revise", "New")], plan="Old")
        node.on_review = Mock()
        with patch.object(node.plan_file, "write", side_effect=OSError("Disk full")):
            result = node(state)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["plan"], "Old")
        self.assertEqual(result["turns"], 0)
        node.on_review.assert_not_called()

    def test_review_callback_only_receives_accepted_saved_reviews(self):
        node, state, _ = self.node([
            response("revise", "Old"), response("revise", "New"),
        ], plan="Old")
        calls = []
        def completed(number, turn, before, after):
            self.assertEqual((self.repo / "plan.md").read_text(), after)
            saved = json.loads((self.store.path / "state.json").read_text())
            self.assertEqual(saved["turns"], number)
            calls.append((number, turn["decision"], before, after))
        node.on_review = completed
        result = node(state)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(calls, [(1, "revise", "Old", "New")])

    def test_external_edit_during_call_stops_approval(self):
        node, state, adapter = self.node([], plan="Old")
        def edit(*args):
            (self.repo / "plan.md").write_text("User edit")
            return response("approve")
        adapter.side_effect = edit
        result = node(state)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["approved"], [])
        self.assertEqual((self.repo / "plan.md").read_text(), "User edit")

    def test_expired_deadline_does_not_call_agent(self):
        node, state, adapter = self.node([])
        node.deadline = 0
        self.assertEqual(node(state)["status"], "limit_reached")
        adapter.assert_not_called()

    def test_late_response_not_published(self):
        node, state, adapter = self.node([])
        def late(*args):
            node.deadline = 0
            return response("revise", "Late plan")
        adapter.side_effect = late
        self.assertEqual(node(state)["status"], "limit_reached")
        self.assertFalse((self.repo / "plan.md").exists())

    def test_retry_after_respected(self):
        node, state, _ = self.node([AgentError("Rate limit", retry_after=12), response("revise", "Plan")])
        node.sleep = Mock()
        self.assertEqual(node(state)["turns"], 1)
        node.sleep.assert_called_once_with(12)

    def test_terminal_state_never_calls_agent(self):
        node, state, adapter = self.node([])
        state["status"] = "blocked"
        self.assertEqual(node(state)["status"], "blocked")
        adapter.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec("langgraph"), "LangGraph dependency not installed")
    def test_real_langgraph_loop(self):
        node, state, _ = self.node([
            response("revise", "Plan"), response("approve"), response("approve"),
        ])
        result = build_graph(node).invoke(state, {"recursion_limit": 17})
        self.assertEqual(result["status"], "agreed")
        self.assertEqual(result["turns"], 3)


class ContractTests(Fixture):
    def test_invalid_payloads(self):
        cases = [None, [], {}, {**response("approve"), "extra": 1},
                 response("unknown"), response("approve", " "),
                 response("blocked", "text"), response("revise", " \n"),
                 response("approve", reason=" "), response("revise", 42)]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(AgentError):
                validate_review(item, "Old")

    def test_whitespace_is_preserved(self):
        text = "  code  \r\nValue\u00a0"
        self.assertEqual(validate_review(response("revise", text), "Old")["plan"],
                         "  code  \nValue\u00a0")

    def test_prompt_is_verbatim_and_collision_checked(self):
        state = initial_state("Task", "Criteria", "<tag> &amp;\ncollision  \n", self.config)
        state["history"] = [{"agent": "claude", "decision": "approve", "reason": "Approved-secret"}]
        with patch("duo.prompts.secrets.token_hex", side_effect=["collision", "new-boundary"]):
            prompt = build_prompt(state)
        self.assertIn("<<<current_plan new-boundary\n" + state["plan"] + "\n>>>current_plan new-boundary", prompt)
        self.assertNotIn("Approved-secret", prompt)

    def test_diff_without_final_newline(self):
        diff = revision_diff("Old", "New", "codex", "Reason")
        self.assertIn("-Old\n\\ No newline at end of file\n+New\n", diff)

    def test_invalid_configuration(self):
        for changes in ({"max_turns": 0}, {"max_attempts": -1}, {"timeout": float("nan")},
                        {"max_seconds": float("inf")}, {"start": "other"}, {"codex_model": ""},
                        {"codex_effort": "typo"}, {"claude_effort": "ultra"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.config, **changes)


class StorageTests(Fixture):
    def test_atomic_write_preserves_mode_and_cleans_temporary_files(self):
        path = self.repo / "plan.md"
        path.write_text("Old")
        path.chmod(0o640)
        file = PlanFile(self.repo)
        file.write("New  \n")
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(path.read_text(), "New  \n")
        self.assertEqual(list(self.repo.iterdir()), [path])

    def test_failed_replace_preserves_original(self):
        path = self.repo / "plan.md"
        path.write_text("Old")
        with patch.object(Path, "replace", side_effect=OSError("No space")), self.assertRaises(OSError):
            atomic_write(path, b"New")
        self.assertEqual(path.read_text(), "Old")
        self.assertEqual(list(self.repo.iterdir()), [path])

    def test_creation_deletion_and_symlink_conflicts(self):
        file = PlanFile(self.repo)
        path = self.repo / "plan.md"
        path.write_text("")
        with self.assertRaises(RunError):
            file.check()
        file = PlanFile(self.repo)
        path.unlink()
        with self.assertRaises(RunError):
            file.check()
        target = self.root / "target"
        target.write_text("Other")
        path.symlink_to(target)
        with self.assertRaises(RunError):
            PlanFile(self.repo)

    def test_lock_excludes_other_run_and_releases(self):
        with run_lock(self.audit, self.repo):
            with self.assertRaises(RunError):
                with run_lock(self.audit, self.repo):
                    self.fail("Acquired a lock twice")
        with run_lock(self.audit, self.repo):
            pass


class AdapterTests(Fixture):
    def test_codex_stdin_schema_and_response_file(self):
        binary = self.executable("codex-fake", '''import json, pathlib, sys
assert sys.argv[-1] == '-'
assert not any(arg.startswith('model_reasoning_effort=') for arg in sys.argv)
assert sys.stdin.read() == 'Literal $(touch unwanted) `echo hello`'
assert sys.argv[sys.argv.index('--sandbox') + 1] == 'read-only'
schema = pathlib.Path(sys.argv[sys.argv.index('--output-schema') + 1])
assert json.loads(schema.read_text())['additionalProperties'] is False
output = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])
output.write_text(json.dumps({'decision':'approve','plan':'','reason':'Ready'}))
print('Activity that is not JSON')
''')
        adapter = CLIAdapter(replace(self.config, codex_bin=binary), self.store)
        value = adapter("codex", 'Literal $(touch unwanted) `echo hello`', 5)
        self.assertEqual(value["decision"], "approve")
        self.assertFalse((self.repo / "unwanted").exists())

    def test_claude_success_envelope(self):
        binary = self.executable("claude-fake", '''import json, sys
assert '--strict-mcp-config' in sys.argv
assert '--effort' not in sys.argv
assert sys.argv[sys.argv.index('--tools') + 1] == 'Read,Glob,Grep'
assert json.loads(sys.argv[sys.argv.index('--json-schema') + 1])['type'] == 'object'
sys.stdin.read()
print(json.dumps({'is_error':False,'subtype':'success','structured_output':{'decision':'approve','plan':'','reason':'Ready'}}))
''')
        adapter = CLIAdapter(replace(self.config, claude_bin=binary), self.store)
        self.assertEqual(adapter("claude", "Review", 5)["reason"], "Ready")

    def test_claude_error_metadata_even_with_zero_exit(self):
        binary = self.executable("claude-error", "print('{\"is_error\": true, \"subtype\": \"error_max_turns\"}')\n")
        adapter = CLIAdapter(replace(self.config, claude_bin=binary), self.store)
        with self.assertRaises(AgentError) as caught:
            adapter("claude", "Review", 5)
        self.assertFalse(caught.exception.retryable)

    def test_missing_and_malformed_output(self):
        binary = self.executable("missing", "print('not json')\n")
        adapter = CLIAdapter(replace(self.config, codex_bin=binary, claude_bin=binary), self.store)
        for agent in ("codex", "claude"):
            with self.subTest(agent=agent), self.assertRaises(AgentError):
                adapter(agent, "Review", 5)

    def test_failure_is_sanitized(self):
        binary = self.executable("failure", "import sys\nprint('invalid api key SECRET', file=sys.stderr)\nsys.exit(1)\n")
        adapter = CLIAdapter(replace(self.config, codex_bin=binary), self.store)
        with self.assertRaises(AgentError) as caught:
            adapter("codex", "Review", 5)
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertFalse(caught.exception.retryable)

    def test_failure_classification_uses_stderr_tail_not_model_output(self):
        # Codex stdout is the model's message; an error after a long transcript is still found.
        binary = self.executable("late-failure", "import sys\n"
                                 "print('Plan: return 401 unauthorized when not logged in')\n"
                                 "print('x' * 100000, file=sys.stderr)\n"
                                 "print('Error: invalid api key', file=sys.stderr)\nsys.exit(1)\n")
        adapter = CLIAdapter(replace(self.config, codex_bin=binary), self.store)
        with self.assertRaises(AgentError) as caught:
            adapter("codex", "Review", 5)
        self.assertEqual(str(caught.exception), "Agent authentication is invalid")
        binary = self.executable("model-text", "print('return 401 unauthorized when not logged in')\n"
                                 "raise SystemExit(1)\n")
        adapter = CLIAdapter(replace(self.config, codex_bin=binary), self.store)
        with self.assertRaises(AgentError) as caught:
            adapter("codex", "Review", 5)
        self.assertTrue(caught.exception.retryable)

    def test_claude_nonzero_exit_classified_from_error_metadata(self):
        binary = self.executable("claude-auth", "import json\n"
                                 "print(json.dumps({'is_error': True, 'subtype': 'success', "
                                 "'result': 'Invalid API key · Please run /login'}))\n"
                                 "raise SystemExit(1)\n")
        adapter = CLIAdapter(replace(self.config, claude_bin=binary), self.store)
        with self.assertRaises(AgentError) as caught:
            adapter("claude", "Review", 5)
        self.assertFalse(caught.exception.retryable)

    def test_timeout(self):
        binary = self.executable("slow", "import time\ntime.sleep(30)\n")
        adapter = CLIAdapter(replace(self.config, codex_bin=binary), self.store)
        with self.assertRaisesRegex(AgentError, "timed out"):
            adapter("codex", "Review", 0.1)

    def test_rate_limit_delay(self):
        self.assertEqual(classify_failure("Rate limited. Retry-after: 20").retry_after, 20)

    def test_adapter_and_reviewer_full_cycle(self):
        binary = self.executable("agent", '''import json, pathlib, sys
prompt = sys.stdin.read()
if '(No plan exists yet.' in prompt:
    result = {'decision':'revise','plan':'# Draft\\n','reason':'Initial draft'}
else:
    result = {'decision':'approve','plan':'','reason':'Ready'}
if '-o' in sys.argv:
    pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(result))
else:
    print(json.dumps({'is_error':False,'subtype':'success','structured_output':result}))
''')
        config = replace(self.config, codex_bin=binary, claude_bin=binary)
        file = PlanFile(self.repo)
        node = Reviewer(config, CLIAdapter(config, self.store), file, self.store)
        result = self.run_turns(node, initial_state("Task", "Criteria", "", config))
        self.assertEqual(result["status"], "agreed")
        self.assertEqual(result["attempts"], 3)
        self.assertEqual((self.repo / "plan.md").read_text(), "# Draft\n")


class CLITests(Fixture):
    def arguments(self):
        return ["--repo", str(self.repo), "--state-dir", str(self.audit),
                "--task", "Task", "--codex-model", "c", "--claude-model", "a", "--json"]

    def test_empty_task_fails_before_agent_or_audit_creation(self):
        args = self.arguments()
        args[args.index("--task") + 1] = " "
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(args), 1)
        self.assertEqual(json.loads(out.getvalue())["status"], "error")

    def test_state_directory_inside_repo_rejected(self):
        args = self.arguments()
        args[args.index("--state-dir") + 1] = str(self.repo / "audit")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 1)
        self.assertFalse((self.repo / "audit").exists())

    def test_machine_readable_outcomes_and_exit_codes(self):
        for status, code in (("agreed", 0), ("blocked", 3), ("limit_reached", 4), ("error", 1)):
            with self.subTest(status=status):
                fake_graph = Mock()
                result = initial_state("Task", "", "", self.config)
                result.update(status=status, history=[{"reason": "Explanation"}], error="" if status != "error" else "Failed")
                fake_graph.invoke.return_value = result
                out = io.StringIO()
                with patch("duo.cli.build_graph", return_value=fake_graph), \
                     patch.object(CLIAdapter, "preflight", return_value={}), redirect_stdout(out):
                    self.assertEqual(main(self.arguments()), code)
                self.assertEqual(json.loads(out.getvalue())["status"], status)

    def test_keyboard_interrupt_returns_130(self):
        out = io.StringIO()
        with patch("duo.cli.build_graph", side_effect=KeyboardInterrupt), redirect_stdout(out):
            self.assertEqual(main(self.arguments()), 130)
        self.assertIn("Interrupted", json.loads(out.getvalue())["message"])

    @unittest.skipUnless(importlib.util.find_spec("langgraph"), "LangGraph dependency not installed")
    def test_effort_options_reach_agents_and_audit(self):
        binary = self.executable("effort-agent", '''import json, pathlib, sys
if '--version' in sys.argv:
    print('test CLI 1.0')
    sys.exit(0)
sys.stdin.read()
result = {'decision': 'approve', 'plan': '', 'reason': 'Ready'}
if 'exec' in sys.argv:
    assert sys.argv[sys.argv.index('-c') + 1] == 'model_reasoning_effort="xhigh"'
    pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(result))
else:
    assert sys.argv[sys.argv.index('--effort') + 1] == 'high'
    print(json.dumps({'is_error': False, 'subtype': 'success', 'structured_output': result}))
''')
        (self.repo / "plan.md").write_text("Existing plan")
        output = io.StringIO()
        args = self.arguments() + [
            "--codex-bin", binary, "--claude-bin", binary, "--skip-git-repo-check",
            "--codex-effort", "xhigh", "--claude-effort", "high",
        ]
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["status"], "agreed")
        self.assertEqual(summary["attempts"], 2)
        manifest = json.loads((Path(summary["run_dir"]) / "manifest.json").read_text())
        self.assertEqual(manifest["config"]["codex_effort"], "xhigh")
        self.assertEqual(manifest["config"]["claude_effort"], "high")

    def test_invalid_effort_is_rejected_before_running(self):
        for flag in ("--codex-effort", "--claude-effort"):
            with self.subTest(flag=flag), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    main(self.arguments() + [flag, "typo"])
                self.assertEqual(caught.exception.code, 2)

    @unittest.skipUnless(importlib.util.find_spec("langgraph"), "LangGraph dependency not installed")
    def test_conversation_is_opt_in_and_keeps_json_clean(self):
        for enabled in (False, True):
            with self.subTest(show_conversation=enabled):
                (self.repo / "plan.md").unlink(missing_ok=True)
                output, conversation = io.StringIO(), io.StringIO()
                replies = [
                    response("revise", "# Plan\nOriginal step\n", "Draft explanation"),
                    response("revise", "# Plan\nBetter step\n", "Revision explanation"),
                    response("approve", reason="Codex approval explanation"),
                    response("approve", reason="Claude approval explanation"),
                ]
                calls = []
                def respond(*args):
                    # Completed reviews are visible before the next invocation.
                    if enabled and calls:
                        self.assertIn(replies[len(calls) - 1]["reason"], conversation.getvalue())
                    reply = replies[len(calls)]
                    calls.append(args)
                    return reply
                args = self.arguments() + (["--show-conversation"] if enabled else [])
                with patch.object(CLIAdapter, "preflight", return_value={}), \
                     patch.object(CLIAdapter, "__call__", side_effect=respond), \
                     redirect_stdout(output), redirect_stderr(conversation):
                    self.assertEqual(main(args), 0)
                summary = json.loads(output.getvalue())
                self.assertEqual(summary["status"], "agreed")
                self.assertEqual(summary["turns"], 4)
                transcript = conversation.getvalue()
                if enabled:
                    for n, (agent, decision) in enumerate(
                        (("codex", "revise"), ("claude", "revise"),
                         ("codex", "approve"), ("claude", "approve")), 1
                    ):
                        self.assertIn(f"--- Turn {n}: {agent} [{decision}] ---", transcript)
                    for reply in replies:
                        self.assertEqual(transcript.count(reply["reason"]), 1)
                    self.assertIn("Initial plan:\n# Plan\nOriginal step\n", transcript)
                    self.assertIn("-Original step\n+Better step\n", transcript)
                    self.assertEqual(transcript.count("Plan diff:"), 1)
                else:
                    self.assertNotIn("--- Turn", transcript)
                    self.assertNotIn("Initial plan:", transcript)
                    self.assertNotIn("Plan diff:", transcript)
                    for reply in replies:
                        self.assertNotIn(reply["reason"], transcript)

    @unittest.skipUnless(importlib.util.find_spec("langgraph"), "LangGraph dependency not installed")
    def test_conversation_displays_blocked_question(self):
        (self.repo / "plan.md").write_text("Existing plan")
        output, conversation = io.StringIO(), io.StringIO()
        with patch.object(CLIAdapter, "preflight", return_value={}), \
             patch.object(CLIAdapter, "__call__", return_value=response("blocked", reason="Which database?")), \
             redirect_stdout(output), redirect_stderr(conversation):
            self.assertEqual(main(self.arguments() + ["--show-conversation"]), 3)
        self.assertEqual(json.loads(output.getvalue())["status"], "blocked")
        self.assertIn("--- Turn 1: codex [blocked] ---\nWhich database?", conversation.getvalue())
        self.assertNotIn("Plan diff:", conversation.getvalue())

    @unittest.skipUnless(importlib.util.find_spec("langgraph"), "LangGraph dependency not installed")
    def test_conversation_shows_existing_plan_revision_without_final_newline(self):
        (self.repo / "plan.md").write_text("Old")
        output, conversation = io.StringIO(), io.StringIO()
        with patch.object(CLIAdapter, "preflight", return_value={}), \
             patch.object(CLIAdapter, "__call__", return_value=response("revise", "New", "Fix omission")), \
             redirect_stdout(output), redirect_stderr(conversation):
            self.assertEqual(main(self.arguments() + ["--show-conversation", "--max-turns", "1"]), 4)
        self.assertEqual(json.loads(output.getvalue())["status"], "limit_reached")
        self.assertNotIn("Initial plan:", conversation.getvalue())
        self.assertIn("-Old\n\\ No newline at end of file\n+New\n", conversation.getvalue())


if __name__ == "__main__":
    unittest.main()
