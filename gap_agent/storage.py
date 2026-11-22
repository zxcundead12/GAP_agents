"""Local immutable plans, hash-bound receipts and a durable execution claim."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from .contracts import workspace_path


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'),
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))


class RunStore:
    def __init__(self, root, secret_env_names=None):
        self.root = Path(root).resolve()
        if isinstance(secret_env_names, str):
            secret_env_names = [secret_env_names]
        if not isinstance(secret_env_names, (list, tuple, set, frozenset)):
            secret_env_names = ()
        self.secret_env_names = {'GROQ_API_KEY'} | {
            name for name in (secret_env_names or ())
            if isinstance(name, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', name)
        }
        self.folder = workspace_path(self.root, 'outputs/agent')
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db_path = self.folder / 'runs.sqlite3'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, plan_hash TEXT NOT NULL, state TEXT NOT NULL, receipt TEXT, result TEXT)')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def run_folder(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r'run_[0-9a-f]{32}', run_id):
            raise ValueError('Invalid run ID')
        folder = workspace_path(self.root, str(self.folder / run_id))
        folder.mkdir(exist_ok=True)
        return folder

    def save(self, run_id, name, value):
        if not re.fullmatch(r'[a-z_]+\.json', name):
            raise ValueError('Invalid artifact name')
        target = workspace_path(self.root, str(self.run_folder(run_id) / name))
        with target.open('xb') as stream:
            stream.write(encode(value))
        return target

    def journal(self, run_id, event, **fields):
        record = {'trace_id': run_id, 'at': now(), 'event': event, **fields}
        secret_names = getattr(self, 'secret_env_names', {'GROQ_API_KEY'})
        secrets = set()
        for name, value in os.environ.items():
            if value and (name in secret_names or (len(value) >= 8 and any(
                    part in name.upper() for part in ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD')))):
                secrets.add(value)

        def redact(value):
            if isinstance(value, str):
                for secret in sorted(secrets, key=len, reverse=True):
                    value = value.replace(secret, '[REDACTED]')
                return value
            if isinstance(value, dict):
                return {redact(key) if isinstance(key, str) else key: redact(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [redact(item) for item in value]
            return value

        # Redact before JSON escaping so quotes/backslashes/newlines in a key
        # cannot bypass matching. Do not mutate caller-owned results or plans.
        raw = json.dumps(redact(record), ensure_ascii=False, allow_nan=False)
        with (self.run_folder(run_id) / 'journal.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(raw + '\n')

    def create_plan(self, plan):
        sha = digest(plan)
        self.save(plan['run_id'], 'plan.json', plan)
        with self.db() as db:
            db.execute('INSERT INTO runs VALUES (?, ?, ?, NULL, NULL)', (plan['run_id'], sha, 'PREPARED'))
        return sha

    def load(self, run_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown plan')
        plan = read_json(self.run_folder(run_id) / 'plan.json')
        if digest(plan) != row['plan_hash'] or plan.get('run_id') != run_id:
            raise ValueError('Plan changed after preparation')
        return dict(row), plan

    def approve(self, run_id, receipt):
        row, _ = self.load(run_id)
        if receipt['plan_sha256'] != row['plan_hash']:
            raise ValueError('Approval hash mismatch')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            if current['state'] == 'APPROVED':
                return json.loads(current['receipt'])
            if current['state'] != 'PREPARED':
                raise ValueError('Plan is not awaiting approval')
            db.execute("UPDATE runs SET state='APPROVED',receipt=? WHERE id=?", (json.dumps(receipt), run_id))
        return receipt

    def claim(self, run_id):
        self.load(run_id)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = dict(db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone())
            if row['result'] is not None:
                return 'CACHED', json.loads(row['result'])
            if row['state'] == 'APPROVED':
                blocker = db.execute("SELECT id FROM runs WHERE id<>? AND state IN ('RUNNING','UNKNOWN') LIMIT 1", (run_id,)).fetchone()
                if blocker:
                    return 'BLOCKED', None
                db.execute("UPDATE runs SET state='RUNNING' WHERE id=?", (run_id,))
                return 'CLAIMED', json.loads(row['receipt'])
            return row['state'], None

    def finish(self, run_id, result, *, native_result=None):
        def explicitly_uncertain(value):
            if not isinstance(value, dict):
                return False
            if value.get('manual_review_required') is True or value.get('plan_state') in ('UNKNOWN', 'RUNNING'):
                return True
            issues = value.get('issues')
            if not isinstance(issues, list):
                return False
            return any(isinstance(issue, dict) and issue.get('code') in (
                'NATIVE_EXECUTION_UNCERTAIN', 'MANUAL_REVIEW_REQUIRED'
            ) for issue in issues)

        # finish is called only after a write plan was claimed. Generic errors
        # at that point may follow partial writes and require manual review.
        # Known refusals (e.g. COM_UNAVAILABLE, SESSION_BUSY, NOT_READY) remain
        # terminal DONE unless the native layer explicitly reports uncertainty.
        uncertain = result['execution_status'] in ('TIMEOUT', 'MCP_UNAVAILABLE', 'API_ERROR', 'ERROR')
        uncertain = uncertain or explicitly_uncertain(result) or explicitly_uncertain(native_result)
        state = 'UNKNOWN' if uncertain else 'DONE'
        with self.db() as db:
            db.execute('UPDATE runs SET state=?,result=? WHERE id=?', (state, json.dumps(result), run_id))

    def resolve(self, run_id, operator, note):
        if not isinstance(operator, str) or not operator.strip() or not isinstance(note, str) or not note.strip():
            raise ValueError('Manual investigation needs an operator and a note')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM runs WHERE id=?', (run_id,)).fetchone()
            if row is None or row['state'] not in ('UNKNOWN', 'RUNNING'):
                raise ValueError('Only uncertain runs can be resolved')
            db.execute("UPDATE runs SET state='RESOLVED_NO_RETRY' WHERE id=?", (run_id,))
        self.journal(run_id, 'manual_resolution', reviewed_by=operator, note=note,
                     execution_cancelled=False, automatic_retry_permitted=False)

