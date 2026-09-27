const token='__TOKEN__';
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const arg=v=>esc(JSON.stringify(v)),rub=v=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(v||0)+' ₽';
const moscowDate=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Moscow'}).format(new Date());
let today=moscowDate();
let dashboardMode='month',dashboardMonth=today.slice(0,7),dashboardYear=today.slice(0,4);
function periodMetrics(data,period){
 const lessons=data.lessons.filter(l=>!l.deleted&&(l.attendance||'completed')==='completed'&&String(l.date||'').startsWith(period));
 const payments=data.payments.filter(p=>!p.deleted&&String(p.date||'').startsWith(period));
 const paid=payments.reduce((n,p)=>n+Number(p.amount)*(p.operation==='Поступление'?1:-1),0);
 const hours=lessons.reduce((n,l)=>n+Number(l.minutes)/60,0);
 const earned=lessons.reduce((n,l)=>n+(l.charges||[]).reduce((s,c)=>s+Number(c.amount),0),0);
 return {paid,hours,count:lessons.length,hourly:hours?earned/hours:null,lessons,payments};
}
let db,stats,page='overview',editKind,editId,context={},cardKind,cardId,financeMonth=today.slice(0,7),financeTab='balance',automation=null;
const names={overview:'Обзор',students:'Ученики',groups:'Группы',finance:'Финансы',lessons:'Занятия',settings:'Настройки'};
const labels={students:'ученика',groups:'группу',enrollments:'условия занятий',bills:'сумму к оплате',payments:'платёж',lessons:'занятие'};
const filters={status:'Занимается',group:'',grade:'',student:'',from:'',to:'',q:''};
const live=k=>db[k].filter(x=>!x.deleted),find=(k,id)=>db[k].find(x=>x.id===id),student=id=>find('students',id)?.name||'—',group=id=>find('groups',id)?.name||'Индивидуально';
const terms=()=>live('enrollments').filter(e=>e.active!==false&&!find('students',e.student)?.deleted&&(!e.group||!find('groups',e.group)?.deleted));
async function api(path,data){
 const writing=data!==undefined,controller=new AbortController();
 // Google calendar discovery can take one token refresh plus an API request.
 const timeout=path==='google/calendars'?160000:writing?45000:20000;
 const timer=setTimeout(()=>controller.abort(),timeout);
 const uncertain=writing?' Ответ не получен: операция могла выполниться. Проверь данные перед повтором.':'';
 try{
  const r=await fetch('/api/'+path,{method:writing?'POST':'GET',headers:{'Content-Type':'application/json','X-CRM-Token':token},body:writing?JSON.stringify(data):undefined,signal:controller.signal});
  if(r.status===401)throw Error('Нужно войти в CRM. Обнови страницу и введи логин и пароль.');
  if(r.status===403)throw Error('Доступ к этой вкладке истёк или отклонён. Скопируй несохранённый текст и обнови страницу.');
  if(r.status>=500)throw Error('Сервер временно не отвечает.'+uncertain);
  let j;try{j=await r.json()}catch(e){if(controller.signal.aborted)throw e;throw Error('Не удалось прочитать ответ сервера.'+uncertain)}
  if(!r.ok)throw Error(j.error||'Не удалось выполнить запрос.');
  return j;
 }catch(e){
  if(controller.signal.aborted)throw Error('Сервер не ответил вовремя. Проверь соединение.'+uncertain);
  if(e instanceof TypeError)throw Error('Связь с сервером прервалась. Проверь соединение.'+uncertain);
  throw e;
 }finally{clearTimeout(timer)}
}
async function load(){const r=await api('data');db=r.data;stats=r.metrics;render();if($('detail').open)showCard(cardKind,cardId)}
function go(p){if(page!==p)filters.group='';page=p;filters.q='';render()}
const btn=(text,js,cls='')=>`<button class="${cls}" onclick="${js}">${text}</button>`;
const editButton=(k,id,text='Изменить')=>btn(text,`edit(${arg(k)},${arg(id)})`,'secondary');
const delButton=(k,id)=>btn('Удалить',`removeRecord(${arg(k)},${arg(id)})`,'danger');
function table(head,rows){return `<div class="panel"><table><thead><tr>${head.map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${rows.join('')||`<tr><td colspan="${head.length}">Нет записей по выбранным условиям</td></tr>`}</tbody></table></div>`}
const row=xs=>'<tr>'+xs.map(x=>'<td>'+x+'</td>').join('')+'</tr>';
const options=(xs,value)=>xs.map(([v,t])=>`<option value="${esc(v)}" ${String(value)===String(v)?'selected':''}>${esc(t)}</option>`).join('');
function selectFilter(key,title,xs,value=filters[key]){return `<label class="filter">${title}<select onchange="filters.${key}=this.value;render()">${options(xs,value)}</select></label>`}
function search(){return `<input class="search" placeholder="Поиск по имени или содержимому" value="${esc(filters.q)}" oninput="filters.q=this.value;filterSearch()">`}
function filterSearch(){document.querySelectorAll('tbody tr,.lesson').forEach(x=>x.hidden=!x.textContent.toLowerCase().includes(filters.q.toLowerCase()))}
function cards(items){return '<div class="cards">'+items.map(([a,b])=>`<div class="card"><span class="muted">${a}</span><strong>${b}</strong></div>`).join('')+'</div>'}
function termPrice(s){return terms().filter(e=>e.student===s).map(e=>`${esc(group(e.group))}: ${rub(e.hourly)}/ч`).join('<br>')}
function render(){
 $('title').textContent=names[page];$('nav').innerHTML=Object.entries(names).map(([k,v])=>btn(v,`go('${k}')`,k===page?'active':'')).join('');let h='';
 if(page==='overview'){
 const period=dashboardMode==='month'?dashboardMonth:dashboardYear,m=periodMetrics(db,period);
 h=`<div class="toolbar"><label>Период<select onchange="dashboardMode=this.value;render()">${options([['month','Месяц'],['year','Год']],dashboardMode)}</select></label>${dashboardMode==='month'?`<label>Месяц<input type="month" value="${esc(dashboardMonth)}" onchange="if(this.value){dashboardMonth=this.value;render()}"></label>`:`<label>Год<input type="number" min="2000" max="2100" value="${esc(dashboardYear)}" onchange="if(this.value>=2000&&this.value<=2100){dashboardYear=this.value;render()}"></label>`}</div>`;
 h+=cards([['Получено за '+(dashboardMode==='month'?'месяц':'год'),rub(m.paid)],['Средняя стоимость часа',m.hourly===null?'—':rub(m.hourly)],['Проведено занятий',m.count],['Часов занятий',new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(m.hours)]]);
 h+='<p class="muted">Получено — оплаты по дате поступления за вычетом возвратов. Средняя стоимость часа — стоимость проведённых уроков ÷ часы занятий.</p>';
 h+='<h2>Занятия сегодня</h2><div class="panel">'+(live('lessons').filter(l=>l.date===today).sort((a,b)=>(b.start_time||'').localeCompare(a.start_time||'')).map(lessonHTML).join('')||'<p>За сегодня занятия пока не загружены. Готовые материалы Zoom появятся автоматически, если подключена автопроверка.</p>')+'</div>';
 h+='<h2>Финансы по ученикам за выбранный период</h2>';
 h+=table(['Ученик','Получено','Проведено занятий','Средняя стоимость часа'],live('students').map(s=>{const ls=m.lessons.filter(l=>(l.charges||[]).some(c=>c.student===s.id)||l.student===s.id),hours=ls.reduce((n,l)=>n+l.minutes/60,0),earned=ls.reduce((n,l)=>n+(l.charges||[]).filter(c=>c.student===s.id).reduce((v,c)=>v+c.amount,0),0),paid=m.payments.filter(p=>p.student===s.id).reduce((n,p)=>n+p.amount*(p.operation==='Поступление'?1:-1),0);return row([btn(esc(s.name),`showCard('students',${arg(s.id)})`,'link'),rub(paid),ls.length,hours?rub(earned/hours):'—'])}));
 }
 if(page==='students'){
 h=`<div class="toolbar">${search()}${btn('+ Ученик',"edit('students')")}</div><div class="filters">${selectFilter('status','Статус',[['','Все статусы'],...['Занимается','Пауза','Завершил','Новый'].map(x=>[x,x])])}${selectFilter('group','Группа',[['','Все группы'],...live('groups').map(g=>[g.id,g.name])])}${selectFilter('grade','Класс',[['','Все классы'],...[...new Set(live('students').map(s=>s.grade).filter(Boolean))].sort().map(g=>[g,g])])}</div>`;
 let students=live('students').filter(s=>(!filters.status||s.status===filters.status)&&(!filters.grade||s.grade===filters.grade)&&(!filters.group||terms().some(e=>e.student===s.id&&e.group===filters.group)));
 h+=table(['Ученик','Статус / класс','Контакты','Занятия и стоимость',''],students.map(s=>row([btn(esc(s.name),`showCard('students',${arg(s.id)})`,'link'),esc(s.status)+(s.grade?'<br>'+esc(s.grade)+' класс':''),esc(s.contact)+'<br><span class="muted">'+esc(s.parent_contact)+'</span>',termPrice(s.id)||'Условия не заданы',editButton('students',s.id)+' '+delButton('students',s.id)])));
 }
 if(page==='groups'){
 h=`<div class="toolbar">${search()}${btn('+ Группа',"edit('groups')")}</div>`;
 h+=table(['Группа','Ученики','Выручка / час',''],live('groups').map(g=>{const es=terms().filter(e=>e.group===g.id);return row([btn(esc(g.name),`showCard('groups',${arg(g.id)})`,'link'),es.map(e=>esc(student(e.student))).join('<br>')||'Пока нет учеников',rub(es.reduce((s,e)=>s+e.hourly,0)),editButton('groups',g.id,'Состав и условия')+' '+delButton('groups',g.id)])}));
 }
 if(page==='finance'){
 h=`<div class="toolbar"><label>Месяц<input type="month" value="${financeMonth}" onchange="financeMonth=this.value;render()"></label>${btn('К оплате',"financeTab='balance';render()",financeTab==='balance'?'':'secondary')}${btn('История переводов',"financeTab='payments';render()",financeTab==='payments'?'':'secondary')}${btn('+ Оплата',"edit('payments',null,{period:financeMonth})")}</div>`;
 if(financeTab==='balance'){
 const bs=stats.bills.filter(b=>b.period===financeMonth),ps=live('payments').filter(p=>p.period===financeMonth);
 h+=cards([['К оплате',rub(bs.reduce((s,b)=>s+b.amount,0))],['Получено за месяц',rub(ps.reduce((s,p)=>s+p.amount*(p.operation==='Возврат'?-1:1),0))],['Осталось оплатить',rub(bs.reduce((s,b)=>s+b.debt,0))],['Переплаты',rub(bs.reduce((s,b)=>s+b.advance,0))]]);
 h+='<p class="muted">Для ученика на месяц можно выбрать фиксированную сумму или расчёт по проведённым занятиям. В расчёт по урокам входят только занятия с отметкой «Проведено». Фиксированная сумма при отметках не увеличивается. «Получено» — реальные переводы; ошибочный перевод можно исправить или удалить в «Истории переводов».</p>';
 const students=db.students.filter(s=>(!s.deleted&&s.status==='Занимается')||bs.some(b=>b.student===s.id)||ps.some(p=>p.student===s.id));
 h+=table(['Ученик','К оплате','Получено','Осталось','Действия'],students.map(s=>{const b=bs.find(b=>b.student===s.id),paid=ps.filter(p=>p.student===s.id).reduce((n,p)=>n+p.amount*(p.operation==='Возврат'?-1:1),0);return row([esc(s.name)+(s.deleted?' <small>(удалён)</small>':''),b?rub(b.amount):'<span class="muted">Не задано</span>',rub(paid),b?rub(b.debt)+(b.advance?'<br>Переплата '+rub(b.advance):''):'—',btn('Добавить оплату',`edit('payments',null,${arg({student:s.id,period:financeMonth})})`)+(b?' '+editButton('bills',b.id,'Изменить сумму')+' '+delButton('bills',b.id):' '+btn('Задать сумму',`edit('bills',null,${arg({student:s.id,period:financeMonth})})`,'secondary'))])}));
 }else{
 h+=table(['Дата перевода','Ученик','Операция','Сумма','За месяц',''],live('payments').filter(p=>p.period===financeMonth).map(p=>row([esc(p.date),esc(student(p.student)),esc(p.operation),rub(p.amount),esc(p.period),editButton('payments',p.id)+' '+delButton('payments',p.id)])));
 h+='<details><summary>Переводы без указанного месяца</summary>'+table(['Дата','Ученик','Сумма',''],live('payments').filter(p=>!p.period).map(p=>row([esc(p.date),esc(student(p.student)),rub(p.amount),editButton('payments',p.id)+' '+delButton('payments',p.id)])))+'</details>';
 }
 }
 if(page==='lessons'){
 h=automationHTML()+`<div class="toolbar">${search()}${btn('Сегодня',"filters.from=moscowDate();filters.to=filters.from;render()",'secondary')}${btn('Все даты',"filters.from='';filters.to='';render()",'secondary')}${btn('+ Занятие',"edit('lessons')")}</div><div class="filters">${selectFilter('student','Ученик',[['','Все ученики'],...db.students.map(s=>[s.id,s.name])])}${selectFilter('group','Группа',[['','Все форматы'],['individual','Индивидуально'],...db.groups.map(g=>[g.id,g.name])])}<label>С даты<input type="date" value="${filters.from}" onchange="filters.from=this.value;render()"></label><label>По дату<input type="date" value="${filters.to}" onchange="filters.to=this.value;render()"></label></div>`;
 const ls=live('lessons').filter(l=>(!filters.group||(filters.group==='individual'?!l.group:l.group===filters.group))&&(!filters.student||l.student===filters.student||l.charges?.some(c=>c.student===filters.student))&&(!filters.from||l.date>=filters.from)&&(!filters.to||l.date<=filters.to)).sort((a,b)=>b.date.localeCompare(a.date));
 h+='<div class="panel">'+(ls.map(lessonHTML).join('')||'<p>Нет занятий по выбранным условиям</p>')+'</div>';
 }
 if(page==='settings'){
 h=`<div class="panel"><h2>Корзина</h2><p class="muted">Удалённые записи можно восстановить. Ученики и группы сохраняют свою финансовую историю.</p>${trashHTML()}</div><div class="panel"><h2>Резервная копия</h2>${btn('Скачать данные',"backup()")}</div><details class="panel"><summary>Подключение Zoom</summary><p id="zoomStatus">Проверка статуса…</p><p>Настройки подключения сохранены. Автопроверка работает каждые 5 минут, пока запущен сервер CRM. Конспекты готовятся в фоне. Собственный обработчик на сервере автоматически готовит отчёт и конспект по расшифровке урока Zoom. Платные API не используются.</p><form id="zoomForm"><div class="grid">${field('account_id','Zoom Account ID')+field('client_id','Zoom Client ID')+field('client_secret','Zoom Client Secret','password')+field('user_id','Email владельца Zoom')+field('provider','Обработчик конспектов','select','rules',[['local','Собственная модель на сервере'],['rules','Правила — прежний обработчик'],['codex','Codex — подписка ChatGPT'],['ollama','Локальная модель Ollama'],['none','Только сохранять сводки']])+field('model','Имя модели Ollama (для локального режима)')+field('enabled','Проверка записей каждые 5 минут','select','false',[['false','Выключена'],['true','Включена']])}</div><p class="muted">Пустые ключи сохраняют предыдущие значения. Ключи хранятся локально и не включаются в экспорт.</p><button>Сохранить настройки</button></form><div class="actions">${btn('Проверить сейчас','syncZoom()','secondary')}</div></details>`;
 }
 $('content').innerHTML=h;filterSearch();
 if(page==='settings'){$('zoomForm').onsubmit=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));d.enabled=d.enabled==='true';try{await api('zoom/config',d);e.target.reset();await zoomStatus()}catch(e){toast(e.message,true)}};zoomStatus().catch(e=>toast(e.message,true))}
}
const attendanceNames={completed:'Проведено',needs_confirmation:'Подтверди проведение',cancelled:'Не состоялось'};
const reportNames={waiting_source:'Готовим материалы',queued:'Готовим материалы',ready:'Конспект готов',manual:'Отчёт изменён вручную'};
function lessonHTML(l){const status=l.attendance||'completed';return `<article class="lesson"><div><span class="muted">${esc(l.date)} · ${l.minutes} мин${l.billing_basis==='schedule'?' по расписанию':''} · ${esc(l.group?group(l.group):student(l.student))}</span><p><span class="badge">${esc(attendanceNames[status]||status)}</span> ${l.report_state?`<span class="muted">${esc(reportNames[l.report_state]||(l.notes?'Конспект готов':'Ожидаем конспект'))}${l.report_provider==='rules'?' · без модели, черновик':''}${l.reviewed?' · проверен':''}</span>`:''}</p>${l.billing_basis==='unassigned'?'<p class="notice">Запись пока не связана с расписанием. Для оплаты нужна длительность урока из расписания.</p>':''}<h3>${esc(l.topic||'Без темы')}</h3><p><b>Домашка:</b> ${esc(l.homework||'Не зафиксирована')}${l.next?`<br><b>Следующий урок:</b> ${esc(l.next)}`:''}</p>${(l.youtube_urls||[l.youtube_url]).filter(url=>/^https:\/\/www\.youtube\.com\//.test(url||'')).map((url,i)=>`<p><a target="_blank" rel="noopener" href="${esc(url)}">Запись на YouTube${i?' · часть '+(i+1):''} ↗</a></p>`).join('')}${/^https:\/\//.test(l.recording||'')?`<a target="_blank" rel="noopener" href="${esc(l.recording)}">Запись урока ↗</a>`:''}</div><div class="lesson-actions">${status!=='completed'?btn('✓ Проведено',`markAttendance(${arg(l.id)},'completed')`):btn('Снять отметку',`markAttendance(${arg(l.id)},'needs_confirmation')`,'secondary')}${status!=='cancelled'?btn('Не состоялось',`markAttendance(${arg(l.id)},'cancelled')`,'secondary'):''}${editButton('lessons',l.id)}${btn('Конспект для ученика',`previewLesson(${arg(l.id)})`,'secondary')}${delButton('lessons',l.id)}</div></article>`}
async function markAttendance(id,attendance){try{await api('lesson/attendance',{id,attendance});await load();toast('Статус и расчёты обновлены')}catch(e){toast(e.message,true)}}
function automationHTML(){if(!automation)return '<div class="panel"><p>Проверяем состояние автоматизации…</p></div>';const s=automation,q=s.queue||{counts:{},items:[]};const pending=(q.counts.pending||0)+(q.counts.retry||0)+(q.counts.running||0);return `<div class="panel"><h2>Автоматические отчёты</h2><p>${s.enabled?'Включены · проверка каждые 5 минут':'Автопроверка выключена'} · ${s.provider==='local'?'Собственный обработчик на сервере':s.provider==='rules'?'Без модели · правила и справочник':s.provider==='codex'?'Codex / подписка ChatGPT':s.provider==='ollama'?'Локальная модель':'Без обработчика'} · В очереди: ${pending}</p><p class="muted">${s.running?'Обработка идёт в фоне. ':''}${esc(s.message)} Последняя проверка: ${s.last_run?esc(s.last_run.replace('T',' ')):'ещё не выполнялась'}</p>${!s.enabled?btn('Включить автоматические отчёты','enableAutomation()'):''}${q.items.length?'<details><summary>Состояние отдельных занятий</summary>'+q.items.map(x=>'<p>'+esc(x.date)+' · '+esc(x.kind==='groups'?group(x.target):student(x.target))+' — '+esc(x.status==='running'?'Готовим конспект':x.message||'Ожидает обработки')+'</p>').join('')+'</details>':''}<p class="muted">Обработка выполняется на сервере автоматически.</p></div>`}
async function enableAutomation(){try{await api('zoom/config',{enabled:true});await refreshAutomation(true)}catch(e){toast(e.message,true)}}

