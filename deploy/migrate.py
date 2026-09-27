"""Explicit export/import of live data. Stop CRM writes before final cutover."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

def summary(path):
    with closing(sqlite3.connect(path)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Database integrity check failed')
        return dict(db.execute('SELECT kind,count(*) FROM records GROUP BY kind'))

def export(folder):
    import app
    import google_services
    import zoom_sync
    folder=Path(folder).resolve()
    folder.mkdir(parents=True,exist_ok=False)
    with closing(sqlite3.connect(app.DB,timeout=30)) as source,closing(sqlite3.connect(folder/'crm.sqlite3')) as target:
        source.backup(target)
    for name,path in [('zoom.local.json',zoom_sync.CONFIG),('google.local.json',google_services.CONFIG)]:
        if path.exists():
            content=json.loads(path.read_text('utf-8'))
            if name.startswith('zoom'): content['enabled']=False; content['provider']='rules'
            else: content['calendar_enabled']=False;content['video_enabled']=False
            (folder/name).write_text(json.dumps(content),encoding='utf-8')
    manifest={'counts':summary(folder/'crm.sqlite3'),'workers_disabled':True}
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    if os.name!='nt':
        folder.chmod(0o700)
        for path in folder.iterdir():path.chmod(0o600)
    print('Migration prepared; credentials are inside this PRIVATE folder:',folder)
    print('Record counts:',manifest['counts'])

def restore(folder):
    source=Path(folder).resolve(); target=ROOT/'server-data'
    if (target/'crm.sqlite3').exists():raise ValueError('Target database exists; refusing to overwrite live data')
    expected=json.loads((source/'manifest.json').read_text('utf-8'))['counts']
    if summary(source/'crm.sqlite3')!=expected:raise ValueError('Record count mismatch')
    target.mkdir(exist_ok=True)
    with closing(sqlite3.connect(source/'crm.sqlite3')) as src,closing(sqlite3.connect(target/'crm.sqlite3')) as dst:src.backup(dst)
    for name in ('zoom.local.json','google.local.json'):
        if (source/name).exists():
            if (target/name).exists():raise ValueError('Target configuration already exists')
            (target/name).write_bytes((source/name).read_bytes())
    if summary(target/'crm.sqlite3')!=expected:raise ValueError('Restored records mismatch')
    if os.name!='nt':
        target.chmod(0o700);os.chown(target,10001,10001)
        for path in target.iterdir():
            if path.is_file():path.chmod(0o600);os.chown(path,10001,10001)
    print('Restore verified. Workers remain disabled until cutover.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['export','import']);parser.add_argument('folder')
    args=parser.parse_args()
    (export if args.action=='export' else restore)(args.folder)
