import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from gap_manifest.sources import resolve_source
from gap_manifest.validation import validate_manifest
from test_validation import fixture


class SourceLocationsTests(unittest.TestCase):
    def test_relocation_preserves_manifest_and_still_checks_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            source = root / 'actual.pdf'
            source.write_bytes(b'synthetic source')
            manifest = fixture()
            manifest['sources'][0]['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
            original = json.dumps(manifest)
            (root / 'data/source_locations.local.json').write_text(json.dumps({'pdf': str(source)}))
            self.assertTrue(validate_manifest(manifest, root)['ready_for_transfer'])
            self.assertEqual(json.dumps(manifest), original)
            source.write_bytes(b'changed')
            self.assertIn('SOURCE_CHANGED', {i['code'] for i in validate_manifest(manifest, root)['issues']})

    def test_malformed_location_file_is_reported_without_accepting_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            (root / 'data/source_locations.local.json').write_text('[]')
            result = validate_manifest(fixture(), root)
            self.assertFalse(result['ready_for_transfer'])
            self.assertIn('SOURCE_UNAVAILABLE', {item['code'] for item in result['issues']})


if __name__ == '__main__':
    unittest.main()
