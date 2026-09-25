import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from podcast_swim.audio import PRESETS, find_ffmpeg, process_episode, probe_duration


@unittest.skipUnless(shutil.which('ffmpeg') or Path('/opt/homebrew/bin/ffmpeg').exists(), 'ffmpeg unavailable')
class AudioTests(unittest.TestCase):
    def test_real_ffmpeg_segmentation_and_preset_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); src=root/'in.wav'; out=root/'out'
            subprocess.run([find_ffmpeg(),'-hide_banner','-loglevel','error','-y','-f','lavfi','-i','sine=frequency=440:duration=12','-c:a','pcm_s16le',str(src)],check=True)
            tracks=process_episode(src,out,'PSP_test','swim',0.1)
            self.assertEqual([p.name for p in tracks],['PSP_test_p001.mp3','PSP_test_p002.mp3'])
            self.assertGreater(sum(probe_duration(p) for p in tracks),11)
            # Exercise every preset through the installed FFmpeg filter parser.
            for name in PRESETS:
                one=process_episode(src,root/name,f'PSP_{name}',name,0)
                self.assertEqual(len(one),1)
                self.assertGreater(one[0].stat().st_size,0)
