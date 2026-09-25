import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from podcast_swim.audio import PRESETS, _merge_tiny_tail, find_ffmpeg, process_episode, probe_duration


@unittest.skipUnless(shutil.which('ffmpeg') or Path('/opt/homebrew/bin/ffmpeg').exists(), 'ffmpeg unavailable')
class AudioTests(unittest.TestCase):
    def test_real_ffmpeg_segmentation_and_preset_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); src=root/'in.wav'; out=root/'out'
            subprocess.run([find_ffmpeg(),'-hide_banner','-loglevel','error','-y','-f','lavfi','-i','sine=frequency=440:duration=12','-c:a','pcm_s16le',str(src)],check=True)
            updates=[]
            tracks=process_episode(src,out,'PSP_test','swim',0.1,progress=updates.append)
            self.assertEqual([p.name for p in tracks],['PSP_test_p001.mp3','PSP_test_p002.mp3'])
            self.assertGreater(sum(probe_duration(p) for p in tracks),11)
            self.assertTrue(updates)
            self.assertGreaterEqual(updates[-1]['fraction'],0.99)
            self.assertEqual(updates[-1]['eta_seconds'],0.0)
            # Exercise every preset through the installed FFmpeg filter parser.
            for name in PRESETS:
                one=process_episode(src,root/name,f'PSP_{name}',name,0)
                self.assertEqual(len(one),1)
                self.assertGreater(one[0].stat().st_size,0)


class AudioRecoveryTests(unittest.TestCase):
    def test_tiny_unprobeable_tail_is_discarded(self):
        from unittest.mock import patch
        from podcast_swim.audio import _merge_tiny_tail
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); first=root/'p1.mp3'; tail=root/'p2.mp3'
            first.write_bytes(b'x' * 8192); tail.write_bytes(b'header')
            with patch('podcast_swim.audio.probe_duration', side_effect=RuntimeError('bad probe')), \
                 patch('podcast_swim.audio._packet_duration', return_value=None):
                result=_merge_tiny_tail([first,tail],600)
            self.assertEqual(result,[first])
            self.assertFalse(tail.exists())

    def test_probe_failure_does_not_drop_final_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); a=root/'a.mp3'; b=root/'b.mp3'; a.write_bytes(b'a' * 8192); b.write_bytes(b'b' * 8192)
            with patch('podcast_swim.audio.probe_duration', side_effect=RuntimeError('probe failed')):
                outputs=_merge_tiny_tail([a,b],600)
            self.assertEqual(outputs,[a,b])
            self.assertTrue(b.exists())


class AudioPresetStructureTests(unittest.TestCase):
    def test_every_preset_ends_with_explicit_limiter(self):
        for name, p in PRESETS.items():
            chain = p.filter_chain
            self.assertTrue(chain.startswith("acompressor="), name)
            self.assertIn(",loudnorm=", chain, name)
            self.assertIn(",volume=", chain, name)
            self.assertIn(",alimiter=", chain, name)
            self.assertTrue(chain.endswith("level=false"), name)

    def test_swim_presets_add_post_normalisation_gain(self):
        self.assertGreater(PRESETS["swim"].final_gain_db, 0)
        self.assertGreater(PRESETS["aggressive"].final_gain_db, PRESETS["swim"].final_gain_db)
        self.assertLess(PRESETS["aggressive"].limiter_limit, PRESETS["swim"].limiter_limit)
