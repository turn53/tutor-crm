"""Tutor CRM. Loopback by default; VPS access through authenticated HTTPS gateway."""
import json, sqlite3, os, secrets, threading, datetime, urllib.request, urllib.parse, base64, socket, sys
from http.cookies import SimpleCookie
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from contextlib import contextmanager
import zoom_sync
import google_services
import calendar_sync
import video_sync
import backup_service
import lesson_links
from jobs import JobStore

ROOT=Path(__file__).parent
DB=Path(os.environ.get('CRM_DB', str(ROOT/'crm.sqlite3')))
PORT=int(os.environ.get('CRM_PORT','8765'))
ORIGIN=os.environ.get('CRM_PUBLIC_ORIGIN',f'http://127.0.0.1:{PORT}').rstrip('/')
GATEWAY=os.environ.get('CRM_GATEWAY_SECRET','')
TOKEN=secrets.token_urlsafe(32)
LOCK=threading.RLock()
LOCAL=threading.local()
KINDS={'students','groups','enrollments','payments','lessons','bills','schedule'}

@contextmanager
def connect():
    if getattr(LOCAL,'connection',None) is not None:
        yield LOCAL.connection
        return
    c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id))')
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally: c.close()

def all_data():
    with connect() as c:
        out={k:[] for k in KINDS}
        for row in c.execute('SELECT kind,data FROM records ORDER BY rowid'):
            out[row['kind']].append(json.loads(row['data']))
        return out

def save(kind,d):
    if kind not in KINDS: raise ValueError('Неизвестный раздел')
    d=dict(d); d['id']=d.get('id') or secrets.token_hex(8)
    with LOCK:
        raw=all_data()
        original=next((x for x in raw[kind] if x['id']==d['id']),None)
        if original and d.get('revision',0)!=original.get('revision',0):
            raise ValueError('Запись уже обновилась. Открой её заново, чтобы сохранить изменения')
        d['revision']=(original.get('revision',0) if original else 0)+1
        data={k:[x for x in v if not x.get('deleted')] for k,v in raw.items()}
        def exists(k,id):
            return any(x['id']==id for x in data[k]) or (original is not None and id in (original.get('student'),original.get('group')) and any(x['id']==id for x in raw[k]))
        if original and original.get('deleted') and d.get('deleted'): raise ValueError('Сначала восстанови запись из корзины')
        if kind in ('students','groups') and not d.get('name','').strip(): raise ValueError('Нужно имя / название')
        if kind=='groups': d['hourly']=money(d.get('hourly',0))
        if kind=='schedule':
            for key in ('calendar_id','event_id','start','end','title'):
                if not isinstance(d.get(key),str): raise ValueError('Некорректное событие календаря')
        if kind in ('payments','enrollments','bills') and not exists('students',d.get('student')): raise ValueError('Выбери ученика')
        if kind=='bills':
            d['amount']=money(d.get('amount',0))
            d['calculation']=d.get('calculation','fixed')
            if d['calculation'] not in ('fixed','lessons'): raise ValueError('Выбери способ расчёта')
            datetime.date.fromisoformat(d['period']+'-01')
            if any(x['student']==d['student'] and x['period']==d['period'] and x['id']!=d['id'] for x in data['bills']): raise ValueError('Начисление за этот месяц уже есть. Измени существующее.')
        if kind=='enrollments':
            if d.get('group') and not exists('groups',d['group']): raise ValueError('Группа не найдена')
            for key in ('hourly','monthly'): d[key]=money(d.get(key,0))
            if d.get('until'): datetime.date.fromisoformat(d['until'])
            if d.get('active',True) and any(x['id']!=d['id'] and x.get('active',True) and x['student']==d['student'] and x.get('group','')==d.get('group','') for x in data['enrollments']): raise ValueError('Активный тариф для этого формата уже есть')
        if kind=='payments':
            d['amount']=money(d.get('amount',0))
            if d['amount']<=0: raise ValueError('Сумма должна быть больше нуля')
            datetime.date.fromisoformat(d['date'])
            if d.get('period'): datetime.date.fromisoformat(d['period']+'-01')
            if d.get('operation') not in ('Поступление','Возврат'): raise ValueError('Выбери операцию')
        if kind=='lessons':
            datetime.date.fromisoformat(d['date'])
            d['minutes']=int(d.get('minutes',60))
            if not 1<=d['minutes']<=1440: raise ValueError('Длительность: от 1 до 1440 минут')
            d['attendance']=d.get('attendance','completed')
            if d['attendance'] not in ('completed','needs_confirmation','cancelled'): raise ValueError('Некорректный статус занятия')
            if d.get('billing_basis')=='unassigned' and d['attendance']=='completed':
                raise ValueError('Сначала привяжи запись к занятию в расписании. Длительность Zoom не используется для оплаты')
            if d.get('merged_into') and not d.get('deleted'):
                raise ValueError('Материалы уже перенесены в основное занятие. Объединённый дубль нельзя восстановить отдельно')
            if d.get('group'):
                if not exists('groups',d['group']): raise ValueError('Выбери группу')
            elif not exists('students',d.get('student')): raise ValueError('Выбери ученика или группу')
            old=original
            # Capture prices and roster at lesson creation, preserve historical snapshots on edit.
            if old:
                if any(d.get(k)!=old.get(k) for k in ('student','group','minutes','date')): raise ValueError('Для сохранения финансовой истории участники, дата и длительность проведённого урока не изменяются в этой версии')
                d['charges']=old.get('charges',[])
            else:
                enroll=[x for x in data['enrollments'] if x.get('active',True) and any(s['id']==x['student'] for s in data['students']) and ((d.get('group') and x.get('group')==d['group']) or (not d.get('group') and not x.get('group') and x.get('student')==d.get('student')))]
                d['charges']=[{'student':x['student'],'amount':round(x['hourly']*d['minutes']/60,2)} for x in enroll]
        with connect() as c: c.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',(kind,d['id'],json.dumps(d,ensure_ascii=False)))
    return d

