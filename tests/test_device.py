import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_swim.catalog import Episode
from podcast_swim.device import (
    MANIFEST_NAME,
    ManifestError,
    device_filename_base,
    load_manifest,
    reconcile,
    recover_pending,
)


def ep(src: Path, uuid='U-1', mtime=None):
    s=src.stat()
    return Episode(uuid,'Episode','Show','',None,1200,str(src),s.st_size,s.st_mtime_ns if mtime is None else mtime)


class DeviceTests(unittest.TestCase):
    def test_corrupt_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp); (d/MANIFEST_NAME).write_text('{bad')
            with self.assertRaises(ManifestError): load_manifest(d)

    def test_unselect_deletes_only_manifest_owned_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp); managed=d/'PSP_owned_p001.mp3'; managed.write_bytes(b'x'); unrelated=d/'music.mp3'; unrelated.write_bytes(b'y')
            (d/MANIFEST_NAME).write_text(json.dumps({'version':1,'episodes':{'U-1':{'files':[{'name':managed.name,'size':1}]}},'pending':None}))
            reconcile(d,{},[], 'swim',10)
            self.assertFalse(managed.exists()); self.assertTrue(unrelated.exists())

    def test_select_repeat_idempotent_and_settings_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); d=root/'device'; d.mkdir(); src=root/'source.mp3'; src.write_bytes(b'source'); e=ep(src)
            calls=[]
            def fake_process(source,out,base,preset_name,segment_minutes):
                calls.append(1); p=out/f'{base}_p001.mp3'; p.write_bytes(b'processed'); return [p]
            with patch('podcast_swim.device.process_episode',fake_process):
                reconcile(d,{e.uuid:e},[e.uuid],'swim',10)
                reconcile(d,{e.uuid:e},[e.uuid],'swim',10)
            self.assertEqual(len(calls),1)
            manifest=load_manifest(d)
            self.assertEqual(manifest['settings'],{'preset':'swim','segment_minutes':10})
            self.assertTrue(next(d.glob('PSP_*.mp3')).is_file())

    def test_manifest_path_traversal_rejected_before_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp); (d/MANIFEST_NAME).write_text(json.dumps({'version':1,'episodes':{'U-1':{'files':[{'name':'../PSP_bad.mp3','size':1}]}},'pending':None}))
            src=d/'s'; src.write_bytes(b'x'); e=ep(src)
            with patch('podcast_swim.device.process_episode') as processor:
                with self.assertRaises(ManifestError): reconcile(d,{e.uuid:e},[e.uuid],'swim',10)
                processor.assert_not_called()

    def test_changed_source_gets_new_fingerprint_before_old_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); d=root/'device'; d.mkdir(); src=root/'source.mp3'; src.write_bytes(b'source'); e1=ep(src, mtime=10); e2=ep(src, mtime=11)
            def fake_process(source,out,base,preset_name,segment_minutes):
                p=out/f'{base}_p001.mp3'; p.write_bytes(base.encode()); return [p]
            with patch('podcast_swim.device.process_episode',fake_process):
                reconcile(d,{e1.uuid:e1},[e1.uuid],'swim',10)
                first=next(d.glob('PSP_*.mp3')).name
                reconcile(d,{e2.uuid:e2},[e2.uuid],'swim',10)
            current=[p.name for p in d.glob('PSP_*.mp3')]
            self.assertEqual(len(current),1)
            self.assertNotEqual(current[0],first)
            self.assertFalse((d/first).exists())

    def test_incomplete_pending_replace_rolls_back_new_and_preserves_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp); old=d/'PSP_old_p001.mp3'; old.write_bytes(b'old'); new=d/'PSP_new_p001.mp3'; new.write_bytes(b'bad')
            manifest={'version':1,'settings':{'preset':'swim','segment_minutes':10},'episodes':{'U-1':{'files':[{'name':old.name,'size':3}]}},'pending':{'type':'replace','uuid':'U-1','old_files':[old.name],'new_entry':{'files':[{'name':new.name,'size':999}]}}}
            recovered=recover_pending(d,manifest)
            self.assertTrue(old.exists()); self.assertFalse(new.exists()); self.assertIsNone(recovered['pending'])


    def test_managed_missing_source_can_be_kept_or_unselected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); d=root/'device'; d.mkdir(); src=root/'source.mp3'; src.write_bytes(b'source'); e=ep(src)
            def fake_process(source,out,base,preset_name,segment_minutes):
                p=out/f'{base}_p001.mp3'; p.write_bytes(b'processed'); return [p]
            with patch('podcast_swim.device.process_episode',fake_process):
                reconcile(d,{e.uuid:e},[e.uuid],'swim',10)
            # Source disappears from Apple Podcasts: keeping the same settings
            # is valid and does not need the source.
            reconcile(d,{},[e.uuid],'swim',10)
            self.assertTrue(next(d.glob('PSP_*.mp3')).exists())
            # Unticking still removes every managed chunk.
            reconcile(d,{},[],'swim',10)
            self.assertFalse(list(d.glob('PSP_*.mp3')))

    def test_managed_missing_source_cannot_be_reprocessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); d=root/'device'; d.mkdir(); src=root/'source.mp3'; src.write_bytes(b'source'); e=ep(src)
            def fake_process(source,out,base,preset_name,segment_minutes):
                p=out/f'{base}_p001.mp3'; p.write_bytes(b'processed'); return [p]
            with patch('podcast_swim.device.process_episode',fake_process):
                reconcile(d,{e.uuid:e},[e.uuid],'swim',10)
            with self.assertRaisesRegex(ValueError,'no longer downloaded'):
                reconcile(d,{},[e.uuid],'aggressive',10)

    def test_untracked_collision_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); d=root/'device'; d.mkdir(); src=root/'source.mp3'; src.write_bytes(b'source'); e=ep(src)
            base=device_filename_base(e,'swim',10); collision=d/f'{base}_p001.mp3'; collision.write_bytes(b'untracked')
            def fake_process(source,out,base_name,preset_name,segment_minutes):
                p=out/f'{base_name}_p001.mp3'; p.write_bytes(b'new'); return [p]
            with patch('podcast_swim.device.process_episode',fake_process):
                with self.assertRaises(ManifestError): reconcile(d,{e.uuid:e},[e.uuid],'swim',10)
            self.assertEqual(collision.read_bytes(),b'untracked')
