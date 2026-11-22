"""Common contracts. LLM output carries intent, never engineering values or API code."""
from dataclasses import asdict, dataclass
from pathlib import Path

OPERATIONS = ('inspect', 'validate', 'report', 'compare', 'build', 'smoke', 'export', 'unsupported')
PATH_FIELDS = ('manifest_path', 'mapping_path', 'export_path', 'model_path', 'tolerances_path')
TASK_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'operation': {'type': 'string', 'enum': list(OPERATIONS)},
        'case_id': {'type': ['string', 'null']},
        **{key: {'type': ['string', 'null']} for key in PATH_FIELDS},
        'clarifications': {'type': 'array', 'items': {'type': 'string'}},
    },
    'required': ['operation', 'case_id', *PATH_FIELDS, 'clarifications'],
}


def workspace_path(root, raw):
    if not isinstance(raw, str) or not raw.strip() or any(c in raw for c in ('\0', '\r', '\n')):
        raise ValueError('Expected a nonempty workspace path')
    path = Path(raw)
    if '..' in path.parts or (path.drive and not path.is_absolute()):
        raise ValueError('Parent traversal and drive-relative paths are forbidden')
    if any(':' in part for part in path.parts if part != path.anchor):
        raise ValueError('Alternate streams are forbidden')
    path = (path if path.is_absolute() else Path(root) / path).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('Path escapes workspace')
    return path


@dataclass(frozen=True)
class AgentTask:
    operation: str
    case_id: str | None
    manifest_path: str | None
    mapping_path: str | None
    export_path: str | None
    model_path: str | None
    tolerances_path: str | None
    clarifications: list[str]

    @classmethod
    def parse(cls, value, root):
        if not isinstance(value, dict) or set(value) != set(TASK_SCHEMA['required']):
            raise ValueError('Task has missing or additional fields')
        if value['operation'] not in OPERATIONS:
            raise ValueError('Operation is not allowed')
        if value['case_id'] is not None and (not isinstance(value['case_id'], str) or not value['case_id'].strip()):
            raise ValueError('Invalid case_id')
        questions = value['clarifications']
        if not isinstance(questions, list) or len(questions) > 10 or any(not isinstance(q, str) or not q.strip() for q in questions):
            raise ValueError('Invalid clarifications')
        for key in PATH_FIELDS:
            raw = value[key]
            if raw is not None:
                if not isinstance(raw, str) or not raw.strip() or len(raw) > 2048:
                    raise ValueError('Invalid ' + key)
                path = workspace_path(root, raw)
                if key != 'model_path' and path.suffix.lower() != '.json':
                    raise ValueError(key + ' must be JSON')
                if key == 'model_path' and path.suffix.lower() != '.gap':
                    raise ValueError('Model must have .gap extension')
        required = {'compare': ('export_path',), 'export': ('model_path',)}.get(value['operation'], ())
        if not questions and any(value[key] is None for key in required):
            raise ValueError('Missing operation input')
        return cls(**value)

    def to_dict(self):
        return asdict(self)


EXECUTION_STATUSES = {
    'SUCCESS', 'ERROR', 'NOT_READY', 'CONFIRMATION_REQUIRED', 'NEEDS_CLARIFICATION',
    'LLM_NOT_CONFIGURED', 'LLM_UNAVAILABLE', 'LLM_INVALID_OUTPUT', 'MCP_UNAVAILABLE',
    'LICENSE_UNAVAILABLE', 'LICENSE_BUSY', 'COM_UNAVAILABLE', 'SESSION_BUSY',
    'TIMEOUT', 'API_ERROR', 'UNSUPPORTED_MAPPING', 'OUT_OF_SCOPE',
}


def validate_result(value):
    fields = {'schema_version', 'run_id', 'trace_id', 'execution_status', 'assessment_status',
              'message', 'task', 'plan', 'artifacts', 'issues', 'llm'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('AgentResult fields are invalid')
    if value['schema_version'] != '1.0' or value['execution_status'] not in EXECUTION_STATUSES:
        raise ValueError('AgentResult status is invalid')
    if value['assessment_status'] not in ('OK', 'OUT_OF_TOLERANCE', 'NOT_ASSESSED'):
        raise ValueError('Invalid assessment status')
    if not all(isinstance(value[k], str) and value[k] for k in ('run_id', 'trace_id', 'message')):
        raise ValueError('Invalid result identity')
    if not all(isinstance(value[k], dict) for k in ('artifacts', 'llm')) or not isinstance(value['issues'], list):
        raise ValueError('Invalid result collections')
    if any(value[k] is not None and not isinstance(value[k], dict) for k in ('task', 'plan')):
        raise ValueError('Invalid task or plan')
    return value

