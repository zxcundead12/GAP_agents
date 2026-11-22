"""Publish the shared machine-readable contracts used by all three parts."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gap_agent.contracts import TASK_SCHEMA, EXECUTION_STATUSES


def engineering_schemas():
    text = {'type': 'string', 'minLength': 1}
    nullable_text = {'type': ['string', 'null']}
    reference = {'type': 'object', 'required': ['source_id', 'raw_text', 'method', 'fields'],
                 'properties': {'source_id': text, 'raw_text': text, 'method': {'enum': ['visual', 'text_layer', 'derived', 'text_file']},
                                'fields': {'type': 'array', 'items': text, 'minItems': 1}}}
    common = {'id': text, 'original_label': text, 'review_status': {'enum': ['pending', 'confirmed', 'rejected']},
              'reviewed_by': nullable_text, 'reviewed_at': nullable_text,
              'source_refs': {'type': 'array', 'items': reference, 'minItems': 1}}
    parameters = {'type': 'object', 'additionalProperties': False,
                  'properties': {key: {'type': ['number', 'null'], 'exclusiveMinimum': 0, 'x-unit': unit}
                                 for key, unit in {'length_m': 'm', 'outer_diameter_mm': 'mm',
                                                  'wall_thickness_mm': 'mm', 'inner_diameter_mm': 'mm'}.items()},
                  'required': ['length_m', 'outer_diameter_mm', 'wall_thickness_mm', 'inner_diameter_mm']}
    def entity(properties, required):
        return {'type': 'object', 'properties': {**common, **properties}, 'required': list(common) + required}
    node = entity({'kind': {'enum': ['junction', 'source', 'boundary']}, 'context': text,
                   'position': {'type': 'array', 'items': {'type': 'number', 'minimum': 0}, 'minItems': 2, 'maxItems': 2}},
                  ['kind', 'context', 'position'])
    pipe = entity({'from_node': text, 'to_node': text, 'parameters': parameters,
                   'role': {'enum': ['pipeline', 'internal_connector']},
                   'active_state': {'enum': ['active', 'inactive', 'unknown']}},
                  ['from_node', 'to_node', 'parameters', 'role', 'active_state'])
    valve = entity({'pipe_id': text, 'physical_state': {'enum': ['open', 'closed', 'unknown']},
                    'size_dn_mm': {'type': ['number', 'null'], 'exclusiveMinimum': 0},
                    'pressure_rating_raw': nullable_text},
                   ['pipe_id', 'physical_state', 'size_dn_mm', 'pressure_rating_raw'])
    manifest = {'type': 'object', 'required': ['schema_version', 'case_id', 'sources', 'selection', 'nodes', 'pipes', 'valves', 'ambiguities'],
                'properties': {'schema_version': {'const': '1.0'}, 'case_id': text,
                    'sources': {'type': 'array', 'items': {'type': 'object', 'required': ['id', 'path', 'sha256', 'pages'],
                        'properties': {'id': text, 'path': text, 'sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
                                       'pages': {'type': 'array'}}}},
                    'selection': {'type': 'object'}, 'ambiguities': {'type': 'array'},
                    'nodes': {'type': 'array', 'items': node}, 'pipes': {'type': 'array', 'items': pipe},
                    'valves': {'type': 'array', 'items': valve}}}
    def exported(source, required):
        return {'type': 'object', 'properties': {key: source['properties'][key] for key in required}, 'required': required}
    export = {'type': 'object', 'required': ['schema_version', 'case_id', 'model', 'nodes', 'pipes', 'valves'],
              'properties': {'schema_version': {'const': '1.0'}, 'case_id': text,
                  'model': {'type': 'object', 'required': ['path', 'software_version', 'exported_at', 'evidence'],
                            'properties': {'path': text, 'software_version': text, 'exported_at': text,
                                           'evidence': {'enum': ['real', 'synthetic']}}},
                  'nodes': {'type': 'array', 'items': exported(node, ['id', 'kind', 'original_label'])},
                  'pipes': {'type': 'array', 'items': exported(pipe, ['id', 'from_node', 'to_node', 'parameters', 'active_state'])},
                  'valves': {'type': 'array', 'items': exported(valve, ['id', 'pipe_id', 'physical_state', 'size_dn_mm', 'pressure_rating_raw'])}}}
    return {'manifest': manifest, 'gap_export': export}


def main():
    folder = ROOT / 'schemas'
    folder.mkdir(exist_ok=True)
    schemas = {'agent_task': TASK_SCHEMA,
               'agent_result': {
                   'type': 'object', 'additionalProperties': False,
                   'properties': {
                       'schema_version': {'const': '1.0'},
                       'run_id': {'type': 'string', 'pattern': '^run_[0-9a-f]{32}$'},
                       'trace_id': {'type': 'string', 'minLength': 1},
                       'execution_status': {'enum': sorted(EXECUTION_STATUSES)},
                       'assessment_status': {'enum': ['OK', 'OUT_OF_TOLERANCE', 'NOT_ASSESSED']},
                       'message': {'type': 'string', 'minLength': 1},
                       'task': {'anyOf': [TASK_SCHEMA, {'type': 'null'}]},
                       'plan': {'type': ['object', 'null']}, 'artifacts': {'type': 'object'},
                       'issues': {'type': 'array'}, 'llm': {'type': 'object'},
                   },
                   'required': ['schema_version', 'run_id', 'trace_id', 'execution_status', 'assessment_status',
                                'message', 'task', 'plan', 'artifacts', 'issues', 'llm'],
               },
               'change_plan': {
                   'type': 'object', 'additionalProperties': False,
                   'properties': {
                       'schema_version': {'const': '1.0'}, 'run_id': {'type': 'string'},
                       'created_at': {'type': 'string'}, 'task': TASK_SCHEMA,
                       'inputs': {'type': 'object'}, 'integration_plan_id': {'type': 'string'},
                       'integration_plan_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
                       'target_model_path': {'type': 'string'}, 'actions': {'type': 'object'}, 'llm': {'type': 'object'},
                   },
                   'required': ['schema_version', 'run_id', 'created_at', 'task', 'inputs', 'integration_plan_id',
                                'integration_plan_sha256', 'target_model_path', 'actions', 'llm'],
               }}
    schemas.update(engineering_schemas())
    for name, schema in schemas.items():
        schema = {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': name, **schema}
        (folder / (name + '.schema.json')).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Schemas: ' + ', '.join(schemas))


if __name__ == '__main__':
    main()
