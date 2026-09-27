"""Daily consistent SQLite backups; retain 14 days, never copy live DB bytes."""
import datetime
import os
from pathlib import Path
import sqlite3
import threading
from contextlib import closing

STATE={'message':'Резервная копия ещё не проверялась'}

def backup(database, folder):
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    if os.name!='nt': folder.chmod(0o700)
    target=folder/('crm-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')+'.sqlite3')
    if not target.exists():
        pending=target.with_suffix('.tmp')
        with closing(sqlite3.connect(database,timeout=30)) as src, closing(sqlite3.connect(pending)) as dest:
            src.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Backup integrity check failed')
        if os.name!='nt': pending.chmod(0o600)
        pending.replace(target)
    with closing(sqlite3.connect(target)) as checked:
        if checked.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Backup integrity check failed')
    for old in sorted(folder.glob('crm-????????.sqlite3'))[:-14]: old.unlink()
    return target

def worker(database):
    folder=os.environ.get('CRM_BACKUP_DIR',str(Path(database).parent/'backups'/'daily'))
    while True:
        try:
            target=backup(database,folder)
            STATE['message']='Резервная копия проверена: '+target.name
        except Exception: STATE['message']='Не удалось создать резервную копию. Проверь свободное место и права доступа.'
        threading.Event().wait(3600)
