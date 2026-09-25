import unittest
from pathlib import Path
from podcast_swim.server import parse_args

class ServerTests(unittest.TestCase):
    def test_default_device_and_settings(self):
        args=parse_args(['--no-open'])
        self.assertEqual(args.device,Path('/Volumes/RUN PLUS'))
        self.assertEqual(args.port,8765)
