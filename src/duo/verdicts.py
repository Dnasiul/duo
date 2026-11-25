"""Versioned, record-only verdicts for external consumers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .models import (AGENTS, DECISIONS, DOCUMENT_MODES, FINDING_STATUSES, SEVERITIES,
                     AgentError, RunError, normalize, validate_location)
from .report import _json, _read
from .storage import atomic_write, content_hash

VERDICT_SCHEMA_VERSION = "1.0"
COMPLETIONS = ("agreed", "blocked", "limit", "error", "interrupted")
AGREEMENT_RULE = "both-reviewers-approve-same-subject"
PANEL_RULE = "unanimous-roster-approve-same-subject"
AUDIT = {"manifest": "manifest.json", "result": "result.json", "state": "state.json",
         "events": "events.jsonl", "report": "report.html", "verdict": "verdict.json"}
VERDICT_KEYS = {"schema_version", "run_id", "started_at", "mode", "completion", "complete",
                "subject", "fixed_inputs", "agreement", "decisions", "approved_subject_sha256",
                "findings", "summary", "audit"}
FINDING_KEYS = {"id", "severity", "title", "description", "location", "status", "reported_by",
                "reported_turn", "reported_attempt", "first_seen_subject_hash",
                "last_seen_subject_hash", "location_history", "disposition"}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def roster_sha256(members: list[dict]) -> str:
    return hashlib.sha256(_canonical(members)).hexdigest()


def policy_sha256(rule: str, roster_hash: str) -> str:
    return hashlib.sha256(_canonical({"rule": rule, "roster_sha256": roster_hash})).hexdigest()


def _object(value, keys, label, *, extensions=False):
    if not isinstance(value, dict) or (not set(keys) <= set(value) if extensions else set(value) != set(keys)):
        raise ValueError(f"{label} must be an object with exactly: {', '.join(sorted(keys))}")


def _string(value, label, *, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError(f"{label} must be {'a nonempty' if nonempty else 'a'} string")


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")


def _hash(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label} must be a SHA-256 hex digest")


def _array(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")


def _choice(value, choices, label):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"Invalid {label}")


def _path(value):
    _string(value, "path", nonempty=True)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value == "." or "\\" in value:
        raise ValueError("path must be repository-relative")


def _location(value):
    try:
        validate_location(value, recorded=True)
    except AgentError as exc:
        raise ValueError(str(exc)) from exc
    _path(value["path"])


def validate_findings(findings: list, path: str, agreed: bool = False, *, extensions=False,
                      roster: set[str] | None = None) -> dict:
    def check_object(value, keys, label):
        _object(value, keys, label, extensions=extensions)

    allowed = AGENTS if roster is None else roster
    _array(findings, "findings")
    ids = set()
    counts = dict.fromkeys(SEVERITIES, 0)
    for item in findings:
        check_object(item, FINDING_KEYS, "finding")
        if not isinstance(item["id"], str) or not re.fullmatch(r"F[1-9][0-9]*", item["id"]):
            raise ValueError("finding id must be F followed by a positive integer")
        if item["id"] in ids:
            raise ValueError("Duplicate finding ID")
        ids.add(item["id"])
        _choice(item["severity"], SEVERITIES, "finding severity")
        _choice(item["status"], FINDING_STATUSES, "finding status")
        _choice(item["reported_by"], tuple(allowed), "finding reporting role")
        for key in ("title", "description"):
            _string(item[key], "finding " + key)
        for key in ("reported_turn", "reported_attempt"):
            _integer(item[key], key, 1)
        for key in ("first_seen_subject_hash", "last_seen_subject_hash"):
            _hash(item[key], key)
        _location(item["location"])
        if item["location"]["path"] != path:
            raise ValueError("finding path must match subject path")
        _array(item["location_history"], "location_history")
        for entry in item["location_history"]:
            check_object(entry, {"subject_hash", "location"}, "location history entry")
            _hash(entry["subject_hash"], "location history subject_hash")
            _location(entry["location"])
            if entry["subject_hash"] != entry["location"]["subject_sha256"]:
                raise ValueError("historical location must bind its recorded subject hash")
            if entry["location"]["path"] != path:
                raise ValueError("historical finding path must match subject path")
        disposition = item["disposition"]
        if item["status"] == "open":
            if agreed:
                raise ValueError("agreed must have no open findings")
            if disposition is not None:
                raise ValueError("open finding must have null disposition")
            counts[item["severity"]] += 1
        else:
            check_object(disposition, {"status", "reason", "subject_hash", "turn", "by"}, "disposition")
            if disposition["status"] != item["status"] or disposition["by"] != item["reported_by"]:
                raise ValueError("disposition must match finding status and reporting role")
            _string(disposition["reason"], "disposition reason", nonempty=True)
            _hash(disposition["subject_hash"], "disposition subject_hash")
            _integer(disposition["turn"], "disposition turn", 1)
            if disposition["subject_hash"] != item["last_seen_subject_hash"]:
                raise ValueError("disposition must bind the last-seen subject")
    return counts


def _validate_panel_verdict(value: dict) -> dict:
    def check_object(item, keys, label):
        _object(item, keys, label)

    check_object(value, VERDICT_KEYS, "verdict")
    for key in ("run_id", "started_at"):
        _string(value[key], key, nonempty=True)
    _choice(value["mode"], DOCUMENT_MODES, "mode")
    _choice(value["completion"], COMPLETIONS, "completion")
    if type(value["complete"]) is not bool or value["complete"] != (value["completion"] != "interrupted"):
        raise ValueError("complete must be a boolean matching completion")
    agreement = value["agreement"]
    check_object(agreement, {"rule", "roster", "roster_sha256", "policy_sha256"}, "agreement")
    _choice(agreement["rule"], (PANEL_RULE,), "agreement rule")
    _hash(agreement["roster_sha256"], "roster_sha256")
    _hash(agreement["policy_sha256"], "policy_sha256")
    _array(agreement["roster"], "roster")
    if len(agreement["roster"]) < 2:
        raise ValueError("roster must contain at least two reviewers")
    roles = []
    members = []
    for item in agreement["roster"]:
        check_object(item, {"role", "kind", "backend", "model", "effort", "cli_version"}, "roster item")
        _string(item["role"], "roster role", nonempty=True)
        _choice(item["kind"], AGENTS, "roster kind")
        _choice(item["backend"], ("native", "copilot"), "backend")
        _string(item["model"], "model", nonempty=True)
        if item["effort"] is not None:
            _string(item["effort"], "effort")
        if item["cli_version"] is not None:
            _string(item["cli_version"], "cli_version")
        roles.append(item["role"])
        members.append({"id": item["role"], "kind": item["kind"], "backend": item["backend"],
                        "model": item["model"], "effort": item["effort"]})
    if len(set(roles)) != len(roles):
        raise ValueError("roster member ids must be unique")
    expected_roster = roster_sha256(members)
    if agreement["roster_sha256"] != expected_roster:
        raise ValueError("roster_sha256 does not match the roster")
    expected_policy = policy_sha256(PANEL_RULE, expected_roster)
    if agreement["policy_sha256"] != expected_policy:
        raise ValueError("policy_sha256 does not match rule+roster hash")
    _array(value["decisions"], "decisions")
    current_subject = None
    approvals = set()
    blocked = False
    epoch = 0
    previous_role = None
    order = {role: index for index, role in enumerate(roles)}
    for index, item in enumerate(value["decisions"], 1):
        keys = {"role", "decision", "subject_sha256", "turn", "timestamp"}
        if not isinstance(item, dict) or set(item) not in (keys, keys | {"approval_epoch"}):
            raise ValueError("decision must be an object with exactly: decision, role, subject_sha256, timestamp, turn")
        _choice(item["role"], tuple(roles), "decision role")
        _choice(item["decision"], DECISIONS, "decision")
        _hash(item["subject_sha256"], "decision subject_sha256")
        _integer(item["turn"], "decision turn", 1)
        if item["turn"] != index:
            raise ValueError("decision turns must be sequential")
        _string(item["timestamp"], "decision timestamp", nonempty=True)
        if approvals == set(roles):
            raise ValueError("decision after agreement is invalid")
        next_epoch = item.get("approval_epoch", 0)
        _integer(next_epoch, "approval epoch")
        if next_epoch < epoch:
            raise ValueError("approval epochs must not decrease")
        if next_epoch != epoch:
            approvals.clear()
            epoch = next_epoch
        if previous_role is not None:
            expected = roles[(order[previous_role] + 1) % len(roles)]
            if item["role"] != expected:
                raise ValueError("decision roles must follow cyclic roster order")
        previous_role = item["role"]
        if current_subject is None:
            current_subject = item["subject_sha256"]
        if item["decision"] == "revise":
            approvals.clear()
            current_subject = item["subject_sha256"]
        elif item["decision"] == "blocked":
            blocked = True
        else:
            if item["subject_sha256"] != current_subject:
                raise ValueError("approve must match the current subject")
            approvals.add(item["role"])
    subject = value["subject"]
    check_object(subject, {"kind", "path", "sha256", "bytes"}, "subject")
    _choice(subject["kind"], ("document",), "subject kind")
    _path(subject["path"])
    _hash(subject["sha256"], "subject.sha256")
    _integer(subject["bytes"], "subject.bytes")
    fixed = value["fixed_inputs"]
    check_object(fixed, {"task_sha256", "criteria_sha256", "context"}, "fixed_inputs")
    for key in ("task_sha256", "criteria_sha256"):
        _hash(fixed[key], key)
    _array(fixed["context"], "context")
    for item in fixed["context"]:
        check_object(item, {"label", "sha256", "bytes"}, "context item")
        _string(item["label"], "context label")
        _hash(item["sha256"], "context sha256")
        _integer(item["bytes"], "context bytes")
    approved = value["approved_subject_sha256"]
    if value["completion"] == "agreed":
        if approved != subject["sha256"]:
            raise ValueError("agreed subject must match the final subject")
        if (blocked or approvals != set(roles) or current_subject != approved
                or not value["decisions"] or value["decisions"][-1]["decision"] != "approve"):
            raise ValueError("agreed requires every roster approval on the current subject after the last revision")
    elif approved is not None:
        raise ValueError("approved_subject_sha256 must be null unless agreed")
    if value["completion"] == "blocked" and (
            not value["decisions"] or value["decisions"][-1]["decision"] != "blocked"):
        raise ValueError("blocked completion requires a final blocked decision")
    counts = validate_findings(value["findings"], subject["path"], value["completion"] == "agreed",
                               roster=set(roles))
    summary = value["summary"]
    check_object(summary, {"turns", "attempts", "open_findings", "open_by_severity"}, "summary")
    for key in ("turns", "attempts", "open_findings"):
        _integer(summary[key], "summary " + key)
    check_object(summary["open_by_severity"], SEVERITIES, "open_by_severity")
    for count in summary["open_by_severity"].values():
        _integer(count, "severity count")
    if (summary["turns"] != len(value["decisions"]) or summary["attempts"] < summary["turns"]
            or summary["open_by_severity"] != counts or summary["open_findings"] != sum(counts.values())):
        raise ValueError("summary must match decisions and findings")
    check_object(value["audit"], AUDIT, "audit")
    if value["audit"] != AUDIT:
        raise ValueError("audit must reference fixed local record names")
    return value


def validate_verdict(value: object) -> dict:
    """Validate the complete 1.x contract, including cross-field bindings."""
    if not isinstance(value, dict):
        raise ValueError("verdict must be an object with exactly: " + ", ".join(sorted(VERDICT_KEYS)))
    if not isinstance(value.get("schema_version"), str) or not re.fullmatch(r"1\.\d+", value["schema_version"]):
        raise ValueError("Unsupported schema_version; expected 1.x")
    if value["schema_version"] == "1.2":
        return _validate_panel_verdict(value)
    # Minor versions may add optional fields without changing the 1.0 meanings.
    extensions = value.get("schema_version") != "1.0"

    def check_object(item, keys, label):
        _object(item, keys, label, extensions=extensions)

    check_object(value, VERDICT_KEYS, "verdict")
    for key in ("run_id", "started_at"):
        _string(value[key], key, nonempty=True)
    _choice(value["mode"], DOCUMENT_MODES, "mode")
    _choice(value["completion"], COMPLETIONS, "completion")
    if type(value["complete"]) is not bool or value["complete"] != (value["completion"] != "interrupted"):
        raise ValueError("complete must be a boolean matching completion")
    subject = value["subject"]
    check_object(subject, {"kind", "path", "sha256", "bytes"}, "subject")
    _choice(subject["kind"], ("document",), "subject kind")
    _path(subject["path"])
    _hash(subject["sha256"], "subject.sha256")
    _integer(subject["bytes"], "subject.bytes")
    fixed = value["fixed_inputs"]
    check_object(fixed, {"task_sha256", "criteria_sha256", "context"}, "fixed_inputs")
    for key in ("task_sha256", "criteria_sha256"):
        _hash(fixed[key], key)
    _array(fixed["context"], "context")
    for item in fixed["context"]:
        check_object(item, {"label", "sha256", "bytes"}, "context item")
        _string(item["label"], "context label")
        _hash(item["sha256"], "context sha256")
        _integer(item["bytes"], "context bytes")
    agreement = value["agreement"]
    check_object(agreement, {"rule", "roster"}, "agreement")
    _choice(agreement["rule"], (AGREEMENT_RULE,), "agreement rule")
    _array(agreement["roster"], "roster")
    roles = []
    for item in agreement["roster"]:
        check_object(item, {"role", "backend", "model", "cli_version"}, "roster item")
        _choice(item["role"], AGENTS, "roster role")
        _choice(item["backend"], ("native", "copilot"), "backend")
        _string(item["model"], "model", nonempty=True)
        if item["cli_version"] is not None:
            _string(item["cli_version"], "cli_version")
        roles.append(item["role"])
    if len(roles) != 2 or set(roles) != AGENTS:
        raise ValueError("roster must contain both roles exactly once")
    _array(value["decisions"], "decisions")
    current_subject = None
    approvals = set()
    blocked = False
    previous_role = None
    epoch = 0
    for index, item in enumerate(value["decisions"], 1):
        check_object(item, {"role", "decision", "subject_sha256", "turn", "timestamp"}, "decision")
        _choice(item["role"], AGENTS, "decision role")
        _choice(item["decision"], DECISIONS, "decision")
        _hash(item["subject_sha256"], "decision subject_sha256")
        _integer(item["turn"], "decision turn", 1)
        if item["turn"] != index:
            raise ValueError("decision turns must be sequential")
        _string(item["timestamp"], "decision timestamp", nonempty=True)
        if approvals == AGENTS:
            raise ValueError("decision after agreement is invalid")
        next_epoch = item.get("approval_epoch", 0)
        _integer(next_epoch, "approval epoch")
        if next_epoch < epoch:
            raise ValueError("approval epochs must not decrease")
        if next_epoch != epoch:
            approvals.clear()
            epoch = next_epoch
        if item["role"] == previous_role:
            raise ValueError("decision roles must strictly alternate")
        previous_role = item["role"]
        if current_subject is None:
            current_subject = item["subject_sha256"]
        if item["decision"] == "revise":
            approvals.clear()
            current_subject = item["subject_sha256"]
        elif item["decision"] == "blocked":
            blocked = True
        else:
            if item["subject_sha256"] != current_subject:
                raise ValueError("approve must match the current subject")
            approvals.add(item["role"])
    approved = value["approved_subject_sha256"]
    if value["completion"] == "agreed":
        if approved != subject["sha256"]:
            raise ValueError("agreed subject must match the final subject")
        if (blocked or approvals != set(roles) or current_subject != approved
                or not value["decisions"] or value["decisions"][-1]["decision"] != "approve"):
            raise ValueError("agreed requires both approvals on the current subject after the last revision")
    elif approved is not None:
        raise ValueError("approved_subject_sha256 must be null unless agreed")
    if value["completion"] == "blocked" and (
            not value["decisions"] or value["decisions"][-1]["decision"] != "blocked"):
        raise ValueError("blocked completion requires a final blocked decision")
    counts = validate_findings(value["findings"], subject["path"], value["completion"] == "agreed",
                               extensions=extensions)
    summary = value["summary"]
    check_object(summary, {"turns", "attempts", "open_findings", "open_by_severity"}, "summary")
    for key in ("turns", "attempts", "open_findings"):
        _integer(summary[key], "summary " + key)
    check_object(summary["open_by_severity"], SEVERITIES, "open_by_severity")
    for count in summary["open_by_severity"].values():
        _integer(count, "severity count")
    if (summary["turns"] != len(value["decisions"]) or summary["attempts"] < summary["turns"]
            or summary["open_by_severity"] != counts or summary["open_findings"] != sum(counts.values())):
        raise ValueError("summary must match decisions and findings")
    check_object(value["audit"], AUDIT, "audit")
    if value["audit"] != AUDIT:
        raise ValueError("audit must reference fixed local record names")
    return value


def build_verdict(run_dir: Path) -> dict:
    """Read fixed audit filenames only, never the live document or input paths."""
    run_dir = Path(run_dir).expanduser().resolve()
    manifest = _json(run_dir / "manifest.json", dict)
    # lexists-style selection rejects even dangling record symlinks via _json.
    result_path = run_dir / "result.json"
    has_result = result_path.exists() or result_path.is_symlink()
    state = _json(result_path if has_result else run_dir / "state.json", dict, optional=not has_result)
    versions = _json(run_dir / "versions.json", dict, optional=True)
    context = _json(run_dir / "context.json", list, optional=True)
    _json(run_dir / "reviewer-policy.json", dict, optional=True)
    error = _json(run_dir / "error.json", dict, optional=True)
    config = manifest.get("config")
    if not isinstance(config, dict):
        raise RunError("Invalid manifest.json: config must be an object")
    if not state:
        try:
            initial = _read(run_dir / "plan-000.md")
        except FileNotFoundError:
            initial = ""
        state = {"plan": initial, "status": "running", "history": [], "approved": [],
                 "turns": 0, "attempts": 0, "findings": []}
    status = state.get("status")
    statuses = {"agreed": "agreed", "blocked": "blocked", "limit_reached": "limit", "error": "error"}
    if has_result:
        if status not in statuses:
            raise RunError("Invalid result.json: expected terminal status")
        completion = statuses[status]
    else:
        # Interrupt handlers use status=error too. The message distinguishes a
        # signal from an early exception, including before the first saved state.
        message = error.get("message", "")
        interrupted = isinstance(message, str) and message.lower().startswith(("interrupted", "terminated"))
        completion = "error" if (error and not interrupted) or (status == "error" and not interrupted) else "interrupted"
    final_hash = content_hash(state["plan"])
    history = state.get("history", [])
    approved = state.get("approved")
    panel_specs = config.get("reviewers")
    panel = isinstance(panel_specs, list) and bool(panel_specs)
    required = set(AGENTS)
    if panel:
        required = set()
        for item in panel_specs:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise RunError("Panel reviewer roster in manifest is malformed")
            required.add(item["id"])
    if completion == "agreed" and (
            not isinstance(approved, list) or any(not isinstance(role, str) for role in approved)
            or set(approved) != required or not isinstance(history, list) or not history
            or not isinstance(history[-1], dict) or history[-1].get("plan_hash") != final_hash):
        raise RunError("Agreed records lack the full required approvals on the final subject")
    mode = config.get("mode", "plan")
    document = manifest.get("document") or config.get("output") or mode + ".md"
    path = Path(document)
    if path.is_absolute():
        try:
            path = path.relative_to(config["repo"])
        except (KeyError, ValueError) as exc:
            raise RunError("Document must be relative to the recorded repository") from exc
    findings = state.get("findings", [])
    counts = {severity: sum(f["status"] == "open" and f["severity"] == severity for f in findings)
              for severity in SEVERITIES}
    roster = []
    members = []
    if panel:
        for item in panel_specs:
            if not isinstance(item, dict):
                raise RunError("Panel reviewer roster in manifest must contain objects")
            for key in ("id", "kind", "backend", "model"):
                if not isinstance(item.get(key), str):
                    raise RunError("Panel reviewer roster in manifest is malformed")
            if item.get("effort") is not None and not isinstance(item["effort"], str):
                raise RunError("Panel reviewer effort in manifest must be string or null")
            backend = item["backend"]
            kind = item["kind"]
            roster.append({"role": item["id"], "kind": kind, "backend": backend, "model": item["model"],
                           "effort": item.get("effort"),
                           "cli_version": versions.get("copilot" if backend == "copilot" else kind)})
            members.append({"id": item["id"], "kind": kind, "backend": backend,
                            "model": item["model"], "effort": item.get("effort")})
    else:
        for role in ("codex", "claude"):
            backend = config.get(role + "_backend", "native")
            roster.append({"role": role, "backend": backend, "model": config[role + "_model"],
                           "cli_version": versions.get("copilot" if backend == "copilot" else role)})
    epochs = {}
    if manifest.get("segments"):
        for line in _read(run_dir / "events.jsonl").splitlines():
            event = json.loads(line)
            if event.get("event") == "approvals_invalidated":
                epochs[event["after_turn"] + 1] = event["approval_epoch"]
    epoch = 0
    decisions = []
    for number, item in enumerate(history, 1):
        epoch = epochs.get(number, epoch)
        decisions.append({"role": item["agent"], "decision": item["decision"],
                          "subject_sha256": item["plan_hash"], "turn": number,
                          "timestamp": item["timestamp"],
                          **({"approval_epoch": epoch} if epochs else {})})
    agreement = {"rule": AGREEMENT_RULE, "roster": roster}
    schema_version = "1.1" if epochs else VERDICT_SCHEMA_VERSION
    if panel:
        schema_version = "1.2"
        roster_hash = roster_sha256(members)
        agreement = {"rule": PANEL_RULE, "roster": roster, "roster_sha256": roster_hash,
                     "policy_sha256": policy_sha256(PANEL_RULE, roster_hash)}
    return {
        "schema_version": schema_version, "run_id": run_dir.name,
        "started_at": manifest["started_at"], "mode": mode,
        "completion": completion, "complete": completion != "interrupted",
        "subject": {"kind": "document", "path": path.as_posix(), "sha256": final_hash,
                    "bytes": len(state["plan"].encode("utf-8"))},
        "fixed_inputs": {"task_sha256": content_hash(normalize(manifest.get("task", ""))),
                         "criteria_sha256": content_hash(normalize(manifest.get("criteria", ""))),
                         "context": [{k: item[k] for k in ("label", "sha256", "bytes")} for item in context]},
        "agreement": agreement,
        "decisions": decisions,
        "approved_subject_sha256": final_hash if completion == "agreed" else None,
        "findings": findings,
        "summary": {"turns": state["turns"], "attempts": state["attempts"],
                    "open_findings": sum(counts.values()), "open_by_severity": counts},
        "audit": dict(AUDIT),
    }


def write_verdict(run_dir: Path) -> Path:
    path = Path(run_dir) / "verdict.json"
    try:
        value = validate_verdict(build_verdict(run_dir))
    except Exception:
        # Do not leave an earlier successful verdict advertising invalid records.
        path.unlink(missing_ok=True)
        raise
    atomic_write(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return path