function field(key,label,type='text',value='',opts=null){let id='f_'+key;let control=opts?`<select id="${id}" name="${key}">${options(opts,value)}</select>`:type==='textarea'?`<textarea id="${id}" name="${key}">${esc(value)}</textarea>`:`<input id="${id}" name="${key}" type="${type}" value="${esc(value)}" ${type==='number'?'min="0" step="0.01"':''}>`;return `<div><label for="${id}">${label}</label>${control}</div>`}
function showCard(kind,id){cardKind=kind;cardId=id;const x=find(kind,id);if(!x||x.deleted){$('detail').close();return}let h=`<div class="top"><h2>${esc(x.name)}</h2>${btn('Закрыть',"$('detail').close()",'secondary')}</div>`;
 if(kind==='students'){
 const s=stats.students.find(s=>s.id===id),debt=stats.bills.filter(b=>b.student===id).reduce((n,b)=>n+b.debt,0);
 h+=`<p>${esc(x.status)}${x.grade?' · '+esc(x.grade)+' класс':''}</p><div class="grid"><div><b>Контакт ученика</b><p>${esc(x.contact||'Не указан')}</p></div><div><b>Родитель</b><p>${esc(x.parent_name)}<br>${esc(x.parent_contact||'Контакт не указан')}</p></div><div><b>Город</b><p>${esc(x.city||'Не указан')}</p></div><div><b>Цель / запрос</b><p>${esc(x.goal||'Не указан')}</p></div><div><b>Источник</b><p>${esc(x.source||'Не указан')}</p></div></div>${cards([['Получено',rub(s.paid)],['Долг',rub(debt)],['План / месяц',rub(s.monthly)],['Потенциал',rub(s.forecast)]])}`;
 h+=`<div class="toolbar"><h3>Условия занятий</h3>${btn('+ Индивидуальные занятия',`edit('enrollments',null,${arg({student:id,group:''})})`)}</div>`;
 h+=table(['Формат','Цена / час','План / месяц','До даты','Состояние',''],live('enrollments').filter(e=>e.student===id).map(e=>row([esc(group(e.group)),rub(e.hourly),rub(e.monthly),esc(e.until||'Не задано'),e.active?'Занимается':'Завершены',editButton('enrollments',e.id)+(e.active?' '+btn('Завершить',`endTerms(${arg(e.id)})`,'secondary'):'')+' '+delButton('enrollments',e.id)])));
 h+='<p class="muted">В группу ученик добавляется через её карточку. Изменение цены не меняет прошлые занятия и согласованные суммы к оплате.</p>';
 }else{
 const es=terms().filter(e=>e.group===id);h+=`<p>${esc(x.comment)}</p><p>Стандартная цена с ученика: <b>${rub(x.hourly)}</b> / час. Всего группа: <b>${rub(es.reduce((s,e)=>s+e.hourly,0))}</b> / час.</p>`;
 h+=table(['Ученик','Цена / час','План / месяц','До даты'],es.map(e=>row([esc(student(e.student)),rub(e.hourly),rub(e.monthly),esc(e.until||'Не задано')])));
 h+='<h3>Проведённые занятия</h3>'+live('lessons').filter(l=>l.group===id).map(lessonHTML).join('');
 }
 h+=`<div class="actions">${editButton(kind,id,kind==='groups'?'Изменить состав и условия':'Изменить карточку')}${delButton(kind,id)}</div>`;
 $('detail').innerHTML=h;if(!$('detail').open)$('detail').showModal();
}
function rosterHTML(g){const current=terms().filter(e=>e.group===g.id);const defaultRate=g.hourly??current[0]?.hourly??0;return `<h3>Состав группы</h3><p class="muted">Отметь учеников и укажи условия каждого. Снятие отметки завершает участие в группе, сохраняя историю.</p><div class="roster">`+live('students').map(s=>{const e=current.find(e=>e.student===s.id)||{};return `<div class="member"><label><input type="checkbox" name="member_${esc(s.id)}" ${e.id?'checked':''} onchange="toggleMember(this)">${esc(s.name)}</label><div class="grid member-fields" ${!e.id?'hidden':''}>${field('rate_'+s.id,'Цена часа, ₽','number',e.hourly??defaultRate)+field('month_'+s.id,'План месяца, ₽','number',e.monthly??0)+field('until_'+s.id,'Занимается до','date',e.until)}</div></div>`}).join('')+'</div>'}
function toggleMember(input){input.closest('.member').querySelector('.member-fields').hidden=!input.checked}
function edit(kind,id=null,defaults={}){
 editKind=kind;editId=id;context=defaults;const x=id?find(kind,id):defaults;let f='';const S=db.students.filter(s=>!s.deleted||s.id===x.student).map(s=>[s.id,s.name]),G=[['','Индивидуально'],...db.groups.filter(g=>!g.deleted||g.id===x.group).map(g=>[g.id,g.name])];
 if(kind==='students'){
 f='<div class="grid">'+field('name','Имя ученика','text',x.name)+field('status','Статус','select',x.status||'Занимается',['Занимается','Пауза','Завершил','Новый'].map(v=>[v,v]))+field('contact','Контакт ученика','text',x.contact)+field('parent_name','Имя родителя','text',x.parent_name)+field('parent_contact','Контакт родителя','text',x.parent_contact)+field('grade','Класс','text',x.grade)+field('city','Город (необязательно)','text',x.city)+field('source','Источник','text',x.source)+field('zoom_id','ID конференции Zoom','text',x.zoom_id)+'</div>'+field('goal','Цель / запрос','textarea',x.goal)+field('comment','Комментарий','textarea',x.comment);
 }
 if(kind==='groups')f=field('name','Название группы','text',x.name)+field('hourly','Стандартная стоимость часа с одного ученика, ₽','number',x.hourly??terms().find(e=>e.group===x.id)?.hourly??0)+field('comment','Комментарий','textarea',x.comment)+field('zoom_id','ID конференции Zoom','text',x.zoom_id)+rosterHTML(x);
 if(kind==='enrollments')f=`<p><b>${esc(student(x.student))}</b> · ${esc(group(x.group))}</p><div class="grid">`+field('hourly','Стоимость часа для ученика, ₽','number',x.hourly)+field('monthly','Ожидаемая стоимость месяца, ₽','number',x.monthly)+field('until','Предполагаемая дата окончания','date',x.until)+field('active','Занятия в этом формате','select',x.active===false?'false':'true',[['true','Продолжаются'],['false','Завершены']])+'</div>';
 if(kind==='bills')f=field('calculation','Как считать оплату','select',x.calculation||'fixed',[['fixed','Фиксированная сумма за месяц'],['lessons','По проведённым занятиям']])+field('student','Ученик','select',x.student,S)+field('period','Месяц','month',x.period||financeMonth)+field('amount','Сумма к оплате за месяц, ₽','number',x.amount??stats.students.find(s=>s.id===(x.student||S[0]?.[0]))?.monthly??0)+field('comment','Комментарий / причина перерасчёта','textarea',x.comment)+'<p class="muted">Для фиксированного месяца укажи согласованную сумму. Для расчёта по занятиям сумма считается автоматически по отметкам «Проведено»; поле суммы не используется. Переводы вносятся отдельно.</p>';
 if(kind==='payments')f=field('student','Ученик','select',x.student,S)+field('date','Дата перевода','date',x.date||today)+field('amount','Сумма перевода, ₽','number',x.amount)+field('operation','Операция','select',x.operation||'Поступление',[['Поступление','Поступление'],['Возврат','Возврат']])+field('period','За какой месяц','month',x.period||financeMonth)+field('comment','Комментарий','textarea',x.comment);
 if(kind==='lessons'){
 f=field('group','Группа или индивидуально','select',x.group,G)+`<div id="individualStudent">${field('student','Ученик','select',x.student,[['','Выбери ученика'],...S])}</div><div class="grid">`+field('date','Дата проведённого урока','date',x.date||today)+field('minutes','Длительность, минуты','number',x.minutes||60)+'</div>'+field('topic','Тема','text',x.topic)+field('homework','Домашнее задание','textarea',x.homework)+field('next','План следующего урока','textarea',x.next)+field('notes','Конспект для ученика','textarea',x.notes)+field('recording','Ссылка на запись','url',x.recording);
 if(x.zoom_uuid&&x.report_provider!=='local')f+=field('reviewed','Проверка автоматического отчёта','select',x.reviewed?'true':'false',[['false','Не проверен'],['true','Проверен']]);
 if(x.raw_summary)f+='<details><summary>Исходная сводка — только для тебя</summary><pre style="white-space:pre-wrap">'+esc(x.raw_summary)+'</pre></details>';
 if(x.report_analysis)f+='<details><summary>Проверка конспекта — только для тебя</summary><p>'+esc((x.report_analysis.warnings||[]).join(' '))+'</p><p>Основания отчёта из материалов урока:</p><pre style="white-space:pre-wrap">'+esc(Object.entries(x.report_analysis.evidence||{}).map(([topic,quote])=>topic+'\n'+quote).join('\n\n'))+'</pre></details>';
 }
 $('formTitle').textContent=(id?'Изменить ':'Добавить ')+labels[kind];$('fields').innerHTML=f;$('error').textContent='';$('save').hidden=false;
 if(kind==='groups')$('f_hourly').oninput=()=>document.querySelectorAll('.member').forEach(m=>{if(!m.querySelector('input[type=checkbox]').checked)m.querySelector('input[name^=rate_]').value=$('f_hourly').value});
 if(kind==='bills'){const toggle=()=>{$('f_amount').disabled=$('f_calculation').value==='lessons'};$('f_calculation').onchange=toggle;toggle()}
 if(kind==='bills'&&!id)$('f_student').onchange=()=>{$('f_amount').value=stats.students.find(s=>s.id===$('f_student').value)?.monthly||0};
 if(kind==='lessons'){
 const change=()=>{$('individualStudent').hidden=!!$('f_group').value};$('f_group').onchange=change;change();
 if(id){for(const key of ['group','student','date','minutes'])$('f_'+key).disabled=true;const p=document.createElement('p');p.className='muted';p.textContent='Дата, участники и длительность сохраняют финансовую историю. Ошибочный урок можно удалить и добавить заново.';$('fields').prepend(p)}
 }
 if(!$('modal').open)$('modal').showModal();
}
$('form').onsubmit=async event=>{
 event.preventDefault();$('save').disabled=true;let vals=Object.fromEntries(new FormData(event.target)),old=editId?find(editKind,editId):context,d={...old,...vals};
 if(editId)d.id=editId;if(editKind==='enrollments')d.active=vals.active==='true';if(editKind==='lessons'&&d.zoom_uuid&&Object.hasOwn(vals,'reviewed'))d.reviewed=vals.reviewed==='true';
 try{
 if(editKind==='groups'){
 const members=live('students').filter(s=>vals['member_'+s.id]).map(s=>({student:s.id,hourly:vals['rate_'+s.id],monthly:vals['month_'+s.id],until:vals['until_'+s.id]}));
 const g={...old,name:vals.name,hourly:vals.hourly,comment:vals.comment,zoom_id:vals.zoom_id};await api('group',{group:g,members});
 }else await api('save/'+editKind,d);
 $('modal').close();await load();toast('Сохранено');
 }catch(e){$('error').textContent=e.message}finally{$('save').disabled=false}
};
function toast(text,error=false){$('toast').textContent=text;$('toast').className=error?'toast error':'toast';$('toast').hidden=false;setTimeout(()=>$('toast').hidden=true,5000)}
async function endTerms(id){try{await api('save/enrollments',{...find('enrollments',id),active:false});await load();toast('Занятия в этом формате завершены. История сохранена.')}catch(e){toast(e.message,true)}}
let pendingDelete;
function removeRecord(kind,id){pendingDelete={kind,id};const effects={students:'Ученик исчезнет из обычного списка и прогноза. Прошлые занятия и финансовые записи сохранятся.',groups:'Группа исчезнет из списка и прогноза. Прошлые занятия и оплаты сохранятся.',enrollments:'Условия перестанут участвовать в новых занятиях и прогнозе. Прошлые цены сохранятся.',bills:'Согласованная сумма перестанет учитываться в долге. Полученные переводы сохранятся.',payments:'Этот перевод перестанет учитываться в полученных деньгах и остатке оплаты.',lessons:'Занятие перестанет учитываться в статистике. Видео в Zoom сохранится.'};$('confirmText').textContent=effects[kind]+' Запись можно восстановить в Настройках → Корзина.';$('confirmTitle').textContent='Удалить '+labels[kind]+'?';$('confirm').showModal()}
async function confirmDelete(){try{await api('trash',pendingDelete);$('confirm').close();await load();toast('Перемещено в корзину')}catch(e){toast(e.message,true)}}
function trashHTML(){return table(['Тип','Запись',''],Object.keys(labels).flatMap(k=>db[k].filter(x=>x.deleted).map(x=>row([esc(labels[k]),esc(x.name||x.topic||student(x.student)+' · '+(x.period||group(x.group))),x.merged_into?'<span class="muted">Материалы перенесены в основное занятие</span>':btn('Восстановить',`restoreRecord(${arg(k)},${arg(x.id)})`,'secondary')]))))}
async function restoreRecord(kind,id){try{await api('trash',{kind,id,restore:true});await load();toast('Восстановлено')}catch(e){toast(e.message,true)}}
function lessonText(l){return `${l.topic||'Конспект урока'}\n${l.date} · ${l.group?group(l.group):student(l.student)}\n\n${l.notes||'Конспект ещё не подготовлен.'}\n\nДомашнее задание\n${l.homework||'Не зафиксировано'}${l.next?`\n\nСледующий урок\n${l.next}`:''}\n\nЗапись\n${(l.youtube_urls||[]).join('\n')||l.youtube_url||l.recording||'Ссылка ещё не добавлена'}`}
function previewLesson(id){const l=find('lessons',id);$('preview').innerHTML=`<div class="top"><h2>Конспект для ученика</h2>${btn('Закрыть',"$('preview').close()",'secondary')}</div>${l.zoom_uuid&&l.report_provider!=='local'&&!l.reviewed?'<p class="notice">Автоматический отчёт ещё не проверен. Проверь задания и формулы перед отправкой.</p>':''}<pre style="white-space:pre-wrap;font:inherit;line-height:1.7">${esc(lessonText(l))}</pre>${btn('Скачать .txt',`downloadLesson(${arg(id)})`)}`;$('preview').showModal()}
function download(name,text,type){const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function downloadLesson(id){const l=find('lessons',id);download('Конспект-'+l.date+'.txt',lessonText(l),'text/plain;charset=utf-8')}
async function backup(){download('crm-backup.json',JSON.stringify(await api('export'),null,2),'application/json')}
async function zoomStatus(){const s=await api('zoom');automation=s;if($('zoomStatus'))$('zoomStatus').textContent=(s.configured?'Ключи сохранены. ':'Zoom не подключён. ')+(s.running?'Обработка… ':s.message)+' Последняя проверка: '+(s.last_run||'не выполнялась');if($('zoomForm')){const f=$('zoomForm');f.elements.enabled.value=s.enabled?'true':'false';f.elements.provider.value=s.provider;if(!f.elements.model.value)f.elements.model.value=s.model||''}}
async function syncZoom(){try{const r=await api('zoom/sync',{});if($('zoomStatus'))$('zoomStatus').textContent=r.message;await refreshAutomation()}catch(e){toast(e.message,true)}}
let refreshing=false;
async function refreshAutomation(force=false){if(refreshing||document.hidden)return;refreshing=true;try{const s=await api('zoom');const changed=JSON.stringify(automation)!==JSON.stringify(s);automation=s;
 const editing=document.querySelector('dialog[open]')||document.activeElement?.matches('input,select,textarea')||page==='settings';
 if(!db)await load();
 else if(!editing){const r=await api('data');const dataChanged=JSON.stringify(db)!==JSON.stringify(r.data);db=r.data;stats=r.metrics;const date=moscowDate(),newDay=date!==today;today=date;if(force||dataChanged||newDay||(changed&&['overview','lessons'].includes(page)))render()}
 if(page==='settings'&&$('zoomStatus'))$('zoomStatus').textContent=(s.enabled?'Автопроверка включена. ':'Автопроверка выключена. ')+(s.running?'Обрабатываем материалы… ':s.message);
}catch(e){if(force)toast(e.message,true)}finally{refreshing=false}}
load().then(()=>refreshAutomation(true)).catch(e=>toast(e.message,true));
setInterval(()=>refreshAutomation(),15000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshAutomation(true)});
