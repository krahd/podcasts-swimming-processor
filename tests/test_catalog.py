import sqlite3
import tempfile
import unittest
from pathlib import Path

from podcast_swim.catalog import load_downloaded_episodes


class CatalogTests(unittest.TestCase):
    def test_cache_uuid_drives_download_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); cache=root/'cache'; cache.mkdir(); db=root/'db.sqlite'
            uuid='ABCDEF00-1111-2222-3333-444444444444'
            (cache/f'{uuid}.mp3').write_bytes(b'abc')
            conn=sqlite3.connect(db)
            conn.executescript('''
              create table ZMTPODCAST (Z_PK integer primary key, ZTITLE text, ZAUTHOR text, ZUUID text);
              create table ZMTEPISODE (Z_PK integer primary key, ZUUID text, ZCLEANEDTITLE text, ZTITLE text, ZAUTHOR text, ZPUBDATE real, ZDURATION real, ZPODCAST integer);
              insert into ZMTPODCAST values (7,'Test Show','Host','SHOW-UUID');
              insert into ZMTEPISODE values (1,'ABCDEF00-1111-2222-3333-444444444444','Episode One','Raw','Guest',800000000,1800,7);
            '''); conn.commit(); conn.close()
            eps=load_downloaded_episodes(db,cache)
            self.assertEqual(len(eps),1)
            self.assertEqual(eps[0].title,'Episode One')
            self.assertEqual(eps[0].show,'Test Show')
            self.assertEqual(eps[0].duration_seconds,1800)
