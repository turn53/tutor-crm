"""Durable Zoom -> YouTube transfer; never deletes Zoom recordings."""
import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import google_services as google
import zoom_sync
import lesson_links

STATE={'running':False,'message':'Подключи YouTube в настройках','last_run':None}
MUTEX=threading.Lock()
CHUNK=8*1024*1024

def initialise(a):
    with a.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS video_jobs(id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, next_try REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT "")')

def jobs(a):
    initialise(a)
    with a.connect() as db:
        return [{**dict(r),'payload':json.loads(r['payload'])} for r in db.execute('SELECT * FROM video_jobs')]

def put(a, job, status=None, message='', delay=0):
    with a.LOCK, a.connect() as db:
        lesson=lesson_links.canonical(a,job['payload']['lesson_id'])
        if lesson: job['payload']['lesson_id']=lesson['id']
        db.execute('UPDATE video_jobs SET payload=?,status=?,message=?,next_try=?,attempts=? WHERE id=?',
            (json.dumps(job['payload']),status or job['status'],message,time.time()+delay,job['attempts'],job['id']))

def overview(a):
    return [{'id':j['id'],'status':j['status'],'message':j['message'],
        'date':j['payload'].get('date',''),'lesson_id':j['payload']['lesson_id'],
        'url':'https://www.youtube.com/watch?v='+j['payload']['video_id'] if j['payload'].get('video_id') else ''} for j in jobs(a)]

def retry(a, identifier, restart=False):
    if not MUTEX.acquire(False): raise ValueError('Дождись окончания текущей проверки видео')
    try:
        job=next(j for j in jobs(a) if j['id']==identifier)
        if job['status']=='ready': raise ValueError('Видео уже перенесено')
        if job['status']=='needs_review' and not restart: raise ValueError('Сначала проверь канал YouTube на дубликаты')
        if restart:
            for key in ('session','video_id','privacy'): job['payload'].pop(key,None)
        put(a,job,'pending','Повтор поставлен в очередь')
        return {'ok':True}
    finally: MUTEX.release()

def select_files(files):
    # One layout per recording segment, avoiding duplicate gallery/screen uploads.
    priority={'shared_screen_with_speaker_view':0,'shared_screen_with_gallery_view':1,'shared_screen':2,'active_speaker':3,'gallery_view':4}
    selected={}
    for file in files:
        if file.get('file_type','').upper()!='MP4' or file.get('status')!='completed': continue
        segment=file.get('recording_start')
        if not segment: continue
        previous=selected.get(segment)
        if previous is None or priority.get(file.get('recording_type'),9)<priority.get(previous.get('recording_type'),9): selected[segment]=file
    return list(selected.values())

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if urllib.parse.urlsplit(req.full_url).hostname != urllib.parse.urlsplit(newurl).hostname:
            redirected.remove_header('Authorization')
        return redirected

def safe_url(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443): raise ValueError('Недопустимый адрес загрузки')
    addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses): raise ValueError('Недопустимый адрес загрузки')

def download(file, path, zoom_token):
    url=file['download_url']; safe_url(url)
    host=urllib.parse.urlsplit(url).hostname
    if not (host=='zoom.us' or host.endswith('.zoom.us')): raise ValueError('Ожидался адрес записи Zoom')
    size=int(file.get('file_size',0))
    limit=int(os.environ.get('CRM_VIDEO_MAX_BYTES',str(8*1024**3)))
    if size<=0 or size>limit: raise ValueError('Размер записи неизвестен или превышает лимит. Нужна ручная проверка')
    if path.exists() and path.stat().st_size==size: return size
    path.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(path.parent).free<size+2*1024**3: raise ValueError('Недостаточно свободного места для записи')
    partial=path.with_suffix('.part')
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+zoom_token})
    try:
        with urllib.request.build_opener(SafeRedirect()).open(req,timeout=120) as response, partial.open('wb') as output:
            if 'text/' in response.headers.get('Content-Type',''): raise ValueError('Zoom вернул страницу вместо видео')
            total=0
            while True:
                block=response.read(CHUNK)
                if not block: break
                total+=len(block)
                if total>size: raise ValueError('Размер видео отличается от заявленного Zoom')
                output.write(block)
            if total!=size: raise ValueError('Запись загружена не полностью; повторим')
        partial.replace(path)
    finally:
        if partial.exists(): partial.unlink()
    return size

