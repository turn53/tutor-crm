"""Calendar occurrences and lesson accounting. Google is the schedule source."""
import datetime as dt
import hashlib
import html
import re
import threading
import urllib.error
import urllib.parse
import google_services as google
import lesson_links

STATE = {'message':'Подключи Google Calendar в настройках', 'last_run':None, 'running':False}
MUTEX = threading.Lock()
MOSCOW = dt.timezone(dt.timedelta(hours=3))
START, END = '[CRM: конспект занятия]', '[/CRM: конспект занятия]'

def event_path(calendar, event=''):
    return '/calendars/'+urllib.parse.quote(calendar,safe='')+'/events'+('/'+urllib.parse.quote(event,safe='') if event else '')

def instant(value):
    parsed=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None: raise ValueError('Нужен часовой пояс времени')
    return parsed

def key(calendar, event):
    return 'cal-'+hashlib.sha256((calendar+'|'+event).encode()).hexdigest()[:24]

def find_lesson(a, item):
    return lesson_links.linked(a.all_data(),item)

def bind(a, item_id, student='', group='', series=False):
    with a.LOCK, a.connect() as conn:
        a.LOCAL.connection=conn
        try:
            item=next(x for x in a.all_data()['schedule'] if x['id']==item_id)
            if bool(student)==bool(group): raise ValueError('Выбери одного ученика или одну группу')
            target_kind,target=('groups',group) if group else ('students',student)
            if not any(x['id']==target and not x.get('deleted') for x in a.all_data()[target_kind]): raise ValueError('Ученик или группа не найдены')
            targets=[x for x in a.all_data()['schedule'] if x['id']==item_id or (series and item.get('series') and x.get('series')==item['series'] and x['calendar_id']==item['calendar_id'])]
            for x in targets:
                lesson=find_lesson(a,x)
                if lesson and (lesson.get('student','')!=student or lesson.get('group','')!=group):
                    raise ValueError('У занятия уже есть финансовая история с другими участниками')
                a.save('schedule',{**x,'student':student,'group':group,'series_binding':bool(series)})
            return {'ok':True}
        finally: a.LOCAL.connection=None

def attendance(a, item_id, status):
    if status not in ('completed','cancelled','planned'): raise ValueError('Неизвестный статус')
    with a.LOCK, a.connect() as conn:
        a.LOCAL.connection=conn
        try:
            item=next(x for x in a.all_data()['schedule'] if x['id']==item_id)
            lesson=find_lesson(a,item)
            if status=='completed':
                if not item.get('student') and not item.get('group'): raise ValueError('Сначала привяжи ученика или группу')
                if item.get('all_day'): raise ValueError('Для занятия укажи время в Google Calendar')
                if item.get('remote_cancelled'): raise ValueError('Событие отменено в Google Calendar; сначала восстанови его там')
                if item.get('needs_review'): raise ValueError('Время события изменилось после привязки материалов или проведения. Сначала проверь занятие')
                # An explicitly cancelled CRM occurrence can be confirmed again.
                if item.get('status')=='cancelled':
                    item=a.save('schedule',{**item,'status':'planned'})
                lesson=lesson_links.reconcile_event(a,item,create=True)
            if lesson:
                a.save('lessons',{**lesson,'calendar_key':item['id'], 'attendance':{'planned':'needs_confirmation','completed':'completed','cancelled':'cancelled'}[status]})
            return a.save('schedule',{**item,'status':status})
        finally: a.LOCAL.connection=None

def import_event(a, calendar, event):
    identifier=key(calendar,event['id'])
    with a.LOCK:
        data=a.all_data()
        old=next((x for x in data['schedule'] if x['id']==identifier),{})
        if event.get('status')=='cancelled':
            if old: a.save('schedule',{**old,'remote_cancelled':True,'status':'cancelled' if old.get('status')!='completed' else 'completed','needs_review':old.get('status')=='completed'})
            return
        start=event.get('start',{}); end=event.get('end',{})
        if not start or not end: return
        begin=start.get('dateTime') or start.get('date'); finish=end.get('dateTime') or end.get('date')
        moved=bool(old and (old.get('start')!=begin or old.get('end')!=finish))
        binding=next((x for x in data['schedule'] if event.get('recurringEventId') and x.get('series')==event['recurringEventId'] and x['calendar_id']==calendar and x.get('series_binding')), {})
        item={**old,'id':identifier,'calendar_id':calendar,'event_id':event['id'],'series':event.get('recurringEventId',''),
            'title':event.get('summary','Без названия'),'start':begin,'end':finish,'all_day':'date' in start,
            'remote_cancelled':False,'url':event.get('htmlLink',''),'etag':event.get('etag',''),
            'student':old.get('student',binding.get('student','')),'group':old.get('group',binding.get('group','')),
            'series_binding':old.get('series_binding',binding.get('series_binding',False)),
            'status':old.get('status','planned'),'needs_review':old.get('needs_review',False)}
        if moved:
            item['previous_start']=old['start']
            if old.get('status')=='completed' or find_lesson(a,old): item['needs_review']=True
            else: item['status']='rescheduled'
        a.save('schedule',item)