def trash(kind,id,restore=False):
    if kind not in KINDS: raise ValueError('Неизвестный раздел')
    with LOCK:
        record=next((x for x in all_data()[kind] if x['id']==id),None)
        if not record: raise ValueError('Запись не найдена')
        record['deleted']=not restore
        record['deleted_at']=None if restore else datetime.datetime.now().isoformat()
        if restore:
            # Normal validation also prevents duplicate bills / active terms on restore.
            return save(kind,record)
        record['revision']=record.get('revision',0)+1
        with connect() as c: c.execute('UPDATE records SET data=? WHERE kind=? AND id=?',(json.dumps(record,ensure_ascii=False),kind,id))
        return record

def save_group(payload):
    """Group and roster are committed together, or not at all."""
    members=payload.get('members',[])
    if len({m['student'] for m in members})!=len(members): raise ValueError('Ученик указан дважды')
    with LOCK,connect() as c:
        LOCAL.connection=c
        try:
            group=save('groups',payload['group'])
            current=[x for x in all_data()['enrollments'] if not x.get('deleted') and x.get('active',True) and x.get('group')==group['id']]
            selected={m['student'] for m in members}
            for old in current:
                if old['student'] not in selected: save('enrollments',{**old,'active':False})
            for member in members:
                old=next((x for x in current if x['student']==member['student']),{})
                save('enrollments',{**old,**member,'group':group['id'],'active':True})
            return group
        finally: LOCAL.connection=None

def money(v):
    from decimal import Decimal, InvalidOperation
    try:
        x=Decimal(str(v or 0)).quantize(Decimal('.01'))
        if not x.is_finite() or x<0 or x>Decimal('1000000000'): raise ValueError('Некорректная сумма')
        return float(x)
    except InvalidOperation: raise ValueError('Некорректная сумма')

