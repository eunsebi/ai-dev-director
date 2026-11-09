'use strict';
/* TRINITY stage HUD. Reads the same read-only endpoints as the workshop (/ops/activity, /status, /chat/messages,
   /ops/replay) and turns them into one model that both the DOM panels and the WebGL scene are painted from.
   Live and replay build the same model, so a replay shows exactly what the live view would have shown. */
(function(){
const $=id=>document.getElementById(id);
const AI={ANTHROPIC:{name:'Claude',rgb:[1,.54,.36],css:'#ff8a5c'},OPENAI:{name:'Codex',rgb:[.24,.95,.75],css:'#3df2c0'},GOOGLE:{name:'Antigravity',rgb:[.42,.65,1],css:'#6aa5ff'}};
const ROLES=['HEAD','BUILDER','REVIEWER'];
const ROLE_KO={HEAD:'HEAD · 지휘',BUILDER:'BUILDER · 구현',REVIEWER:'REVIEWER · 검수'};
const DEFAULT_ASSIGN={HEAD:'ANTHROPIC',BUILDER:'GOOGLE',REVIEWER:'OPENAI'};
const PIPE=['PLANNING','BUILDING','VERIFYING','REVIEWING','DECIDING'];
const PHASE={
  IDLE:{word:'대기',sub:'세 AI가 지시를 기다립니다',core:.18},
  BETWEEN:{word:'준비',sub:'다음 AI 호출을 준비합니다',core:.42},
  PLANNING:{word:'계획',sub:'HEAD가 할 일을 정합니다',core:.56},
  BUILDING:{word:'구현',sub:'BUILDER가 코드를 씁니다',core:.72},
  VERIFYING:{word:'테스트',sub:'Director가 직접 실행해 확인합니다',core:1},
  REVIEWING:{word:'검수',sub:'REVIEWER가 독립적으로 살핍니다',core:.72},
  DECIDING:{word:'판단',sub:'HEAD가 통과 여부를 결정합니다',core:.86}};
const STAGE_TEXT={CHAT:'요청을 읽고 있습니다',MISSION_PLANNING:'다음 단계를 계획하고 있습니다',PLANNING:'작업 지시서를 쓰고 있습니다',ORDER_READY:'작업 지시서를 쓰고 있습니다',
  BUILDING:'코드를 작성하고 있습니다',REVIEWING:'결과물을 검수하고 있습니다',DECIDING:'통과 여부를 판단하고 있습니다'};
const SHORT={CHAT:'요청 확인 중',MISSION_PLANNING:'다음 단계 계획 중',PLANNING:'지시서 작성 중',ORDER_READY:'지시서 작성 중',BUILDING:'코드 작성 중',REVIEWING:'검수 중',DECIDING:'판단 중'};
const STATE_KO={PASS:'통과',FAIL:'실패',TIMEOUT:'시간 초과',HUMAN_REQUIRED:'확인 필요',RECOVERED:'복구 완료'};
const STEP_CLASS={PASS:'ok',RECOVERED:'ok',FAIL:'bad',TIMEOUT:'bad',HUMAN_REQUIRED:'warn'};
const MISSION_BADGE={DONE:['완료','ok'],INCOMPLETE:['예산 소진','warn'],BLOCKED:['확인 필요','warn'],FAILED:['실패','bad']};
const PULSE={ok:[.35,1,.7],bad:[1,.32,.4],warn:[1,.78,.4],done:[1,.93,.75]};

let activity=null,status=null,win=null,winAuto=true,replay=null,lastModel=null,center=null,timerBase=null;
const seenSteps=new Map(),seenFeed={event:new Set(),call:new Set()};let firstPaint=true;

function el(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;}
function utc(iso){return iso?new Date(iso+(/Z|[+]\d\d:\d\d$/.test(iso)?'':'Z')).getTime():0;}
function ago(ms){const s=Math.max(0,Math.floor((Date.now()-ms)/1000));return s<60?s+'초 전':s<3600?Math.floor(s/60)+'분 전':s<86400?Math.floor(s/3600)+'시간 전':Math.floor(s/86400)+'일 전';}
function clock(sec){sec=Math.max(0,Math.floor(sec));const m=Math.floor(sec/60),s=sec%60;return (m>=60?Math.floor(m/60)+':'+String(m%60).padStart(2,'0'):String(m).padStart(2,'0'))+':'+String(s).padStart(2,'0');}
function span(sec){sec=Math.round(sec||0);if(sec<60)return sec+'초';const m=Math.floor(sec/60);return m<60?m+'분':Math.floor(m/60)+'시간 '+(m%60)+'분';}
function tok(n){n=Math.round(n||0);return n>=1e8?(n/1e8).toFixed(2)+'억':n>=1e4?(n/1e4).toFixed(1)+'만':n.toLocaleString('ko-KR');}
function countTo(node,value,fmt){
  const from=node._v||0;node._v=value;if(node._raf)cancelAnimationFrame(node._raf);
  if(from===value||Math.abs(value-from)<1){node.textContent=fmt(value);return;}
  const t0=performance.now(),step=now=>{const k=Math.min(1,(now-t0)/900),e=1-Math.pow(1-k,3);node.textContent=fmt(from+(value-from)*e);if(k<1)node._raf=requestAnimationFrame(step);};
  node._raf=requestAnimationFrame(step);
}
async function getJSON(url){const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw Error(url+' '+r.status);return r.json();}

// ------------------------------------------------------------------ model
const CLI={claude:'ANTHROPIC',antigravity:'GOOGLE',openai:'OPENAI'},CLI_OF={ANTHROPIC:'claude',GOOGLE:'antigravity',OPENAI:'openai'};
let roleSettings=null,roleToken=null;                 // what the NEXT task will use (data/role_settings.json via /ops/roles)
async function pollRoles(){try{const r=await getJSON('/ops/roles');roleSettings=r.roles;roleToken=r.csrf;repaint();}catch(e){/* keep the last known roles */}}
// fork 2026-11-07: subscription windows per AI (Claude 5h/weekly via a tiny probe, Codex from its session log).
// An older server without /ops/limits answers 404: the section simply stays hidden until the server is restarted.
let limits=null;
async function pollLimits(){try{limits=await getJSON('/ops/limits');if(limits.error)throw Error(limits.error);paintLimits();}catch(e){if(!limits||limits.error)$('limits').hidden=true;}}
function leftText(sec){if(sec<=0)return '곧 초기화';const d=Math.floor(sec/86400),h=Math.floor(sec%86400/3600),m=Math.floor(sec%3600/60);
  return d?d+'일 '+h+'시간':h?h+'시간 '+m+'분':m+'분';}
function clockText(ts){const t=new Date(ts*1000),p=n=>String(n).padStart(2,'0'),hm=p(t.getHours())+':'+p(t.getMinutes());
  return (ts*1000-Date.now()<86400000?'':(t.getMonth()+1)+'/'+t.getDate()+' ')+hm;}
function paintLimits(){
  const box=$('limit-rows'),prov=(limits&&limits.providers)||{};box.replaceChildren();
  for(const p of ['ANTHROPIC','OPENAI','GOOGLE']){const info=prov[p]||{},card=el('div','','limit-ai');card.style.setProperty('--c',AI[p].css);
    card.append(el('b',AI[p].name));
    if(info.checked_at)card.append(el('small',clockText(info.checked_at)+' 확인'+(info.probing?' · 갱신 중':'')));
    for(const w of info.windows||[]){const row=el('div','','limit-w'),pct=typeof w.used_percent==='number'?w.used_percent:null,
        left=w.resets_at?w.resets_at-Date.now()/1000:null;
      row.classList.toggle('hot',(pct!=null&&pct>=80)||w.status==='rejected');
      const bar=el('span','','bar'),fill=el('i');fill.style.width=(pct==null?0:Math.min(100,pct))+'%';bar.append(fill);
      row.title=w.label+' 한도'+(pct!=null?' '+pct+'% 사용':'')+(w.resets_at?' · '+new Date(w.resets_at*1000).toLocaleString('ko-KR')+' 초기화':'');
      row.append(el('span',w.label),bar,el('em',(pct!=null?Math.round(pct)+'% 사용':'—')+(left!=null?' · '+leftText(left)+' 후 초기화':'')));card.append(row);}
    let note='';
    if(p==='GOOGLE'&&!(info.windows||[]).length&&info.limit_hit_at)note='한도 초과 기록 · '+new Date(info.limit_hit_at).toLocaleString('ko-KR')+(info.limit_detail?' · '+info.limit_detail:'');
    else if(info.disabled&&!(info.windows||[]).length)note='확인 꺼짐 (config.yaml subscription_limits.'+(p==='GOOGLE'?'antigravity':'claude')+'_probe_minutes)';
    else if(info.error)note=((info.windows||[]).length?'최근 확인 실패, 이전 값 표시 · ':'')+info.error;
    else if(!(info.windows||[]).length)note=info.probing?'확인 중…':'정보 없음';
    if(note)card.append(el('div',note,'limit-note'));box.append(card);}
  // folded header: the most-used window per AI, e.g. "Claude 43% · Codex 7% · Antigravity 51%"
  // short names + AI colour so all three fit in the narrow panel; full names in the tooltip
  const SHORT_AI={ANTHROPIC:'Claude',OPENAI:'Codex',GOOGLE:'AGY'},sum=$('limits-summary'),full=[];sum.replaceChildren();
  for(const p of ['ANTHROPIC','OPENAI','GOOGLE']){const ws=((prov[p]||{}).windows||[]).filter(w=>typeof w.used_percent==='number');if(!ws.length)continue;
    const pct=Math.round(Math.max(...ws.map(w=>w.used_percent))),s=el('span','','ls');s.style.color=AI[p].css;
    s.append(el('b',SHORT_AI[p]+' ','nm'),el('b','● ','dot'),pct+'%');
    if(sum.childNodes.length)sum.append(el('span',' · ','sep'));sum.append(s);full.push(AI[p].name+' '+pct+'% 사용');}
  // own tooltip (data-tip + CSS): some Chromium-based browsers never show a native title inside a button
  $('limits-toggle').dataset.tip=full.join(' · ');
  $('limits').hidden=false;fitLimitsSummary();}
// fork 2026-11-07: fold/unfold the limits section; remembered per browser (falls back to open)
// names -> coloured dots when the narrow panel would cut the summary off (re-checked on resize)
function fitLimitsSummary(){const s=$('limits-summary');if(!s)return;s.classList.remove('compact');if(s.scrollWidth>s.clientWidth+1)s.classList.add('compact');}
function setLimitsFolded(f){$('limits').classList.toggle('folded',f);$('limits-toggle').setAttribute('aria-expanded',String(!f));
  try{localStorage.setItem('stage.limitsFolded',f?'1':'0');}catch(e){}}
function initLimitsFold(){let f=false;try{f=localStorage.getItem('stage.limitsFolded')==='1';}catch(e){}
  setLimitsFolded(f);$('limits-toggle').addEventListener('click',()=>{setLimitsFolded(!$('limits').classList.contains('folded'));fitLimitsSummary();});
  window.addEventListener('resize',fitLimitsSummary);}
function assignment(){
  if(roleSettings&&!isLiveNow())return {HEAD:CLI[roleSettings.head],BUILDER:CLI[roleSettings.builder],REVIEWER:CLI[roleSettings.reviewer]};
  const a={...DEFAULT_ASSIGN},prov=(status&&status.providers)||{};
  // fork 2026-11-07: one AI may hold several roles, so /status sends e.g. "HEAD, BUILDER"
  for(const [p,info] of Object.entries(prov)){if(!info||!AI[p])continue;
    for(const r of String(info.role||'').replace(/·/g,',').split(',').map(x=>x.trim().toUpperCase()))if(ROLES.includes(r))a[r]=p;}
  return a;
}
function isLiveNow(){const a=activity||{};return (a.working_now||[]).length>0||(a.missions||[]).some(m=>m.live);}
function openRoleMenu(label){
  const menu=$('role-menu'),slot=Number(label.dataset.slot),role=ROLES[slot];
  if(!roleSettings||replay){return;}
  if(!menu.hidden&&menu.dataset.slot===String(slot)){menu.hidden=true;return;}
  menu.dataset.slot=String(slot);menu.replaceChildren(el('p',ROLE_KO[role]+' 를 맡을 AI'+(isLiveNow()?' · 진행 중인 작업은 그대로, 다음 작업부터 적용':'')));
  const key=role.toLowerCase();
  for(const p of ['ANTHROPIC','GOOGLE','OPENAI']){
    // fork 2026-11-07: only this role changes; one AI may hold several roles (no forced swap)
    const b=el('button',AI[p].name),cur=roleSettings[key]===CLI_OF[p],others=ROLES.filter(r=>r!==role&&roleSettings[r.toLowerCase()]===CLI_OF[p]);
    b.type='button';b.setAttribute('role','menuitem');b.style.setProperty('--c',AI[p].css);b.classList.toggle('on',cur);
    b.append(el('small',cur?'현재':others.length?others.join('·')+'와 함께 맡음':'이 역할만 바뀜'));
    b.onclick=async()=>{if(cur){menu.hidden=true;return;}
      const next={...roleSettings};next[key]=CLI_OF[p];
      for(const x of menu.querySelectorAll('button'))x.disabled=true;
      try{const res=await fetch('/ops/roles',{method:'POST',headers:{'Content-Type':'application/json','X-Solomon-Bridge':roleToken},body:JSON.stringify(next)}),out=await res.json();
        if(!res.ok)throw Error(out.error||'저장 실패');roleSettings=out.roles;notice('역할을 바꿨습니다 · '+ROLE_KO[role]+' = '+AI[p].name+' (다음 작업부터 적용)');}
      catch(e){notice('역할 변경 실패 · '+e.message);}
      menu.hidden=true;repaint();};
    menu.append(b);}
  const r=label.getBoundingClientRect(),c=$('stage-center').getBoundingClientRect();
  menu.hidden=false;
  menu.style.transform='translate('+Math.max(4,Math.min(r.left-c.left,c.width-menu.offsetWidth-4)).toFixed(0)+'px,'+(r.bottom-c.top+6+menu.offsetHeight>c.height?Math.max(4,r.top-c.top-menu.offsetHeight-6):r.bottom-c.top+6).toFixed(0)+'px)';   // no room below: open above the label
}
// ------------------------------------------------------------------ project menu (2026-11-05 fork): pick from the
// registered projects (config `projects:`, git folders under solomon.allowed_roots, past mission targets) or browse.
async function selectProject(path){
  const res=await fetch('/project',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','Accept':'application/json'},body:new URLSearchParams({path}).toString()}).then(r=>r.json());
  return {path,ok:res.status==='ok',message:res.message||''};
}
function placeProjectMenu(){
  const menu=$('project-menu'),r=$('top-project').getBoundingClientRect();
  menu.style.top=(r.bottom+6).toFixed(0)+'px';
  menu.style.left=Math.max(8,Math.min(r.left,window.innerWidth-menu.offsetWidth-8)).toFixed(0)+'px';
}
async function openProjectMenu(){
  const menu=$('project-menu'),btn=$('top-project');
  if(btn.disabled||replay)return;
  if(!menu.hidden){menu.hidden=true;return;}
  $('role-menu').hidden=true;
  menu.replaceChildren(el('p','프로젝트 목록 불러오는 중…'));menu.hidden=false;placeProjectMenu();
  let data;
  try{data=await fetch('/ops/projects',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('HTTP '+r.status);return r.json();});}
  catch(e){menu.replaceChildren(el('p','목록을 불러오지 못했습니다 · '+e.message));}
  const busy=isLiveNow();
  const done=async job=>{
    for(const x of menu.querySelectorAll('button'))x.disabled=true;btn.disabled=true;
    try{const r=await job();if(r)notice(r.ok?'작업 폴더를 바꿨습니다 · '+r.path:'선택할 수 없는 폴더입니다 · '+r.message);}
    catch(e){notice('폴더를 바꾸지 못했습니다 · '+e.message+' (작업실 탭에서 경로를 직접 입력할 수 있습니다)');}
    finally{menu.hidden=true;btn.disabled=false;pollStatus();pollChat();}
  };
  if(data){
    const current=(data.current||'').toLowerCase(),rows=data.projects||[];
    menu.replaceChildren(el('p',rows.length?'작업할 프로젝트'+(busy?' · 진행 중인 미션은 원래 프로젝트에서 계속됩니다':''):'등록된 프로젝트가 없습니다 · config.yaml의 projects 또는 solomon.allowed_roots에 추가하세요'));
    for(const p of rows){
      const on=p.path.toLowerCase()===current,b=el('button',p.name+(on?'  · 현재':''));
      b.type='button';b.setAttribute('role','menuitem');b.classList.toggle('on',on);
      const lm=p.last_mission;
      b.append(el('small',p.path+(lm?' · 최근 미션 '+(lm.status||''):'')));
      b.title=p.path;
      b.onclick=()=>{if(on){menu.hidden=true;return;}done(()=>selectProject(p.path));};
      menu.append(b);
    }
  }
  const other=el('button','다른 폴더 선택…','other');other.type='button';other.setAttribute('role','menuitem');
  other.append(el('small','폴더 선택 창 열기 (목록에 없는 git 프로젝트)'));
  other.onclick=()=>done(()=>chooseProjectFolder());
  menu.append(other);
  placeProjectMenu();
}
function phaseOf(working,verifying,live){
  const w=working.find(x=>x.role==='BUILDER')||working.find(x=>x.role==='REVIEWER')||working[0];
  if(w){
    if(w.role==='BUILDER')return {phase:'BUILDING',w};
    if(w.role==='REVIEWER')return {phase:'REVIEWING',w};
    return {phase:w.stage==='DECIDING'?'DECIDING':'PLANNING',w};
  }
  return {phase:verifying?'VERIFYING':live?'BETWEEN':'IDLE',w:null};
}
function liveModel(){
  const a=activity||{missions:[],working_now:[],usage:{},recent_calls:[],events:[]};
  const focus=(a.missions||[]).find(m=>m.mission_id===a.focus_mission_id)||null;
  const working=(a.working_now||[]).map(w=>({provider:w.provider,role:w.role,stage:w.stage,elapsed:w.elapsed_sec,at:Date.now()-w.elapsed_sec*1000}));
  const step=focus&&focus.steps.length?focus.steps[focus.steps.length-1]:null,ev=(a.events||[])[0];
  const verifying=!!(focus&&focus.live&&step&&ev&&ev.run_id===step.run_id&&ev.to_state==='VERIFYING'&&Date.now()-utc(ev.at)<30*60e3);
  const usage=a.usage||{};
  if(winAuto)win=usage.mission&&focus&&(focus.live||!(usage.today&&usage.today.total.calls))?'mission':(usage.today&&usage.today.total.calls?'today':(usage.mission?'mission':'all'));
  if(!usage[win])win=usage.today?'today':'all';
  const today=(usage.today&&usage.today.by_provider)||{},stats={};
  for(const p of Object.keys(AI))stats[p]=today[p]?'오늘 '+today[p].calls+'회 · '+(today[p].exact_tokens<today[p].tokens?'~':'')+tok(today[p].tokens)+' 토큰':'오늘 호출 없음';
  return {mode:'live',working,assign:assignment(),...phaseOf(working,verifying,!!(focus&&focus.live)),
    mission:focus&&{goal:focus.goal,project:focus.project,status:focus.status,live:focus.live,max:Math.max(focus.max_steps||0,focus.steps.length),
      steps:focus.steps.map(s=>({index:s.index,state:s.state,tests:s.tests})),passed:focus.passed_steps,updated:utc(focus.updated_at),id:focus.mission_id,
      deliverable:focus.deliverable,applied:focus.applied,applyState:focus.apply_state,source:focus.source,profile:focus.profile,delivery:focus.delivery},
    usage:usage[win]||{by_role:{},by_provider:{},total:{calls:0,seconds:0,tokens:0,exact_tokens:0}},windows:{mission:!!usage.mission,today:!!usage.today,all:!!usage.all},
    calls:(a.recent_calls||[]).slice(0,30).map(c=>({key:c.at+c.role+c.provider,role:c.role,provider:c.provider,seconds:c.seconds,tokens:c.tokens,exact:c.exact,status:c.status,at:utc(c.at),
      reason:c.reason,detail:c.reason_detail,hint:c.reason_hint,run:c.run_id})),
    events:(a.events||[]).slice(0,25).map(e=>({key:e.at+e.run_id+e.to_state,state:e.to_state,label:e.state_ko||e.to_state,sub:e.project,at:utc(e.at),detail:e.details,run:e.run_id})),
    stats,project:(status&&status.project_name)||(focus&&focus.project)||'—'};
}

// ------------------------------------------------------------------ painting
function actorColor(m){return m.w?AI[m.w.provider]?.css||'#fff':m.phase==='VERIFYING'?'#b9ffe2':'#9fb4d8';}
function sentence(m){
  if(m.w){const name=(AI[m.w.provider]||{name:m.w.provider}).name;return [name,'가 '+(STAGE_TEXT[m.w.stage]||(m.w.role==='BUILDER'?STAGE_TEXT.BUILDING:m.w.role==='REVIEWER'?STAGE_TEXT.REVIEWING:'생각하고 있습니다'))];}
  if(m.phase==='VERIFYING')return ['Director','가 테스트를 실행하고 있습니다'];
  if(m.phase==='BETWEEN')return ['','다음 AI 호출을 준비하고 있습니다'];
  return ['','세 AI가 지시를 기다리고 있습니다'];
}
function setSentence(node,m){const [who,rest]=sentence(m);node.replaceChildren();if(who)node.append(el('em',who));node.append(rest);}
function paintNow(m){
  document.body.dataset.phase=m.phase;
  document.documentElement.style.setProperty('--actor',actorColor(m));
  setSentence($('now-headline'),m);setSentence($('caption'),m);
  const ms=m.mission,step=ms&&ms.steps.length?ms.steps[ms.steps.length-1].index:0;
  $('now-sub').textContent=m.phase==='IDLE'?(ms?'마지막 미션 '+(MISSION_BADGE[ms.status]||[ms.status])[0]+' · '+ago(ms.updated):'아래 입력창에 지시하면 세 AI가 일을 시작합니다.')
    :(ms&&(ms.live||m.mode==='replay')?'미션 '+step+'/'+ms.max+'단계 · '+ms.project:PHASE[m.phase].sub);
  $('now-stage').textContent=m.w?ROLE_KO[m.w.role]||m.w.role:PHASE[m.phase].word;
  timerBase=m.w?{at:m.w.at,fixed:m.mode==='replay'?m.w.elapsed:null}:null;tickTimer();
  $('phase-word').textContent=PHASE[m.phase].word;$('phase-sub').textContent=PHASE[m.phase].sub;
  const at=PIPE.indexOf(m.phase);
  for(const li of $('pipeline').children){const i=PIPE.indexOf(li.dataset.stage);li.classList.toggle('on',i===at);li.classList.toggle('done',at>i&&at>=0);
    const role=li.dataset.stage==='BUILDING'?'BUILDER':li.dataset.stage==='REVIEWING'?'REVIEWER':li.dataset.stage==='VERIFYING'?null:'HEAD';
    li.style.setProperty('--c',i===at?actorColor(m):role?AI[m.assign[role]].css:'#b9ffe2');}
  const chip=$('top-live');chip.dataset.mode=m.mode==='replay'?'replay':(m.working.length||(ms&&ms.live))?'live':'idle';
  chip.lastElementChild.textContent=m.mode==='replay'?'REPLAY':chip.dataset.mode==='live'?'LIVE':'STANDBY';
  $('top-project').textContent='프로젝트 · '+((ms&&(ms.live||m.mode==='replay')&&ms.project)||m.project);
}
function tickTimer(){
  for(const label of document.querySelectorAll('.slot-label'))if(label._work&&label._work.fixed==null)label.querySelector('.slot-status').textContent=label._work.text+' · '+clock((Date.now()-label._work.at)/1000);
  const node=$('now-elapsed');
  if(!timerBase){node.textContent='--:--';return;}
  node.textContent=clock(timerBase.fixed!=null?timerBase.fixed:(Date.now()-timerBase.at)/1000);
}
function paintMission(m){
  const ms=m.mission,badge=$('mission-badge'),steps=$('mission-steps');
  if(!ms){badge.textContent='—';badge.className='badge';$('mission-goal').textContent='아직 미션 기록이 없습니다. 지시를 보내면 여기에 단계별 진행이 표시됩니다.';steps.replaceChildren();$('mission-meta').textContent='';return;}
  const running=ms.status==='RUNNING',b=running?(ms.live?['진행 중','run']:['중단됨 · 재개 대기','warn']):(MISSION_BADGE[ms.status]||[ms.status,'']);
  badge.textContent=b[0];badge.className='badge '+b[1];$('mission-goal').textContent=ms.goal;
  while(steps.children.length>ms.max)steps.lastChild.remove();
  while(steps.children.length<ms.max)steps.append(el('span',String(steps.children.length+1)));
  for(let i=0;i<ms.max;i++){const s=ms.steps.find(x=>x.index===i+1),chip=steps.children[i];
    chip.className=s?(STEP_CLASS[s.state]||'run'):'todo';chip.title=s?(i+1)+'단계 · '+(STATE_KO[s.state]||'진행 중'):'남은 단계 예산';}
  const last=[...ms.steps].reverse().find(s=>s.tests);
  $('mission-meta').textContent=ms.passed+'개 단계 통과 · '+ms.steps.length+'/'+ms.max+'단계 사용'+(last?' · 테스트 '+last.tests.passed+'/'+last.tests.total:'');
  paintDelivery(m);
  if(m.mode!=='live')return;
  for(const s of ms.steps){const key=ms.id+':'+s.index,prev=seenSteps.get(key);seenSteps.set(key,s.state);
    if(!firstPaint&&prev!==s.state&&STEP_CLASS[s.state])Trinity.pulse(PULSE[STEP_CLASS[s.state]]);}
  const mk=ms.id+':status',prev=seenSteps.get(mk);seenSteps.set(mk,ms.status);
  if(!firstPaint&&prev&&prev!==ms.status&&ms.status==='DONE')Trinity.pulse(PULSE.done);
}
const APPLY_KO={applied:['원본에 적용됨','ok'],blocked:['적용 보류 · 충돌/조건','warn'],pending:['적용 대기','warn'],discarded:['폐기됨',''],rolled_back:['적용 실패 · 복구됨','bad'],busy:['적용 진행 중','run'],error:['적용 오류','bad']};
let deliveryBusy=false;
async function decideMission(id,action,extra){
  if(deliveryBusy)return;deliveryBusy=true;
  try{const body=new URLSearchParams({mission_id:id,action,...(extra||{})});
    const res=await fetch('/ops/mission/decide',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'},body});
    const out=await res.json().catch(()=>({}));
    const msg={applied:'원본에 적용했습니다 · '+(out.files||[]).length+'개 파일',blocked:'적용할 수 없습니다 · '+((out.blocks||[]).join('; ')||'').slice(0,160),discarded:'결과물을 폐기했습니다',resumed:'미션을 이어서 진행합니다 (단계 예산 '+out.max_steps+')',busy:out.message||'다른 작업이 진행 중입니다',
      pipeline_started:'적용 절차를 시작했습니다 · '+(out.steps||[]).join(' → '),continued:'확인했습니다 · 다음 단계를 진행합니다',rework:'실패로 기록했습니다 · 원본을 되돌리고 HEAD에 재작업을 맡깁니다'}[out.status]||(out.status+' '+(out.error||out.reason||''));
    notice(msg);Trinity.pulse(out.status==='applied'?PULSE.done:out.status==='blocked'?PULSE.warn:PULSE.ok);}
  catch(e){notice('요청 실패 · '+e.message);}
  finally{deliveryBusy=false;pollActivity();}
}
function paintDelivery(m){
  let box=$('mission-delivery');
  if(!box){box=el('div','','delivery');box.id='mission-delivery';$('mission-card').append(box);}
  box.replaceChildren();const ms=m.mission;
  if(!ms||m.mode!=='live'||!ms.deliverable||ms.status==='RUNNING'){return;}
  const dv=ms.delivery,pipeBusy=!!dv&&(dv.status==='RUNNING'||dv.status==='AWAITING_USER');
  const state=pipeBusy?'busy':(ms.applied&&ms.applied.status)||ms.applyState||'pending',[label,cls]=APPLY_KO[state]||[state,''];
  const head=el('div','','delivery-head');head.append(el('span',(ms.source==='solomon'?'솔로몬 지시 · ':'')+'결과물','kicker'),el('span',label,'badge '+cls));box.append(head);
  if(ms.applied&&ms.applied.files)box.append(el('small',ms.applied.files.slice(0,6).join(', ')+(ms.applied.files.length>6?' 외 '+(ms.applied.files.length-6)+'개':''),'ai-sub'));
  if(state==='blocked'&&ms.applied&&ms.applied.blocks)box.append(el('small',ms.applied.blocks.join('; ').slice(0,220),'ai-sub'));
  if(dv)paintPipeline(box,ms,dv);
  if(!pipeBusy&&state!=='applied'&&state!=='discarded'){
    const row=el('div','','delivery-actions');
    const apply=el('button','원본에 적용');apply.type='button';apply.onclick=()=>decideMission(ms.id,'apply',{on_conflict:state==='blocked'?'skip':'block'});
    const more=el('button','이어서 진행');more.type='button';more.onclick=()=>decideMission(ms.id,'extend');
    const drop=el('button','폐기');drop.type='button';drop.className='ghost';drop.onclick=()=>{if(confirm('이 미션의 작업본을 폐기할까요? 기록은 남습니다.'))decideMission(ms.id,'discard');};
    row.append(apply,more,drop);box.append(row);
    if(state==='blocked')box.append(el('small','"원본에 적용"을 다시 누르면 충돌 파일만 건너뛰고 나머지를 적용합니다.','ai-sub'));
  }
}
// fork 2026-11-07: delivery pipeline (apply → checks → manual checks such as ⌘U → commit → cleanup → record)
const PIPE_MARK={pending:'○',running:'◐',ok:'✓',warn:'⚠',failed:'✕',waiting:'⏳',skipped:'–'};
const PIPE_STATUS={RUNNING:'적용 절차 진행 중',AWAITING_USER:'사용자 확인 대기',DONE:'적용 절차 완료',DONE_WITH_WARNINGS:'완료 · 경고 있음',REWORK:'확인 실패 · HEAD 재작업',BLOCKED:'적용 보류',ERROR:'적용 절차 오류'};
function paintPipeline(box,ms,dv){
  const wrap=el('div','','pipe');wrap.append(el('small',(PIPE_STATUS[dv.status]||dv.status)+(dv.commit?' · 커밋 '+dv.commit.slice(0,7):''),'pipe-title'));
  const list=el('ol','','pipe-steps');
  for(const s of dv.steps||[]){const li=el('li','','pipe-step '+s.status);li.append(el('b',PIPE_MARK[s.status]||'·'),el('span',s.name));
    if(s.detail)li.title=s.detail;
    // fork 2026-11-09: failure reason and ⚠ reports (e.g. String Catalog) shown inline, skip reasons in one line
    if(s.detail&&(s.status==='failed'||s.status==='warn'))li.append(el('small',s.detail.slice(0,s.status==='failed'?400:280),'pipe-detail'));
    else if(s.detail&&s.status==='skipped')li.append(el('small',s.detail.split('\n')[0].slice(0,80),'pipe-detail'));
    list.append(li);}
  wrap.append(list);
  if(dv.status==='AWAITING_USER'&&dv.waiting){const w=dv.waiting,ask=el('div','','pipe-ask');
    ask.append(el('p',w.name+'을(를) 실행한 뒤 결과를 눌러 주세요.'+(w.instructions?' '+w.instructions:'')));
    const ok=el('button','통과');ok.type='button';ok.onclick=()=>decideMission(ms.id,'manual_pass');
    const bad=el('button','실패');bad.type='button';bad.className='ghost';
    bad.onclick=()=>{const note=prompt('실패 내용을 적어 주세요(실패한 테스트 이름, 오류 메시지). HEAD 재작업에 그대로 전달됩니다.','');
      if(note!==null)decideMission(ms.id,'manual_fail',{reason:note});};
    const row=el('div','','delivery-actions');row.append(ok,bad);ask.append(row);wrap.append(ask);}
  if(dv.error&&dv.status!=='AWAITING_USER')wrap.append(el('small',dv.error.slice(0,240),'pipe-detail'));
  if((dv.warnings||[]).length)wrap.append(el('small','경고: '+dv.warnings.join('; ').slice(0,300),'pipe-detail'));
  box.append(wrap);
}
function paintWork(m){
  for(const b of $('win-tabs').children){b.classList.toggle('on',b.dataset.win===win&&m.mode==='live');b.disabled=m.mode!=='live'||!m.windows[b.dataset.win];}
  const u=m.usage,total=u.total||{},approx=(total.exact_tokens||0)<(total.tokens||0);
  countTo($('total-tokens'),total.tokens||0,v=>(approx?'~':'')+tok(v));
  $('total-sub').textContent='호출 '+(total.calls||0)+'회 · CLI '+span(total.seconds)+(total.failed?' · 실패 '+total.failed:'');
  const sum=Object.keys(AI).reduce((a,p)=>a+((u.by_provider[p]||{}).tokens||0),0)||1;
  for(const bar of $('share-bar').children)bar.style.width=(((u.by_provider[bar.dataset.p]||{}).tokens||0)/sum*100)+'%';
  const rows=$('role-rows');
  if(!rows.children.length)for(const role of ROLES){const row=el('div','','role-row');row.dataset.role=role;
    const top=el('div','','role-top');top.append(el('b',ROLE_KO[role]),el('strong','—'));
    const nums=el('div','','role-nums');for(const k of ['호출','CLI 시간','토큰']){const d=el('div');d.append(el('b','0'),el('span',k));nums.append(d);}
    const bar=el('div','','role-bar');bar.append(el('i'));row.append(top,nums,bar);rows.append(row);}
  for(const row of rows.children){const role=row.dataset.role,b=u.by_role[role],busy=m.working.find(w=>w.role===role);
    const provs=b?Object.entries(b.providers||{}).sort((x,y)=>y[1]-x[1]):[],main=busy?busy.provider:provs.length?provs[0][0]:m.assign[role];
    row.style.setProperty('--c',(AI[main]||{}).css||'#8fa0bd');row.classList.toggle('busy',!!busy);
    row.querySelector('strong').textContent=provs.length?provs.map(([p,n])=>((AI[p]||{name:p}).name)+(provs.length>1?' '+n:'')).join(' · '):(AI[m.assign[role]]||{}).name;
    const n=row.querySelectorAll('.role-nums b');
    countTo(n[0],b?b.calls:0,v=>String(Math.round(v)));n[1].textContent=b?span(b.seconds):'0초';
    countTo(n[2],b?b.tokens:0,v=>(b&&b.exact_tokens<b.tokens?'~':'')+tok(v));
    row.querySelector('.role-bar i').style.width=((b?b.tokens:0)/(total.tokens||1)*100)+'%';}
}
// fork 2026-11-07: the lists scroll, and a row opens to show its details (reason, hint, run) - kept open across repaints
const openFeed=new Set();
function paintFeed(list,items,seen,render,more){
  const frag=[],top=list.scrollTop;
  for(const it of items){const li=el('li');render(li,it);if(!seen.has(it.key)){seen.add(it.key);if(!firstPaint)li.classList.add('new');}
    const extra=more&&more(it);
    if(extra){li.classList.add('has-more');li.tabIndex=0;li.title='눌러서 자세히 보기';
      if(openFeed.has(it.key)){li.classList.add('open');li.append(el('p',extra,'more'));}
      const toggle=()=>{openFeed.has(it.key)?openFeed.delete(it.key):openFeed.add(it.key);repaint();};
      li.onclick=toggle;li.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();toggle();}};}
    frag.push(li);}
  list.replaceChildren(...frag);
  if(!items.length)list.append(el('li','아직 기록이 없습니다.'));
  list.scrollTop=top;
}
function stateColor(m,state){
  if(STEP_CLASS[state])return {ok:'#5ef2b0',bad:'#ff6b7a',warn:'#ffc46b'}[STEP_CLASS[state]];
  if(state==='BUILDING')return AI[m.assign.BUILDER].css;if(state==='REVIEWING')return AI[m.assign.REVIEWER].css;
  if(state==='VERIFYING')return '#b9ffe2';return AI[m.assign.HEAD].css;
}
function paintFeeds(m){
  paintFeed($('event-feed'),m.events,seenFeed.event,(li,e)=>{li.style.setProperty('--dot',e.color||stateColor(m,e.state));li.append(el('b',e.label),el('span',e.sub||''),el('time',e.when||ago(e.at)));},
    e=>e.detail?e.detail+(e.run?'\n'+e.run:''):'');
  paintFeed($('call-feed'),m.calls,seenFeed.call,(li,c)=>{const bad=c.status&&c.status!=='OK';li.style.setProperty('--dot',bad?'#ff6b7a':(AI[c.provider]||{}).css||'#8fa0bd');
    li.append(el('b',(AI[c.provider]||{name:c.provider}).name),el('span',bad?(c.role||'?')+' · '+(c.reason||c.status):(c.role||'?')+' · '+span(c.seconds)+(c.tokens?' · '+(c.exact?'':'~')+tok(c.tokens)+' 토큰':'')),el('time',c.when||ago(c.at)));
    if(bad)li.classList.add('bad');},
    c=>c.status&&c.status!=='OK'?[c.reason||c.status,c.detail,c.hint&&('→ '+c.hint),span(c.seconds)+(c.run?' · '+c.run:'')].filter(Boolean).join('\n'):'');
}

// ------------------------------------------------------------------ 실패 원인 panel + alerts (fork 2026-11-07)
let failSeen=null;
function failData(){return (activity&&activity.failures)||{items:[],by_reason:[],steps:[],total:0};}
function paintFailButton(){
  const f=failData(),n=(f.total||0)+(f.steps||[]).length,b=$('fail-open');
  b.textContent=n?'실패 원인 '+n:'실패 원인';b.classList.toggle('bad',n>0);
}
function openFailPanel(){
  const f=failData(),box=$('fail-body');box.replaceChildren();
  const head=el('div','','fail-sum');
  if(!(f.by_reason||[]).length&&!(f.steps||[]).length)head.append(el('p','최근 '+(f.since_hours||72)+'시간 동안 실패가 없습니다.'));
  for(const r of f.by_reason||[]){const c=el('div','','fail-chip');c.append(el('b',r.label),el('span',r.count+'건'),el('small',r.hint||''));head.append(c);}
  box.append(head);
  if((f.steps||[]).length){box.append(el('h4','작업 단계 실패'));const ol=el('ol','','fail-list');
    for(const s of f.steps){const li=el('li');li.append(el('b',s.reason_label||s.state_ko),el('time',ago(utc(s.at))+' · '+(s.project||'')),
      el('p',s.reason_hint||'','hint'),el('pre',s.reason_detail_ko||s.details||''));
      // fork 2026-11-09: records from before the Korean rule keep the AI's English sentences
      if(s.reason_english)li.append(el('small','AI가 영어로 남긴 기록입니다(2026-11-09 이후 판정은 한국어로 씁니다).','hint'));
      ol.append(li);}box.append(ol);}
  if((f.items||[]).length){box.append(el('h4','AI 호출 실패'));const ol=el('ol','','fail-list');
    for(const c of f.items){const li=el('li');li.style.setProperty('--c',(AI[c.provider]||{}).css||'#8fa0bd');
      li.append(el('b',(c.provider_name||c.provider)+' '+(c.role||'')+' · '+c.reason),el('time',ago(utc(c.at))+' · '+span(c.seconds)),
        el('p',c.reason_hint||'','hint'));if(c.reason_detail)li.append(el('pre',c.reason_detail));ol.append(li);}box.append(ol);}
  const perm=$('fail-notify');
  perm.hidden=!('Notification' in window)||Notification.permission==='granted';
  $('fail-panel').hidden=false;
}
function failAlert(text){
  const t=$('fail-toast');$('fail-toast-text').textContent=text;t.hidden=false;
  try{if('Notification' in window&&Notification.permission==='granted'&&document.visibilityState!=='visible')new Notification('AI DEV DIRECTOR · 실패',{body:text,tag:'director-fail'});}catch(e){/* optional */}
}
function checkNewFailures(){
  const f=failData(),ids=new Set([...(f.items||[]).map(c=>'c'+c.id),...(f.steps||[]).map(s=>'s'+s.at+s.run_id)]);
  if(failSeen===null){failSeen=ids;paintFailButton();return;}
  const fresh=[...(f.steps||[]).filter(s=>!failSeen.has('s'+s.at+s.run_id)).map(s=>s.reason_label||s.state_ko),
               ...(f.items||[]).filter(c=>!failSeen.has('c'+c.id)&&c.reason_code!=='INTERRUPTED').map(c=>(c.provider_name||c.provider)+' '+(c.role||'')+' · '+c.reason)];
  failSeen=ids;paintFailButton();
  if(fresh.length)failAlert(fresh[0]+(fresh.length>1?' 외 '+(fresh.length-1)+'건':''));
  if(!$('fail-panel').hidden)openFailPanel();
}
function paintScene(m){
  const act=[.1,.1,.1],edge=[0,0,0],colors=[];
  ROLES.forEach((role,i)=>{const w=m.working.find(x=>x.role===role),p=w?w.provider:m.assign[role];if(w)act[i]=1;colors.push((AI[p]||AI[m.assign[role]]).rgb);
    const label=document.querySelector('.slot-label[data-slot="'+i+'"]'),acting=w&&w.provider!==m.assign[role];
    label.style.setProperty('--c',(AI[p]||{}).css||'#fff');label.classList.toggle('busy',!!w);
    label.querySelector('.slot-name').textContent=((AI[p]||{name:p}).name)+(acting?' (대행)':'');
    label._work=w?{text:SHORT[w.stage]||(role==='BUILDER'?SHORT.BUILDING:role==='REVIEWER'?SHORT.REVIEWING:'작업 중'),at:w.at,fixed:m.mode==='replay'?w.elapsed:null}:null;
    label.querySelector('.slot-status').textContent=label._work?label._work.text+' · '+clock(label._work.fixed!=null?label._work.fixed:(Date.now()-label._work.at)/1000):'대기';
    label.querySelector('.slot-stat').textContent=m.stats[p]||'';});
  if(m.phase==='BUILDING')edge[0]=1;if(m.phase==='REVIEWING')edge[1]=1;if(m.phase==='DECIDING')edge[2]=1;
  if(m.phase==='VERIFYING')act[1]=.5;
  Trinity.set({act,edge,colors,core:PHASE[m.phase].core,verify:m.phase==='VERIFYING'?1:0});
}
let lastPhase=null;
function stinger(m){
  const key=m.phase+(m.w?m.w.provider:'');
  if(key===lastPhase)return;const first=lastPhase===null;lastPhase=key;
  if(first||m.phase==='IDLE'||m.phase==='BETWEEN')return;
  const s=$('stinger');s.replaceChildren(el('small',m.w?(AI[m.w.provider]||{name:''}).name+' · '+(m.w.role||''):'DIRECTOR'),el('strong',PHASE[m.phase].word));
  s.style.setProperty('--c',actorColor(m));s.classList.remove('show');void s.offsetWidth;s.classList.add('show');
}
function paint(m){lastModel=m;stinger(m);paintNow(m);paintMission(m);paintWork(m);paintFeeds(m);paintScene(m);firstPaint=false;document.body.classList.add('ready');}
const params=new URLSearchParams(location.search),attract=params.get('attract')==='1';let idleSince=null;
function isLive(){const a=activity||{};return (a.working_now||[]).length>0||(a.missions||[]).some(m=>m.live);}
function repaint(){
  if(attract&&activity){
    if(replay&&isLive())stopReplay();
    else if(!replay&&!isLive()){idleSince=idleSince||Date.now();if(Date.now()-idleSince>12000){idleSince=null;startReplay();}}
    else if(isLive())idleSince=null;
  }
  if(!replay)paint(liveModel());
}

// ------------------------------------------------------------------ scene-pinned labels
function onFrame(f){
  center=$('stage-center').getBoundingClientRect();
  const W=window.innerWidth,H=window.innerHeight,foot=$('stage-foot').offsetHeight+10;
  Trinity.focus({x:center.left/W,y:center.top/H,w:center.width/W,h:Math.max(.2,(center.height-foot)/H)});
  const u=f.unit,labels=document.querySelectorAll('.slot-label');
  $('stage-center').classList.toggle('compact',u<185);              // small scene: labels drop their stat line
  labels.forEach((n,i)=>{const [x,y]=f.slots[i],w=n.offsetWidth,h=n.offsetHeight;
    let lx=i===0?x+u*.27:x-w/2,ly=i===0?y-h+4:y+u*.27;                 // HEAD: above-right; BUILDER/REVIEWER: centred below
    lx=Math.min(Math.max(lx-center.left,2),center.width-w-2);ly=Math.max(ly-center.top,2);
    n.style.transform='translate('+lx.toFixed(1)+'px,'+ly.toFixed(1)+'px)';});
}

// ------------------------------------------------------------------ live polling
async function pollActivity(){try{activity=await getJSON('/ops/activity');if(activity.error)throw Error(activity.error);$('shell-error').hidden=true;repaint();checkNewFailures();}
  catch(e){const s=$('shell-error');s.hidden=false;s.textContent='작업 현황 연결을 확인하고 있습니다 · '+e.message;}}
async function pollStatus(){try{status=await getJSON('/status');
  const pending=status.pending_result_count||0,need=status.human_required_count||0,badge=$('pending-badge');
  badge.hidden=!pending;badge.textContent=pending>99?'99+':String(pending);
  $('attention').hidden=!need||document.body.dataset.view==='workshop';$('attention-text').textContent='확인이 필요한 작업이 '+need+'건 있습니다.';
  repaint();}catch(e){/* the activity poll reports connection trouble */}}

// ------------------------------------------------------------------ replay (real ledger of a finished mission)
async function startReplay(){
  if(replay){if(attract)return;return stopReplay();}
  let data;try{data=await getJSON('/ops/replay');}catch(e){data={calls:[]};}
  const calls=(data.calls||[]).filter(c=>ROLES.includes(c.role)&&AI[c.provider]).map(c=>({...c,t0:utc(c.started_at)/1000,dur:Math.max(1,c.seconds||1)}));
  if(!calls.length){const s=$('shell-error');s.hidden=false;s.textContent='다시 볼 미션 기록이 아직 없습니다.';setTimeout(()=>{s.hidden=true;},3500);return;}
  let v=0,prevEnd=calls[0].t0,real=0;const gaps=[];
  calls.forEach((c,i)=>{const gap=Math.max(0,c.t0-prevEnd),vg=Math.min(gap,40);
    if(vg>6&&i&&calls[i-1].role==='BUILDER')gaps.push([v,v+vg]);                       // Director ran the tests between build and review
    v+=vg;c.v0=v;c.v1=v+c.dur;v=c.v1;real+=gap+c.dur;prevEnd=Math.max(prevEnd,c.t0+c.dur);});
  const stepOf=new Map((data.steps||[]).map(s=>[s.run_id,s])),lastCall=new Map();
  calls.forEach(c=>{if(stepOf.has(c.run_id))lastCall.set(c.run_id,c);});
  replay={data,calls,gaps,total:v,real,speed:v/72,started:performance.now(),stepOf,lastCall,done:new Set(),events:[],finished:false};
  $('replay-bar').hidden=false;document.body.classList.add('replaying');
  $('replay-text').textContent=data.project+' 미션 · 실제 기록 '+span(real)+' → '+Math.round(replay.speed)+'배속';
  $('btn-replay').classList.add('on');seenFeed.event.clear();seenFeed.call.clear();
  replay.timer=setInterval(replayTick,120);replayTick();
}
function stopReplay(){
  if(!replay)return;clearInterval(replay.timer);replay=null;$('replay-bar').hidden=true;document.body.classList.remove('replaying');$('btn-replay').classList.remove('on');
  seenFeed.event.clear();seenFeed.call.clear();firstPaint=true;repaint();
}
function replayTick(){
  const r=replay;if(!r)return;
  const vt=Math.min(r.total,(performance.now()-r.started)/1000*r.speed);
  $('replay-fill').style.width=(vt/r.total*100)+'%';
  const working=r.calls.filter(c=>c.v0<=vt&&vt<c.v1).map(c=>({provider:c.provider,role:c.role,stage:c.stage,elapsed:vt-c.v0,at:0}));
  const done=r.calls.filter(c=>c.v1<=vt),usage={by_role:{},by_provider:{},total:{calls:0,seconds:0,tokens:0,exact_tokens:0,failed:0}};
  for(const c of done){
    for(const [group,key] of [[usage.by_role,c.role],[usage.by_provider,c.provider]]){const b=group[key]||(group[key]={calls:0,seconds:0,tokens:0,exact_tokens:0,failed:0,providers:{}});
      b.calls++;b.seconds+=c.dur;b.tokens+=c.tokens||0;b.exact_tokens+=c.exact?(c.tokens||0):0;b.failed+=c.status!=='OK'?1:0;b.providers[c.provider]=(b.providers[c.provider]||0)+1;}
    const t=usage.total;t.calls++;t.seconds+=c.dur;t.tokens+=c.tokens||0;t.exact_tokens+=c.exact?(c.tokens||0):0;t.failed+=c.status!=='OK'?1:0;
    if(!r.done.has(c)){r.done.add(c);
      const s=r.stepOf.get(c.run_id);
      if(s&&r.lastCall.get(c.run_id)===c){r.events.unshift({key:'step'+s.index,state:s.state,label:s.index+'단계 '+(STATE_KO[s.state]||s.state||'종료'),sub:s.tests?'테스트 '+s.tests.passed+'/'+s.tests.total:'',when:'기록'});
        if(STEP_CLASS[s.state])Trinity.pulse(PULSE[STEP_CLASS[s.state]]);}}
  }
  for(const c of r.calls)if(c.v0<=vt&&!c.announced){c.announced=true;r.events.unshift({key:'c'+c.v0,color:AI[c.provider].css,label:(ROLE_KO[c.role]||c.role).split(' · ')[1]+' 시작',sub:AI[c.provider].name,when:'기록'});}
  const steps=(r.data.steps||[]).filter(s=>{const first=r.calls.find(c=>c.run_id===s.run_id);return first&&first.v0<=vt;})
    .map(s=>{const last=r.lastCall.get(s.run_id);return {index:s.index,state:last&&last.v1<=vt?s.state:null,tests:last&&last.v1<=vt?s.tests:null};});
  const verifying=!working.length&&r.gaps.some(g=>g[0]<=vt&&vt<g[1]),stats={};
  for(const p of Object.keys(AI)){const b=usage.by_provider[p];stats[p]=b?b.calls+'회 · '+tok(b.tokens)+' 토큰':'';}
  const finished=vt>=r.total;
  paint({mode:'replay',working,assign:assignment(),...phaseOf(working,verifying,!finished),
    mission:{goal:r.data.goal,project:r.data.project,status:finished?r.data.status:'RUNNING',live:!finished,max:Math.max(r.data.max_steps||0,(r.data.steps||[]).length),steps,
      passed:steps.filter(s=>s.state==='PASS').length,updated:Date.now(),id:'replay'},
    usage,windows:{},calls:done.slice(-6).reverse().map(c=>({key:'d'+c.v0,role:c.role,provider:c.provider,seconds:c.dur,tokens:c.tokens,exact:c.exact,status:c.status,when:'기록'})),
    events:r.events.slice(0,7),stats,project:r.data.project});
  if(finished&&!r.finished){r.finished=true;if(r.data.status==='DONE')Trinity.pulse(PULSE.done);clearInterval(r.timer);setTimeout(()=>{if(replay===r)stopReplay();},6000);}
}

// ------------------------------------------------------------------ chat with HEAD
let lastMsg=0,sending=false;
async function pollChat(){
  try{const data=await getJSON('/chat/messages?after_id='+lastMsg),log=$('chat-log'),stick=log.scrollTop+log.clientHeight>=log.scrollHeight-40;
    for(const msg of data.messages||[]){lastMsg=Math.max(lastMsg,msg.id);const role=['USER','HEAD','SYSTEM'].includes(msg.role)?msg.role:'SYSTEM';
      const node=el('div','','chat-msg '+role);if(role!=='SYSTEM')node.append(el('small',role==='USER'?'나':'HEAD'));const body=String(msg.content||'');node.append(role==='SYSTEM'&&body.length>260?body.slice(0,260)+'…':body);log.append(node);
      $('peek-role').textContent=role==='USER'?'나':role;$('peek-role').dataset.role=role;$('peek-text').textContent=String(msg.content||'').replace(/\s+/g,' ').slice(0,200);}
    while(log.children.length>80)log.firstChild.remove();
    if(stick)log.scrollTop=log.scrollHeight;
    if(!lastMsg)$('peek-text').textContent='아직 대화가 없습니다. 오른쪽에 지시를 입력해 보세요.';
  }catch(e){/* next poll retries */}
}
async function send(text){
  if(sending||!text.trim())return;sending=true;const btn=$('chat-send');btn.disabled=true;btn.textContent='전송 중…';
  try{
    const body=new URLSearchParams({message:text,request_id:'stage-'+Date.now().toString(36)+Math.random().toString(36).slice(2,8)});
    const pending=fetch('/chat',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'},body});
    $('chat-text').value='';grow();setTimeout(pollChat,600);
    const res=await pending,out=await res.json().catch(()=>({}));
    if(out.status==='busy')notice('HEAD가 이전 요청을 처리하고 있습니다. 잠시 뒤 다시 보내 주세요.');
    else if(out.status==='error')notice('전송 실패 · '+(out.error||res.status));
  }catch(e){notice('전송 실패 · '+e.message);}
  finally{sending=false;btn.disabled=false;btn.textContent='보내기';pollChat();pollActivity();}
}
function notice(text){const s=$('shell-error');s.hidden=false;s.textContent=text;setTimeout(()=>{s.hidden=true;},5000);}
function grow(){const t=$('chat-text');t.style.height='48px';t.style.height=Math.min(160,Math.max(48,t.scrollHeight+2))+'px';}

// ------------------------------------------------------------------ views
function setView(view){
  document.body.dataset.view=view;const work=view==='workshop',frame=$('workspace');
  $('workshop').hidden=!work;$('tab-stage').classList.toggle('on',!work);$('tab-workshop').classList.toggle('on',work);
  $('tab-stage').setAttribute('aria-pressed',String(!work));$('tab-workshop').setAttribute('aria-pressed',String(work));
  if(work&&!frame.getAttribute('src'))frame.src='/classic';
  if(work){$('attention').hidden=true;setBroadcast(false);}
}
function setBroadcast(on){
  document.body.classList.toggle('broadcast',on);$('btn-broadcast').textContent=on?'방송 모드 끝내기':'방송 모드';
  try{if(on&&!document.fullscreenElement)document.documentElement.requestFullscreen().catch(()=>{});else if(!on&&document.fullscreenElement)document.exitFullscreen().catch(()=>{});}catch(e){/* fullscreen is optional */}
}
function start(){
  Trinity.init($('trinity-canvas'),{onFrame});
  if(params.get('broadcast')==='1'||attract)document.body.classList.add('broadcast');   // e.g. an OBS browser source: /?attract=1
  $('tab-stage').onclick=()=>setView('stage');$('tab-workshop').onclick=()=>setView('workshop');$('attention-go').onclick=()=>setView('workshop');
  $('top-project').title='작업할 프로젝트 고르기';
  $('top-project').onclick=e=>{e.stopPropagation();openProjectMenu();};
  for(const label of document.querySelectorAll('.slot-label')){label.title='이 역할을 맡을 AI 바꾸기';label.onclick=e=>{e.stopPropagation();openRoleMenu(label);};}
  document.addEventListener('click',e=>{if(!e.target.closest('#role-menu'))$('role-menu').hidden=true;if(!e.target.closest('#project-menu'))$('project-menu').hidden=true;});
  $('btn-replay').onclick=startReplay;$('replay-stop').onclick=stopReplay;
  $('fail-open').onclick=openFailPanel;$('fail-close').onclick=()=>{$('fail-panel').hidden=true;};
  $('fail-panel').onclick=e=>{if(e.target===$('fail-panel'))$('fail-panel').hidden=true;};
  $('fail-toast-go').onclick=()=>{$('fail-toast').hidden=true;openFailPanel();};$('fail-toast-x').onclick=()=>{$('fail-toast').hidden=true;};
  $('fail-notify').onclick=async()=>{try{await Notification.requestPermission();}catch(e){}openFailPanel();};
  $('btn-broadcast').onclick=()=>setBroadcast(!document.body.classList.contains('broadcast'));
  document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement&&document.body.classList.contains('broadcast'))setBroadcast(false);});
  for(const b of $('win-tabs').children)b.onclick=()=>{win=b.dataset.win;winAuto=false;repaint();};
  $('chat-peek').onclick=()=>{const log=$('chat-log');log.hidden=!log.hidden;$('chat-peek').setAttribute('aria-expanded',String(!log.hidden));if(!log.hidden)log.scrollTop=log.scrollHeight;};
  $('chat-form').addEventListener('submit',e=>{e.preventDefault();send($('chat-text').value);});
  $('chat-text').addEventListener('input',grow);
  $('chat-text').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send($('chat-text').value);}});
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){$('fail-panel').hidden=true;$('chat-log').hidden=true;$('role-menu').hidden=true;$('project-menu').hidden=true;if(replay)stopReplay();return;}
    if(e.target.closest('textarea,input,[contenteditable]')||e.ctrlKey||e.metaKey||e.altKey||document.body.dataset.view!=='stage')return;
    if(e.key==='b'||e.key==='B'||e.key==='ㅠ')setBroadcast(!document.body.classList.contains('broadcast'));
    if(e.key==='r'||e.key==='R'||e.key==='ㄱ')startReplay();});
  setInterval(()=>{const d=new Date();$('top-clock').textContent=[d.getHours(),d.getMinutes(),d.getSeconds()].map(n=>String(n).padStart(2,'0')).join(':');if(!replay)tickTimer();},1000);
  pollActivity();pollStatus();pollChat();pollRoles();setInterval(pollRoles,8000);
  initLimitsFold();pollLimits();setInterval(pollLimits,60000);setInterval(()=>{if(limits&&!limits.error)paintLimits();},30000);
  setInterval(pollActivity,3000);setInterval(pollStatus,6000);setInterval(pollChat,3000);
}
window.TrinityStage={model:()=>lastModel,replaying:()=>!!replay};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
