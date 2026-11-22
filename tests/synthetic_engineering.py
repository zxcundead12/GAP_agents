"""Explicit unit fixtures, not participant 1's engineering validator or source data."""
from copy import deepcopy
from gap_integration.contracts import PARAMETERS, confirmed, finite_number


def validate_synthetic(manifest, project_root=None, check_sources=True):
    """Injectable status stub for service/compiler tests; not used by production."""
    objects = [manifest.get('selection', {})]
    for name in ('nodes', 'pipes', 'valves'):
        objects.extend(manifest.get(name, []))
    ready = all(confirmed(item) for item in objects)
    ready = ready and all(all(finite_number(pipe.get('parameters', {}).get(key))
                             and pipe['parameters'][key] > 0 for key in PARAMETERS)
                          for pipe in manifest.get('pipes', []))
    ready = ready and all(valve.get('physical_state') in ('open', 'closed') for valve in manifest.get('valves', []))
    return {'schema_version': '1.0', 'case_id': manifest.get('case_id'), 'execution_status': 'SUCCESS',
            'ready_for_transfer': bool(ready), 'issues': [], 'stats': {}, 'topology': {}}


def fixture():
    reviewed = {'review_status': 'confirmed', 'reviewed_by': 'Synthetic test reviewer',
                'reviewed_at': '2026-11-22T12:00:00+03:00'}
    ref = {'source_id': 'synthetic', 'page': 1, 'bbox': [1, 1, 20, 20], 'raw_text': 'synthetic test only',
           'method': 'visual', 'fields': list(PARAMETERS)}
    return {'schema_version': '1.0', 'case_id': 'synthetic-unit-only',
            'selection': {'id': 'selection', 'regions': [ref], **reviewed},
            'sources': [], 'ambiguities': [],
            'nodes': [{'id': f'N{i}', 'original_label': f'Node {i}', 'kind': 'source' if i < 2 else 'junction',
                       'source_refs': [deepcopy(ref)], **reviewed} for i in range(12)],
            'pipes': [{'id': f'P{i}', 'original_label': f'Pipe {i}', 'from_node': f'N{i}', 'to_node': f'N{(i+1)%12}',
                       'parameters': {'length_m': 10, 'outer_diameter_mm': 159, 'wall_thickness_mm': 8, 'inner_diameter_mm': 143},
                       'active_state': 'active', 'source_refs': [deepcopy(ref)], **reviewed} for i in range(12)],
            'valves': [{'id': f'V{i}', 'pipe_id': f'P{i}', 'original_label': f'Valve {i}',
                        'physical_state': 'closed' if i == 0 else 'open', 'size_dn_mm': 150,
                        'pressure_rating_raw': '40', 'source_refs': [deepcopy(ref)], **reviewed} for i in range(4)]}
