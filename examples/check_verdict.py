#!/usr/bin/env python3
"""Fail-closed reference CI gate for Duo verdicts (stdlib only).

Exit 0 means all checks passed; exit 2 means a gate failure, distinct from Duo's
run exit codes 0/1/3/4. Generate the artifact in a trusted CI execution path.
Hashes bind normalized UTF-8 content; they are not signatures and do not protect
against someone rewriting both the artifact and its subject or inputs.

Pair verdicts are checked by default. Panel verdicts (schema 1.2) require
`--rule unanimous-roster-approve-same-subject`; `--roster-sha256` can bind an
expected roster hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

SEVERITIES = ("info", "low", "medium", "high", "critical")
RULE = "both-reviewers-approve-same-subject"
PANEL_RULE = "unanimous-roster-approve-same-subject"


def text_hash(path):
    # read_text uses universal newline conversion (CRLF and CR become LF).
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def roster_hash(roster):
    members = []
    for item in roster:
        members.append({
            "id": item.get("role"),
            "kind": item.get("kind"),
            "backend": item.get("backend"),
            "model": item.get("model"),
            "effort": item.get("effort"),
        })
    return hashlib.sha256(canonical(members)).hexdigest()


def policy_hash(rule, roster_sha):
    return hashlib.sha256(canonical({"rule": rule, "roster_sha256": roster_sha})).hexdigest()


def validate_structure(value):
    """Check the fixed artifact structure before considering policy checks."""
    def obj(item, fields, *, exact=False):
        keys = set(fields.split())
        extensions = not exact and isinstance(value, dict) and value.get('schema_version') != '1.0'
        if not isinstance(item, dict) or (not keys <= set(item) if extensions else set(item) != keys):
            raise ValueError(f"malformed object; expected fields: {fields}")

    def text(item):
        if not isinstance(item, str):
            raise ValueError("expected a string")

    def number(item, minimum=0):
        if type(item) is not int or item < minimum:
            raise ValueError(f"expected an integer >= {minimum}")

    def digest(item):
        if not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item):
            raise ValueError("expected a SHA-256 digest")

    def array(item):
        if not isinstance(item, list):
            raise ValueError("expected an array")

    def location(item):
        if not isinstance(item, dict):
            raise ValueError("malformed location")
        if item.get('kind') == 'lines':
            obj(item, 'kind path subject_sha256 start_line end_line', exact=True)
            number(item['start_line'], 1)
            number(item['end_line'], item['start_line'])
        elif item.get('kind') == 'subject':
            obj(item, 'kind path subject_sha256 explanation', exact=True)
            text(item['explanation'])
        elif item.get('kind') == 'section':
            obj(item, 'kind path subject_sha256 section', exact=True)
            text(item['section'])
        else:
            raise ValueError('unknown location kind')
        digest(item['subject_sha256'])
        if item['path'] != value['subject']['path']:
            raise ValueError('finding path differs from subject path')

    obj(value, 'schema_version run_id started_at mode completion complete subject fixed_inputs agreement '
               'decisions approved_subject_sha256 findings summary audit')
    for field in ('schema_version', 'run_id', 'started_at', 'mode', 'completion'):
        text(value[field])
        if not value[field].strip():
            raise ValueError(f'empty {field}')
    if value['mode'] not in ('plan', 'report', 'description', 'review'):
        raise ValueError('unknown mode')
    obj(value['subject'], 'kind path sha256 bytes')
    text(value['subject']['path'])
    path = value['subject']['path']
    if not path or path == '.' or path.startswith('/') or '..' in path.split('/') or '\\' in path:
        raise ValueError('subject path must be repository-relative')
    number(value['subject']['bytes'])
    obj(value['fixed_inputs'], 'task_sha256 criteria_sha256 context')
    for field in ('task_sha256', 'criteria_sha256'):
        digest(value['fixed_inputs'][field])
    array(value['fixed_inputs']['context'])
    for item in value['fixed_inputs']['context']:
        obj(item, 'label sha256 bytes')
        text(item['label']); digest(item['sha256']); number(item['bytes'])
    obj(value['agreement'], 'rule roster')
    array(value['agreement']['roster'])
    for item in value['agreement']['roster']:
        obj(item, 'role backend model cli_version')
        text(item['role'])
        if not item['role'].strip():
            raise ValueError('empty roster role')
        if item['backend'] not in ('native', 'copilot'):
            raise ValueError('unknown reviewer backend')
        text(item['model'])
        if not item['model'].strip():
            raise ValueError('empty reviewer model')
        if item.get('cli_version') is not None:
            text(item['cli_version'])
    array(value['decisions'])
    for turn, item in enumerate(value['decisions'], 1):
        obj(item, 'role decision subject_sha256 turn timestamp')
        text(item['role'])
        if not item['role'].strip():
            raise ValueError('empty decision role')
        if item['turn'] != turn or not item['timestamp']:
            raise ValueError('decision turns must be sequential with timestamps')
    array(value['findings'])
    counts = dict.fromkeys(SEVERITIES, 0)
    for item in value['findings']:
        obj(item, 'id severity title description location status reported_by reported_turn reported_attempt '
                  'first_seen_subject_hash last_seen_subject_hash location_history disposition')
        text(item['title']); text(item['description'])
        text(item['reported_by'])
        if not item['reported_by'].strip():
            raise ValueError('empty reporting role')
        number(item['reported_turn'], 1); number(item['reported_attempt'], 1)
        digest(item['first_seen_subject_hash']); digest(item['last_seen_subject_hash'])
        location(item['location'])
        array(item['location_history'])
        for entry in item['location_history']:
            obj(entry, 'subject_hash location')
            digest(entry['subject_hash']); location(entry['location'])
            if entry['subject_hash'] != entry['location']['subject_sha256']:
                raise ValueError('historical location differs from its recorded subject hash')
        if item['disposition'] is not None:
            obj(item['disposition'], 'status reason subject_hash turn by')
            digest(item['disposition']['subject_hash']); number(item['disposition']['turn'], 1)
        if item['status'] == 'open' and item['severity'] in SEVERITIES:
            counts[item['severity']] += 1
    obj(value['summary'], 'turns attempts open_findings open_by_severity')
    summary = value['summary']
    for field in ('turns', 'attempts', 'open_findings'):
        number(summary[field])
    obj(summary['open_by_severity'], ' '.join(SEVERITIES))
    for count in summary['open_by_severity'].values():
        number(count)
    if (summary['turns'] != len(value['decisions']) or summary['attempts'] < summary['turns']
            or summary['open_findings'] != sum(counts.values()) or summary['open_by_severity'] != counts):
        raise ValueError('summary does not match decisions and findings')
    audit = {'manifest': 'manifest.json', 'result': 'result.json', 'state': 'state.json',
             'events': 'events.jsonl', 'report': 'report.html', 'verdict': 'verdict.json'}
    if value['audit'] != audit:
        raise ValueError('malformed audit references')


def check(value, args):
    errors = []

    def require(condition, message):
        if not condition:
            errors.append(message)

    if not isinstance(value, dict):
        return ["verdict must be an object"]
    try:
        validate_structure(value)
    except ValueError as exc:
        errors.append(f"malformed verdict: {exc}")
    version = value.get("schema_version")
    require(isinstance(version, str) and re.fullmatch(r"1\.\d+", version), "unsupported schema_version (expected 1.x)")
    if args.roster_sha256 is not None:
        require(bool(re.fullmatch(r"[0-9a-f]{64}", args.roster_sha256)),
                "--roster-sha256 must be a SHA-256 hex digest")
    require(value.get("complete") is True, "review is incomplete")
    require(value.get("completion") == "agreed", "review did not reach agreement")
    agreement = value.get("agreement")
    if not isinstance(agreement, dict):
        errors.append("missing or malformed agreement")
        agreement = {}
    require(agreement.get("rule") == args.rule, "agreement rule does not match")
    roster = agreement.get("roster")
    require(isinstance(roster, list), "malformed reviewer roster")
    roster = roster if isinstance(roster, list) else []
    roster_ids = [item.get("role") for item in roster if isinstance(item, dict)]
    panel = args.rule == PANEL_RULE
    if panel:
        require(len(roster) >= 2, "malformed reviewer roster")
        require(len(set(roster_ids)) == len(roster_ids), "malformed reviewer roster")
        for item in roster:
            valid = (isinstance(item, dict) and isinstance(item.get("role"), str)
                     and item["role"].strip()
                     and item.get("backend") in ("native", "copilot")
                     and isinstance(item.get("model"), str) and item["model"].strip())
            require(valid, "malformed reviewer roster")
        if "roster_sha256" in agreement:
            require(isinstance(agreement["roster_sha256"], str)
                    and re.fullmatch(r"[0-9a-f]{64}", agreement["roster_sha256"]),
                    "malformed roster_sha256")
            computed = roster_hash(roster)
            require(agreement["roster_sha256"] == computed, "roster_sha256 does not match roster")
            if args.roster_sha256:
                require(agreement["roster_sha256"] == args.roster_sha256,
                        "--roster-sha256 does not match artifact")
            if "policy_sha256" in agreement:
                require(isinstance(agreement["policy_sha256"], str)
                        and agreement["policy_sha256"] == policy_hash(args.rule, computed),
                        "policy_sha256 does not match rule and roster")
            else:
                errors.append("missing policy_sha256")
        else:
            errors.append("missing roster_sha256")
    else:
        valid_roster = (len(roster) == 2
                        and all(isinstance(r, dict) and r.get("role") in ("codex", "claude")
                                and r.get("backend") in ("native", "copilot")
                                and isinstance(r.get("model"), str) and r["model"].strip()
                                and (r.get("cli_version") is None or isinstance(r["cli_version"], str))
                                for r in roster)
                        and {r["role"] for r in roster} == {"codex", "claude"})
        require(valid_roster, "malformed reviewer roster")
        if args.roster_sha256:
            errors.append("--roster-sha256 requires --rule unanimous-roster-approve-same-subject")
    subject = value.get("subject")
    if not isinstance(subject, dict):
        subject = {}
    require(subject.get("kind") == "document", "subject must be a document")
    digest = subject.get("sha256")
    require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), "malformed subject SHA-256")
    require(value.get("approved_subject_sha256") == digest and digest is not None,
            "approved subject does not match subject SHA-256")
    decisions = value.get("decisions")
    if not isinstance(decisions, list):
        decisions = []
        errors.append("malformed decisions")
    approvals = set()
    current_subject = None
    blocked = False
    previous_role = None
    epoch = 0
    for item in decisions:
        if (not isinstance(item, dict) or item.get("role") not in roster_ids
                or item.get("decision") not in ("approve", "revise", "blocked")
                or not isinstance(item.get("subject_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["subject_sha256"])
                or type(item.get("turn")) is not int or item["turn"] < 1
                or not isinstance(item.get("timestamp"), str)):
            errors.append("malformed decision")
        else:
            require(approvals != set(roster_ids), "decision after agreement is invalid")
            next_epoch = item.get("approval_epoch", 0)
            if type(next_epoch) is not int or next_epoch < epoch:
                errors.append("invalid approval epoch")
            elif next_epoch != epoch:
                approvals.clear()
                epoch = next_epoch
            if panel and previous_role is not None and roster_ids:
                expected = roster_ids[(roster_ids.index(previous_role) + 1) % len(roster_ids)]
                require(item["role"] == expected, "decision roles must follow cyclic roster order")
            else:
                require(item["role"] != previous_role, "decision roles must strictly alternate")
            previous_role = item["role"]
            if current_subject is None:
                current_subject = item["subject_sha256"]
            if item["decision"] == "revise":
                approvals.clear()
                current_subject = item["subject_sha256"]
            elif item["decision"] == "blocked":
                blocked = True
            else:
                require(item["subject_sha256"] == current_subject, "approve must match the current subject")
                approvals.add(item["role"])
    needed = set(roster_ids)
    require(not blocked and approvals == needed and current_subject == digest
            and bool(decisions) and isinstance(decisions[-1], dict)
            and decisions[-1].get("decision") == "approve",
            ("agreement requires every roster approval on the current subject after the last revision"
             if panel else
             "agreement requires both approvals on the current subject after the last revision"))
    fixed = value.get("fixed_inputs")
    if not isinstance(fixed, dict):
        fixed = {}
        errors.append("malformed fixed_inputs")
    for label, path, expected in (("document", args.document, digest),
                                  ("task", args.task_file, fixed.get("task_sha256")),
                                  ("criteria", args.criteria_file, fixed.get("criteria_sha256"))):
        if path is not None:
            try:
                require(text_hash(path) == expected, f"{label} SHA-256 does not match current content")
            except (OSError, UnicodeError) as exc:
                errors.append(f"cannot read {label}: {exc}")
    findings = value.get("findings")
    if not isinstance(findings, list):
        errors.append("missing or malformed findings")
        findings = []
    ids = set()
    for item in findings:
        if not isinstance(item, dict):
            errors.append("malformed finding")
            continue
        ident, severity, status = item.get("id"), item.get("severity"), item.get("status")
        require(item.get("reported_by") in roster_ids, "finding reported_by is not in the reviewer roster")
        if not isinstance(ident, str) or not re.fullmatch(r"F[1-9][0-9]*", ident):
            errors.append("malformed finding ID")
        elif ident in ids:
            errors.append("duplicate finding ID")
        else:
            ids.add(ident)
        require(severity in SEVERITIES, "unknown finding severity")
        require(status in ("open", "resolved", "dismissed"), "unknown finding status")
        disposition = item.get("disposition")
        if status == "open":
            require(value.get("completion") != "agreed", "agreed must have no open findings")
            require("disposition" in item and disposition is None, "open finding has malformed disposition")
            if severity in SEVERITIES and SEVERITIES.index(severity) >= SEVERITIES.index(args.threshold):
                errors.append(f"open finding {ident} has severity {severity} (threshold {args.threshold})")
        else:
            require(isinstance(disposition, dict) and disposition.get("status") == status
                    and isinstance(disposition.get("reason"), str) and disposition["reason"].strip()
                    and disposition.get("by") == item.get("reported_by")
                    and disposition.get("subject_hash") == item.get("last_seen_subject_hash"),
                    "closed finding lacks a valid disposition")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verdict", type=Path, required=True)
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--task-file", type=Path)
    parser.add_argument("--criteria-file", type=Path)
    parser.add_argument("--threshold", choices=SEVERITIES, default="high")
    parser.add_argument("--rule", default=RULE)
    parser.add_argument("--roster-sha256")
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.verdict.read_text(encoding="utf-8"))
        errors = check(value, args)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        errors = [f"cannot read verdict: {exc}"]
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 2
    print(f"PASS: complete agreement on current content; no open finding at or above {args.threshold}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
