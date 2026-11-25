"""Panel roster coverage for config, workflow, CLI, verdicts, gate, and resume."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

try:
    from .test_duo import Fixture, response
except ImportError:
    from test_duo import Fixture, response

from duo.adapters import CLIAdapter
from duo.checkpoints import canonical
from duo.cli import main, parse_reviewer
from duo.diversity import classify_roster
from duo.models import AgentError, Config, ReviewerSpec
from duo.storage import content_hash
from duo.verdicts import PANEL_RULE, validate_verdict

FIXTURES = Path(__file__).parent / "fixtures" / "verdicts"
GATE = Path(__file__).resolve().parents[1] / "examples" / "check_verdict.py"


def panel_roster() -> tuple[ReviewerSpec, ...]:
    return (
        ReviewerSpec("codex", "codex", "native", "gpt-5", "high"),
        ReviewerSpec("claude", "claude", "native", "claude-sonnet-5.5", "max"),
        ReviewerSpec("gptx", "codex", "copilot", "gpt-5.6", "medium"),
    )


def finding():
    return {
        "severity": "high",
        "title": "Missing rollback",
        "description": "Specify recovery steps.",
        "location": {"kind": "lines", "start_line": 1, "end_line": 1},
    }


class PanelConfigAndDiversityTests(Fixture):
    def test_roster_validation(self):
        with self.assertRaisesRegex(ValueError, "at least two reviewers"):
            Config(self.repo, "", "", reviewers=(ReviewerSpec("codex", "codex", "native", "gpt-5"),),
                   start="codex", max_turns=1, skip_git_repo_check=True)
        with self.assertRaisesRegex(ValueError, "unique"):
            Config(self.repo, "", "", reviewers=(ReviewerSpec("a", "codex", "native", "gpt-5"),
                                                 ReviewerSpec("a", "claude", "native", "claude-sonnet-5.5")),
                   start="a", max_turns=2, skip_git_repo_check=True)
        with self.assertRaisesRegex(ValueError, "Invalid reviewer id"):
            Config(self.repo, "", "", reviewers=(ReviewerSpec("A", "codex", "native", "gpt-5"),
                                                 ReviewerSpec("claude", "claude", "native", "claude-sonnet-5.5")),
                   start="claude", max_turns=2, skip_git_repo_check=True)
        with self.assertRaisesRegex(ValueError, "id codex must use kind codex"):
            Config(self.repo, "", "", reviewers=(ReviewerSpec("codex", "claude", "native", "claude-sonnet-5.5"),
                                                 ReviewerSpec("other", "codex", "native", "gpt-5")),
                   start="codex", max_turns=2, skip_git_repo_check=True)
        with self.assertRaisesRegex(ValueError, "kind must be codex or claude"):
            Config(self.repo, "", "", reviewers=(ReviewerSpec("r1", "other", "native", "x"),
                                                 ReviewerSpec("r2", "codex", "native", "gpt-5")),
                   start="r1", max_turns=2, skip_git_repo_check=True)
        with self.assertRaisesRegex(ValueError, "backend must be native or copilot"):
            Config(self.repo, "", "", reviewers=(ReviewerSpec("r1", "codex", "other", "gpt-5"),
                                                 ReviewerSpec("r2", "claude", "native", "claude-sonnet-5.5")),
                   start="r1", max_turns=2, skip_git_repo_check=True)
        with self.assertRaisesRegex(ValueError, "explicit model, not auto"):
            Config(self.repo, "", "", reviewers=(ReviewerSpec("r1", "codex", "copilot", "auto"),
                                                 ReviewerSpec("r2", "claude", "native", "claude-sonnet-5.5")),
                   start="r1", max_turns=2, skip_git_repo_check=True)
        with self.assertRaisesRegex(ValueError, "effort"):
            Config(self.repo, "", "", reviewers=(ReviewerSpec("r1", "codex", "native", "gpt-5", "none"),
                                                 ReviewerSpec("r2", "claude", "native", "claude-sonnet-5.5")),
                   start="r1", max_turns=2, skip_git_repo_check=True)
        with self.assertRaisesRegex(ValueError, "Starting agent"):
            Config(self.repo, "", "", reviewers=panel_roster(), start="missing", max_turns=3,
                   skip_git_repo_check=True)
        with self.assertRaisesRegex(ValueError, "full roster cycle"):
            Config(self.repo, "", "", reviewers=panel_roster(), start="codex", max_turns=2,
                   skip_git_repo_check=True)
        with self.assertRaisesRegex(ValueError, "native Claude reviewer"):
            Config(self.repo, "", "", reviewers=(ReviewerSpec("r1", "codex", "native", "gpt-5"),
                                                 ReviewerSpec("r2", "claude", "copilot", "claude-sonnet-5.5")),
                   start="r1", max_turns=2, claude_max_turns=2, skip_git_repo_check=True)

    def test_diversity_classification(self):
        roster = (
            ReviewerSpec("a", "claude", "native", "claude-sonnet-5.5"),
            ReviewerSpec("b", "claude", "native", "claude-sonnet-5-5"),
            ReviewerSpec("c", "codex", "native", "gpt-5"),
            ReviewerSpec("d", "codex", "native", "other-model"),
        )
        diversity = classify_roster(roster)
        pairs = {(item["a"], item["b"]): item["relation"] for item in diversity["pairs"]}
        self.assertEqual(pairs[("a", "b")], "identical")
        self.assertEqual(pairs[("a", "c")], "different-family")
        self.assertEqual(pairs[("a", "d")], "unknown")
        self.assertIn("d", diversity["unknown"])
        warnings = "\n".join(diversity["warnings"])
        self.assertIn("same model", warnings)
        self.assertIn("Same-family concentration", warnings)
        self.assertIn("family for d is unknown", warnings)

    def test_default_pair_same_model_warns(self):
        config = Config(self.repo, "gpt-5", "gpt-5", skip_git_repo_check=True)
        warnings = "\n".join(classify_roster(config.roster())["warnings"])
        self.assertIn("same model", warnings)


class PanelCLITests(Fixture):
    def invoke(self, args, replies):
        output = io.StringIO()
        with patch.object(CLIAdapter, "preflight", return_value={}), \
                patch.object(CLIAdapter, "__call__", side_effect=replies), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = main(args)
        return code, json.loads(output.getvalue())

    def test_reviewer_spec_grammar(self):
        for spec in ("a:b:c", "a:b:c:d:e:f", "a::c:d", ":b:c:d"):
            with self.subTest(spec=spec), self.assertRaisesRegex(Exception, "expected ID:KIND:BACKEND:MODEL"):
                parse_reviewer(spec)

    def test_reviewer_conflicts_legacy_flags(self):
        code, summary = self.invoke([
            "--repo", str(self.repo), "--state-dir", str(self.audit), "--task", "Task", "--json",
            "--reviewer", "codex:codex:native:gpt-5",
            "--reviewer", "claude:claude:native:claude-sonnet-5.5",
            "--codex-model", "legacy",
        ], [])
        self.assertEqual(code, 1)
        self.assertIn("--reviewer cannot be combined", summary["message"])

    def test_missing_legacy_models_without_reviewer(self):
        code, summary = self.invoke([
            "--repo", str(self.repo), "--state-dir", str(self.audit), "--task", "Task", "--json",
        ], [])
        self.assertEqual(code, 1)
        self.assertIn("--codex-model and --claude-model are required", summary["message"])

    def test_panel_invocation_builds_roster_config(self):
        (self.repo / "plan.md").write_text("Existing\n")
        args = [
            "--repo", str(self.repo), "--state-dir", str(self.audit), "--task", "Task", "--json",
            "--reviewer", "codex:codex:native:gpt-5:high",
            "--reviewer", "claude:claude:native:claude-sonnet-5.5:max",
            "--reviewer", "gptx:codex:copilot:gpt-5.6:medium",
        ]
        code, summary = self.invoke(args, [response("approve"), response("approve"), response("approve")])
        self.assertEqual(code, 0, summary)
        self.assertEqual(summary["status"], "agreed")
        self.assertIn("All 3 reviewers approved", summary["message"])
        self.assertIn("diversity", summary)
        run = Path(summary["run_dir"])
        manifest = json.loads((run / "manifest.json").read_text())
        reviewers = manifest["config"]["reviewers"]
        self.assertEqual([item["id"] for item in reviewers], ["codex", "claude", "gptx"])
        self.assertEqual(manifest["config"]["codex_model"], "")
        self.assertEqual(manifest["config"]["claude_model"], "")


class PanelWorkflowTests(Fixture):
    def test_three_member_unanimity_and_round_robin(self):
        node, state, adapter = self.node(
            [response("approve"), response("approve"), response("approve")],
            plan="Old\n", reviewers=panel_roster(), start="codex", max_turns=3)
        state = node(state)
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["approved"], ["codex"])
        state = node(state)
        self.assertEqual(state["status"], "running")
        self.assertEqual(set(state["approved"]), {"codex", "claude"})
        state = node(state)
        self.assertEqual(state["status"], "agreed")
        self.assertEqual(set(state["approved"]), {"codex", "claude", "gptx"})
        self.assertEqual([call.args[0] for call in adapter.call_args_list], ["codex", "claude", "gptx"])

    def test_revision_clears_panel_approvals(self):
        node, state, _ = self.node(
            [response("approve"), response("approve"), response("revise", "New\n")],
            plan="Old\n", reviewers=panel_roster(), start="codex", max_turns=5)
        state = node(state)
        state = node(state)
        self.assertEqual(set(state["approved"]), {"codex", "claude"})
        state = node(state)
        self.assertEqual(state["approved"], [])
        self.assertEqual(state["next_agent"], "codex")

    def test_blocked_and_nonretryable_error_are_terminal(self):
        node, state, _ = self.node(
            [response("approve"), response("blocked", reason="Need user choice")],
            plan="Old\n", reviewers=panel_roster(), start="codex", max_turns=5)
        state = node(state)
        self.assertEqual(node(state)["status"], "blocked")
        node, state, _ = self.node([AgentError("boom", retryable=False)],
                                   plan="Old\n", reviewers=panel_roster(), max_turns=5)
        result = node(state)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["turns"], 0)

    def test_findings_are_unique_and_role_owned(self):
        node, state, _ = self.node(
            [response("revise", "New\n", findings=[finding()]),
             response("revise", "Newer\n", findings=[finding()]),
             response("blocked", findings=[finding()])],
            plan="Old\n", reviewers=panel_roster(), max_turns=6)
        result = self.run_turns(node, state)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual([item["id"] for item in result["findings"]], ["F1", "F2", "F3"])
        self.assertEqual([item["reported_by"] for item in result["findings"]], ["codex", "claude", "gptx"])

    def test_only_reporting_member_can_close_its_finding(self):
        node, state, _ = self.node(
            [response("revise", "New\n", findings=[finding()]),
             response("approve", finding_updates=[{
                 "id": "F1", "status": "resolved", "reason": "fixed",
                 "subject_hash": content_hash("New\n"),
             }])],
            plan="Old\n", reviewers=panel_roster(), max_turns=6, max_attempts=1)
        state = node(state)
        result = node(state)
        self.assertEqual(result["status"], "error")
        self.assertIn("own findings", result["error"])

    def test_approving_with_open_own_finding_is_rejected(self):
        node, state, _ = self.node(
            [response("revise", "New\n", findings=[finding()]),
             response("revise", "Newer\n"),
             response("approve"),
             response("approve")],
            plan="Old\n", reviewers=panel_roster(), max_turns=8, max_attempts=1)
        state = node(state)
        state = node(state)
        state = node(state)
        result = node(state)
        self.assertEqual(result["status"], "error")
        self.assertIn("Reconcile all your open findings", result["error"])


class PanelAdapterDispatchTests(Fixture):
    def test_two_copilot_members_use_their_own_models(self):
        config = replace(
            self.config,
            reviewers=(
                ReviewerSpec("a", "codex", "copilot", "gpt-5.6", "low"),
                ReviewerSpec("b", "codex", "copilot", "gpt-5.7", "high"),
            ),
            start="a",
            max_turns=2,
        )
        adapter = CLIAdapter(config, self.store)
        first = adapter.command("a", self.store.attempt_dir())
        second = adapter.command("b", self.store.attempt_dir())
        self.assertEqual(first[first.index("--model") + 1], "gpt-5.6")
        self.assertEqual(second[second.index("--model") + 1], "gpt-5.7")
        self.assertEqual(first[first.index("--reasoning-effort") + 1], "low")
        self.assertEqual(second[second.index("--reasoning-effort") + 1], "high")

    def test_two_native_codex_members_use_their_own_models(self):
        binary = self.executable("native-codex", "sys.exit(0)\n")
        config = replace(
            self.config,
            codex_bin=binary,
            reviewers=(
                ReviewerSpec("a", "codex", "native", "gpt-5", "high"),
                ReviewerSpec("b", "codex", "native", "gpt-5.2", None),
            ),
            start="a",
            max_turns=2,
        )
        adapter = CLIAdapter(config, self.store)
        first = adapter.command("a", self.store.attempt_dir())
        second = adapter.command("b", self.store.attempt_dir())
        self.assertEqual(first[first.index("-m") + 1], "gpt-5")
        self.assertEqual(second[second.index("-m") + 1], "gpt-5.2")
        self.assertIn('model_reasoning_effort="high"', " ".join(first))
        self.assertNotIn("model_reasoning_effort", " ".join(second))


class PanelVerdictAndGateTests(Fixture):
    def run_cli(self, args, replies):
        output = io.StringIO()
        with patch.object(CLIAdapter, "preflight", return_value={}), \
                patch.object(CLIAdapter, "__call__", side_effect=replies), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = main(args)
        return code, json.loads(output.getvalue())

    def test_panel_verdict_schema_and_missing_approval(self):
        valid = json.loads((FIXTURES / "valid-panel-agreed.json").read_text())
        self.assertEqual(validate_verdict(valid)["schema_version"], "1.2")
        missing = json.loads((FIXTURES / "invalid-panel-missing-approval.json").read_text())
        with self.assertRaisesRegex(ValueError, "every roster approval"):
            validate_verdict(missing)

    def test_cli_panel_run_emits_schema_1_2(self):
        (self.repo / "plan.md").write_text("Existing\n")
        args = [
            "--repo", str(self.repo), "--state-dir", str(self.audit), "--task", "Task", "--json",
            "--reviewer", "codex:codex:native:gpt-5:high",
            "--reviewer", "claude:claude:native:claude-sonnet-5.5:max",
            "--reviewer", "gptx:codex:copilot:gpt-5.6:medium",
        ]
        code, summary = self.run_cli(args, [response("approve"), response("approve"), response("approve")])
        self.assertEqual(code, 0, summary)
        verdict = json.loads((Path(summary["run_dir"]) / "verdict.json").read_text())
        self.assertEqual(verdict["schema_version"], "1.2")
        validate_verdict(verdict)

    def test_default_pair_still_emits_legacy_schema(self):
        (self.repo / "plan.md").write_text("Existing\n")
        code, summary = self.run_cli([
            "--repo", str(self.repo), "--state-dir", str(self.audit), "--task", "Task", "--json",
            "--codex-model", "gpt-5", "--claude-model", "claude-sonnet-5.5",
        ], [response("approve"), response("approve")])
        self.assertEqual(code, 0, summary)
        verdict = json.loads((Path(summary["run_dir"]) / "verdict.json").read_text())
        self.assertEqual(verdict["schema_version"], "1.0")
        validate_verdict(verdict)

    def gate(self, verdict, *extra):
        verdict_path = self.root / "verdict.json"
        verdict_path.write_text(json.dumps(verdict))
        document = self.root / "document.md"
        document.write_text("Old\r\n")
        return subprocess.run(
            [sys.executable, str(GATE), "--verdict", str(verdict_path), "--document", str(document), *extra],
            capture_output=True, text=True)

    def test_gate_panel_rule_and_roster_hash(self):
        panel = json.loads((FIXTURES / "valid-panel-agreed.json").read_text())
        self.assertEqual(self.gate(panel).returncode, 2)
        result = self.gate(panel, "--rule", PANEL_RULE)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.gate(panel, "--rule", PANEL_RULE, "--roster-sha256", "0" * 64)
        self.assertEqual(result.returncode, 2)
        pair = json.loads((FIXTURES / "valid-agreed.json").read_text())
        result = self.gate(pair, "--rule", PANEL_RULE)
        self.assertEqual(result.returncode, 2)


class PanelResumeTests(Fixture):
    def run_cli(self, args, replies):
        output = io.StringIO()
        with patch.object(CLIAdapter, "preflight", return_value={}) as preflight, \
                patch.object(CLIAdapter, "__call__", side_effect=replies) as adapter, \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = main(args)
        return code, json.loads(output.getvalue()), adapter, preflight

    def test_panel_resume_clears_approvals_and_requires_unanimity(self):
        (self.repo / "plan.md").write_text("Existing\n")
        args = [
            "--repo", str(self.repo), "--state-dir", str(self.audit), "--task", "Task", "--json",
            "--reviewer", "codex:codex:native:gpt-5:high",
            "--reviewer", "claude:claude:native:claude-sonnet-5.5:max",
            "--reviewer", "gptx:codex:copilot:gpt-5.6:medium",
        ]
        code, summary, _, _ = self.run_cli(args, [response("approve"), KeyboardInterrupt()])
        self.assertEqual(code, 130, summary)
        run = Path(summary["run_dir"])
        checkpoint = json.loads((run / "checkpoint.json").read_text())
        self.assertEqual(checkpoint["schema_version"], 2)
        self.assertIn("roster_sha256", checkpoint)
        self.assertIn("policy_sha256", checkpoint)
        code, summary, adapter, preflight = self.run_cli(
            ["--resume", str(run), "--json"],
            [response("approve"), response("approve"), response("approve")],
        )
        self.assertEqual(code, 0, summary)
        self.assertEqual([call.args[0] for call in adapter.call_args_list], ["claude", "gptx", "codex"])
        preflight.assert_called_once()
        result = json.loads((run / "result.json").read_text())
        self.assertEqual(result["status"], "agreed")
        self.assertEqual(set(result["approved"]), {"codex", "claude", "gptx"})
        events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
        invalidation = [event for event in events if event.get("event") == "approvals_invalidated"]
        self.assertEqual(invalidation[-1]["agents"], ["codex"])

    def test_tampered_panel_roster_hash_refuses_before_calls(self):
        (self.repo / "plan.md").write_text("Existing\n")
        args = [
            "--repo", str(self.repo), "--state-dir", str(self.audit), "--task", "Task", "--json",
            "--reviewer", "codex:codex:native:gpt-5:high",
            "--reviewer", "claude:claude:native:claude-sonnet-5.5:max",
            "--reviewer", "gptx:codex:copilot:gpt-5.6:medium",
        ]
        code, summary, _, _ = self.run_cli(args, [response("approve"), KeyboardInterrupt()])
        self.assertEqual(code, 130, summary)
        run = Path(summary["run_dir"])
        checkpoint = json.loads((run / "checkpoint.json").read_text())
        checkpoint["roster_sha256"] = "0" * 64
        checkpoint["integrity"] = hashlib.sha256(canonical(checkpoint)).hexdigest()
        (run / "checkpoint.json").write_text(json.dumps(checkpoint))
        output = io.StringIO()
        with patch("duo.cli.CLIAdapter") as factory, redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = main(["--resume", str(run), "--json"])
        self.assertEqual(code, 1)
        self.assertIn("roster hash mismatch", json.loads(output.getvalue())["message"].lower())
        factory.assert_not_called()

    def test_schema_1_pair_checkpoint_without_reviewers_still_resumes(self):
        (self.repo / "plan.md").write_text("Existing\n")
        code, summary, _, _ = self.run_cli([
            "--repo", str(self.repo), "--state-dir", str(self.audit), "--task", "Task", "--json",
            "--codex-model", "gpt-5", "--claude-model", "claude-sonnet-5.5",
        ], [response("approve"), KeyboardInterrupt()])
        self.assertEqual(code, 130, summary)
        run = Path(summary["run_dir"])
        checkpoint = json.loads((run / "checkpoint.json").read_text())
        self.assertEqual(checkpoint["schema_version"], 1)
        del checkpoint["config"]["reviewers"]
        checkpoint["integrity"] = hashlib.sha256(canonical(checkpoint)).hexdigest()
        (run / "checkpoint.json").write_text(json.dumps(checkpoint))
        manifest = json.loads((run / "manifest.json").read_text())
        del manifest["config"]["reviewers"]
        (run / "manifest.json").write_text(json.dumps(manifest))
        code, summary, _, _ = self.run_cli(
            ["--resume", str(run), "--json"],
            [response("approve"), response("approve")],
        )
        self.assertEqual(code, 0, summary)
