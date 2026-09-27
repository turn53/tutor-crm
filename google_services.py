"""Google OAuth and HTTP transport. Credentials never enter CRM exports."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CONFIG = Path(os.environ.get('CRM_GOOGLE_CONFIG', str(Path(__file__).with_name('google.local.json'))))
LOCK = threading.RLock()
SCOPES = {
    'calendar': ['https://www.googleapis.com/auth/calendar.events', 'https://www.googleapis.com/auth/calendar.calendarlist.readonly'],
    'youtube': ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.readonly'],
}

class ServiceError(ValueError):
    pass

def config():
    with LOCK:
        return json.loads(CONFIG.read_text('utf-8')) if CONFIG.exists() else {}

def update(values):
    with LOCK:
        c = config()
        c.update(values)
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG.with_suffix('.tmp')
        tmp.write_text(json.dumps(c, ensure_ascii=False), encoding='utf-8')
        if os.name != 'nt': tmp.chmod(0o600)
        tmp.replace(CONFIG)
        return c

def configure(d):
    values = {}
    for key in ('client_id', 'client_secret'):
        if d.get(key): values[key] = str(d[key]).strip()
    if 'calendar_id' in d: values['calendar_id'] = str(d['calendar_id']).strip() or 'primary'
    for key in ('calendar_enabled', 'video_enabled', 'publish_notes'):
        if key in d: values[key] = d[key] is True
    if 'video_privacy' in d:
        if d['video_privacy'] not in ('private', 'unlisted'): raise ServiceError('Выбери закрытый доступ или доступ по ссылке')
        values['video_privacy'] = d['video_privacy']
    update(values)
    return status()

def status():
    c = config()
    return {k: c.get(k, default) for k, default in {
        'calendar_id':'primary', 'calendar_enabled':False, 'video_enabled':False,
        'publish_notes':False, 'video_privacy':'private'}.items()} | {
        'configured':bool(c.get('client_id') and c.get('client_secret')),
        'calendar_connected':bool(c.get('calendar_refresh_token')),
        'youtube_connected':bool(c.get('youtube_refresh_token'))}

def token_request(values):
    req = urllib.request.Request('https://oauth2.googleapis.com/token',
        data=urllib.parse.urlencode(values).encode(), headers={'Content-Type':'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response: return json.load(response)
    except (urllib.error.URLError, ValueError):
        raise ServiceError('Google не обновил доступ. Проверь соединение или подключи аккаунт заново') from None

def begin(service, origin, db):
    if service not in SCOPES: raise ServiceError('Неизвестное подключение')
    c = config()
    if not c.get('client_id') or not c.get('client_secret'): raise ServiceError('Сначала сохрани OAuth Client ID и Client Secret Google')
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    with db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS oauth_states(state TEXT PRIMARY KEY, service TEXT, verifier TEXT, expires REAL)')
        conn.execute('DELETE FROM oauth_states WHERE expires<?', (time.time(),))
        conn.execute('INSERT INTO oauth_states VALUES(?,?,?,?)', (state, service, verifier, time.time()+600))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    query = dict(client_id=c['client_id'], redirect_uri=origin+'/oauth/google/callback', response_type='code',
        scope=' '.join(SCOPES[service]), access_type='offline', prompt='consent', state=state,
        code_challenge=challenge, code_challenge_method='S256')
    return 'https://accounts.google.com/o/oauth2/v2/auth?'+urllib.parse.urlencode(query), state

def complete(query, cookie_state, origin, db):
    state = query.get('state', [''])[0]
    if not state or not secrets.compare_digest(state, cookie_state or ''): raise ServiceError('Сессия подключения истекла. Начни подключение заново')
    with db() as conn:
        row = conn.execute('SELECT * FROM oauth_states WHERE state=? AND expires>?', (state, time.time())).fetchone()
        if not row: raise ServiceError('Сессия подключения истекла')
        conn.execute('DELETE FROM oauth_states WHERE state=?', (state,))
    if query.get('error'): raise ServiceError('Разрешение Google не выдано')
    c = config()
    result = token_request(dict(code=query.get('code',[''])[0],client_id=c['client_id'],client_secret=c['client_secret'],
        redirect_uri=origin+'/oauth/google/callback', grant_type='authorization_code',code_verifier=row['verifier']))
    if not result.get('refresh_token'): raise ServiceError('Google не выдал постоянный доступ. Подключи аккаунт повторно')
    if not set(SCOPES[row['service']]).issubset(set(result.get('scope','').split())):
        raise ServiceError('Не выданы все необходимые разрешения Google')
    update({row['service']+'_refresh_token':result['refresh_token']})

def access_token(service):
    c = config()
    if not c.get(service+'_refresh_token'): raise ServiceError('Подключи '+service+' в настройках CRM')
    return token_request(dict(client_id=c['client_id'],client_secret=c['client_secret'],
        refresh_token=c[service+'_refresh_token'],grant_type='refresh_token'))['access_token']

def request(service, url, method='GET', body=None, headers=None, token=None):
    if urllib.parse.urlsplit(url).scheme != 'https' or urllib.parse.urlsplit(url).hostname not in ('www.googleapis.com','youtube.googleapis.com'):
        raise ServiceError('Недопустимый адрес Google API')
    h = {'Authorization':'Bearer '+(token or access_token(service))}
    h.update(headers or {})
    if isinstance(body, dict): body=json.dumps(body).encode(); h['Content-Type']='application/json'
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            raw=response.read()
            return response.status, dict(response.headers), json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 308: return 308, dict(e.headers), {}
        raise

def api(service, path, **kwargs):
    root = 'https://www.googleapis.com/'+('calendar/v3' if service=='calendar' else 'youtube/v3')
    return request(service, root+path, **kwargs)[2]