def upload(a, job, path, token, privacy):
    p=job['payload']; size=p['size']
    if not p.get('session'):
        title='Занятие '+p['date']+' · '+job['id'][-6:]
        _,headers,_=google.request('youtube','https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status',method='POST',
            body={'snippet':{'title':title,'description':'Запись занятия','categoryId':'27'},'status':{'privacyStatus':privacy}},
            headers={'X-Upload-Content-Length':str(size),'X-Upload-Content-Type':'video/mp4'},token=token)
        p['session']=next(v for k,v in headers.items() if k.lower()=='location')
        put(a,job,'uploading')
    # Probe persisted session even after a lost final response; avoids duplicate uploads.
    code,headers,result=google.request('youtube',p['session'],method='PUT',body=b'',headers={'Content-Range':f'bytes */{size}'},token=token)
    if code in (200,201) and result.get('id'):
        p['video_id']=result['id']; put(a,job,'processing'); return
    if code!=308: raise ValueError('Не удалось определить состояние загрузки YouTube')
    received=next((v for k,v in headers.items() if k.lower()=='range'),'')
    offset=int(received.rsplit('-',1)[-1])+1 if received else 0
    with path.open('rb') as source:
        source.seek(offset)
        while offset<size:
            block=source.read(min(CHUNK,size-offset))
            if not block: raise ValueError('Локальная запись неполная')
            code,headers,result=google.request('youtube',p['session'],method='PUT',body=block,
                headers={'Content-Type':'video/mp4','Content-Range':f'bytes {offset}-{offset+len(block)-1}/{size}'},token=token)
            if code in (200,201) and result.get('id'):
                p['video_id']=result['id']; put(a,job,'processing'); return
            if code!=308: raise ValueError('Неожиданный ответ загрузки YouTube')
            received=next((v for k,v in headers.items() if k.lower()=='range'),'')
            confirmed=int(received.rsplit('-',1)[-1])+1 if received else 0
            if confirmed<=offset: raise ValueError('YouTube пока не подтвердил следующий фрагмент')
            offset=confirmed; source.seek(offset)
            put(a,job,'uploading')
    raise ValueError('Ожидается подтверждение загрузки YouTube')

