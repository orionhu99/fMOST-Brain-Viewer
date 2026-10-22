"""Update startup restores scene state without atlas downloads or source writes."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import fmost_brain_viewer as v
from viewer_update_state import write_update_resume


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / ('resume-' + 'a' * 32 + '.fmost-session.json')
        self.original = self.root / 'saved.json'
        self.original.write_text('unchanged')
        self.payload = {'format': 'fmost-brain-viewer-session', 'format_version': 2,
                        'datasets': [], 'atlas_signature': 'fixture', 'visible_neurons': ['sample_A::1'],
                        'camera_position': [[1, 2, 3], [0, 0, 0], [0, 1, 0]], 'axon_width': 2.0}
        write_update_resume(self.path, self.root, self.payload, original_session=self.original,
                            atlas_directory=self.root / 'atlas', target_version='3.2.0', was_dirty=True)
        self.settings = v.QtCore.QSettings(str(self.root / 'settings.ini'), v.QtCore.QSettings.Format.IniFormat)
        self.settings.setValue('updates/pending_resume', str(self.path))
        for patch in (mock.patch.object(v, 'UPDATE_ROOT', self.root),
                      mock.patch.object(v.QtCore, 'QSettings', return_value=self.settings)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_startup_preserves_original_save_path_and_scene(self):
        with mock.patch.object(v, '_snapshot_active_atlas', return_value={}), \
             mock.patch.object(v, 'activate_atlas') as activate, \
             mock.patch.object(v, 'ATLAS_SIGNATURE', 'fixture'), \
             mock.patch.object(v, 'load_session_file', return_value=([('sample_A', self.root)], self.payload)), \
             mock.patch.object(v, 'ensure_atlas_configured', side_effect=AssertionError('must not download')):
            projects, config, original = v.load_update_startup(self.path)
        activate.assert_called_once_with((self.root / 'atlas').resolve())
        self.assertEqual(original, self.original.resolve())
        self.assertEqual(config['camera_position'], self.payload['camera_position'])
        self.assertEqual(self.original.read_text(), 'unchanged')

    def test_failed_restore_rolls_back_atlas_and_keeps_pending_snapshot(self):
        with mock.patch.object(v, '_snapshot_active_atlas', return_value={'saved': True}), \
             mock.patch.object(v, 'activate_atlas', side_effect=OSError('unavailable')), \
             mock.patch.object(v, '_restore_active_atlas') as rollback:
            with self.assertRaises(OSError):
                v.load_update_startup(self.path)
        rollback.assert_called_once_with({'saved': True})
        self.assertTrue(self.path.exists())
        self.assertEqual(self.settings.value('updates/pending_resume'), str(self.path))

    def test_success_acknowledges_only_after_restoration_and_keeps_dirty_state(self):
        window = SimpleNamespace(_saved_state='old', _write_recovery=mock.Mock(), status=mock.Mock())
        v.complete_update_resume(window, self.path)
        self.assertEqual(window._saved_state, '')
        window._write_recovery.assert_called_once()
        self.assertFalse(self.settings.contains('updates/pending_resume'))
        self.assertTrue(self.path.exists())
        self.assertEqual(self.original.read_text(), 'unchanged')