def metrics(data, today=None):
    # Removed people remain in financial history, unlike deleted financial errors.
    data={k:([x for x in v if not x.get('deleted')] if k not in ('students','groups') else v) for k,v in data.items()}
    data['lessons']=[x for x in data['lessons'] if x.get('attendance','completed')=='completed']
    today=today or datetime.date.today(); out=[]
    for s in data['students']:
        paid=round(sum(p['amount']*(1 if p['operation']=='Поступление' else -1) for p in data['payments'] if p['student']==s['id']),2)
        future=0; monthly=0
        for e in data['enrollments']:
            if e['student']!=s['id'] or not e.get('active',True) or s.get('deleted') or s.get('status')!='Занимается': continue
            if e.get('group') and any(g['id']==e['group'] and g.get('deleted') for g in data['groups']): continue
            monthly+=e['monthly']
            if e.get('until'):
                end=datetime.date.fromisoformat(e['until']); months=max(0,(end.year-today.year)*12+end.month-today.month)
                future+=months*e['monthly']
        hours=0; value=0
        for l in data['lessons']:
            for c in l.get('charges',[]):
                if c['student']==s['id']: hours+=l['minutes']/60; value+=c['amount']
        out.append({**s,'paid':paid,'monthly':monthly,'forecast':round(future,2),'hourly':round(value/hours,2) if hours else None})
    hours=sum(l['minutes']/60 for l in data['lessons'])
    revenue=sum(c['amount'] for l in data['lessons'] for c in l.get('charges',[]))
    bills=[]
    for b in data['bills']:
        if b.get('calculation')=='lessons':
            b={**b,'amount':round(sum(c['amount'] for l in data['lessons'] if l['date'][:7]==b['period'] for c in l.get('charges',[]) if c['student']==b['student']),2)}
        paid=round(sum(p['amount']*(1 if p['operation']=='Поступление' else -1) for p in data['payments'] if p['student']==b['student'] and p.get('period')==b['period']),2)
        bills.append({**b,'paid':paid,'debt':round(max(0,b['amount']-paid),2),'advance':round(max(0,paid-b['amount']),2)})
    return {'students':out,'bills':bills,'debt':round(sum(b['debt'] for b in bills),2),'paid':round(sum(s['paid'] for s in out),2),'forecast':round(sum(s['forecast'] for s in out),2),'hourly':round(revenue/hours,2) if hours else 0,'hours':round(hours,1)}

def seed():
    if any(all_data().values()): return
    for id,name in [('anna','Анна Смирнова'),('boris','Борис Орлов'),('vera','Вера Волкова')]: save('students',{'id':id,'name':name,'status':'Занимается','source':'Демо','contact':'','comment':'Тестовые данные'})
    save('groups',{'id':'ege','name':'ЕГЭ · математика','comment':'Тестовая группа'})
    for id,student,group,h,m in [('e1','anna','ege',1000,8000),('e2','boris','ege',1000,8000),('e3','vera','',2000,16000),('e4','anna','',1800,3600)]:
        save('enrollments',{'id':id,'student':student,'group':group,'hourly':h,'monthly':m,'until':'2028-05-31','active':True})
    today=datetime.date.today().isoformat()
    for id,student,amount in [('p1','anna',3000),('p2','anna',5000),('p3','boris',4000),('p4','vera',16000)]: save('payments',{'id':id,'student':student,'amount':amount,'date':today,'operation':'Поступление','period':today[:7],'comment':'Демо'})
    for student,amount in [('anna',11600),('boris',8000),('vera',16000)]: save('bills',{'student':student,'period':today[:7],'amount':amount,'comment':'Группа и индивидуальные уроки за месяц'})
    save('lessons',{'id':'l1','group':'ege','student':'','date':today,'minutes':60,'topic':'Квадратные уравнения','understanding':'Два примера решили самостоятельно; нужно повторить знаки.','homework':'Задачи 1–5','next':'Теорема Виета','notes':'Дискриминант: D = b² − 4ac. При D > 0 два корня, D = 0 — один, D < 0 — действительных корней нет.','recording':''})
    save('lessons',{'id':'l2','group':'','student':'vera','date':today,'minutes':90,'topic':'Планиметрия','understanding':'Решила задачу с подсказкой.','homework':'Повторить признаки подобия','next':'Подобие треугольников','notes':'Разбирали, как находить соответствующие стороны подобных треугольников.','recording':''})

