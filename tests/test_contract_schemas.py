"""Published contracts must accept actual data and reject uncontrolled fields."""
import importlib.util
import json
from pathlib import Path
import unittest

from gap_agent.contracts import validate_result
from gap_agent.diagnostics import doctor
from test_agent import task

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'JSON Schema dependency is optional offline')
class ContractSchemaTests(unittest.TestCase):
    def test_published_schemas_are_valid_and_accept_actual_candidate(self):
        import jsonschema
        for file in (ROOT / 'schemas').glob('*.schema.json'):
            jsonschema.Draft202012Validator.check_schema(json.loads(file.read_text(encoding='utf-8')))
        schema = json.loads((ROOT / 'schemas/manifest.schema.json').read_text(encoding='utf-8'))
        manifest = json.loads((ROOT / 'data/candidate_manifest.json').read_text(encoding='utf-8'))
        jsonschema.validate(manifest, schema)

    def test_schema_accepts_current_agent_result_and_rejects_raw_command(self):
        import jsonschema
        schema = json.loads((ROOT / 'schemas/agent_result.schema.json').read_text(encoding='utf-8'))
        result = doctor(ROOT)
        jsonschema.validate(result, schema)
        validate_result(result)
        task_schema = json.loads((ROOT / 'schemas/agent_task.schema.json').read_text(encoding='utf-8'))
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(task(command='GAP.NEWFILE()'), task_schema)
