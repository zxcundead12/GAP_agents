"""Offline compiler/normalizer evidence only; no real GAP is simulated as verified."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gap_integration.compiler import MappingError, PROFILE, compile_manifest, metadata_text, normalize_snapshot
from gap_integration.production import verify_runtime_profile
from gap_integration.worker import GapError, run
from gap_integration.contracts import PARAMETERS
from synthetic_engineering import fixture, validate_synthetic


def mapping():
    return {'profile_id': PROFILE, 'review_status': 'confirmed', 'reviewed_by': 'Synthetic test fixture',
            'reviewed_at': '2026-11-22T12:00:00+03:00',
            'node_types': {'junction': 'JOINT', 'source': 'SOURCE', 'boundary': 'SINK'},
            'valve_representation': 'reviewed_pipe_mask_with_metadata',
            'metadata_fields_acknowledged': True, 'section_index': 1, 'array_field_index': False,
            'units': deepcopy(PARAMETERS), 'pipe_model_mode': 'enthalpy_balance_preconfigured_template',
            'implicit_pipe_pairs': [['JOINT', 'JOINT'], ['JOINT', 'SEP']]}


def recorded_snapshot():
    # Explicit synthetic responses shaped like native reads. Parameter values differ from the manifest fixture.
    nodes = [{'label': 'NativeA', 'type': 'SOURCE', 'unique_id': '11',
              'comment': metadata_text('case', {'id': 'A', 'kind': 'source', 'original_label': 'Куст A'})},
             {'label': 'NativeB', 'type': 'JOINT', 'unique_id': '12',
              'comment': metadata_text('case', {'id': 'B', 'kind': 'junction', 'original_label': 'Узел B'})}]
    pipe = {'label': 'NativeP', 'type': 'PIPE', 'unique_id': '13', 'from_node': 'NativeA', 'to_node': 'NativeB',
            'is_masked': '1', 'native_units': deepcopy(PARAMETERS),
            'native_parameters': {'length_m': 123.5, 'outer_diameter_mm': 219, 'wall_thickness_mm': 8, 'inner_diameter_mm': 203},
            'comment': metadata_text('case', {'id': 'P', 'kind': 'pipe', 'original_label': 'Труба', 'active_state': 'active',
                       'valves': [{'id': 'V', 'pipe_id': 'P', 'physical_state': 'closed', 'original_label': 'Задвижка',
                                   'size_dn_mm': 150, 'pressure_rating_raw': '40'}]})}
    return {'count': 3, 'equipment': nodes + [pipe]}


class ProductionTests(unittest.TestCase):
    def setUp(self):
        validator = patch('gap_integration.compiler.validate_manifest', side_effect=validate_synthetic)
        validator.start()
        self.addCleanup(validator.stop)

    def test_compiler_produces_stable_ids_and_never_authorizes_runtime(self):
        first = compile_manifest(fixture(), mapping())
        self.assertEqual(first, compile_manifest(fixture(), mapping()))
        self.assertEqual((len(first['nodes']), len(first['pipes'])), (12, 12))
        self.assertFalse(first['runtime_verified'])
        self.assertTrue(first['pipes'][0]['masked'])
        self.assertFalse(first['pipes'][1]['masked'])
        self.assertTrue(all(len(item['label']) == 9 for item in first['nodes'] + first['pipes']))

    def test_pending_or_unknown_engineering_data_cannot_compile(self):
        for mutate in ('selection', 'parameter', 'state'):
            manifest = fixture()
            if mutate == 'selection':
                manifest['selection']['review_status'] = 'pending'
            elif mutate == 'parameter':
                manifest['pipes'][0]['parameters']['length_m'] = None
            else:
                manifest['valves'][0]['physical_state'] = 'unknown'
            with self.subTest(mutate=mutate), self.assertRaises(MappingError):
                compile_manifest(manifest, mapping())

    def test_mapping_cannot_inject_a_command_or_assume_units(self):
        for key, value in (('node_types', {'junction': 'JOINT");GAP.NEWFILE()', 'source': 'SOURCE', 'boundary': 'SINK'}),
                           ('units', {'length_m': 'feet'}), ('section_index', True),
                           ('metadata_fields_acknowledged', False), ('valve_representation', 'native_unknown')):
            altered = mapping()
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(MappingError):
                compile_manifest(fixture(), altered)

    def test_multiple_valves_are_refused_without_a_supported_series_mapping(self):
        manifest = fixture()
        manifest['valves'][1]['pipe_id'] = manifest['valves'][0]['pipe_id']
        with self.assertRaises(MappingError):
            compile_manifest(manifest, mapping())

    def test_normalized_export_uses_only_actual_snapshot_values(self):
        snapshot = recorded_snapshot()
        before = deepcopy(snapshot)
        export = normalize_snapshot(snapshot, {'evidence': 'synthetic'}, 'case')
        self.assertEqual(export['pipes'][0]['parameters']['length_m'], 123.5)
        self.assertEqual(export['pipes'][0]['from_node'], 'A')
        self.assertEqual(export['valves'][0]['physical_state'], 'closed')
        self.assertEqual(export['model']['evidence'], 'synthetic')
        self.assertEqual(snapshot, before)
        self.assertFalse(export['hydraulics_performed'])

    def test_inconsistent_native_mask_cannot_be_reported_as_a_closed_valve(self):
        snapshot = recorded_snapshot()
        snapshot['equipment'][2]['is_masked'] = '0'
        with self.assertRaises(MappingError):
            normalize_snapshot(snapshot, {'evidence': 'synthetic'}, 'case')

    def test_missing_native_parameters_are_not_replaced_by_metadata(self):
        snapshot = recorded_snapshot()
        del snapshot['equipment'][2]['native_parameters']['length_m']
        with self.assertRaises(MappingError):
            normalize_snapshot(snapshot, {'evidence': 'synthetic'}, 'case')

    def test_wrong_units_dangling_endpoints_and_foreign_equipment_are_rejected(self):
        for mutation in ('unit', 'endpoint', 'comment', 'duplicate'):
            snapshot = recorded_snapshot()
            if mutation == 'unit':
                snapshot['equipment'][2]['native_units']['length_m'] = 'ft'
            elif mutation == 'endpoint':
                snapshot['equipment'][2]['to_node'] = 'Missing'
            elif mutation == 'comment':
                snapshot['equipment'][0]['comment'] = 'not metadata'
            else:
                snapshot['equipment'][1]['label'] = snapshot['equipment'][0]['label']
            with self.subTest(mutation=mutation), self.assertRaises(MappingError):
                normalize_snapshot(snapshot, {'evidence': 'synthetic'}, 'case')

    def test_unverified_profile_fails_before_com_activation(self):
        with tempfile.TemporaryDirectory() as directory, patch('gap_integration.worker.Client') as client:
            with self.assertRaises(GapError) as caught:
                run({'workspace': directory, 'trace_id': 'synthetic', 'journal_path': str(Path(directory) / 'log'),
                     'job': {'operation': 'build', 'manifest': fixture(), 'mapping': mapping()}})
            self.assertEqual(caught.exception.status, 'UNSUPPORTED_MAPPING')
            client.assert_not_called()


if __name__ == '__main__':
    unittest.main()
