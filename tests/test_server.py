import unittest
from pathlib import Path
from podcast_swim.server import Handler, RuntimeState, parse_args

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

    def test_eject_admission_blocks_sync_and_second_eject(self):
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmp:
            app=RuntimeState(Path(tmp))
            try:
                self.assertTrue(app.begin_eject())
                self.assertFalse(app.begin_eject())
                self.assertFalse(app.begin_sync())
                app.status_update({'phase':'error','message':'failed'})
                self.assertTrue(app.begin_sync())
            finally:
                shutil.rmtree(app.preview_dir,ignore_errors=True)

    def test_query_token_is_only_accepted_for_preview(self):
        import tempfile, shutil
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            app=RuntimeState(Path(tmp),token='secret')
            try:
                handler=object.__new__(Handler)
                handler.server=SimpleNamespace(app=app)
                handler.headers={}
                handler.path='/api/state?token=secret'
                self.assertEqual(handler._token(),'')
                handler.path='/preview/a.mp3?token=secret'
                self.assertEqual(handler._token(),'secret')
                handler.headers={'X-PSP-Token':'secret'}
                handler.path='/api/state'
                self.assertEqual(handler._token(),'secret')
            finally:
                shutil.rmtree(app.preview_dir,ignore_errors=True)
