import unittest
from pathlib import Path
from podcast_swim.server import RuntimeState, parse_args

class ServerTests(unittest.TestCase):
    def test_default_device_and_settings(self):
        args=parse_args(['--no-open'])
        self.assertEqual(args.device,Path('/Volumes/RUN PLUS'))
        self.assertEqual(args.port,8765)


    def test_begin_sync_is_atomic_and_rejects_second_start(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            app=RuntimeState(Path(tmp))
            try:
                self.assertTrue(app.begin_sync())
                self.assertFalse(app.begin_sync())
                self.assertTrue(app.status_snapshot()['running'])
                app.status_update({'phase':'complete','message':'done'})
                self.assertFalse(app.status_snapshot()['running'])
                self.assertTrue(app.begin_sync())
            finally:
                import shutil
                shutil.rmtree(app.preview_dir,ignore_errors=True)
