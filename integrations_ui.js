const googleField=(...args)=>field(...args).replaceAll('id="f_','id="g_').replaceAll('for="f_','for="g_');
names.schedule='Расписание';
let integrations=null,scheduleDay=today;
const originalRender=render;
render=function(){
 originalRender();
 if(page==='schedule')renderSchedule();
 if(page==='settings'){
  const panel=document.createElement('div');panel.className='panel';panel.id='googlePanel';panel.textContent='Загрузка подключений…';$('content').prepend(panel);loadIntegrations();
 }
};
const scheduleStatus={planned:'Запланировано',rescheduled:'Перенесено',completed:'Проведено',cancelled:'Отменено'};
let scheduleView=window.innerWidth<650?'day':'week',scheduleScroll=8*64;
const calDate=d=>new Date(d+'T00:00:00Z');
function calShift(d,n){const v=calDate(d);v.setUTCDate(v.getUTCDate()+n);return v.toISOString().slice(0,10)}
function calWeek(d){return calShift(d,-((calDate(d).getUTCDay()+6)%7))}
function calMove(n){scheduleDay=calShift(scheduleDay,n*(scheduleView==='week'?7:1));render()}
function calSelect(d){if(!/^\d{4}-\d{2}-\d{2}$/.test(d))return;scheduleDay=d;render()}
function calTime(value){return new Date(value).toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'})}
function calSegments(events,day){
 const start=Date.parse(day+'T00:00:00+03:00'),end=start+86400000;
 const items=events.filter(x=>!x.all_day).map(event=>({event,start:Math.max(start,Date.parse(event.start)),end:Math.min(end,Date.parse(event.end))})).filter(x=>x.end>x.start).sort((a,b)=>a.start-b.start||b.end-a.end);
 let cluster=[],clusterEnd=0;
 function finish(){const ends=[];for(const x of cluster){let lane=ends.findIndex(e=>e<=x.start);if(lane<0)lane=ends.length;ends[lane]=x.end;x.lane=lane}for(const x of cluster)x.lanes=ends.length;cluster=[]}
 for(const x of items){if(cluster.length&&x.start>=clusterEnd){finish();clusterEnd=0}cluster.push(x);clusterEnd=Math.max(clusterEnd,x.end)}finish();
 return items.map(x=>({...x,top:(x.start-start)/60000*64/60,height:(x.end-x.start)/60000*64/60}));
}
function calEvent(x,style='',allDay=false){
 const who=x.group?group(x.group):x.student?student(x.student):'Не привязано';
 const time=allDay?'Весь день':calTime(x.start)+'–'+calTime(x.end);
 return `<button class="cal-event cal-${['completed','cancelled','rescheduled'].includes(x.status)?x.status:'planned'}" style="${style}" onclick="openSchedule(${arg(x.id)})" title="${esc(time+' · '+x.title+' · '+who+' · '+(scheduleStatus[x.status]||''))}"><span>${esc(time)}${x.status==='completed'?' ✓':''}</span><strong>${esc(x.title)}</strong><small>${esc(who)}</small></button>`;
}
function renderSchedule(){
 const first=scheduleView==='week'?calWeek(scheduleDay):scheduleDay,days=Array.from({length:scheduleView==='week'?7:1},(_,i)=>calShift(first,i));
 const events=live('schedule'),now=new Date(),current=moscowDate();
 const label=d=>calDate(d).toLocaleDateString('ru-RU',{timeZone:'UTC',day:'numeric',month:'long'});
 const caption=scheduleView==='week'?label(first)+' — '+label(days[6])+', '+days[6].slice(0,4):label(first)+', '+first.slice(0,4);
 const allDay=days.map(day=>events.filter(x=>x.all_day&&x.start<=day&&x.end>day));
 const hasAllDay=allDay.some(x=>x.length);
 $('content').innerHTML=`<section class="calendar"><div class="cal-toolbar"><div class="cal-controls">${btn('Сегодня',"scheduleDay=moscowDate();render()",'secondary')}<button class="secondary" aria-label="Предыдущий период" onclick="calMove(-1)">‹</button><button class="secondary" aria-label="Следующий период" onclick="calMove(1)">›</button></div><h2>${esc(caption)}</h2><div class="cal-controls"><button aria-pressed="${scheduleView==='week'}" class="${scheduleView==='week'?'':'secondary'}" onclick="scheduleView='week';render()">Неделя</button><button aria-pressed="${scheduleView==='day'}" class="${scheduleView==='day'?'':'secondary'}" onclick="scheduleView='day';render()">День</button><input aria-label="Дата расписания" type="date" value="${scheduleDay}" onchange="calSelect(this.value)"></div></div><div class="cal-meta"><span>Уроки · Московское время</span><a href="https://calendar.google.com/calendar/u/0/r" target="_blank" rel="noopener">Открыть Google Calendar ↗</a></div><div class="cal-viewport" id="calViewport"><div class="cal-grid ${scheduleView}" style="--days:${days.length}"><div class="cal-header"><div class="cal-zone">МСК</div>${days.map(d=>`<button class="cal-day ${d===current?'is-today':''}" onclick="scheduleDay='${d}';scheduleView='day';render()"><span>${calDate(d).toLocaleDateString('ru-RU',{timeZone:'UTC',weekday:'short'})}</span><strong>${Number(d.slice(-2))}</strong></button>`).join('')}</div>${hasAllDay?`<div class="cal-allday"><span>Весь день</span>${allDay.map(xs=>`<div>${xs.map(x=>calEvent(x,'',true)).join('')}</div>`).join('')}</div>`:''}<div class="cal-body"><div class="cal-hours">${Array.from({length:24},(_,h)=>`<span style="top:${h*64}px">${String(h).padStart(2,'0')}:00</span>`).join('')}</div>${days.map(d=>{
 const parts=calSegments(events,d),clock=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(now).split(':').map(Number);
 return `<div class="cal-column ${d===current?'is-today':''}">${parts.map(x=>calEvent(x.event,`top:${x.top}px;height:${Math.max(20,x.height-2)}px;left:calc(${x.lane/x.lanes*100}% + 3px);width:calc(${100/x.lanes}% - 6px)`)).join('')}${d===current?`<div class="cal-now" style="top:${(clock[0]*60+clock[1])*64/60}px"></div>`:''}</div>`;
 }).join('')}</div></div></div><div class="cal-legend"><span>● Запланировано</span><span class="done">● Проведено</span><span class="cancelled">● Отменено</span><span>Нажми на урок, чтобы открыть действия</span></div></section>`;
 const viewport=$('calViewport');viewport.scrollTop=scheduleScroll;viewport.onscroll=()=>{scheduleScroll=viewport.scrollTop};
}
function openSchedule(id){
 const x=find('schedule',id);if(!x)return;
 const lesson=live('lessons').find(l=>l.calendar_key===id);
 $('preview').innerHTML=`<h2>${esc(x.title)}</h2><p>${esc(x.all_day?x.start+' · Весь день':new Date(x.start).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'long',timeStyle:'short'})+'–'+calTime(x.end))} · МСК</p><p><span class="badge">${esc(scheduleStatus[x.status]||x.status)}</span></p><p>${esc(x.group?group(x.group):x.student?student(x.student):'Ученик / группа ещё не выбраны')}</p>${x.needs_review?'<p class="notice">Время события изменилось после привязки материалов или проведения. Проверь занятие и начисление.</p>':''}${x.publish_error?'<p class="notice">'+esc(x.publish_error)+'</p>':''}${x.remote_cancelled?'<p>Отменено в Google Calendar</p>':''}<div class="cal-dialog-actions">${btn('Ученик / группа',`$('preview').close();bindCalendar(${arg(id)})`,'secondary')}${!x.remote_cancelled&&!x.all_day&&x.status!=='completed'?btn('✓ Проведено',`$('preview').close();calendarAttendance(${arg(id)},'completed')`):''}${x.status==='completed'?btn('Снять отметку',`$('preview').close();calendarAttendance(${arg(id)},'planned')`,'secondary'):''}${x.status!=='cancelled'?btn('Не состоялось',`$('preview').close();calendarAttendance(${arg(id)},'cancelled')`,'secondary'):''}${lesson?btn('Конспект',`$('preview').close();previewLesson(${arg(lesson.id)})`,'secondary'):''}</div>${/^https:\/\//.test(x.url||'')?`<p><a target="_blank" rel="noopener" href="${esc(x.url)}">Открыть событие в Google Calendar ↗</a></p>`:''}<div class="actions">${btn('Закрыть',"$('preview').close()",'secondary')}</div>`;
 $('preview').showModal();
}

function bindCalendar(id){
 const item=find('schedule',id);
 $('preview').innerHTML=`<h2>Привязать занятие</h2><form id="bindCalendarForm">${field('target','Ученик или группа','select',item.group?'g:'+item.group:item.student?'s:'+item.student:'',[['','Выбери'],...live('students').map(s=>['s:'+s.id,s.name]),...live('groups').map(g=>['g:'+g.id,'Группа: '+g.name])])}${item.series?field('series','Применить к повторяющимся занятиям','select','true',[['true','Ко всей серии'],['false','Только к этому событию']]):''}<div class="actions">${btn('Закрыть',"$('preview').close()",'secondary')}<button type="submit">Сохранить</button></div></form>`;
 $('bindCalendarForm').onsubmit=async e=>{e.preventDefault();const v=Object.fromEntries(new FormData(e.target));const [kind,target]=(v.target||'').split(':');try{await api('calendar/bind',{id,student:kind==='s'?target:'',group:kind==='g'?target:'',series:v.series==='true'});$('preview').close();await load()}catch(error){toast(error.message,true)}};
 $('preview').showModal();
}
async function calendarAttendance(id,status){try{await api('calendar/attendance',{id,status});await load();toast('Статус и финансы обновлены')}catch(e){toast(e.message,true)}}
async function loadIntegrations(){
 try{
  integrations=await api('integrations');if(!$('googlePanel'))return;
  const g=integrations.google;
  $('googlePanel').innerHTML=`<h2>Google Calendar и YouTube</h2><p>${g.configured?'Данные приложения Google сохранены.':'Сначала нужно создать OAuth-приложение Google.'} Календарь: ${g.calendar_connected?'подключён':'не подключён'}. YouTube: ${g.youtube_connected?'подключён':'не подключён'}.</p><p class="muted">Адрес возврата для приложения Google: <code>${esc(integrations.redirect_uri)}</code></p><form id="googleForm"><div class="grid">${googleField('client_id','Google OAuth Client ID')}${googleField('client_secret','Google OAuth Client Secret','password')}${field('calendar_id','ID календаря','text',g.calendar_id)}${field('calendar_enabled','Автозагрузка расписания','select',String(g.calendar_enabled),[['false','Выключена'],['true','Включена']])}${field('publish_notes','Добавлять конспект в описание события','select',String(g.publish_notes),[['false','Выключено'],['true','Включено']])}${field('video_enabled','Автоперенос записей Zoom на YouTube','select',String(g.video_enabled),[['false','Выключен'],['true','Включён']])}${field('video_privacy','Доступ к новым видео','select',g.video_privacy,[['private','Закрытый — только владелец / приглашённые'],['unlisted','По ссылке — любой получивший ссылку']])}</div><p class="muted">Пустые ключи сохраняют прежние значения. Конспект в календаре увидят пользователи с доступом к описанию события. YouTube может ограничивать загрузки нового API-проекта закрытым доступом.</p><button>Сохранить настройки</button></form><div class="toolbar" style="margin-top:18px">${btn('Подключить календарь',"connectGoogle('calendar')")}${btn('Подключить YouTube',"connectGoogle('youtube')")}${btn('Выбрать календарь','chooseCalendar()','secondary')}</div><p>${esc(integrations.calendar.message)}</p><p>${esc(integrations.video.message)}</p><p class="muted">${esc(integrations.backup.message)}</p><details><summary>Перенос видео</summary>${integrations.videos.map(v=>`<p>${esc(v.date)} · ${esc(v.message||v.status)} ${v.status==='needs_review'?btn('Повторить после проверки канала',`retryVideo(${arg(v.id)})`,'secondary'):''} ${v.url?'<a target="_blank" rel="noopener" href="'+esc(v.url)+'">YouTube ↗</a>':''}</p>`).join('')||'<p>Записей в очереди пока нет.</p>'}</details>`;
  $('googleForm').onsubmit=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));for(const k of ['calendar_enabled','publish_notes','video_enabled'])d[k]=d[k]==='true';try{await api('google/config',d);await api('integrations/sync',{});await loadIntegrations();toast('Настройки сохранены')}catch(error){toast(error.message,true)}};
 }catch(e){toast(e.message,true)}
}
async function connectGoogle(service){try{const r=await api('google/connect',{service});location.assign(r.url)}catch(e){toast(e.message,true)}}
async function chooseCalendar(){
 try{const r=await api('google/calendars');const input=$('googleForm').elements.calendar_id;const select=document.createElement('select');select.name='calendar_id';select.id=input.id;select.innerHTML=options(r.items.map(x=>[x.id,x.name+' · '+x.role]),input.value);input.replaceWith(select)}catch(e){toast(e.message,true)}
}

async function retryVideo(id){if(!window.confirm('Проверь свой канал YouTube. Начать новую загрузку этой записи? Если видео уже есть на канале, повтор создаст дубликат.'))return;try{await api('video/retry',{id,restart:true});await loadIntegrations();toast('Повтор поставлен в очередь')}catch(e){toast(e.message,true)}}