class Handler(BaseHTTPRequestHandler):
    def send(self,status,payload,ctype='application/json; charset=utf-8',headers=None):
        body=payload.encode() if isinstance(payload,str) else json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer'); self.send_header('X-Frame-Options','DENY')
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(body)
    def allowed(self):
        if GATEWAY:
            return self.headers.get('Host')==urllib.parse.urlsplit(ORIGIN).netloc and secrets.compare_digest(self.headers.get('X-CRM-Gateway',''),GATEWAY)
        return self.headers.get('Host') in (f'127.0.0.1:{PORT}',f'localhost:{PORT}')
    def do_GET(self):
        if not self.allowed(): return self.send(403,{'error':'Нет доступа'})
        if self.path in ('/about','/privacy'):
            title='Tutor CRM' if self.path=='/about' else 'Политика конфиденциальности Tutor CRM'
            content=('<p>Личная система преподавателя для расписания, учёта занятий и оплат, конспектов и записей уроков.</p><p>Google Calendar используется для календаря «Уроки», YouTube — для переноса записей из Zoom на канал владельца.</p><p>Доступ к рабочей CRM защищён авторизацией.</p><p><a href="/privacy">Политика конфиденциальности</a></p>' if self.path=='/about' else
                '<p>Обновлено 23 сентября 2026 года.</p><p>Tutor CRM — личный инструмент преподавателя Вячеслава Иванова. Контакт: lturn5353@gmail.com.</p><h2>Какие данные обрабатываются</h2><p>Имена и контакты учеников, необязательный город, расписание, отметки о занятиях, оплаты, конспекты и ссылки на записи. Данные хранятся на сервере CRM и в его резервных копиях.</p><h2>Подключения Google и Zoom</h2><p>С разрешения владельца CRM получает список календарей, читает события выбранного календаря и дополняет их описания конспектами и ссылками. Записи занятий, расшифровки речи и сводки получаются из Zoom. Видео передаются на YouTube-канал владельца. Для новых видео запрашивается доступ по ссылке; ограничения YouTube могут оставить их закрытыми. Любой получивший ссылку на видео с доступом по ссылке может посмотреть и переслать её.</p><p>Ключи подключения хранятся на сервере, не отображаются ученикам и не включаются в обычный экспорт CRM. Доступ к рабочим данным ограничен авторизацией.</p><h2>Использование и удаление</h2><p>Данные используются для проведения и учёта занятий. CRM не продаёт данные и не использует их для рекламы или обучения моделей. Конспекты формируются собственным обработчиком и локальной языковой моделью на сервере CRM. Расшифровки и сводки не передаются внешним модельным API. Данные Google используются только для указанных функций, с соблюдением Google API Services User Data Policy, включая Limited Use.</p><p>Владелец может исправить или удалить данные в CRM, а доступ Google отозвать в настройках своего Google-аккаунта. Удаление из CRM само по себе не удаляет оригиналы из Calendar, Zoom или YouTube. Автоматические резервные копии базы хранятся 14 дней; отдельные копии перед техническими изменениями удаляются владельцем отдельно. По вопросам доступа, исправления и удаления данных обращайтесь по указанному контакту.</p><p><a href="/about">О приложении</a> · <a href="https://policies.google.com/privacy">Политика Google</a> · <a href="https://developers.google.com/terms/api-services-user-data-policy">Google API Services User Data Policy</a></p>')
            return self.send(200,'<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title><style>body{max-width:800px;margin:40px auto;padding:0 20px;font:17px/1.6 system-ui;color:#233047}a{color:#2359ae}</style><h1>'+title+'</h1>'+content+'</html>','text/html; charset=utf-8')
        if self.path.startswith('/oauth/google/callback?'):
            try:
                cookies=SimpleCookie(self.headers.get('Cookie',''))
                state=cookies['crm_oauth'].value if 'crm_oauth' in cookies else ''
                google_services.complete(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query),state,ORIGIN,connect)
                return self.send(303,'','text/plain',{'Location':'/','Set-Cookie':'crm_oauth=; Max-Age=0; Path=/oauth/google/callback; HttpOnly; SameSite=Lax'})
            except (ValueError,sqlite3.Error): return self.send(400,'Подключение не завершено. Вернись в настройки CRM и повтори вход Google.','text/plain; charset=utf-8')
        if self.path=='/':
            script=(ROOT/'ui.js').read_text('utf-8')+'\n'+(ROOT/'integrations_ui.js').read_text('utf-8')
            return self.send(200,(ROOT/'index.html').read_text(encoding='utf-8').replace('__APP_SCRIPT__',script).replace('__TOKEN__',TOKEN),'text/html; charset=utf-8')
        if self.headers.get('X-CRM-Token')!=TOKEN: return self.send(403,{'error':'Нет доступа'})
        if self.path=='/api/data':
            d=all_data(); return self.send(200,{'data':d,'metrics':metrics(d)})
        if self.path=='/api/export': return self.send(200,all_data())
        if self.path=='/api/integrations':
            return self.send(200,{'google':google_services.status(),'calendar':calendar_sync.STATE,'video':video_sync.STATE,'backup':backup_service.STATE,'videos':video_sync.overview(sys.modules[__name__]),'redirect_uri':ORIGIN+'/oauth/google/callback'})
        if self.path=='/api/google/calendars':
            try:
                result=google_services.api('calendar','/users/me/calendarList?maxResults=250')
                return self.send(200,{'items':[{'id':x['id'],'name':x.get('summary',''),'role':x.get('accessRole','')} for x in result.get('items',[])]})
            except Exception: return self.send(400,{'error':'Не удалось получить календари. Проверь подключение Google'})
        if self.path=='/api/zoom':
            c=zoom_sync.config()
            return self.send(200,{**zoom_sync.STATE,'configured':all(c.get(k) for k in ('account_id','client_id','client_secret','user_id')),'enabled':c.get('enabled',False),'model':c.get('model',''),'provider':zoom_sync.provider(c),'queue':JobStore(DB).overview()})
        return self.send(404,{'error':'Не найдено'})
    def do_POST(self):
        if not self.allowed() or self.headers.get('X-CRM-Token')!=TOKEN: return self.send(403,{'error':'Нет доступа'})
        if self.headers.get('Origin') and self.headers['Origin'] not in (ORIGIN,f'http://localhost:{PORT}' if not GATEWAY else ORIGIN): return self.send(403,{'error':'Недопустимый источник запроса'})
        try:
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<=1000000: raise ValueError('Недопустимый размер запроса')
            d=json.loads(self.rfile.read(size))
            if not isinstance(d,dict): raise ValueError('Некорректный запрос')
            if self.path=='/api/google/config': return self.send(200,google_services.configure(d))
            if self.path=='/api/video/retry': return self.send(200,video_sync.retry(sys.modules[__name__],d['id'],d.get('restart') is True))
            if self.path=='/api/google/connect':
                url,state=google_services.begin(d['service'],ORIGIN,connect)
                cookie='crm_oauth='+state+'; Max-Age=600; Path=/oauth/google/callback; HttpOnly; SameSite=Lax'+('; Secure' if ORIGIN.startswith('https://') else '')
                return self.send(200,{'url':url},headers={'Set-Cookie':cookie})
            if self.path=='/api/calendar/bind': return self.send(200,calendar_sync.bind(sys.modules[__name__],d['id'],d.get('student',''),d.get('group',''),d.get('series') is True))
            if self.path=='/api/calendar/attendance': return self.send(200,calendar_sync.attendance(sys.modules[__name__],d['id'],d['status']))
            if self.path=='/api/integrations/sync':
                for module in (calendar_sync,video_sync): threading.Thread(target=module.sync,args=(sys.modules[__name__],),daemon=True).start()
                return self.send(202,{'ok':True})
            if self.path=='/api/zoom/config': return self.send(200,zoom_sync.configure(d))
            if self.path=='/api/group': return self.send(200,save_group(d))
            if self.path=='/api/trash': return self.send(200,trash(d['kind'],d['id'],d.get('restore') is True))
            if self.path=='/api/zoom/sync':
                threading.Thread(target=zoom_sync.sync,args=(all_data,save,JobStore(DB),resolve_zoom),daemon=True).start()
                return self.send(202,{'message':'Проверка запущена. Результат появится автоматически.'})
            if self.path=='/api/lesson/attendance':
                with LOCK:
                    old=next(x for x in all_data()['lessons'] if x['id']==d['id'] and not x.get('deleted'))
                    if old.get('zoom_uuid') and not old.get('calendar_key'):
                        old=resolve_zoom({'uuid':old['zoom_uuid'],'kind':'groups' if old.get('group') else 'students',
                            'target':old.get('group') or old.get('student'),'date':old['date'],
                            'start_time':old.get('zoom_start_time') or old['start_time'],
                            'minutes':old.get('zoom_minutes',old['minutes']),'recording':old.get('recording','')})
                    if old.get('calendar_key'):
                        return self.send(200,calendar_sync.attendance(sys.modules[__name__],old['calendar_key'],{'completed':'completed','cancelled':'cancelled','needs_confirmation':'planned'}[d['attendance']]))
                    return self.send(200,save('lessons',{**old,'attendance':d['attendance']}))
            if self.path.startswith('/api/save/'):
                kind=self.path.rsplit('/',1)[1]
                if kind=='schedule': raise ValueError('Расписание изменяется через Google Calendar')
                with LOCK:
                    if kind=='lessons':
                        old=next((x for x in all_data()['lessons'] if x['id']==d.get('id')),None)
                        if old and (old.get('zoom_uuid') or old.get('calendar_key')) and any(old.get(k,'')!=d.get(k,'') for k in zoom_sync.FIELDS):
                            d['report_locked']=True
                            d['report_state']='manual'
                    return self.send(200,save(kind,d))
            return self.send(404,{'error':'Не найдено'})
        except (ValueError,KeyError,TypeError,StopIteration) as e: self.send(400,{'error':str(e) or 'Запись не найдена'})
    def log_message(self,*args): pass

class LocalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address=False
    def get_request(self):
        connection,address=super().get_request()
        connection.settimeout(30)
        return connection,address

    def server_bind(self):
        # Windows SO_REUSEADDR otherwise allows two CRM instances on one port.
        if os.name=='nt': self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


def resolve_zoom(payload):
    return lesson_links.resolve_zoom(sys.modules[__name__],payload)


if __name__=='__main__':
    # Production starts empty. Demo fixtures are created explicitly by tests.
    all_data()
    bind_address=os.environ.get('CRM_BIND','127.0.0.1')
    if bind_address not in ('127.0.0.1','localhost') and (len(GATEWAY)<32 or not ORIGIN.startswith('https://')):
        raise SystemExit('External binding requires HTTPS origin and a gateway secret')
    server=LocalHTTPServer((bind_address,PORT),Handler)
    threading.Thread(target=zoom_sync.worker,args=(all_data,save,JobStore(DB)),kwargs={'resolver':resolve_zoom},daemon=True).start()
    for module in (calendar_sync,video_sync): threading.Thread(target=module.worker,args=(sys.modules[__name__],),daemon=True).start()
    threading.Thread(target=backup_service.worker,args=(DB,),daemon=True).start()
    print(f'Tutor CRM: http://127.0.0.1:{PORT}',flush=True)
    server.serve_forever()
