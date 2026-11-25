"""Transactional document journal. JSON only; no provider sessions or environments.

State is the last committed review boundary. attempts_current_turn reserves calls
beyond that boundary, so recovery adds those reservations to state.attempts once.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, fields
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path

from . import __version__
from .models import DECISIONS, MAX_CONTEXT_BYTES, MAX_TEXT_BYTES, Config, ReviewerSpec, RunError, State, normalize
from .storage import (ContextFile, RunStore, content_hash, durable_write, fsync_directory,
                      read_input, timestamp, UserClarification)
from .verdicts import (PANEL_RULE, _array, _choice, _hash, _integer, _object, _path, _string,
                       policy_sha256, roster_sha256,
                       validate_findings)

CHECKPOINT_SCHEMA_VERSION = 1
PANEL_CHECKPOINT_SCHEMA_VERSION = 2
MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024
STATUSES = ('running', 'agreed', 'blocked', 'error', 'limit_reached')
KEYS = set('schema_version duo_version run_id segment sequence created_at updated_at deadline '
           'max_seconds status repo document output config inputs state document_sha256 '
           'attempts_current_turn in_flight last_attempt pending approval_epoch segments integrity '
           'document_bytes_sha256'.split())
PANEL_KEYS = KEYS | {'roster_sha256', 'policy_sha256'}


def config_payload(config: Config) -> dict:
    payload = json.loads(json.dumps({**asdict(config), 'repo': str(config.repo)}))
    if not config.reviewers:
        payload.pop('reviewers', None)
    return payload


def utc(value: str) -> datetime:
    _string(value, 'UTC timestamp', nonempty=True)
    result = datetime.fromisoformat(value)
    if result.utcoffset() != timedelta(0):
        raise ValueError('Timestamp must be UTC')
    return result


def remaining_seconds(value: dict, now: datetime) -> float:
    if now < utc(value['updated_at']) or now < utc(value['segments'][-1]['started_at']):
        raise RunError('Inconsistent clock state: recorded update or segment is in the future')
    effective = min(utc(value['deadline']), utc(value['created_at']) +
                    timedelta(seconds=value['max_seconds']))
    return min(value['max_seconds'], (effective - now).total_seconds())


def canonical(value: dict) -> bytes:
    return json.dumps({k: v for k, v in value.items() if k != 'integrity'},
                      sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def strict_json(data: bytes):
    # Bound nesting before parsing, including on Python versions whose JSON
    # parser no longer raises RecursionError for deeply nested inputs.
    depth = 0
    quoted = escaped = False
    for char in data:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > 128:
                raise ValueError('JSON nesting exceeds 128 levels')
        elif char in (93, 125):
            depth -= 1

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate JSON key')
            value[key] = item
        return value

    def constant(value):
        raise ValueError('Non-finite JSON number')

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def read_record(path: Path):
    try:
        return strict_json(read_input(path, follow_symlinks=False,
                                    max_bytes=MAX_CHECKPOINT_BYTES))
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise RunError(f'Could not read {path.name}: {exc}') from exc


def config_from(value: dict) -> Config:
    expected = {f.name for f in fields(Config)}
    payload = dict(value)
    payload.setdefault('reviewers', [])
    _object(payload, expected, 'config')
    booleans = {'skip_git_repo_check', 'allow_untested_cli', 'allow_web'}
    integers = {'max_turns', 'max_attempts', 'history_limit', 'max_output_mb', 'max_run_mb',
                'max_prompt_bytes', 'claude_max_turns'}
    numbers = {'timeout', 'max_seconds', 'claude_max_budget_usd'}
    nullable = {'claude_max_turns', 'claude_max_budget_usd', 'codex_effort', 'claude_effort'}
    reviewers = []
    for key, item in payload.items():
        if item is None and key in nullable:
            continue
        if key in booleans:
            if type(item) is not bool:
                raise ValueError(f'config.{key} must be boolean')
        elif key in integers:
            _integer(item, key, 1)
        elif key in numbers:
            if type(item) not in (int, float) or not math.isfinite(item) or item <= 0:
                raise ValueError(f'config.{key} must be finite and positive')
        elif key == 'pass_env':
            _array(item, key)
            for name in item:
                _string(name, 'environment name', nonempty=True)
        elif key == 'reviewers':
            _array(item, key)
            for reviewer in item:
                _object(reviewer, {'id', 'kind', 'backend', 'model', 'effort'}, 'reviewer')
                for field in ('id', 'kind', 'backend', 'model'):
                    _string(reviewer[field], 'reviewer ' + field, nonempty=True)
                if reviewer['effort'] is not None and not isinstance(reviewer['effort'], str):
                    raise ValueError('reviewer effort must be a string or null')
                reviewers.append(ReviewerSpec(**reviewer))
        else:
            _string(item, key)
    return Config(**{**payload, 'repo': Path(payload['repo']), 'pass_env': tuple(payload['pass_env']),
                     'reviewers': tuple(reviewers)})


def validate_state(state: dict, config: Config) -> None:
    _object(state, State.__annotations__, 'state')
    for key in ('task', 'criteria', 'plan', 'last_diff', 'error'):
        _string(state[key], 'state.' + key)
    for key in ('task', 'criteria', 'plan'):
        if len(state[key].encode('utf-8')) > MAX_TEXT_BYTES:
            raise ValueError(f'{key} exceeds the input size limit')
    if not state['task'].strip():
        raise ValueError('Task must not be empty')
    for key in ('turns', 'attempts'):
        _integer(state[key], key)
    if (state['turns'] > config.max_turns or state['attempts'] < state['turns']
            or state['attempts'] > (state['turns'] + 1) * config.max_attempts):
        raise ValueError('Invalid state counters')
    _choice(state['status'], STATUSES, 'status')
    if state['status'] == 'running' and state['attempts'] > state['turns'] * config.max_attempts:
        raise ValueError('Committed attempt count exceeds accepted turn allowances')
    roster = config.roster_ids()
    roster_set = set(roster)
    _choice(state['next_agent'], roster, 'next agent')
    _array(state['approved'], 'approved')
    for role in state['approved']:
        _choice(role, roster, 'approval')
    if len(set(state['approved'])) != len(state['approved']):
        raise ValueError('Duplicate approval')
    if state['status'] == 'agreed' and set(state['approved']) != roster_set:
        raise ValueError('Agreement requires all live approvals' if config.reviewers
                         else 'Agreement requires both live approvals')
    _array(state['history'], 'history')
    if len(state['history']) != state['turns']:
        raise ValueError('History length must equal turns')
    if state['status'] == 'blocked' and (not state['history'] or state['history'][-1]['decision'] != 'blocked'):
        raise ValueError('Blocked state requires a final blocked decision')
    role = config.start
    index = {member: position for position, member in enumerate(roster)}
    blocked = False
    subject = None
    for turn in state['history']:
        _object(turn, {'agent', 'decision', 'reason', 'plan_hash', 'timestamp', 'backend', 'model'}, 'turn')
        if turn['agent'] != role:
            raise ValueError('History roles must follow the configured roster order'
                             if config.reviewers else
                             'History roles must alternate from the configured start')
        role = roster[(index[role] + 1) % len(roster)]
        _choice(turn['decision'], DECISIONS, 'decision')
        if blocked:
            raise ValueError('Review after blocked decision')
        blocked = turn['decision'] == 'blocked'
        if subject is not None and turn['decision'] != 'revise' and turn['plan_hash'] != subject:
            raise ValueError('Unchanged decision changed the document hash')
        subject = turn['plan_hash']
        _string(turn['reason'], 'reason', nonempty=True)
        _hash(turn['plan_hash'], 'turn hash')
        utc(turn['timestamp'])
        spec = config.spec(turn['agent'])
        if turn['backend'] != spec.backend or turn['model'] != spec.model:
            raise ValueError('History roster mismatch')
    if state['next_agent'] != role:
        raise ValueError('Next agent disagrees with history')
    if state['history'] and state['history'][-1]['plan_hash'] != content_hash(state['plan']):
        raise ValueError('History does not bind the committed plan')
    validate_findings(state['findings'], config.output, state['status'] == 'agreed',
                      roster=set(config.roster_ids()))
    for finding in state['findings']:
        if finding['reported_turn'] > state['turns'] or finding['reported_attempt'] > config.max_attempts:
            raise ValueError('Finding exceeds recorded turn/attempt limits')
        if finding['disposition'] and finding['disposition']['turn'] > state['turns']:
            raise ValueError('Finding disposition exceeds committed turns')


def validate(value: dict, path: Path) -> Config:
    if not isinstance(value, dict):
        raise ValueError('Checkpoint must be an object')
    version = value.get('schema_version')
    if type(version) is not int or version not in (CHECKPOINT_SCHEMA_VERSION, PANEL_CHECKPOINT_SCHEMA_VERSION):
        raise ValueError(f'Unsupported checkpoint schema_version: {version!r}')
    _object(value, PANEL_KEYS if version == PANEL_CHECKPOINT_SCHEMA_VERSION else KEYS, 'checkpoint')
    _hash(value['integrity'], 'integrity')
    if hashlib.sha256(canonical(value)).hexdigest() != value['integrity']:
        raise ValueError('Checkpoint integrity mismatch')
    config = config_from(value['config'])
    if version == CHECKPOINT_SCHEMA_VERSION and config.reviewers:
        raise ValueError('Schema version 1 checkpoints require the default reviewer pair')
    if version == PANEL_CHECKPOINT_SCHEMA_VERSION:
        if not config.reviewers:
            raise ValueError('Schema version 2 checkpoints require a non-empty reviewer roster')
        _hash(value['roster_sha256'], 'roster_sha256')
        _hash(value['policy_sha256'], 'policy_sha256')
        members = [{"id": spec.id, "kind": spec.kind, "backend": spec.backend,
                    "model": spec.model, "effort": spec.effort}
                   for spec in config.roster()]
        expected_roster = roster_sha256(members)
        if value['roster_sha256'] != expected_roster:
            raise ValueError('Checkpoint roster hash mismatch')
        expected_policy = policy_sha256(PANEL_RULE, expected_roster)
        if value['policy_sha256'] != expected_policy:
            raise ValueError('Checkpoint policy hash mismatch')
    for key in ('segment', 'sequence', 'approval_epoch', 'attempts_current_turn'):
        _integer(value[key], key)
    if value['attempts_current_turn'] > config.max_attempts:
        raise ValueError('Attempt reservations exceed the turn limit')
    for key in ('duo_version', 'run_id', 'repo', 'document'):
        _string(value[key], key, nonempty=True)
    _path(value['output'])
    if (value['run_id'] != path.name or not Path(value['repo']).is_absolute()
            or Path(value['repo']).resolve() != config.repo or value['output'] != config.output
            or Path(value['document']) != config.repo / config.output):
        raise ValueError('Checkpoint repository/output identity mismatch')
    if type(value['max_seconds']) not in (int, float) or value['max_seconds'] != config.max_seconds:
        raise ValueError('Checkpoint time budget mismatch')
    created = utc(value['created_at'])
    if utc(value['deadline']) < created or utc(value['updated_at']) < created:
        raise ValueError('Inconsistent clock state: deadline/update before creation')
    _choice(value['status'], STATUSES, 'status')
    validate_state(value['state'], config)
    if value['status'] != value['state']['status']:
        raise ValueError('Checkpoint status mismatch')
    _hash(value['document_sha256'], 'document hash')
    _hash(value['document_bytes_sha256'], 'published document bytes hash')
    if content_hash(value['state']['plan']) != value['document_sha256']:
        raise ValueError('Checkpoint plan hash mismatch')
    inputs = value['inputs']
    _object(inputs, {'task', 'criteria', 'context', 'clarification'}, 'inputs')
    clarification = inputs['clarification']
    if clarification is not None:
        _object(clarification, {f.name for f in fields(UserClarification)}, 'clarification')
        for key, item in clarification.items():
            _string(item, key, nonempty=True)
        for key in ('blocked_document_sha256', 'current_document_sha256'):
            _hash(clarification[key], key)
    for key in ('task', 'criteria'):
        _string(inputs[key], key)
        if inputs[key] != value['state'][key]:
            raise ValueError('Checkpoint input mismatch')
    _array(inputs['context'], 'context')
    total = 0
    for index, item in enumerate(inputs['context'], 1):
        _object(item, {'artifact', 'sha256', 'bytes'}, 'context entry')
        if item['artifact'] != f'context-{index:03d}.bin':
            raise ValueError('Invalid context artifact')
        _hash(item['sha256'], 'context hash')
        _integer(item['bytes'], 'context bytes')
        total += item['bytes']
    if total > MAX_CONTEXT_BYTES:
        raise ValueError('Aggregate context exceeds 64 KiB')
    for key in ('in_flight', 'last_attempt'):
        attempt = value[key]
        if attempt is None:
            continue
        keys = {'agent', 'turn', 'attempt'} | ({'reserved_at'} if key == 'in_flight' else {'outcome', 'error'})
        _object(attempt, keys, key)
        if (attempt['agent'] != value['state']['next_agent'] or type(attempt['turn']) is not int
                or attempt['turn'] != value['state']['turns'] + 1):
            raise ValueError('Attempt must bind the next turn')
        _integer(attempt['attempt'], 'attempt', 1)
        if attempt['attempt'] != value['attempts_current_turn']:
            raise ValueError('Attempt reservation mismatch')
        if key == 'in_flight':
            utc(attempt['reserved_at'])
        else:
            _choice(attempt['outcome'], ('interrupted', 'failed'), 'attempt outcome')
            _string(attempt['error'], 'attempt error')
    if (value['attempts_current_turn'] and value['in_flight'] is None
            and value['last_attempt'] is None and value['pending'] is None):
        raise ValueError('Missing reservation retry state')
    _array(value['segments'], 'segments')
    if len(value['segments']) != value['segment'] + 1 or value['approval_epoch'] != value['segment']:
        raise ValueError('Invalid segment lineage')
    previous_sequence = -1
    for index, segment in enumerate(value['segments']):
        _object(segment, {'segment', 'started_at', 'from_sequence'}, 'segment')
        _integer(segment['segment'], 'segment')
        _integer(segment['from_sequence'], 'from_sequence')
        utc(segment['started_at'])
        if (segment['segment'] != index or segment['from_sequence'] <= previous_sequence
                or segment['from_sequence'] > value['sequence']):
            raise ValueError('Invalid segment sequence')
        previous_sequence = segment['from_sequence']
    pending = value['pending']
    if pending is not None:
        if not value['attempts_current_turn']:
            raise ValueError('Transition has no reserved attempt')
        _object(pending, {'turn', 'agent', 'decision', 'before_sha256', 'after_sha256',
                          'staged_state', 'finding_events'}, 'pending')
        for key in ('before_sha256', 'after_sha256'):
            _hash(pending[key], key)
        _choice(pending['decision'], DECISIONS, 'pending decision')
        staged = pending['staged_state']
        validate_state(staged, config)
        if (type(pending['turn']) is not int or pending['turn'] != value['state']['turns'] + 1
                or staged['turns'] != pending['turn'] or pending['agent'] != value['state']['next_agent']
                or staged['history'][:-1] != value['state']['history']
                or pending['decision'] != staged['history'][-1]['decision']
                or pending['before_sha256'] != value['document_bytes_sha256']
                or pending['after_sha256'] != (content_hash(staged['plan']) if pending['decision'] == 'revise'
                                                  else pending['before_sha256'])
                or staged['attempts'] != value['state']['attempts'] + value['attempts_current_turn']
                or any(staged[k] != value['state'][k] for k in ('task', 'criteria'))
                or (pending['decision'] == 'revise') == (pending['before_sha256'] == pending['after_sha256'])):
            raise ValueError('Invalid transition intent')
        _array(pending['finding_events'], 'finding events')
        for event in pending['finding_events']:
            _object(event, {'event', 'id', 'agent', 'turn'}, 'finding event')
            _choice(event['event'], ('finding_reported', 'finding_updated'), 'finding event')
            _string(event['id'], 'finding ID', nonempty=True)
            if event['agent'] != pending['agent'] or event['turn'] != pending['turn']:
                raise ValueError('Finding event turn mismatch')
    return config


def load(path: Path) -> tuple[dict, Config]:
    if not (path / 'checkpoint.json').exists() and not (path / 'checkpoint.json').is_symlink():
        if (path / 'manifest.json').exists() or (path / 'state.json').exists():
            raise RunError('Audit-only run recorded before checkpoints; not resumable (no silent import)')
        raise RunError('Missing checkpoint.json; run cannot be resumed')
    value = read_record(path / 'checkpoint.json')
    try:
        config = validate(value, path)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        raise RunError(f'Invalid checkpoint: {exc}') from exc
    return value, config


def reconcile(value: dict, actual_hash: str, audit: dict | None) -> tuple[dict, list[dict]]:
    """Pure recovery decision; never publish a proposal or touch the document."""
    recovered = deepcopy(value)
    state, pending = recovered['state'], recovered['pending']
    allowed = [state] + ([pending['staged_state']] if pending else [])
    if audit is not None and audit not in allowed:
        raise RunError('Divergent audit state: state.json differs from checkpoint')
    events = []
    if pending:
        if actual_hash == pending['after_sha256']:
            recovered.update(state=pending['staged_state'],
                             document_sha256=content_hash(pending['staged_state']['plan']),
                             document_bytes_sha256=pending['after_sha256'],
                             status=pending['staged_state']['status'], attempts_current_turn=0,
                             in_flight=None, last_attempt=None)
            events = pending['finding_events'] + [review_event(pending)]
        elif actual_hash == pending['before_sha256']:
            events = [{'event': 'proposal_discarded', 'turn': pending['turn'], 'agent': pending['agent']}]
        else:
            raise RunError('document changed outside Duo during pending publication')
        recovered['pending'] = None
    elif actual_hash != recovered['document_bytes_sha256']:
        raise RunError('document changed outside Duo')
    if recovered['in_flight']:
        attempt = recovered['in_flight']
        recovered['last_attempt'] = {k: attempt[k] for k in ('agent', 'turn', 'attempt')}
        recovered['last_attempt'].update(outcome='interrupted', error='Previous attempt was interrupted')
        events.append({'event': 'attempt_interrupted', **recovered['last_attempt']})
        recovered['in_flight'] = None
    return recovered, events


def review_event(pending: dict) -> dict:
    return {'event': 'review', 'turn': pending['turn'], **pending['staged_state']['history'][-1]}


class Journal:
    def __init__(self, store: RunStore, value: dict):
        self.store, self.value = store, deepcopy(value)

    @classmethod
    def create(cls, store, config, state, manifest, context, created_at, deadline):
        try:
            document_bytes = read_input(Path(manifest['document']), follow_symlinks=False)
        except FileNotFoundError:
            document_bytes = b''
        if normalize(document_bytes.decode('utf-8')) != state['plan']:
            raise RunError('document changed outside Duo before checkpoint creation')
        schema = (PANEL_CHECKPOINT_SCHEMA_VERSION if config.reviewers else CHECKPOINT_SCHEMA_VERSION)
        value = dict(schema_version=schema, duo_version=__version__, run_id=store.path.name,
                     segment=0, sequence=0, created_at=created_at, updated_at=created_at,
                     deadline=deadline, max_seconds=config.max_seconds, status='running',
                     repo=str(config.repo), document=manifest['document'], output=config.output,
                     config=config_payload(config),
                     inputs={'task': state['task'], 'criteria': state['criteria'],
                             'clarification': manifest.get('answer'),
                             'context': [{k: item[k] for k in ('artifact', 'sha256', 'bytes')}
                                         for item in context]}, state=deepcopy(state),
                     document_sha256=content_hash(state['plan']),
                     document_bytes_sha256=hashlib.sha256(document_bytes).hexdigest(),
                     attempts_current_turn=0,
                     in_flight=None, last_attempt=None, pending=None, approval_epoch=0,
                     segments=[{'segment': 0, 'started_at': created_at, 'from_sequence': 0}])
        if config.reviewers:
            members = [{"id": spec.id, "kind": spec.kind, "backend": spec.backend,
                        "model": spec.model, "effort": spec.effort}
                       for spec in config.roster()]
            value['roster_sha256'] = roster_sha256(members)
            value['policy_sha256'] = policy_sha256(PANEL_RULE, value['roster_sha256'])
        return cls(store, value)

    def write(self, **changes):
        value = deepcopy({**self.value, **changes})
        value['sequence'] += 1
        value['updated_at'] = timestamp()
        value['integrity'] = hashlib.sha256(canonical(value)).hexdigest()
        data = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        if len(data) > MAX_CHECKPOINT_BYTES:
            raise RunError('Checkpoint exceeds the 16 MiB limit')
        events = self.store.path / 'events.jsonl'
        if events.exists():
            with events.open('rb') as stream:
                os.fsync(stream.fileno())
        durable_write(self.store.path / 'checkpoint.json', data)
        self.value = value

    def reserve(self, state, agent, attempt):
        self.write(attempts_current_turn=attempt, last_attempt=None,
                   in_flight={'agent': agent, 'turn': state['turns'] + 1,
                              'attempt': attempt, 'reserved_at': timestamp()})

    def failed(self, error, outcome='failed'):
        flight = self.value['in_flight']
        if flight:
            last = {k: flight[k] for k in ('agent', 'turn', 'attempt')}
            self.write(in_flight=None, last_attempt={**last, 'outcome': outcome, 'error': error})

    def intent(self, state, staged, agent, decision, events):
        # All turn snapshots, including unchanged approvals, have fixed names.
        # Reviewer already saves revision proposals before entering here.
        if decision != 'revise':
            self.store.snapshot(staged['turns'], staged['plan'])
        fsync_directory(self.store.path)
        self.write(pending={'turn': staged['turns'], 'agent': agent, 'decision': decision,
                            'before_sha256': self.value['document_bytes_sha256'],
                            'after_sha256': (content_hash(staged['plan']) if decision == 'revise'
                                             else self.value['document_bytes_sha256']),
                            'staged_state': staged, 'finding_events': events})

    def commit(self, state):
        published_hash = (self.value['pending']['after_sha256'] if self.value['pending']
                          else self.value['document_bytes_sha256'])
        self.write(document_bytes_sha256=published_hash, state=state, status=state['status'], document_sha256=content_hash(state['plan']),
                   pending=None, in_flight=None, last_attempt=None, attempts_current_turn=0)

    def terminal(self, state):
        if self.value['pending']:
            # Local failures are terminal, unlike process loss. Preserve the
            # published boundary if it can be established, and retain the
            # intent as diagnostic evidence if an external edit prevents it.
            status, error = state['status'], state['error']
            try:
                recovered, _, _ = self.recover(config_from(self.value['config']))
            except (OSError, RunError):
                committed = deepcopy(self.value['state'])
                committed.update(status=status, error=error)
                self.write(state=committed, status=status)
                self.store.json('state.json', committed)
                return
            recovered['attempts'] += self.value['attempts_current_turn']
            recovered.update(status=status, error=error)
            state.update(recovered)
        self.commit(state)
        self.store.json('state.json', state)

    def interrupt(self):
        if not (self.store.path / 'checkpoint.json').exists():
            self.write()
        self.failed('Previous attempt was interrupted', 'interrupted')
        self.store.event({'event': 'interrupted', 'segment': self.value['segment']})

    def recover(self, config):
        """Validate all referenced records before writing recovery artifacts."""
        path, value = self.store.path, self.value
        manifest = read_record(path / 'manifest.json')
        if not isinstance(manifest, dict):
            raise RunError('Invalid manifest.json')
        if (manifest.get('task') != value['inputs']['task']
                or manifest.get('criteria') != value['inputs']['criteria']
                or manifest.get('config') != value['config']
                or manifest.get('document') != value['document']
                or manifest.get('answer') != value['inputs']['clarification']):
            raise RunError('Manifest task/criteria/config/document mismatch')
        context_meta = read_record(path / 'context.json')
        if not isinstance(context_meta, list) or len(context_meta) != len(value['inputs']['context']):
            raise RunError('Context manifest mismatch')
        context = []
        for item, meta in zip(value['inputs']['context'], context_meta):
            if not isinstance(meta, dict) or any(meta.get(k) != v for k, v in item.items()):
                raise RunError('Context manifest mismatch')
            data = read_input(path / item['artifact'], follow_symlinks=False, max_bytes=MAX_CONTEXT_BYTES)
            if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise RunError('Context artifact hash mismatch')
            if not isinstance(meta.get('label'), str) or not isinstance(meta.get('source'), str):
                raise RunError('Invalid context metadata')
            data.decode('utf-8')
            context.append(ContextFile(meta['label'], meta['source'], data))
        for state in [value['state']] + ([value['pending']['staged_state']] if value['pending'] else []):
            data = read_input(path / f"plan-{state['turns']:03d}.md", follow_symlinks=False)
            if hashlib.sha256(data).hexdigest() != content_hash(state['plan']):
                raise RunError('Snapshot hash mismatch')
        try:
            data = read_input(Path(value['document']), follow_symlinks=False)
        except FileNotFoundError:
            data = b''
        actual_hash = hashlib.sha256(data).hexdigest()
        audit_path = path / 'state.json'
        audit = read_record(audit_path) if audit_path.exists() or audit_path.is_symlink() else None
        # Parse the bounded event log before any mutations. A torn tail is refused.
        recorded = []
        if (path / 'events.jsonl').exists() or (path / 'events.jsonl').is_symlink():
            try:
                recorded = [strict_json(line) for line in read_input(
                    path / 'events.jsonl', follow_symlinks=False,
                    max_bytes=MAX_CHECKPOINT_BYTES).splitlines()]
                if any(not isinstance(event, dict) for event in recorded):
                    raise ValueError('event must be an object')
            except (ValueError, UnicodeError) as exc:
                raise RunError(f'Invalid events.jsonl: {exc}') from exc
        # A segment checkpoint precedes its audit-state write. Accept only the
        # old approval list explicitly invalidated at this exact boundary.
        if isinstance(audit, dict) and value['state']['approved'] == []:
            for event in recorded:
                if (event.get('event') == 'approvals_invalidated'
                        and event.get('approval_epoch') == value['approval_epoch']
                        and event.get('after_turn') == value['state']['turns']
                        and event.get('agents') == audit.get('approved')
                        and {**audit, 'approved': []} == value['state']):
                    audit = {**audit, 'approved': []}
                    break
        recovered, events = reconcile(value, actual_hash, audit)
        if normalize(data.decode('utf-8')) != recovered['state']['plan']:
            raise RunError('Published bytes do not match the committed normalized document')
        epochs = {}
        invalidated = {}
        for event in recorded:
            if event.get('event') == 'approvals_invalidated':
                epoch, turn = event.get('approval_epoch'), event.get('after_turn')
                if (type(epoch) is not int or epoch < 1 or type(turn) is not int
                        or not 0 <= turn <= recovered['state']['turns']):
                    raise RunError('Invalid approval invalidation event')
                # Events may precede the segment checkpoint by one interrupted
                # write. They invalidate conservatively, never grant approval.
                if epoch <= value['approval_epoch']:
                    epochs[turn + 1] = epoch
                    invalidated[epoch] = event
        if set(invalidated) != set(range(1, value['approval_epoch'] + 1)):
            raise RunError('Missing approval invalidation evidence')
        roster = set(config.roster_ids())
        for checked in [value['state'], recovered['state']]:
            approvals = set()
            for number, turn in enumerate(checked['history'], 1):
                if approvals == roster:
                    raise RunError('Review history continues after agreement')
                if number in epochs:
                    approvals.clear()
                if turn['decision'] == 'revise':
                    approvals.clear()
                elif turn['decision'] == 'approve':
                    approvals.add(turn['agent'])
            if len(checked['history']) + 1 in epochs:
                approvals.clear()
            if approvals != set(checked['approved']):
                raise RunError('Recorded approvals do not match history and approval epochs')
        review_turns = set()
        for event in recorded:
            if event.get('event') != 'review':
                continue
            turn = event.get('turn')
            history = recovered['state']['history']
            if (type(turn) is not int or not 1 <= turn <= len(history) or turn in review_turns
                    or any(event.get(k) != v for k, v in history[turn - 1].items())):
                raise RunError('Divergent or duplicate review event')
            review_turns.add(turn)
        for turn in range(1, value['state']['turns'] + 1):
            if turn not in review_turns:
                raise RunError('Missing committed review event')
        for event in events:
            # A partial finding-event batch can precede a crash: dedupe each
            # event, not just the final review marker.
            if not any(all(old.get(k) == v for k, v in event.items()) for old in recorded):
                self.store.event(event)
        self.store.json('state.json', recovered['state'])
        self.value = recovered
        # The old pending intent explains either audit state until this commit.
        self.write()
        return deepcopy(recovered['state']), tuple(context), manifest

    def start_segment(self, state, manifest):
        historical = list(state['approved'])
        segment = self.value['segment'] + 1
        entry = {'segment': segment, 'started_at': timestamp(), 'from_sequence': self.value['sequence']}
        # Keep the audit/checkpoint boundary identical; counters are applied to
        # the working state only after this checkpoint has been persisted.
        self.store.event({'event': 'resumed', **entry})
        self.store.event({'event': 'approvals_invalidated', 'agents': historical,
                          'approval_epoch': segment, 'after_turn': state['turns']})
        state['approved'] = []
        self.write(state=state, segment=segment, approval_epoch=segment,
                   segments=[*self.value['segments'], entry])
        self.store.json('state.json', self.value['state'])
        self.store.json('manifest.json', {**manifest, 'segments': self.value['segments']})
        state['attempts'] += self.value['attempts_current_turn']
