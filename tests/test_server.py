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

    def test_http_auth_and_security_headers(self):
        import tempfile, shutil, threading, urllib.request, urllib.error
        from http.server import ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as tmp:
            app = RuntimeState(Path(tmp), token="secret")
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.app = app
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with urllib.request.urlopen(base + "/", timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
                    self.assertEqual(response.headers.get("Cross-Origin-Resource-Policy"), "same-origin")
                    self.assertIn("default-src 'self'", response.headers.get("Content-Security-Policy", ""))
                for path in ("/api/status", "/api/status?token=secret"):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(base + path, timeout=3)
                    try:
                        self.assertEqual(caught.exception.code, 403)
                    finally:
                        caught.exception.close()
                req = urllib.request.Request(base + "/api/status", headers={"X-PSP-Token": "secret"})
                with urllib.request.urlopen(req, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get("Cache-Control"), "no-store")
                    self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
                shutil.rmtree(app.preview_dir, ignore_errors=True)