def public_notes(lesson):
    parts=[lesson.get('topic','Занятие')]
    if lesson.get('notes'): parts.append(lesson['notes'])
    if lesson.get('homework'): parts.append('Домашнее задание\n'+lesson['homework'])
    urls=lesson.get('youtube_urls') or ([lesson['youtube_url']] if lesson.get('youtube_url') else [])
    if urls: parts.append('Запись:\n'+'\n'.join(urls))
    elif lesson.get('recording'): parts.append('Запись: '+lesson['recording'])
    return '\n\n'.join(parts)[:7000]

def description(original, text):
    block=START+'\n'+html.escape(text)+'\n'+END
    pattern=re.escape(START)+r'.*?'+re.escape(END)
    return re.sub(pattern,lambda _:block, original,flags=re.S) if START in original and END in original else original.rstrip()+'\n\n'+block

def sync(a):
    if not MUTEX.acquire(False): return
    STATE['running']=True
    try:
        c=google.config()
        if not c.get('calendar_enabled'): return
        calendar=c.get('calendar_id','primary'); token=google.access_token('calendar')
        now=dt.datetime.now(dt.timezone.utc)
        params={'timeMin':(now-dt.timedelta(days=90)).isoformat(),'timeMax':(now+dt.timedelta(days=180)).isoformat(),
            'singleEvents':'true','showDeleted':'true','maxResults':250}
        events=[]
        for _ in range(40):
            result=google.api('calendar',event_path(calendar)+'?'+urllib.parse.urlencode(params),token=token)
            events.extend(result.get('items',[]))
            if not result.get('nextPageToken'): break
            params['pageToken']=result['nextPageToken']
        else: raise ValueError('Слишком много событий. Выбери отдельный учебный календарь')
        for event in events: import_event(a,calendar,event)
        # Check disappeared occurrences explicitly: never infer deletion from pagination.
        seen={e['id'] for e in events}
        for item in a.all_data()['schedule']:
            if item['calendar_id']!=calendar or item['event_id'] in seen or item.get('remote_cancelled'): continue
            if not ((now-dt.timedelta(days=90)).date().isoformat()<=item['start'][:10]<=(now+dt.timedelta(days=180)).date().isoformat()): continue
            try: event=google.api('calendar',event_path(calendar,item['event_id']),token=token)
            except urllib.error.HTTPError as e:
                if e.code not in (404,410): raise
                event={'id':item['event_id'],'status':'cancelled'}
            import_event(a,calendar,event)
        failures=0
        for item in a.all_data()['schedule']:
            if item['calendar_id']!=calendar or item.get('all_day') or item.get('remote_cancelled'): continue
            try:
                with a.LOCK:
                    item=next(x for x in a.all_data()['schedule'] if x['id']==item['id'])
                    lesson=lesson_links.reconcile_event(a,item)
                    if lesson and lesson.get('attendance')=='completed' and item.get('status') not in ('completed','cancelled'):
                        item=a.save('schedule',{**item,'status':'completed'})
                if not lesson or lesson.get('attendance')!='completed' or not lesson.get('notes') or not c.get('publish_notes'): continue
                text=public_notes(lesson); digest=hashlib.sha256(text.encode()).hexdigest()
                if item.get('published_hash')!=digest:
                    path=event_path(calendar,item['event_id'])
                    remote=google.api('calendar',path,token=token)
                    if remote.get('status')=='cancelled': continue
                    google.api('calendar',path+'?sendUpdates=none',method='PATCH',body={'description':description(remote.get('description',''),text)},
                        headers={'If-Match':remote['etag']},token=token)
                    with a.LOCK:
                        latest=next(x for x in a.all_data()['schedule'] if x['id']==item['id'])
                        a.save('schedule',{**latest,'published_hash':digest,'publish_error':''})
            except Exception as error:
                failures+=1
                message=(str(error) if isinstance(error,ValueError) else
                         f'Google Calendar: HTTP {error.code}. Повторим автоматически.' if isinstance(error,urllib.error.HTTPError) else
                         'Не удалось связать или опубликовать материалы. Повторим автоматически.')
                with a.LOCK:
                    latest=next(x for x in a.all_data()['schedule'] if x['id']==item['id'])
                    a.save('schedule',{**latest,'publish_error':message})
        active=sum(e.get('status')!='cancelled' for e in events)
        STATE['message']=f'Событий в расписании: {active}. Расписание обновлено.'+(f' Требуют проверки: {failures}.' if failures else '')
    except (google.ServiceError,ValueError) as e: STATE['message']=str(e)
    except urllib.error.HTTPError as e: STATE['message']=f'Google Calendar: HTTP {e.code}. Повторим автоматически.'
    except Exception: STATE['message']='Не удалось обновить календарь. Повторим автоматически.'
    finally:
        STATE['running']=False; STATE['last_run']=dt.datetime.now(MOSCOW).isoformat(timespec='seconds'); MUTEX.release()

def worker(a):
    while True:
        sync(a)
        threading.Event().wait(300)