def sync(a):
    if not MUTEX.acquire(False): return
    STATE['running']=True
    try:
        c=google.config()
        if not c.get('video_enabled'): return
        token=google.access_token('youtube')
        ztoken=zoom_sync.access_token()
        def recording(uuid): return zoom_sync.request('https://api.zoom.us/v2/meetings/'+zoom_sync.uuid_path(uuid)+'/recordings',headers={'Authorization':'Bearer '+ztoken})
        initialise(a)
        records={}
        for lesson in a.all_data()['lessons']:
            if lesson.get('deleted') or not lesson.get('zoom_uuid'): continue
            if lesson.get('attendance')=='cancelled': continue
            try:
                participants=zoom_sync.request('https://api.zoom.us/v2/past_meetings/'+zoom_sync.uuid_path(lesson['zoom_uuid']),headers={'Authorization':'Bearer '+ztoken})
                if participants.get('participants_count',0)<2: continue
                info=recording(lesson['zoom_uuid']); records[lesson['zoom_uuid']]=info
            except Exception:
                STATE['message']='Часть записей Zoom недоступна; сохранённая очередь продолжает обрабатываться.'
                continue
            for file in select_files(info.get('recording_files',[])):
                identifier=hashlib.sha256((lesson['zoom_uuid']+'|'+file['id']).encode()).hexdigest()
                payload={'lesson_id':lesson['id'],'uuid':lesson['zoom_uuid'],'file_id':file['id'],'date':lesson['date']}
                with a.LOCK, a.connect() as db:
                    current=lesson_links.canonical(a,lesson['id'])
                    if not current or current.get('deleted'): continue
                    payload['lesson_id']=current['id']
                    db.execute("INSERT OR IGNORE INTO video_jobs(id,payload,status) VALUES(?,?,'pending')",(identifier,json.dumps(payload)))
        root=Path(os.environ.get('CRM_VIDEO_DIR',str(Path(a.DB).parent/'.runtime'/'videos')))
        root.mkdir(parents=True,exist_ok=True)
        for job in jobs(a):
            if job['status'] in ('ready','needs_review') or job['next_try']>time.time(): continue
            p=job['payload']; path=root/(job['id']+'.mp4')
            lesson=lesson_links.canonical(a,p['lesson_id'])
            if not lesson or lesson.get('deleted'): continue
            p['lesson_id']=lesson['id']
            job['attempts']+=1
            try:
                if not p.get('video_id'):
                    if not path.exists() or not p.get('size') or path.stat().st_size!=p['size']:
                        info=records.get(p['uuid']) or recording(p['uuid'])
                        file=next(f for f in info['recording_files'] if f['id']==p['file_id'])
                        p['size']=download(file,path,ztoken); put(a,job,'uploading')
                    token=google.access_token('youtube')
                    upload(a,job,path,token,c.get('video_privacy','private'))
                result=google.api('youtube','/videos?part=status,processingDetails&id='+urllib.parse.quote(p['video_id']),token=token)
                video=next(iter(result.get('items',[])),{})
                if video.get('processingDetails',{}).get('processingStatus')=='succeeded':
                    p['privacy']=video.get('status',{}).get('privacyStatus','private')
                    with a.LOCK:
                        lesson=lesson_links.canonical(a,p['lesson_id'])
                        p['lesson_id']=lesson['id']
                        if lesson.get('deleted'): continue
                        urls=list(dict.fromkeys(lesson.get('youtube_urls',[])+['https://www.youtube.com/watch?v='+p['video_id']]))
                        a.save('lessons',{**lesson,'youtube_urls':urls,'youtube_url':urls[0]})
                    message='Видео готово · '+('доступ по ссылке' if p['privacy']=='unlisted' else 'закрытый доступ')+'.'
                    if c.get('video_privacy')=='unlisted' and p['privacy']!='unlisted':
                        message='YouTube оставил видео закрытым: ученики не смогут открыть ссылку. Проверь ограничение API-проекта и аудит YouTube.'
                    put(a,job,'ready',message)
                    if path.exists(): path.unlink()
                elif video.get('processingDetails',{}).get('processingStatus') in ('failed','terminated') or video.get('status',{}).get('uploadStatus') in ('rejected','failed'):
                    put(a,job,'needs_review','YouTube не обработал видео. Исходник в Zoom сохранён.')
                else: put(a,job,'processing','YouTube обрабатывает видео',delay=300)
            except urllib.error.HTTPError as e:
                if p.get('session') and e.code in (404,410):
                    put(a,job,'needs_review','Сессия загрузки истекла. Проверь канал перед повтором, чтобы не создать дубликат.')
                else: put(a,job,'retry',f'Сервис вернул HTTP {e.code}; повторим',delay=min(21600,300*2**min(job['attempts'],6)))
            except Exception:
                put(a,job,'retry','Перенос не завершён. Проверь доступ Google/Zoom, размер записи и свободное место; повторим автоматически.',delay=min(21600,300*2**min(job['attempts'],6)))
        STATE['message']='Очередь переноса проверена. Исходники в Zoom сохраняются.'
    except google.ServiceError as e: STATE['message']=str(e)
    except Exception: STATE['message']='Не удалось проверить перенос видео. Проверь подключения; повторим автоматически.'
    finally:
        STATE['running']=False; STATE['last_run']=datetime.datetime.now(zoom_sync.MOSCOW).isoformat(timespec='seconds'); MUTEX.release()

def worker(a):
    while True:
        sync(a)
        threading.Event().wait(300)
