"""Regression checks for review bindings; all responses/files are unit fixtures."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from gap_integration.compiler import MappingError, normalize_snapshot
from gap_integration.production import REQUIRED_FEATURES, enriched_snapshot, ref, verify_runtime_profile
from test_production import mapping, recorded_snapshot


class ProductionReviewTests(unittest.TestCase):
    def test_real_normalization_requires_a_reviewed_mapping(self):
        with self.assertRaises(MappingError):
            normalize_snapshot(recorded_snapshot(), {'evidence': 'real'}, 'case')
        pending = mapping()
        pending['review_status'] = 'pending'
        with self.assertRaises(MappingError):
            normalize_snapshot(recorded_snapshot(), {'evidence': 'real'}, 'case', mapping=pending)

    def test_real_normalization_matches_exact_approved_native_type(self):
        snapshot = recorded_snapshot()
        snapshot['equipment'][0]['type'] = 'WELL'
        with self.assertRaises(MappingError):
            normalize_snapshot(snapshot, {'evidence': 'real'}, 'case', mapping=mapping())
        selected = mapping()
        selected['node_types']['source'] = 'WELL'
        normalized = normalize_snapshot(snapshot, {'evidence': 'real'}, 'case', mapping=selected)
        self.assertEqual(normalized['nodes'][0]['kind'], 'source')

    def test_synthetic_unit_snapshot_remains_supported_without_a_runtime_proof(self):
        export = normalize_snapshot(recorded_snapshot(), {'evidence': 'synthetic'}, 'case')
        self.assertEqual(export['model']['evidence'], 'synthetic')

    def test_native_model_data_cannot_inject_tag_fragments(self):
        class RecordingClient:
            def __init__(self):
                self.calls = []

            def get(self, tag):
                self.calls.append(tag)
                raise AssertionError('No tag should be formed from foreign data')

        for item in ({'type': 'JOINT', 'label': 'Nabcd1234}].OTHER'},
                     {'type': 'JOINT].OTHER', 'label': 'Nabcd1234'},
                     {'type': 'PIPE', 'label': 'Nabcd1234'}):
            client = RecordingClient()
            with self.subTest(item=item), self.assertRaises(MappingError):
                enriched_snapshot(client, mapping(), lambda _: {'count': 1, 'equipment': [deepcopy(item)]})
            self.assertEqual(client.calls, [])
        self.assertEqual(ref('JOINT', 'Nabcd1234'), 'MOD[0].JOINT[{Nabcd1234}]')

    def test_normalized_metadata_rejects_non_object_comment_and_invalid_valve_values(self):
        for malformed in ('[]', 'null', '42'):
            snapshot = recorded_snapshot()
            snapshot['equipment'][0]['comment'] = malformed
            with self.subTest(comment=malformed), self.assertRaises(MappingError):
                normalize_snapshot(snapshot, {'evidence': 'synthetic'}, 'case')
        for value in (True, float('nan'), -1, '150'):
            snapshot = recorded_snapshot()
            metadata = json.loads(snapshot['equipment'][2]['comment'])
            metadata['valves'][0]['size_dn_mm'] = value
            snapshot['equipment'][2]['comment'] = json.dumps(metadata)
            with self.subTest(dn=value), self.assertRaises(MappingError):
                normalize_snapshot(snapshot, {'evidence': 'synthetic'}, 'case')

    def test_native_binary_mask_format_does_not_change_its_meaning(self):
        snapshot = recorded_snapshot()
        snapshot['equipment'][2]['is_masked'] = '1.0'
        self.assertEqual(normalize_snapshot(snapshot, {'evidence': 'synthetic'}, 'case')['valves'][0]['physical_state'], 'closed')
        for invalid in ('NaN', '2', True, []):
            snapshot['equipment'][2]['is_masked'] = invalid
            with self.subTest(mask=invalid), self.assertRaises(MappingError):
                normalize_snapshot(snapshot, {'evidence': 'synthetic'}, 'case')

    def test_runtime_proof_needs_its_own_review_not_only_mapping_review(self):
        # These placeholder bytes test bindings only.  They are not real GAP
        # evidence and are never copied into a working verification protocol.
        work = Path(__file__).resolve().parents[1] / 'work'
        with tempfile.TemporaryDirectory(prefix='review-proof-test-', dir=work) as directory:
            root = Path(directory)
            template, model, journal = root / 'template.gap', root / 'fixture.gap', root / 'fixture.jsonl'
            for file in (template, model, journal):
                file.write_bytes(b'Synthetic unit fixture, not runtime evidence.')
            selected = mapping()
            selected.update(template_path='template.gap', template_sha256=hashlib.sha256(template.read_bytes()).hexdigest(),
                            verification_path='proof.json')
            frozen = {key: value for key, value in selected.items() if key != 'verification_path'}
            proof = {'evidence': 'real', 'execution_status': 'SUCCESS', 'profile_id': selected['profile_id'],
                     'software_version': 'IPM 7.5 Build 600 / GAP 8.5',
                     'mapping_sha256': hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest(),
                     'verified_features': sorted(REQUIRED_FEATURES),
                     'model': {'path': model.name, 'sha256': hashlib.sha256(model.read_bytes()).hexdigest()},
                     'journal': {'path': journal.name, 'sha256': hashlib.sha256(journal.read_bytes()).hexdigest()}}
            path = root / 'proof.json'
            path.write_text(json.dumps(proof), encoding='utf-8')
            with self.assertRaises(MappingError):
                verify_runtime_profile(selected, root)
            proof.update(review_status='confirmed', reviewed_by='Synthetic unit reviewer', reviewed_at='2026-11-23T01:00:00+03:00')
            path.write_text(json.dumps(proof), encoding='utf-8')
            self.assertEqual(verify_runtime_profile(selected, root), template)
            model.write_bytes(b'changed fixture')
            with self.assertRaises(MappingError):
                verify_runtime_profile(selected, root)


if __name__ == '__main__':
    unittest.main()
