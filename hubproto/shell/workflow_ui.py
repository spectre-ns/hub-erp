"""Workflows: each process's graph drawn on the server, and the graph editor (change control v2, section 10.5), modelled
on a study of Jira's workflow editor (docs/design/jira-workflow-study.md).

``/workflows`` lists the processes. ``/workflows/<process>`` is the editor for document controllers and policy owners
(everyone else reads the same page without the controls): a toolbar, the canvas and a side panel on one page. Click a
status or transition to open it in the panel, where every rule reads as a sentence; drag a status to move it, its label
to move the label, and from its handle to another status to connect them; Add status and Add transition (several From,
or Any status) open dialogs. Every edit is one operation posted to ``/workflow-op``: the server applies it to the
person's draft (saved at once), and answers with the drawing, the checks (each selecting its element), the changes from
the workflow in force, and what to announce. Undo and redo move along the draft's operation log; Tidy lays the graph out
again. ``?view=text`` is the Text view: every edit as a plain form, no script needed. ``?view=changes`` is the visual diff
against the workflow in force (Changes, Before, After). "Carry on a change order" puts the draft on one.

The keyboard: Tab reaches every status and transition (named for assistive technology); arrows move a status one grid
step (with Shift five); Enter opens the panel; C connects from the focused status; Delete deletes (a status with items in
it asks where they go); Escape cancels and deselects; Ctrl+Z and Ctrl+Shift+Z undo and redo; + - 0 zoom and fit. The
single keys (C, + - 0) work only while the canvas has focus and stop when the shell's keyboard shortcuts are switched
off; the toolbar has the shell's leader-key letters. Changes are announced in the page's live region.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from .. import authz, workflow_model as wm, workflows as wfs
from .forms import ShellError
from .handler import hidden_as, href, log_line, table
from .icons import icon
from .text import esc, type_name, when
from .workflow_svg import DIFF_JS, WF_CSS, diff_html, path_taken, render

CHANGES = "Quality/Changes"
EDITOR_CSS = """
.wf-ed{--wf-h:clamp(420px,66vh,920px)}.wf-tools{position:sticky;top:0;z-index:3;background:var(--bg);padding:var(--sp-1) 0;gap:var(--sp-1)}
.wf-tools form{display:inline}.wf-tools .sep{width:1px;align-self:stretch;background:var(--line);margin:0 var(--sp-1)}.wf-tools .saved{margin-left:auto;font-size:var(--fs-s);color:var(--muted)}
body:has(.wf-ed) .layout{grid-template-columns:minmax(0,1fr)}body:has(.wf-ed) nav.rail{display:none}body:has(.wf-ed) main{max-width:none}
.wf-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(17rem,22rem);gap:var(--sp-3);align-items:start}
.wf-canvas{position:relative;height:var(--wf-h);border:1px solid var(--line);border-radius:var(--r-lg);background:var(--bg);background-image:radial-gradient(var(--line) 1px,transparent 1px);background-size:16px 16px;overflow:hidden;touch-action:none}
.wf-canvas svg.wf{width:100%;height:100%;max-height:none}.wf-canvas:focus-within{border-color:var(--focus)}
.wf-side{max-height:calc(var(--wf-h) + 60px);overflow:auto;padding-right:2px}.wf-side h2{font-size:var(--fs-m);margin:var(--sp-3) 0 var(--sp-1)}
.wf-panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--sp-3)}.wf-panel h2{margin-top:0;font-size:var(--fs-l)}
.wf-panel .fields,.wf-text .fields{display:grid;gap:var(--sp-2)}.wf-panel form{margin:0 0 var(--sp-3)}.wf-panel fieldset,.wf-text fieldset{border:1px solid var(--line);border-radius:var(--r);padding:var(--sp-2);margin:0}
.wf-panel legend,.wf-text legend{font-size:var(--fs-s);color:var(--muted);font-weight:500}.wf-rule{background:var(--accent-soft);border-radius:var(--r);padding:var(--sp-2);margin:0 0 var(--sp-3);line-height:1.45}
.wf-checklist{display:flex;flex-wrap:wrap;gap:2px var(--sp-3)}.wf-checklist label{display:flex;gap:4px;align-items:center;font-size:var(--fs-s)}
ul.wf-checks{list-style:none;margin:0;padding:0}ul.wf-checks li{padding:4px 6px;border-radius:var(--r);margin:2px 0;font-size:var(--fs-s)}ul.wf-checks li.err{background:var(--crit-bg);color:var(--crit)}
ul.wf-checks li.warn{background:var(--warn-bg);color:var(--warn)}ul.wf-checks li a{color:inherit}ul.wf-checks li.ok{color:var(--ok)}
.wf-ways{list-style:none;margin:var(--sp-1) 0;padding:0}.wf-ways li{margin:2px 0}dialog.wf-dlg{border:1px solid var(--line);border-radius:var(--r-lg);background:var(--panel);color:var(--fg);padding:var(--sp-4);width:min(36rem,94vw);box-shadow:0 18px 50px var(--shadow-lg)}
dialog.wf-dlg::backdrop{background:rgba(0,0,0,.35)}.wf-dlg .close{float:right}html:not(.js) .wf-jsonly{display:none!important}.js .wf-nojs{display:none}
.wf-text section.card{scroll-margin-top:var(--sp-4)}.wf-text section.card:target{outline:3px solid var(--focus);outline-offset:2px}.wf-text details{margin:var(--sp-2) 0}
.wf-help{font-size:var(--fs-s);color:var(--muted);margin:var(--sp-1) 0}.wf-err{color:var(--crit)}
@media(max-width:1000px){.wf-grid{grid-template-columns:1fr}.wf-side{max-height:none}}
"""

WF_JS = r"""(function(){
var root=document.getElementById('wf');if(!root)return;var D=JSON.parse(document.getElementById('wf-data').textContent);
var svg=root.querySelector('.wf-canvas svg.wf');var live=document.getElementById('live-say');var G=D.geo;
var sel=D.sel||null,seq=0,applied=0,busy=0,content=D.vb.slice(),vb=D.vb.slice(),drag=null,pending=null,nudge=null,panelSeq=0;
var NS='http://www.w3.org/2000/svg';
function hotOff(){try{return localStorage.getItem('hub.hotkeys')==='off'}catch(e){return false}}
function say(t){if(!live||!t)return;live.textContent='';setTimeout(function(){live.textContent=t},40)}
function saved(t){var s=document.getElementById('wf-saved');if(s)s.textContent=t}
function esc(s){return CSS.escape(s)}
function n1(v){return Math.round(v*10)/10}
function snap(v){return Math.round(v/G.GRID)*G.GRID}
function setVB(){svg.setAttribute('viewBox',vb.map(n1).join(' '))}
function pt(ev){var p=svg.createSVGPoint();p.x=ev.clientX;p.y=ev.clientY;return p.matrixTransform(svg.getScreenCTM().inverse())}
function scale(){var r=svg.getBoundingClientRect();return Math.max(vb[2]/r.width,vb[3]/r.height)}
function node(id){return svg.querySelector('.wf-s[data-id="'+esc(id)+'"]')}
function lbl(id){return svg.querySelector('.wf-l[data-id="'+esc(id)+'"]')}
function pos(g){var m=/translate\(([-\d.]+),([-\d.]+)\)/.exec(g.getAttribute('transform')||'');return m?[+m[1],+m[2]]:[0,0]}
function setPos(g,x,y){g.setAttribute('transform','translate('+n1(x)+','+n1(y)+')')}
function boxOf(id){var g=node(id);if(!g)return null;var p=pos(g);return [p[0],p[1],G.W,G.H]}
function center(b){return [b[0]+b[2]/2,b[1]+b[3]/2]}
// The geometry of hubproto.workflow_model, recomputed while something is dragged.
function port(b,t){var c=center(b),dx=t[0]-c[0],dy=t[1]-c[1];if(Math.abs(dx)*b[3]>=Math.abs(dy)*b[2])return dx>0?[b[0]+b[2],c[1]]:[b[0],c[1]];return dy>0?[c[0],b[1]+b[3]]:[c[0],b[1]]}
function curve(p1,p2,bend){var dx=p2[0]-p1[0],dy=p2[1]-p1[1],ln=Math.sqrt(dx*dx+dy*dy)||1,nx=-dy/ln,ny=dx/ln;if(bend){var s=bend>0?8:-8;p1=[p1[0]+nx*s,p1[1]+ny*s];p2=[p2[0]+nx*s,p2[1]+ny*s]}
 var c=[(p1[0]+p2[0])/2+nx*bend,(p1[1]+p2[1])/2+ny*bend];return {d:'M'+n1(p1[0])+','+n1(p1[1])+' Q'+n1(c[0])+','+n1(c[1])+' '+n1(p2[0])+','+n1(p2[1]),label:[(p1[0]+2*c[0]+p2[0])/4,(p1[1]+2*c[1]+p2[1])/4]}}
function loopG(b,k){var cx=b[0]+b[2]/2,base=b[1]+b[3],hh=G.LOOP_H+G.LOOP_STEP*k,x1=cx-18,x2=cx+18;return {d:'M'+n1(x1)+','+n1(base)+' C'+n1(x1-22)+','+n1(base+hh)+' '+n1(x2+22)+','+n1(base+hh)+' '+n1(x2)+','+n1(base),label:[cx,base+hh*0.75+16]}}
function stemD(l,b){var p=port(b,l);return 'M'+n1(l[0])+','+n1(l[1])+' L'+n1(p[0])+','+n1(p[1])}
function geo(L){var ds=L.dataset,dst=boxOf(ds.dst);if(!dst)return null;var off=[+ds.dx||0,+ds.dy||0],k=+ds.k||0,n=+ds.n||1,bend=+ds.bend||0,src=(ds.src||'').split(',').filter(Boolean);
 if(ds.kind==='initial'){var sp=[dst[0]-G.START_DX,dst[1]+dst[3]/2];return {parts:{line:'M'+n1(sp[0])+','+n1(sp[1])+' L'+n1(dst[0])+','+n1(sp[1])},start:sp,label:[sp[0]+off[0],sp[1]-30+off[1]]}}
 if(ds.kind==='global'){var l=[dst[0]+dst[2]/2+(k-(n-1)/2)*G.GLOBAL_PITCH+off[0],dst[1]-G.GLOBAL_DY+off[1]];return {parts:{line:stemD([l[0],l[1]+18],dst)},label:l}}
 if(ds.kind==='loop'){var lg=loopG(dst,k);return {parts:{line:lg.d},label:[lg.label[0]+off[0],lg.label[1]+off[1]]}}
 if(ds.kind==='edge'){var a=boxOf(src[0]);if(!a)return null;var c=curve(port(a,center(dst)),port(dst,center(a)),bend);return {parts:{line:c.d},label:[c.label[0]+off[0],c.label[1]+off[1]]}}
 var others=src.filter(function(s){return s!==ds.dst}),boxes=others.map(boxOf).filter(Boolean);if(!boxes.length)boxes=[dst];var sx=0,sy=0;
 boxes.forEach(function(b){sx+=b[0]+b[2]/2;sy+=b[1]+b[3]/2});sx/=boxes.length;sy/=boxes.length;var tc=center(dst),j=[(sx+tc[0])/2+off[0],(sy+tc[1])/2+off[1]],parts={line:stemD(j,dst)};
 others.forEach(function(s){var b=boxOf(s);if(!b)return;var p=port(b,j);parts['arm:'+s]='M'+n1(p[0])+','+n1(p[1])+' L'+n1(j[0])+','+n1(j[1])});if(src.indexOf(ds.dst)>=0)parts.loop=loopG(dst,k).d;return {parts:parts,label:j}}
function route(L){var g=geo(L);if(!g)return;var id=L.dataset.id;svg.querySelectorAll('.wf-e[data-t="'+esc(id)+'"] path').forEach(function(p){var d=g.parts[p.getAttribute('data-part')];if(d)p.setAttribute('d',d)});
 if(g.start){var c=svg.querySelector('.wf-e[data-t="'+esc(id)+'"] .wf-start');if(c){c.setAttribute('cx',n1(g.start[0]));c.setAttribute('cy',n1(g.start[1]))}}setPos(L,g.label[0],g.label[1])}
function routeAround(sid){svg.querySelectorAll('.wf-l').forEach(function(L){if(L.dataset.dst===sid||(','+L.dataset.src+',').indexOf(','+sid+',')>=0)route(L)})}
function fitLabels(){svg.querySelectorAll('.wf-l').forEach(function(L){var a=L.querySelector('.wf-lname'),b=L.querySelector('.wf-lbadge');if(!a||!b||!a.getComputedTextLength)return;
 var w=Math.ceil(Math.max(a.getComputedTextLength(),b.getComputedTextLength()+15)+20);var r=L.querySelector('.wf-lbox');r.setAttribute('x',-w/2);r.setAttribute('width',w);
 a.setAttribute('x',-w/2+10);b.setAttribute('x',-w/2+25);var u=L.querySelector('use');if(u)u.setAttribute('x',-w/2+9);
 L.querySelectorAll('.wf-mk circle').forEach(function(c){c.setAttribute('cx',w/2)});L.querySelectorAll('.wf-mk text').forEach(function(t){t.setAttribute('x',w/2)});
 L.querySelectorAll('.wf-flag circle').forEach(function(c){c.setAttribute('cx',-w/2)});L.querySelectorAll('.wf-flag text').forEach(function(t){t.setAttribute('x',-w/2)})})}
// --- selection and the panel
function selEl(s){if(!s)return null;var i=s.indexOf(':'),k=s.slice(0,i),id=s.slice(i+1);return k==='s'?node(id):lbl(id)}
function handle(id){return svg.querySelector('.wf-handle[data-handle="'+esc(id)+'"]')}
function moveHandle(id){var h=handle(id),b=boxOf(id);if(h&&b){h.setAttribute('cx',n1(b[0]+b[2]));h.setAttribute('cy',n1(b[1]+b[3]/2))}}
svg.addEventListener('pointerover',function(e){var s=e.target.closest&&e.target.closest('.wf-s,.wf-handle');svg.querySelectorAll('.wf-handle.hov').forEach(function(h){h.classList.remove('hov','on')});
 if(!s)return;var id=s.dataset.id||s.getAttribute('data-handle'),h=handle(id);if(h){h.classList.add('hov','on')}mark()});
function mark(){svg.querySelectorAll('.wf-sel').forEach(function(e){e.classList.remove('wf-sel')});svg.querySelectorAll('.wf-handle.on:not(.hov)').forEach(function(h){h.classList.remove('on')});if(!sel)return;var e=selEl(sel);if(!e){sel=null;return}e.classList.add('wf-sel');if(sel.charAt(0)==='s'){var hh=handle(sel.slice(2));if(hh)hh.classList.add('on')}
 if(sel.charAt(0)==='t')svg.querySelectorAll('.wf-e[data-t="'+esc(sel.slice(2))+'"]').forEach(function(x){x.classList.add('wf-sel')})}
function select(s,o){o=o||{};sel=s;mark();if(!o.keepPanel)loadPanel(o.focus);if(o.reveal&&s)reveal(selEl(s));if(o.focusEl&&s){var e=selEl(s);if(e)e.focus({preventScroll:true})}}
function loadPanel(focus,what){var my=++panelSeq;var want=what||sel||'';fetch(D.urls.panel+'&sel='+encodeURIComponent(want),{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(j){if(my!==panelSeq)return;
 if(what&&what.indexOf('add-')===0||what&&what.indexOf('delete:')===0){openDialog(j.html);return}setPanel(j.html,focus)}).catch(function(){})}
function wire(box){box.querySelectorAll('[data-wf-show]').forEach(function(el){var f=el.closest('form');if(!f)return;var spec=el.getAttribute('data-wf-show').split('='),name=spec[0],vals=spec[1].split('|');
 var upd=function(){var v=null;f.querySelectorAll('[name="'+name+'"]').forEach(function(i){if((i.type==='radio'||i.type==='checkbox')){if(i.checked)v=i.type==='checkbox'?'on':i.value}else v=i.value});
  var neg=vals[0].charAt(0)==='!';var want=neg?[vals[0].slice(1)]:vals;var show=neg?want.indexOf(v||'')<0:want.indexOf(v||'')>=0;el.hidden=!show};
 f.addEventListener('change',upd);upd()})}
function setPanel(html,focus){var pane=document.getElementById('wf-panel');var a=document.activeElement,keep=null;
 if(a&&pane.contains(a)&&a.name&&a.form){keep={form:a.form.getAttribute('data-wf-form'),name:a.name,value:(a.type==='checkbox'||a.type==='radio')?a.value:null}}
 pane.innerHTML=html;wire(pane);
 if(keep){var q='[data-wf-form="'+esc(keep.form||'')+'"] [name="'+esc(keep.name)+'"]'+(keep.value!=null?'[value="'+esc(keep.value)+'"]':'');var e=pane.querySelector(q);if(e){e.focus();return}}
 if(focus){var f=typeof focus==='string'?pane.querySelector(focus):pane.querySelector('input:not([type=hidden]),select,textarea,button');if(f){f.focus();if(f.select&&f.type==='text')f.select()}else{var h=pane.querySelector('h2');if(h){h.tabIndex=-1;h.focus()}}}}
var dlg=document.getElementById('wf-dlg');
function openDialog(html){var body=document.getElementById('wf-dlg-body');body.innerHTML=html;wire(body);if(!dlg.open)dlg.showModal();var f=body.querySelector('input:not([type=hidden]),select');if(f)f.focus()}
if(dlg)dlg.addEventListener('click',function(e){if(e.target.closest('[data-wf-close]')){dlg.close()}});
if(dlg)dlg.addEventListener('close',function(){var e=selEl(sel);if(e)e.focus({preventScroll:true});else svg.focus()});
// --- talking to the server
function send(data,o){o=o||{};var fd=data instanceof FormData?data:new FormData();if(!(data instanceof FormData)){fd.append('op','json');fd.append('op_json',JSON.stringify(data));if(o.coalesce)fd.append('coalesce','1')}
 fd.set('as',D.as);fd.set('process',D.process);fd.set('fmt','json');if(!fd.has('sel'))fd.set('sel',sel||'');var my=++seq;busy++;saved('Saving…');
 return fetch(D.urls.op,{method:'POST',body:new URLSearchParams(fd),credentials:'same-origin',headers:{'Accept':'application/json'}}).then(function(r){return r.json()}).then(function(j){busy--;
  if(my<applied)return j;applied=my;if(!j.ok){saved('Not saved');showError(j.error||'Not done.',o.form);say(j.error);if(j.svg)swap(j);return j}
  showError('',o.form);if(o.form&&dlg&&dlg.contains(o.form))dlg.close();swap(j);say(j.said);saved(j.saved||'Draft saved');return j}).catch(function(){busy--;saved('Could not reach the hub; your last change was not saved.')})}
function showError(t,form){var box=(form&&form.querySelector('.wf-err'))||document.getElementById('wf-error');if(box){box.textContent=t;box.hidden=!t}}
var quiet=false;
function swap(j){if(drag){pending=j;return}var f=document.activeElement,fid=(f&&svg.contains(f))?f.id:null;
 svg.innerHTML=j.svg;content=j.vb;D.names=j.names||D.names;D.items=j.items||D.items;if(j.sel!==undefined)sel=j.sel;mark();fitLabels();
 if(j.panel!=null)setPanel(j.panel,j.focus);var c=document.getElementById('wf-checks');if(c&&j.checks_html!=null)c.innerHTML=j.checks_html;var d=document.getElementById('wf-delta');if(d&&j.delta_html!=null)d.innerHTML=j.delta_html;
 var n=document.getElementById('wf-nchanges');if(n&&j.n_changes!=null)n.textContent=j.n_changes;
 ['undo','redo'].forEach(function(k){var b=root.querySelector('[data-wf-'+k+']');if(b)b.disabled=!j['can_'+k]});
 if(!j.focus){var e=fid?document.getElementById(fid):null;if(!e&&j.focus_el)e=selEl(sel);if(e){quiet=true;e.focus({preventScroll:true});quiet=false}}
 if(j.reveal&&sel)reveal(selEl(sel))}
function reveal(el){if(!el)return;var b=el.getBBox(),p=el.transform&&el.transform.baseVal.consolidate();var x=b.x+(p?p.matrix.e:0),y=b.y+(p?p.matrix.f:0);
 if(x<vb[0]||y<vb[1]||x+b.width>vb[0]+vb[2]||y+b.height>vb[1]+vb[3]){vb[0]=x+b.width/2-vb[2]/2;vb[1]=y+b.height/2-vb[3]/2;setVB()}}
// --- zoom and pan through the viewBox
function zoom(f,cx,cy){var w=vb[2]*f,h=vb[3]*f;if(w<content[2]/6||w>content[2]*4)return;if(cx==null){cx=vb[0]+vb[2]/2;cy=vb[1]+vb[3]/2}
 vb=[cx-(cx-vb[0])*f,cy-(cy-vb[1])*f,w,h];setVB()}
function fit(){vb=content.slice();setVB()}
svg.addEventListener('wheel',function(e){e.preventDefault();if(e.ctrlKey||e.metaKey){var p=pt(e);zoom(Math.exp(e.deltaY*0.002),p.x,p.y)}else{var k=scale();vb[0]+=(e.shiftKey?e.deltaY:e.deltaX)*k;vb[1]+=(e.shiftKey?0:e.deltaY)*k;setVB()}},{passive:false});
// --- pointer: select, drag a status or a label, connect from a handle, pan the background
svg.addEventListener('pointerdown',function(e){if(e.button>0)return;var h=e.target.closest('.wf-handle'),s=e.target.closest('.wf-s'),L=e.target.closest('.wf-l'),E=e.target.closest('.wf-e'),p=pt(e);
 if(h&&D.editable){var g0=h.closest('.wf-s');drag={kind:'connect',src:h.getAttribute('data-handle'),ghost:document.createElementNS(NS,'path'),target:null};drag.ghost.setAttribute('class','wf-ghost');
  svg.appendChild(drag.ghost);svg.classList.add('connecting');svg.setPointerCapture(e.pointerId);ghost(p);e.preventDefault();return}
 if(s){var q=pos(s);if(D.editable){drag={kind:'node',el:s,id:s.dataset.id,ox:p.x-q[0],oy:p.y-q[1],x:q[0],y:q[1],x0:q[0],y0:q[1],moved:false};svg.setPointerCapture(e.pointerId)}
  select('s:'+s.dataset.id,{keepPanel:true});s.focus({preventScroll:true});if(!D.editable)loadPanel();e.preventDefault();return}
 if(L){if(D.editable){drag={kind:'label',el:L,id:L.dataset.id,sx:p.x,sy:p.y,ox:+L.dataset.dx||0,oy:+L.dataset.dy||0,moved:false};svg.setPointerCapture(e.pointerId)}
  select('t:'+L.dataset.id,{keepPanel:true});L.focus({preventScroll:true});if(!D.editable)loadPanel();e.preventDefault();return}
 if(E){select('t:'+E.getAttribute('data-t'));var l2=lbl(E.getAttribute('data-t'));if(l2)l2.focus({preventScroll:true});return}
 drag={kind:'pan',sx:e.clientX,sy:e.clientY,vb0:vb.slice(),k:scale(),moved:false};svg.setPointerCapture(e.pointerId)});
function ghost(p){var b=boxOf(drag.src);if(!b)return;var t=drag.target&&boxOf(drag.target);var end=t?(drag.target===drag.src?[b[0]+b[2]/2+18,b[1]+b[3]]:port(t,center(b))):[p.x,p.y];var st=port(b,end);
 drag.ghost.setAttribute('d',drag.target===drag.src?loopG(b,0).d:'M'+n1(st[0])+','+n1(st[1])+' L'+n1(end[0])+','+n1(end[1]))}
svg.addEventListener('pointermove',function(e){if(!drag)return;var p=pt(e);
 if(drag.kind==='node'){var x=snap(p.x-drag.ox),y=snap(p.y-drag.oy);if(!drag.moved&&x===drag.x0&&y===drag.y0)return;if(!drag.moved)svg.classList.add('dragging');drag.moved=true;if(x!==drag.x||y!==drag.y){drag.x=x;drag.y=y;setPos(drag.el,x,y);moveHandle(drag.id);routeAround(drag.id)}}
 else if(drag.kind==='label'){var dx=snap(drag.ox+p.x-drag.sx),dy=snap(drag.oy+p.y-drag.sy);if(!drag.moved&&dx===drag.ox&&dy===drag.oy)return;if(!drag.moved)svg.classList.add('dragging');drag.moved=true;drag.el.dataset.dx=dx;drag.el.dataset.dy=dy;route(drag.el)}
 else if(drag.kind==='pan'){var mx=e.clientX-drag.sx,my=e.clientY-drag.sy;if(Math.abs(mx)+Math.abs(my)>3)drag.moved=true;vb[0]=drag.vb0[0]-mx*drag.k;vb[1]=drag.vb0[1]-my*drag.k;setVB()}
 else if(drag.kind==='connect'){var el=document.elementFromPoint(e.clientX,e.clientY),t=el&&el.closest?el.closest('.wf-s'):null,id=t?t.dataset.id:null;
  if(id!==drag.target){svg.querySelectorAll('.wf-target').forEach(function(x){x.classList.remove('wf-target')});if(t)t.classList.add('wf-target');drag.target=id}ghost(p)}});
function endDrag(e,cancel){var d=drag;drag=null;svg.classList.remove('dragging','connecting');if(!d)return;
 if(d.kind==='node'){if(cancel&&d.moved){setPos(d.el,d.x0,d.y0);moveHandle(d.id);routeAround(d.id)}else if(d.moved)send({op:'move',id:d.id,at:[d.x,d.y]});else loadPanel()}
 else if(d.kind==='label'){if(cancel&&d.moved){d.el.dataset.dx=d.ox;d.el.dataset.dy=d.oy;route(d.el)}else if(d.moved)send({op:'move_label',id:d.id,offset:[+d.el.dataset.dx,+d.el.dataset.dy]});else loadPanel()}
 else if(d.kind==='pan'){if(!d.moved&&!cancel){select(null)}}
 else if(d.kind==='connect'){d.ghost.remove();svg.querySelectorAll('.wf-target').forEach(function(x){x.classList.remove('wf-target')});
  if(d.target&&!cancel){send({op:'add_transition',from:[d.src],to:d.target,name:D.names[d.target]||d.target},{})}else if(!cancel)say('Not connected: let go over a status to connect it.')}
 if(pending){var j=pending;pending=null;swap(j)}}
svg.addEventListener('pointerup',function(e){endDrag(e,false)});svg.addEventListener('pointercancel',function(e){endDrag(e,true)});
// --- keyboard on the canvas
svg.addEventListener('focusin',function(e){var g=e.target.closest&&e.target.closest('.wf-s,.wf-l');if(!g||drag||quiet)return;var s=(g.classList.contains('wf-s')?'s:':'t:')+g.dataset.id;if(s!==sel){select(s,{keepPanel:true});clearTimeout(focusTimer);focusTimer=setTimeout(function(){if(sel===s)loadPanel()},180)}});
var focusTimer=null;
function del(g){if(!D.editable)return;var id=g.dataset.id;if(g.classList.contains('wf-l')){send({op:'delete_transition',id:id});return}
 if((D.items||{})[id]>0){loadPanel(false,'delete:'+id);return}send({op:'delete_status',id:id})}
svg.addEventListener('keydown',function(e){var k=e.key,g=e.target.closest&&e.target.closest('.wf-s,.wf-l');
 if(k==='Escape'){if(drag){endDrag(e,true);e.preventDefault();return}if(sel){select(null);svg.focus();e.preventDefault()}return}
 if(e.ctrlKey||e.metaKey||e.altKey)return;
 if(!hotOff()&&(k==='+'||k==='=')){zoom(1/1.25);e.preventDefault();return}if(!hotOff()&&(k==='-'||k==='_')){zoom(1.25);e.preventDefault();return}if(!hotOff()&&k==='0'){fit();e.preventDefault();return}
 if(!g)return;var isS=g.classList.contains('wf-s'),id=g.dataset.id;
 if(k.indexOf('Arrow')===0&&D.editable){e.preventDefault();var st=G.GRID*(e.shiftKey?5:1),dx=k==='ArrowLeft'?-st:k==='ArrowRight'?st:0,dy=k==='ArrowUp'?-st:k==='ArrowDown'?st:0;
  if(isS){var q=pos(g);setPos(g,q[0]+dx,q[1]+dy);moveHandle(id);routeAround(id);reveal(g);clearTimeout(nudge);nudge=setTimeout(function(){var q2=pos(g);send({op:'move',id:id,at:[q2[0],q2[1]]},{coalesce:true})},350)}
  else{g.dataset.dx=(+g.dataset.dx||0)+dx;g.dataset.dy=(+g.dataset.dy||0)+dy;route(g);clearTimeout(nudge);nudge=setTimeout(function(){send({op:'move_label',id:id,offset:[+g.dataset.dx,+g.dataset.dy]},{coalesce:true})},350)}return}
 if(k==='Enter'||k===' '){e.preventDefault();loadPanel(true);return}
 if((k==='Delete'||k==='Backspace')&&D.editable){e.preventDefault();del(g);return}
 if((k==='c'||k==='C')&&isS&&D.editable&&!hotOff()){e.preventDefault();clearTimeout(focusTimer);loadPanel('[data-wf-form="connect"] select[name="to"]');return}});
root.addEventListener('keydown',function(e){if(!(e.ctrlKey||e.metaKey)||e.altKey)return;var t=e.target;if(t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'||t.isContentEditable))return;var k=e.key.toLowerCase();
 if(k==='z'||k==='y'){e.preventDefault();var redo=k==='y'||e.shiftKey;var b=root.querySelector(redo?'[data-wf-redo]':'[data-wf-undo]');if(b&&!b.disabled)b.form.requestSubmit()}});
// --- forms (panel, dialogs, toolbar) and links that select
root.addEventListener('submit',function(e){var f=e.target;if(!f.matches||!f.matches('form[data-wf-op]'))return;e.preventDefault();send(new FormData(f),{form:f})});
root.addEventListener('change',function(e){var f=e.target.form;if(f&&f.hasAttribute('data-wf-live')&&f.matches('form[data-wf-op]')&&!e.target.hasAttribute('data-wf-nolive'))f.requestSubmit()});
root.addEventListener('click',function(e){var a=e.target.closest('[data-sel]');if(a){e.preventDefault();var s=a.getAttribute('data-sel');select(s,{reveal:true,focusEl:true});return}
 var d=e.target.closest('[data-wf-dialog]');if(d){e.preventDefault();loadPanel(false,d.getAttribute('data-wf-dialog'));return}
 var z=e.target.closest('[data-wf-zoom]');if(z){var v=z.getAttribute('data-wf-zoom');if(v==='in')zoom(1/1.25);else if(v==='out')zoom(1.25);else fit()}});
addEventListener('beforeunload',function(e){if(busy>0){e.preventDefault();e.returnValue=''}});
fitLabels();mark();wire(root);svg.setAttribute('tabindex','-1');
(function(){var r=svg.getBoundingClientRect();if(!r.width)return;var k=Math.max(content[2]/r.width,content[3]/r.height),most=1.3;
 if(k>most){vb=[content[0],content[1],r.width*1.15,r.height*1.15];setVB()}})();
})();"""


def _json_in_script(data) -> str:
    return json.dumps(data, default=str).replace("</", "<\\/")


def _num_or_text(v: str):
    for cast in (int, float):
        try:
            return cast(v)
        except (TypeError, ValueError):
            continue
    return v


def _csv(text: str) -> list[str]:
    return [x.strip() for x in str(text or "").replace("\n", ",").split(",") if x.strip()]


class WorkflowsMixin:
    # ------------------------------------------------------------ who, which
    def _wf_editor(self) -> bool:
        return bool(self.bundle) and wfs.may_edit(self.bundle, self.user) and not self._viewing_cog()

    def _wf_member(self) -> None:
        self._need_release()
        if not self._is_member():
            raise ShellError("Only members of the organization see the workflows.", 403)

    def _wf_require_edit(self) -> None:
        self._wf_member()
        if not self._wf_editor():
            raise ShellError("Only document controllers and policy owners change workflows; anyone may read them.", 403)

    def _wf_process(self, name: str) -> str:
        name = (name or "").strip("/")
        if name not in wfs.processes(self.bundle):
            raise ShellError(f"There is no process '{name}'.", 404)
        return name

    def _wf_title(self, process: str) -> str:
        return self._process_name(process)

    def _wf_url(self, process: str, view: str = "", anchor: str = "", **kw) -> str:
        return href("/workflows/" + process, self.user, view=view if view and view != "diagram" else None, **kw) + (f"#{anchor}" if anchor else "")

    def _wf_state(self, process: str) -> dict:
        """What the page shows: the document (draft, else in force), its base, the draft, the checks and the changes."""
        editable = self._wf_editor()
        if editable:
            doc, base, draft = wfs.working(self.hub, self.user, process)
        else:
            g = wfs.graph_in_force(self.hub, process, self.bundle)
            base = {"graph": g, "layout": wfs.layout_in_force(self.hub, process, g), "release": (self.meta or {}).get("digest")}
            doc, draft = {"graph": g, "layout": dict(base["layout"]), "state_map": {}}, None
        an = wfs.analysis(self.hub, process, doc, base, by=self.user, bundle=self.bundle)
        undo, redo = wfs.can_undo_redo(draft)
        return {"doc": doc, "base": base, "draft": draft, "editable": editable, "checks": an["checks"], "delta": an["delta"],
                "items": an["items"], "can_undo": undo, "can_redo": redo, "stale": wfs.stale(self.hub, draft, process)}

    def _wf_flags(self, checks: list[dict]) -> dict:
        out = {}
        for c in checks:
            if c.get("element") and c.get("id"):
                key = f"{c['element']}:{c['id']}"
                if out.get(key) != "error":
                    out[key] = c["level"]
        return out

    def _wf_svg(self, process: str, st: dict, sel: str | None = None, inner: bool = False):
        doc = st["doc"]
        return render(doc["graph"], doc["layout"], idp="wfc", mode="edit" if st["editable"] else "read", items=st["items"],
                      flags=self._wf_flags(st["checks"]) if st["editable"] else None, selected=sel, inner_only=inner,
                      label=f"{self._wf_title(process)} workflow: {wm.plural(len(doc['graph']['statuses']), 'status')}, "
                            f"{wm.plural(len(doc['graph']['transitions']), 'transition')}. Tab to each one; Enter opens it.")

    # ------------------------------------------------------------ lists of checks and changes
    def _wf_sel_link(self, process: str, element: str | None, eid: str | None, text: str) -> str:
        if element in ("status", "transition") and eid:
            k = "s" if element == "status" else "t"
            return f"<a href='{esc(self._wf_url(process, 'text', f'{k}-{eid}'))}' data-sel='{k}:{esc(eid)}'>{esc(text)}</a>"
        return esc(text)

    def _wf_checks_html(self, process: str, checks: list[dict]) -> str:
        if not checks:
            return "<li class='ok'>No problems: every status can be reached and left, and every gate has someone to decide.</li>"
        rows = []
        for c in sorted(checks, key=lambda c: (c["level"] != "error", c.get("code") or "")):
            cls = "err" if c["level"] == "error" else "warn"
            word = "Error" if c["level"] == "error" else "Warning"
            rows.append(f"<li class='{cls}'><span class='vh'>{word}: </span>{self._wf_sel_link(process, c.get('element'), c.get('id'), c['text'])}</li>")
        return "".join(rows)

    def _wf_delta_html(self, process: str, delta: list[dict]) -> str:
        if not delta:
            return "<li class='muted'>No change from the workflow in force.</li>"
        out = []
        for i in delta:
            sign = {"add": ("add", "+", "Added"), "remove": ("del", "−", "Removed")}.get(i.get("op"), ("chg", "~", "Changed"))
            kids = "".join(f"<li>{esc(c.get('text'))}</li>" for c in i.get("children") or [])
            flag = " <span class='chip warn'>widening</span>" if i.get("widening") else ""
            out.append(f"<li><span class='wf-sign {sign[0]}' aria-hidden='true'>{sign[1]}</span><span class='vh'>{sign[2]}: </span>"
                       f"{self._wf_sel_link(process, i.get('element'), i.get('element_id'), i.get('text') or '')}{flag}" + (f"<ul>{kids}</ul>" if kids else "") + "</li>")
        return "".join(out)

    # ------------------------------------------------------------ forms
    def _wf_form(self, process: str, op: str, inner: str, *, button: str = "Save", key: str = "", live: bool = False, view: str = "",
                 hidden: dict | None = None, btn_cls: str = "secondary", anchor: str = "") -> str:
        if not self._wf_editor():
            return ""
        extra = "".join(f"<input type='hidden' name='{esc(k)}' value='{esc(v)}'>" for k, v in (hidden or {}).items())
        return (f"<form method='post' action='/workflow-op' class='wf-form' data-wf-op data-wf-form='{esc(key or op)}'{' data-wf-live' if live else ''}>"
                f"{hidden_as(self.user)}{self._once()}<input type='hidden' name='process' value='{esc(process)}'><input type='hidden' name='op' value='{esc(op)}'>"
                f"<input type='hidden' name='back' value='{esc(view or 'text')}'><input type='hidden' name='anchor' value='{esc(anchor)}'>{extra}"
                f"<p class='wf-err' role='alert' hidden></p>{inner}"
                + (f"<div class='actions'><button class='{btn_cls}'>{button}</button></div>" if button else "") + "</form>")

    @staticmethod
    def _sel(name: str, options: list[tuple[str, str]], value, *, label: str, blank: str | None = None, attrs: str = "") -> str:
        opts = (f"<option value=''>{esc(blank)}</option>" if blank is not None else "") + "".join(
            f"<option value='{esc(v)}'{' selected' if str(v) == str(value) else ''}>{esc(t)}</option>" for v, t in options)
        return f"<label class='f'><span>{esc(label)}</span><select name='{esc(name)}'{attrs}>{opts}</select></label>"

    @staticmethod
    def _inp(name: str, value, *, label: str, kind: str = "text", hint: str = "", attrs: str = "") -> str:
        return (f"<label class='f'><span>{esc(label)}" + (f" <i>({esc(hint)})</i>" if hint else "") + f"</span>"
                f"<input type='{kind}' name='{esc(name)}' value='{esc('' if value is None else value)}'{attrs}></label>")

    def _wf_roles(self) -> list[tuple[str, str]]:
        roles = set((self.bundle or {}).get("roles") or [])
        for m in ((self.bundle or {}).get("members") or {}).values():
            roles |= set(m.get("roles") or [])
        out = [(r, r.replace("-", " ")) for r in sorted(roles)]
        return out + [(r, f"the item's {r}" if r != "system" else "the hub (system)") for r in wm.PSEUDO_ROLES if r not in roles]

    def _wf_cogs(self) -> list[tuple[str, str]]:
        return [(m, m) for m, info in sorted(((self.bundle or {}).get("members") or {}).items()) if info.get("type") == "cog"]

    def _wf_status_options(self, g: dict, skip: str | None = None) -> list[tuple[str, str]]:
        return [(s["id"], s["name"]) for s in g["statuses"] if s["id"] != skip]

    def _wf_checkboxes(self, name: str, options: list[tuple[str, str]], chosen, *, legend: str, attrs: str = "") -> str:
        boxes = "".join(f"<label><input type='checkbox' name='{esc(name)}' value='{esc(v)}'{' checked' if v in (chosen or []) else ''}> {esc(t)}</label>"
                        for v, t in options)
        return f"<fieldset{attrs}><legend>{esc(legend)}</legend><div class='wf-checklist'>{boxes}</div></fieldset>"

    def _wf_gate_fields(self, g: dict, gate: dict | None, tid: str | None = None) -> str:
        gate = gate or dict(wm.DEFAULT_GATE)
        kind = gate.get("kind") or "person"
        radios = "".join(f"<label><input type='radio' name='gate_kind' value='{k}'{' checked' if kind == k else ''}> {w}</label>"
                         for k, w in (("person", "a person in a role"), ("cog", "a cog, within its criteria"), ("auto", "automatically")))
        roles = self._wf_roles()
        nots = gate.get("not") or []
        others = [(t["id"], t["name"]) for t in g["transitions"] if t["id"] != tid and t.get("type") != "initial"]
        not_t = next((n for n in nots if n != "author"), "")
        whens = [(w, wm.condition_words(w) if w != "actions-succeed" else "once its actions succeed") for w in wm.AUTO_WHEN] + \
                [(f"not {c}", f"not: {wm.condition_words(c)}") for c in wm.CONDITIONS]
        return (f"<fieldset><legend>Who decides</legend><div class='wf-checklist'>{radios}</div>"
                f"<div data-wf-show='gate_kind=person' class='fields'>{self._sel('role', roles, gate.get('role') or 'owner', label='Role')}"
                f"<label class='wf-checklist'><input type='checkbox' name='not_author' value='1'{' checked' if 'author' in nots else ''}> not the item's author</label>"
                + (self._sel("not_t", others, not_t, label="and not whoever took", blank="(nobody else)") if others else "") + "</div>"
                f"<div data-wf-show='gate_kind=cog' class='fields'>{self._sel('cog', self._wf_cogs(), gate.get('cog') or '', label='Cog', blank='choose…')}"
                f"{self._sel('fallback', roles, gate.get('fallback') or '', label='Otherwise (the role it falls back to)', blank='choose…')}</div>"
                f"<div data-wf-show='gate_kind=auto' class='fields'>{self._sel('when', whens, gate.get('when') or 'actions-succeed', label='When')}</div></fieldset>")

    def _wf_gate_from_form(self) -> dict:
        kind = self._f("gate_kind") or "person"
        if kind == "cog":
            return {"kind": "cog", "cog": self._f("cog"), "fallback": self._f("fallback")}
        if kind == "auto":
            return {"kind": "auto", "when": self._f("when") or "actions-succeed"}
        nots = (["author"] if self._f("not_author") else []) + ([self._f("not_t")] if self._f("not_t") else [])
        return {"kind": "person", "role": self._f("role") or "owner", **({"not": nots} if nots else {})}

    # ------------------------------------------------------------ panels
    def _wf_overview_panel(self, process: str, st: dict) -> str:
        g, user = st["doc"]["graph"], self.user
        rt = g.get("record_type") or ""
        items = st["items"]
        src = g.get("source") or {}
        rows = [[esc(s["name"]), (f"<a href='{href('/items/' + rt, user, state=s['id'])}'>{int(items.get(s['id']) or 0)}</a>"
                                                           if items.get(s["id"]) and rt else str(int(items.get(s["id"]) or 0)))] for s in g["statuses"]]
        routes = [r for r in (self.bundle or {}).get("routing") or [] if r.get("process") == process]
        cos = [co for co in self.hub.kernel.query(type_="change-order") if process in (co["fields"].get("workflow_changes") or {})
               and co["state"] not in ("released", "cancelled", "rejected") and self._can_read(co["folder"])["allowed"]]
        out = (f"<h2 id='wf-panel-h'>{icon('workflow')} {esc(self._wf_title(process))}</h2>"
               f"<p class='why'>{esc(type_name(rt)) if rt else 'No record type'}: {wm.plural(len(g['statuses']), 'status')}, "
               f"{wm.plural(len(g['transitions']), 'transition')}. Stated in "
               + (self._rule_link({"frame": src.get("frame"), "line": (src.get("lines") or [None])[0]}) if src.get("frame") else "no SOP yet") + ".</p>")
        if st["editable"]:
            out += ("<p class='wf-help'>Select a status or a transition to change it (click it, or Tab to it and press Enter). Drag a status "
                    "to move it; drag from its round handle to another status to connect them.</p>")
        out += "<h3>Items in each status</h3>" + table(["Status", "Items"], rows)
        if routes:
            out += "<h3>Routing and due dates</h3><ul>" + "".join(f"<li>{self._rule_html(r.get('source'))}</li>" for r in routes[:12]) + "</ul>"
        if cos:
            out += "<h3>Open change orders on it</h3><ul>" + "".join(f"<li><a href='{self._co_href(co)}'>{esc(co['number'])}</a> {esc(co['fields'].get('title') or '')}</li>" for co in cos) + "</ul>"
        return out

    def _wf_ways(self, process: str, g: dict, sid: str) -> str:
        outs = [t for a, t, b in wm.edges(g) if a == sid]
        ins = [t for t in g["transitions"] if t.get("to") == sid]
        li = lambda t: f"<li>{self._wf_sel_link(process, 'transition', t['id'], t['name'])} <span class='why'>{esc(wm.route_words(g, t))}</span></li>"
        return ("<h3>Ways out</h3><ul class='wf-ways'>" + ("".join(li(t) for t in outs) or "<li class='muted'>none</li>") + "</ul>"
                "<h3>Ways in</h3><ul class='wf-ways'>" + ("".join(li(t) for t in ins) or "<li class='muted'>none</li>") + "</ul>")

    def _wf_status_panel(self, process: str, st: dict, sid: str, view: str = "diagram") -> str:
        g = st["doc"]["graph"]
        s = wm.status_map(g).get(sid)
        if s is None:
            return self._wf_overview_panel(process, st)
        n = int(st["items"].get(sid) or 0)
        anchor = f"s-{sid}"
        head = (f"<h2 id='wf-panel-h'>{esc(s['name'])} <span class='chip'>{esc(wm.CATEGORY_WORDS.get(s.get('category'), ''))}</span></h2>"
                f"<p class='why'>{esc(wm.status_words(s).capitalize())}. {wm.plural(n, 'item')} {'is' if n == 1 else 'are'} here now.</p>")
        if not st["editable"]:
            return head + self._wf_ways(process, g, sid)
        cat_opts = [(c, wm.CATEGORY_WORDS[c]) for c in wm.CATEGORIES]
        out_opts = [(o, o) for o in wm.OUTCOMES]
        edit = self._wf_form(process, "edit_status", "<div class='fields'>"
                             + self._inp("name", s["name"], label="Name", attrs=" required maxlength='60'")
                             + self._sel("category", cat_opts, s.get("category"), label="Category")
                             + f"<div data-wf-show='category=done'>{self._sel('outcome', out_opts, s.get('outcome') or 'completed', label='Outcome')}</div>"
                             + self._inp("sla_days", s.get("sla_days") or "", label="Due within (days)", kind="number", hint="empty: no due date", attrs=" min='1' max='365'")
                             + "</div>", key="edit-status", live=True, view=view, hidden={"id": sid}, anchor=anchor)
        has_global = any(t.get("type") == "global" and t.get("to") == sid for t in g["transitions"])
        allow = self._wf_form(process, "allow_all", f"<label class='wf-checklist'><input type='checkbox' name='on' value='1'{' checked' if has_global else ''}> "
                              f"Allow all statuses to move here</label>", key="allow-all", live=True, button="Save" if view == "text" else "",
                              view=view, hidden={"id": sid, "was": "1" if has_global else ""}, anchor=anchor)
        connect = self._wf_form(process, "connect", "<div class='fields'>"
                                + self._sel("to", [(x["id"], x["name"] + (" (itself: a loop)" if x["id"] == sid else "")) for x in g["statuses"]], "",
                                            label=f"Connect from here to", blank="choose…", attrs=" required")
                                + self._inp("name", "", label="Name", hint="defaults to the target's name") + "</div>",
                                key="connect", button=f"{icon('plus')}Add the transition", view=view, hidden={"id": sid}, anchor=anchor)
        x, y = st["doc"]["layout"].get(sid) or [0, 0]
        move = self._wf_form(process, "move", "<div class='fields'>" + self._inp("x", x, label="Across (px)", kind="number", attrs=" step='8'")
                             + self._inp("y", y, label="Down (px)", kind="number", attrs=" step='8'") + "</div>", key="move", button="Move", view=view,
                             hidden={"id": sid}, anchor=anchor)
        return (head + edit + allow + f"<h3>{icon('link')}Connect</h3>" + connect + self._wf_ways(process, g, sid)
                + f"<details><summary>Position</summary>{move}</details>" + self._wf_delete_status_form(process, st, sid, view=view))

    def _wf_delete_status_form(self, process: str, st: dict, sid: str, view: str = "diagram", dialog: bool = False) -> str:
        g = st["doc"]["graph"]
        s = wm.status_map(g).get(sid) or {"name": sid}
        n = int(st["items"].get(sid) or 0) if sid in wm.status_map(st["base"]["graph"]) else 0
        kept = [x for x in g["statuses"] if x["id"] != sid]
        inner = ""
        if n:
            default = st["doc"].get("state_map", {}).get(sid) or wm.default_move_to(g, sid)
            inner = (f"<p><b>{wm.plural(n, 'item')} {'is' if n == 1 else 'are'} in {esc(s['name'])}.</b> Move "
                     f"{'it' if n == 1 else 'them'} to:</p>" + self._sel("move_to", [(x["id"], x["name"]) for x in kept], default, label="Move them to")
                     + "<p class='why'>Asked again on the change order with the consequences; the release moves them without running rules, "
                       "and records the move on each item.</p>")
        else:
            inner = f"<p class='why'>Deleting {esc(s['name'])} deletes the transitions into it (undo brings them back).</p>"
        head = (f"<h2 id='wf-dlg-h'>Delete {esc(s['name'])}?</h2>" if dialog else f"<h3>{icon('x')}Delete</h3>")
        return head + self._wf_form(process, "delete_status", inner, key="delete-status", button=f"{icon('x')}Delete the status", view=view,
                                    hidden={"id": sid}, btn_cls="reject")

    def _wf_transition_fields(self, g: dict, t: dict | None, *, sid_from: list[str] | None = None, fresh: bool = False) -> str:
        t = t or {"type": "directed", "from": sid_from or [], "to": "", "name": "", "gate": dict(wm.DEFAULT_GATE)}
        kind = t.get("type") or "directed"
        statuses = self._wf_status_options(g)
        name = self._inp("name", t.get("name") or "", label="Name", attrs=" maxlength='60'" + ("" if fresh else " required"),
                         hint="defaults to the target's name" if fresh else "")
        if kind == "initial":
            route = self._sel("to", statuses, t.get("to"), label="Starts items in")
        else:
            anyst = kind == "global"
            ex_default = t.get("except") is None
            route = (f"<label class='wf-checklist'><input type='checkbox' name='any' value='1'{' checked' if anyst else ''}> From any status</label>"
                     + self._wf_checkboxes("from", statuses, t.get("from") or [], legend="From", attrs=" data-wf-show='any=!on'")
                     + f"<div data-wf-show='any=on'><label class='wf-checklist'><input type='checkbox' name='except_default' value='1'"
                       f"{' checked' if ex_default else ''}> except done statuses</label>"
                     + self._wf_checkboxes("except", statuses, [] if ex_default else (t.get("except") or []), legend="or except these (when the box above is clear)")
                     + "</div>" + self._sel("to", statuses, t.get("to"), label="To", blank="choose…", attrs=" required"))
        vs = t.get("validators") or []
        fields_set = next((v["fields_set"] for v in vs if "fields_set" in v), [])
        visited = next((v["status_visited"] for v in vs if "status_visited" in v), "")
        linked = next((v["linked"] for v in vs if "linked" in v), "")
        cond = next((v["form_condition"] for v in vs if "form_condition" in v and isinstance(v["form_condition"], dict) and "field" in v["form_condition"]), {})
        ops = [(o, o) for o in ("==", "!=", ">", "<", ">=", "<=", "set", "empty")]
        checks = ("<fieldset><legend>Checks before it moves (on failure nothing moves and no action runs)</legend><div class='fields'>"
                  + self._inp("fields_set", ", ".join(fields_set), label="These fields are filled in", hint="comma separated")
                  + self._sel("visited", statuses, visited, label="It has been in", blank="(no check)")
                  + self._sel("linked", list(wm.LINKED.items()), linked, label="Linked records", blank="(no check)")
                  + "<div class='wf-checklist'>" + self._inp("cond_field", cond.get("field") or "", label="Field") + self._sel("cond_op", ops, cond.get("op") or "==", label="is")
                  + self._inp("cond_value", "" if cond.get("value") is None else cond.get("value"), label="Value") + "</div></div></fieldset>")
        rest = ("<div class='fields'>" + self._inp("fields", ", ".join(t.get("fields") or []), label="Asks for these fields at the decision", hint="comma separated")
                + f"<label class='f'><span>After the move, in order <i>(one per line: a step, notify:&lt;role&gt;, set:&lt;field&gt;=&lt;value&gt;)</i></span>"
                  f"<textarea name='actions' rows='3'>{esc(chr(10).join(t.get('actions') or []))}</textarea></label></div>")
        return f"<div class='fields'>{name}{route}</div>{self._wf_gate_fields(g, t.get('gate'), t.get('id'))}{checks}{rest}"

    def _wf_transition_panel(self, process: str, st: dict, tid: str, view: str = "diagram") -> str:
        g = st["doc"]["graph"]
        t = wm.transition_map(g).get(tid)
        if t is None:
            return self._wf_overview_panel(process, st)
        anchor = f"t-{tid}"
        head = f"<h2 id='wf-panel-h'>{esc(t['name'])}</h2><p class='wf-rule'>{esc(wm.sentence(g, t))}</p>"
        if not st["editable"]:
            return head
        edit = self._wf_form(process, "edit_transition", self._wf_transition_fields(g, t), key="edit-transition", button=f"{icon('check')}Save the transition",
                             view=view, hidden={"id": tid}, anchor=anchor, btn_cls="")
        reset = ""
        if st["doc"]["layout"].get(wm.label_key(tid)):
            reset = self._wf_form(process, "reset_label", "", key="reset-label", button="Put the label back in its place", view=view, hidden={"id": tid}, anchor=anchor)
        delete = self._wf_form(process, "delete_transition", "", key="delete-transition", button=f"{icon('x')}Delete the transition", view=view,
                               hidden={"id": tid}, btn_cls="reject")
        return head + edit + reset + delete

    def _wf_add_status_form(self, process: str, st: dict, view: str = "diagram", dialog: bool = False) -> str:
        cat_opts = [(c, wm.CATEGORY_WORDS[c]) for c in wm.CATEGORIES]
        inner = ("<div class='fields'>" + self._inp("name", "", label="Name", attrs=" required maxlength='60' placeholder='for example Awaiting verification'")
                 + self._sel("category", cat_opts, "in_progress", label="Category")
                 + f"<div data-wf-show='category=done'>{self._sel('outcome', [(o, o) for o in wm.OUTCOMES], 'completed', label='Outcome')}</div>"
                 + self._inp("sla_days", "", label="Due within (days)", kind="number", hint="optional", attrs=" min='1' max='365'")
                 + "<label class='wf-checklist'><input type='checkbox' name='global' value='1'> Allow all statuses to move here</label></div>")
        head = f"<h2 id='wf-dlg-h'>{icon('plus')}Add status</h2>" if dialog else ""
        return head + self._wf_form(process, "add_status", inner, key="add-status", button=f"{icon('plus')}Add the status", view=view, btn_cls="")

    def _wf_add_transition_form(self, process: str, st: dict, view: str = "diagram", dialog: bool = False) -> str:
        g = st["doc"]["graph"]
        head = f"<h2 id='wf-dlg-h'>{icon('plus')}Add transition</h2>" if dialog else ""
        return head + self._wf_form(process, "add_transition", self._wf_transition_fields(g, None, fresh=True), key="add-transition",
                                    button=f"{icon('plus')}Add the transition", view=view, btn_cls="")

    def _wf_panel_for(self, process: str, st: dict, sel: str, view: str = "diagram") -> str:
        sel = sel or ""
        if sel.startswith("s:"):
            return self._wf_status_panel(process, st, sel[2:], view)
        if sel.startswith("t:"):
            return self._wf_transition_panel(process, st, sel[2:], view)
        return self._wf_overview_panel(process, st)

    def _wf_dialog_html(self, process: str, st: dict, what: str) -> str:
        close = "<button type='button' class='ghost close' data-wf-close aria-label='Close'>" + icon("x") + "</button>"
        if what == "add-status":
            return close + self._wf_add_status_form(process, st, dialog=True)
        if what == "add-transition":
            return close + self._wf_add_transition_form(process, st, dialog=True)
        if what.startswith("delete:"):
            return close + self._wf_delete_status_form(process, st, what[7:], dialog=True)
        return close + "<p>Nothing to show.</p>"

    def page_workflow_panel(self):
        self._wf_member()
        process = self._wf_process(self.arg)
        st = self._wf_state(process)
        what = self.q.get("sel", [""])[0]
        if what.startswith(("add-", "delete:")):
            if not st["editable"]:
                raise ShellError("Only document controllers and policy owners change workflows.", 403)
            return self._json({"html": self._wf_dialog_html(process, st, what)})
        return self._json({"html": self._wf_panel_for(process, st, what)})

    # ------------------------------------------------------------ pages
    def page_workflows(self):
        self._wf_member()
        if self.arg:
            return self._wf_page(self._wf_process(self.arg))
        user = self.user
        drafts = wfs.store(self.hub).drafts(user) if self._wf_editor() else {}
        rows = []
        for p in wfs.processes(self.bundle):
            g = wfs.graph_in_force(self.hub, p, self.bundle)
            entry = wfs.layout_entry(self.hub, p)
            mine = ""
            if p in drafts:
                d = drafts[p]
                n = len(wm.diff(d["base"]["graph"], d["doc"]["graph"], old_layout=d["base"]["layout"], new_layout=d["doc"]["layout"]))
                mine = f"<span class='chip warn'>draft with {wm.plural(n, 'change')}</span>"
            rows.append([f"<a href='{self._wf_url(p)}'>{esc(self._wf_title(p))}</a> <code>{esc(p)}</code>",
                         f"{wm.plural(len(g['statuses']), 'status')}, {wm.plural(len(g['transitions']), 'transition')}",
                         (f"layout v{int(entry['version'])} <span class='why'>{self._co_link(entry.get('released_with'))}</span>" if entry else "<span class='muted'>tidy's</span>"),
                         mine, f"<a class='button ghost' href='{self._wf_url(p)}'>{icon('edit' if self._wf_editor() else 'eye')}{'Edit' if self._wf_editor() else 'View'}</a>"])
        body = (f"<h1>{icon('workflow', cls='lg')} Workflows</h1><p class='why'>How items of each kind move: their statuses and the transitions "
                "between them, who decides each, what is checked and what runs after. A change is drafted in the workflow editor and carried on "
                "a change order; it comes into force when that change order is released.</p>"
                + table(["Process", "Graph", "Layout in force", "Your draft", ""], rows, "No processes in the release in force."))
        if not self._wf_editor():
            body += "<p class='why'>You can read the workflows. Document controllers and policy owners change them.</p>"
        self._page("Workflows", body, crumbs=[("Settings", href("/settings", user)), ("Workflows", None)])

    def _wf_page(self, process: str, status: int = 200):
        user = self.user
        view = self.q.get("view", ["diagram"])[0]
        if view not in ("diagram", "text", "changes"):
            view = "diagram"
        st = self._wf_state(process)
        doc, base = st["doc"], st["base"]
        title = self._wf_title(process)
        n_changes = len(st["delta"])
        tabs = "".join(f"<a href='{esc(self._wf_url(process, k))}'{' aria-current=\"page\"' if view == k else ''}>{icon(ic)}{label}</a>"
                       for k, label, ic in (("diagram", "Diagram", "workflow"), ("text", "Text", "list"),
                                            ("changes", f"Changes <b id='wf-nchanges' class='count'>{n_changes}</b>", "layers")))
        head = f"<h1>{icon('workflow', cls='lg')} {esc(title)} workflow <span class='muted'>{esc(self._version_label((self.meta or {}).get('digest')))} in force</span></h1>"
        if st["editable"]:
            if st["stale"]:
                head += (f"<div class='card warn' role='status'><b>The workflow in force changed since you started this draft</b> (another change order "
                         f"was released). Discard the draft and start again from the workflow in force. {self._wf_discard_form(process)}</div>")
            elif st["draft"]:
                head += (f"<p class='why'>Your draft, saved {esc(when(st['draft'].get('updated_at') or ''))}: {wm.plural(n_changes, 'change')} from the "
                         "workflow in force. Every edit is saved as you go; carry it on a change order to have it reviewed and released.</p>")
            else:
                head += ("<p class='why'>This is the workflow in force. Your first edit starts your own draft (saved as you go); nothing changes for "
                         "anyone until a change order carrying it is released.</p>")
        else:
            head += "<div class='card quiet'>You are reading the workflow in force. Document controllers and policy owners change it.</div>"
        body = head + f"<nav class='chips wf-tabs' aria-label='Views'>{tabs}</nav>"
        if view == "text":
            body += self._wf_text_view(process, st)
        elif view == "changes":
            body += self._wf_changes_view(process, st)
        else:
            body += self._wf_diagram_view(process, st)
        if st["editable"] and st["draft"] and not st["stale"]:
            body += self._wf_carry_form(process) + self._wf_discard_form(process)
        body += self._wf_history(process)
        self._page(f"{title} workflow", body, status=status, crumbs=[("Settings", href("/settings", user)), ("Workflows", href("/workflows", user)), (title, None)])

    def _wf_tool_form(self, process: str, op: str, label: str, ic: str, *, disabled: bool = False, attr: str = "", hotkey: str = "", view: str = "diagram") -> str:
        hk = f" data-hotkey='{hotkey}'" if hotkey else ""
        return (f"<form method='post' action='/workflow-op' data-wf-op>{hidden_as(self.user)}<input type='hidden' name='process' value='{esc(process)}'>"
                f"<input type='hidden' name='op' value='{op}'><input type='hidden' name='back' value='{view}'>"
                f"<button type='submit' class='ghost' {attr}{hk}{' disabled' if disabled else ''}>{icon(ic)}{label}"
                + (f"<kbd class='hk'>{hotkey}</kbd>" if hotkey else "") + "</button></form>")

    def _wf_toolbar(self, process: str, st: dict, view: str = "diagram") -> str:
        if not st["editable"]:
            return ("<div class='toolbar wf-tools wf-jsonly' role='toolbar' aria-label='Diagram'>"
                    + "".join(f"<button type='button' class='ghost' data-wf-zoom='{z}'>{icon(ic)}{lab}</button>" for z, lab, ic in
                              (("out", "Zoom out", "chevron-down"), ("in", "Zoom in", "plus"), ("fit", "Fit", "grid"))) + "</div>")
        tools = (f"<a class='button ghost' href='{esc(self._wf_url(process, 'text', 'add-status'))}' data-wf-dialog='add-status' data-hotkey='s'>{icon('plus')}Add status<kbd class='hk'>s</kbd></a>"
                 f"<a class='button ghost' href='{esc(self._wf_url(process, 'text', 'add-transition'))}' data-wf-dialog='add-transition' data-hotkey='t'>{icon('link')}Add transition<kbd class='hk'>t</kbd></a>"
                 "<span class='sep' aria-hidden='true'></span>"
                 + self._wf_tool_form(process, "undo", "Undo", "retry", disabled=not st["can_undo"], attr="data-wf-undo aria-keyshortcuts='Control+Z'", view=view)
                 + self._wf_tool_form(process, "redo", "Redo", "chevron-right", disabled=not st["can_redo"], attr="data-wf-redo aria-keyshortcuts='Control+Shift+Z'", view=view)
                 + "<span class='sep' aria-hidden='true'></span>" + self._wf_tool_form(process, "tidy", "Tidy", "grid", hotkey="y", view=view))
        if view == "diagram":
            tools += ("<span class='sep wf-jsonly' aria-hidden='true'></span>" + "".join(
                f"<button type='button' class='ghost wf-jsonly' data-wf-zoom='{z}' aria-label='{lab}'>{icon(ic)}{lab}</button>"
                for z, lab, ic in (("out", "Zoom out", "chevron-down"), ("in", "Zoom in", "plus"), ("fit", "Fit", "search"))))
        tools += (f"<span class='saved' id='wf-saved' role='status'>{'Draft saved' if st['draft'] else 'No draft yet'}</span>")
        return f"<div class='toolbar wf-tools' role='toolbar' aria-label='Workflow editor'>{tools}</div>"

    def _wf_diagram_view(self, process: str, st: dict) -> str:
        user = self.user
        svg = self._wf_svg(process, st)
        vb = [int(v) for v in svg.split('viewBox="', 1)[1].split('"', 1)[0].split()]
        names = {s["id"]: s["name"] for s in st["doc"]["graph"]["statuses"]}
        data = {"process": process, "as": user, "editable": st["editable"], "vb": vb, "names": names, "items": st["items"], "sel": None,
                "urls": {"op": "/workflow-op", "panel": href("/workflow-panel/" + process, user)},
                "geo": {"W": wm.W, "H": wm.H, "GRID": wm.GRID, "LOOP_H": wm.LOOP_H, "LOOP_STEP": wm.LOOP_STEP, "GLOBAL_DY": wm.GLOBAL_DY,
                        "GLOBAL_PITCH": wm.GLOBAL_PITCH, "START_DX": wm.START_DX}}
        keys = ("<details class='wf-help'><summary>Keyboard</summary><p>Tab reaches each status and transition. Arrows move a status one grid step "
                "(Shift: five); Enter opens it here; C connects from it; Delete deletes it; Escape deselects. Ctrl+Z undoes, Ctrl+Shift+Z redoes; "
                "+ and − zoom, 0 fits. With keyboard shortcuts off, only the arrows, Enter, Delete and Escape work. The "
                f"<a href='{esc(self._wf_url(process, 'text'))}'>Text view</a> makes every change with plain forms.</p></details>")
        side = (f"<aside class='wf-side' aria-label='Selection, checks and changes'><div id='wf-panel' class='wf-panel' aria-live='off'>{self._wf_overview_panel(process, st)}</div>"
                f"<p id='wf-error' class='err' role='alert' hidden></p>"
                f"<h2>{icon('check-circle')}Checks</h2><ul id='wf-checks' class='wf-checks'>{self._wf_checks_html(process, st['checks'])}</ul>"
                f"<h2>{icon('layers')}Changes from the workflow in force</h2><ol id='wf-delta' class='wf-changes'>{self._wf_delta_html(process, st['delta'])}</ol>{keys}</aside>")
        nojs = (f"<p class='why wf-nojs'>Without JavaScript the drawing is a picture: use the <a href='{esc(self._wf_url(process, 'text'))}'>Text view</a> "
                "to read and change every status and transition.</p>")
        return (f"<style>{WF_CSS}{EDITOR_CSS}</style><div id='wf' class='wf-ed'>{self._wf_toolbar(process, st)}{nojs}<div class='wf-grid'>"
                f"<div class='wf-canvas'>{svg}</div>{side}</div>"
                f"<dialog id='wf-dlg' class='wf-dlg' aria-labelledby='wf-dlg-h'><div id='wf-dlg-body'></div></dialog>"
                f"<span id='live-say' class='vh' role='status' aria-live='polite'></span></div>"
                f"<script type='application/json' id='wf-data'>{_json_in_script(data)}</script><script>{WF_JS}</script>")

    def _wf_text_view(self, process: str, st: dict) -> str:
        """Every status with its ways out, every transition with its rules in a sentence, and (for editors) every edit as a
        plain form: the whole editor without JavaScript."""
        g = st["doc"]["graph"]
        out = f"<style>{WF_CSS}{EDITOR_CSS}</style><div id='wf' class='wf-text'>"
        if st["editable"]:
            out += self._wf_toolbar(process, st, view="text")
        out += (f"<h2>{icon('check-circle')}Checks</h2><ul class='wf-checks'>{self._wf_checks_html(process, st['checks'])}</ul>")
        if st["editable"]:
            out += (f"<section class='card' id='add-status'><h2>{icon('plus')}Add status</h2>{self._wf_add_status_form(process, st, view='text')}</section>"
                    f"<section class='card' id='add-transition'><h2>{icon('link')}Add transition</h2>{self._wf_add_transition_form(process, st, view='text')}</section>")
        listed = set()
        out += f"<h2>{icon('workflow')}Statuses</h2>"
        starts = [t for t in g["transitions"] if t.get("type") in ("initial", "global")]
        for s in g["statuses"]:
            outs = [t for t in g["transitions"] if (t.get("type") or "directed") == "directed" and (t.get("from") or [None])[0] == s["id"]]
            tr = ""
            for t in outs:
                listed.add(t["id"])
                tr += self._wf_text_transition(process, st, t)
            more = [t for t in g["transitions"] if t["id"] not in listed and s["id"] in (t.get("from") or [])]
            also = ("<p class='why'>Also leaves by " + ", ".join(self._wf_sel_link(process, "transition", t["id"], t["name"]) for t in more) + ".</p>") if more else ""
            panel = self._wf_status_panel(process, st, s["id"], view="text")
            out += (f"<section class='card' id='s-{esc(s['id'])}'>{panel}<h3>Transitions out of {esc(s['name'])}</h3>"
                    + (tr or "<p class='muted'>None of its own.</p>") + also + "</section>")
        if starts:
            out += f"<h2>{icon('play')}The start, and transitions from any status</h2>"
            for t in starts:
                out += f"<section class='card' id='t-{esc(t['id'])}'>{self._wf_transition_panel(process, st, t['id'], view='text')}</section>"
        smap = st["doc"].get("state_map") or {}
        if smap:
            osm = wm.status_map(st["base"]["graph"])
            out += (f"<h2>{icon('move')}Where items in removed statuses go</h2>"
                    + table(["Removed status", "Items now", "They move to"], [[esc((osm.get(k) or {}).get("name") or k), str(int(st["items"].get(k) or 0)),
                                                                                esc(wm.name_of(g, v))] for k, v in smap.items() if k not in wm.status_map(g)]))
        out += f"<h2>{icon('layers')}Changes from the workflow in force</h2><ol class='wf-changes'>{self._wf_delta_html(process, st['delta'])}</ol></div>"
        return out

    def _wf_text_transition(self, process: str, st: dict, t: dict) -> str:
        g = st["doc"]["graph"]
        if not st["editable"]:
            return f"<div id='t-{esc(t['id'])}'><h4>{esc(t['name'])}</h4><p class='wf-rule'>{esc(wm.sentence(g, t))}</p></div>"
        return (f"<details id='t-{esc(t['id'])}' class='card quiet'><summary><b>{esc(t['name'])}</b> <span class='why'>{esc(wm.sentence(g, t))}</span></summary>"
                f"{self._wf_transition_panel(process, st, t['id'], view='text')}</details>")

    def _wf_changes_view(self, process: str, st: dict) -> str:
        doc, base = st["doc"], st["base"]
        if not st["delta"]:
            return "<p class='muted'>No change from the workflow in force.</p>"
        return diff_html(base["graph"], doc["graph"], base["layout"], doc["layout"], idp="wfd", items=st["delta"], title=self._wf_title(process))

    def _wf_carry_form(self, process: str) -> str:
        user = self.user
        cos = [co for co in self._forms_my_cos()]
        opts = "".join(f"<option value='{esc(c['locator'])}'>{esc(c['number'])} {esc((c['fields'].get('title') or '')[:60])}</option>" for c in cos)
        may_new = authz.check(self.bundle, user, "create", "change-order", CHANGES)["allowed"]
        if not opts and not may_new:
            return "<p class='why'>You have no open change order to carry it on, and may not prepare one.</p>"
        return (f"<form method='post' action='/workflow-carry' class='card sform'>{hidden_as(user)}{self._once()}<input type='hidden' name='process' value='{esc(process)}'>"
                f"<h2>{icon('layers')}Carry on a change order</h2><div class='fields'><label class='f'><span>Change order</span><select name='co'>{opts}"
                + ("<option value='new'>a new change order…</option>" if may_new else "") + "</select></label>"
                + (f"<label class='f'><span>Title of a new change order</span><input name='title' placeholder='{esc(self._wf_title(process))} workflow changes'></label>" if may_new else "")
                + f"</div><div class='actions'><button>{icon('send')}Carry the draft</button><span class='why'>A snapshot of the draft with its changes "
                  "in words: carry it again after a later edit. It comes into force when the change order is released.</span></div></form>")

    def _wf_discard_form(self, process: str) -> str:
        return (f"<form method='post' action='/workflow-discard' class='inline'>{hidden_as(self.user)}{self._once()}<input type='hidden' name='process' value='{esc(process)}'>"
                f"<button class='ghost reject'>{icon('x')}Discard my draft</button></form>")

    def _wf_history(self, process: str) -> str:
        hist = wfs.store(self.hub).history(process)
        if not hist:
            return ""
        rows = [[f"v{int(h.get('version') or 0)}", self._co_link(h.get("released_with")), esc(when(h.get("at") or "")), esc(h.get("by") or ""),
                 ("<ul>" + "".join(f"<li>{esc(c)}</li>" for c in (h.get("changes") or [])[:12]) + "</ul>") if h.get("changes") else "-"] for h in reversed(hist)]
        return f"<h2>{icon('clock')}History</h2>" + table(["Layout", "Change order", "When", "Carried by", "What changed"], rows)

    # ------------------------------------------------------------ the operation endpoint
    def _wf_op_from_form(self, doc: dict) -> tuple[dict, bool]:
        """The operation a form (or the editor's script) asks for, and whether it coalesces with the last one."""
        g = doc["graph"]
        op = self._f("op")
        sid = self._f("id")
        if op == "json":
            try:
                raw = json.loads(self._f("op_json") or "")
            except json.JSONDecodeError:
                raise ShellError("The operation does not parse.") from None
            if not isinstance(raw, dict) or raw.get("op") not in wm.OPS:
                raise ShellError("Unknown operation.")
            return raw, bool(self._f("coalesce"))
        if op in ("undo", "redo", "tidy"):
            return {"op": op}, False
        if op == "add_status":
            return {"op": "add_status", "name": self._f("name"), "category": self._f("category") or "in_progress",
                    "outcome": self._f("outcome") if self._f("category") == "done" else None, "sla_days": self._f("sla_days") or None,
                    "global": bool(self._f("global"))}, False
        if op == "edit_status":
            s = wm.status_map(g).get(sid)
            if not s:
                raise ShellError("That status is no longer in the draft.", 404)
            ops = []
            if self._f("name") and self._f("name") != s["name"]:
                ops.append({"op": "rename_status", "id": sid, "name": self._f("name")})
            cat = self._f("category") or s.get("category")
            outc = self._f("outcome") if cat == "done" else None
            if cat != s.get("category") or (cat == "done" and (outc or "completed") != (s.get("outcome") or "completed")):
                ops.append({"op": "recategorise_status", "id": sid, "category": cat, "outcome": outc})
            days = self._f("sla_days")
            if str(days or "") != str(s.get("sla_days") or ""):
                ops.append({"op": "set_sla", "id": sid, "days": days or None})
            return {"op": "batch", "ops": ops}, False
        if op == "allow_all":
            on = bool(self._f("on"))
            existing = [t for t in g["transitions"] if t.get("type") == "global" and t.get("to") == sid]
            if on and not existing:
                return {"op": "add_transition", "from": "any", "to": sid, "name": wm.name_of(g, sid)}, False
            if not on and existing:
                return {"op": "batch", "ops": [{"op": "delete_transition", "id": t["id"]} for t in existing]}, False
            return {"op": "batch", "ops": []}, False
        if op == "connect":
            to = self._f("to")
            return {"op": "add_transition", "from": [sid], "to": to, "name": self._f("name") or wm.name_of(g, to)}, False
        if op == "add_transition":
            t = self._wf_transition_from_form(g, None)
            return {"op": "add_transition", **t}, False
        if op == "edit_transition":
            old = wm.transition_map(g).get(sid)
            if not old:
                raise ShellError("That transition is no longer in the draft.", 404)
            new = self._wf_transition_from_form(g, old)
            return {"op": "batch", "ops": self._wf_transition_ops(g, old, new)}, False
        if op == "delete_status":
            return {"op": "delete_status", "id": sid, "move_to": self._f("move_to") or None}, False
        if op == "delete_transition":
            return {"op": "delete_transition", "id": sid}, False
        if op == "move":
            try:
                return {"op": "move", "id": sid, "at": [int(float(self._f("x") or 0)), int(float(self._f("y") or 0))]}, False
            except ValueError:
                raise ShellError("Give the position in whole pixels.") from None
        if op == "reset_label":
            return {"op": "move_label", "id": sid, "offset": None}, False
        raise ShellError("Unknown operation.")

    def _wf_transition_from_form(self, g: dict, old: dict | None) -> dict:
        kind = (old or {}).get("type") or "directed"
        out: dict = {"name": self._f("name"), "to": self._f("to") or (old or {}).get("to")}
        if kind != "initial":
            if self._f("any"):
                out["type"] = "global"
                out["from"] = "any"
                out["except"] = None if self._f("except_default") else self._multi("except")
            else:
                out["type"] = "directed"
                out["from"] = self._multi("from")
        else:
            out["type"] = "initial"
        if not out["name"] and out.get("to"):
            out["name"] = wm.name_of(g, out["to"])
        out["gate"] = self._wf_gate_from_form()
        vs = []
        if _csv(self._f("fields_set")):
            vs.append({"fields_set": _csv(self._f("fields_set"))})
        if self._f("visited"):
            vs.append({"status_visited": self._f("visited")})
        if self._f("linked"):
            vs.append({"linked": self._f("linked")})
        if self._f("cond_field"):
            cop = self._f("cond_op") or "=="
            cond = {"field": self._f("cond_field"), "op": cop}
            if cop not in ("set", "empty"):
                cond["value"] = _num_or_text(self._f("cond_value"))
            vs.append({"form_condition": cond})
        seen: set[str] = set()
        for v in (old or {}).get("validators") or []:
            k = next((x for x in v if x in wm.VALIDATORS), None)
            simple = k != "form_condition" or (isinstance(v.get("form_condition"), dict) and "field" in v["form_condition"])
            if not simple or k in seen:
                vs.append(v)  # a check the form does not show (a second of a kind, a combined condition): kept as it is
            elif k:
                seen.add(k)
        out["validators"] = vs
        out["actions"] = [a.strip() for a in (self.form.get("actions") or "").replace(",", "\n").splitlines() if a.strip()]
        out["fields"] = _csv(self._f("fields"))
        return out

    def _wf_transition_ops(self, g: dict, old: dict, new: dict) -> list[dict]:
        tid = old["id"]
        ops = []
        if new.get("name") and new["name"] != old.get("name"):
            ops.append({"op": "rename_transition", "id": tid, "name": new["name"]})
        if old.get("type") != "initial":
            if new["type"] == "global" and old.get("type") != "global":
                ops.append({"op": "set_global", "id": tid, "global": True, "except": new.get("except")})
            elif new["type"] == "directed" and old.get("type") == "global":
                ops.append({"op": "set_global", "id": tid, "global": False, "from": new.get("from") or []})
            elif new["type"] == "directed" and sorted(new.get("from") or []) != sorted(old.get("from") or []):
                ops.append({"op": "reroute_transition", "id": tid, "from": new.get("from") or []})
            elif new["type"] == "global" and new.get("except") != old.get("except"):
                ops.append({"op": "set_except", "id": tid, "except": new.get("except")})
        if new.get("to") and new["to"] != old.get("to"):
            ops.append({"op": "reroute_transition", "id": tid, "to": new["to"]})
        if not wm._same(new["gate"], old.get("gate") or {}):
            ops.append({"op": "set_gate", "id": tid, "gate": new["gate"]})
        if not wm._same(new["validators"], old.get("validators") or []):
            ops.append({"op": "set_validators", "id": tid, "validators": new["validators"]})
        if new["actions"] != list(old.get("actions") or []):
            ops.append({"op": "set_actions", "id": tid, "actions": new["actions"]})
        if new["fields"] != list(old.get("fields") or []):
            ops.append({"op": "set_fields", "id": tid, "fields": new["fields"]})
        return ops

    def post_workflow_op(self):
        as_json = self._f("fmt") == "json"
        try:
            self._wf_require_edit()
            process = self._wf_process(self._f("process"))
        except ShellError as e:
            if as_json:
                return self._json({"ok": False, "error": str(e)}, e.status)
            raise
        if not as_json:
            done = self._once_done()
            if done:
                return self._redirect(done)
        doc, base, draft = wfs.working(self.hub, self.user, process)
        sel = self._f("sel")
        try:
            op, coalesce = self._wf_op_from_form(doc)
            if op.get("op") == "batch" and not op.get("ops"):
                result = {"doc": doc, "op": op, "said": "No change"}
            else:
                result = wfs.edit(self.hub, self.user, process, op, coalesce=coalesce)
        except (ValueError, ShellError) as e:
            msg = str(e)
            msg = msg[:1].upper() + msg[1:] + ("" if msg.endswith(".") else ".")
            log_line(f"workflow op refused process={process} by={self.user} op={self._f('op')}: {msg}")
            if as_json:
                return self._json({"ok": False, "error": msg}, 400)
            self.err = (msg, None)
            self._force_status = 400
            self.q["view"] = [self._f("back") or "text"]
            return self._wf_page(process, status=400)
        prepared = result["op"]
        kind = prepared.get("op")
        if kind == "add_status":
            sel = f"s:{prepared['id']}"
        elif kind == "add_transition":
            sel = f"t:{prepared['id']}"
        elif kind == "batch" and prepared.get("ops") and prepared["ops"][-1].get("op") == "add_transition" and len(prepared["ops"]) == 1:
            sel = f"t:{prepared['ops'][0]['id']}"
        log_line(f"workflow op process={process} by={self.user} op={kind}")
        if not as_json:
            back = self._f("back") or "text"
            anchor = self._f("anchor") or (f"{sel[0]}-{sel[2:]}" if sel and sel[1:2] == ":" else "")
            if kind in ("delete_status", "delete_transition"):
                anchor = ""
            url = href("/workflows/" + process, self.user, view=back if back != "diagram" else None, done=result["said"])
            return self._redirect(self._once_finish(url + (f"#{anchor}" if anchor else "")))
        st = self._wf_state(process)
        exists = (sel.startswith("s:") and sel[2:] in wm.status_map(st["doc"]["graph"])) or (sel.startswith("t:") and sel[2:] in wm.transition_map(st["doc"]["graph"]))
        if not exists:
            sel = None
        inner, vb = self._wf_svg(process, st, sel=sel, inner=True)
        new_el = kind in ("add_status", "add_transition") or (kind == "batch" and sel and sel.startswith("t:") and len(prepared.get("ops") or []) == 1)
        return self._json({"ok": True, "svg": inner, "vb": vb, "said": result["said"], "sel": sel,
                           "panel": self._wf_panel_for(process, st, sel or ""), "focus": "[data-wf-form='edit-transition'] input[name='name']" if new_el and sel and sel.startswith("t:")
                           else ("[data-wf-form='edit-status'] input[name='name']" if new_el and sel else None),
                           "focus_el": False, "reveal": bool(new_el), "checks_html": self._wf_checks_html(process, st["checks"]),
                           "delta_html": self._wf_delta_html(process, st["delta"]), "n_changes": len(st["delta"]),
                           "can_undo": st["can_undo"], "can_redo": st["can_redo"], "names": {s["id"]: s["name"] for s in st["doc"]["graph"]["statuses"]},
                           "items": st["items"], "errors": sum(1 for c in st["checks"] if c["level"] == "error"),
                           "saved": "Draft saved" + (f": {sum(1 for c in st['checks'] if c['level'] == 'error')} error(s) to fix before carrying" if any(c["level"] == "error" for c in st["checks"]) else "")})

    def post_workflow_discard(self):
        self._wf_require_edit()
        done = self._once_done()
        if done:
            return self._redirect(done)
        process = self._wf_process(self._f("process"))
        wfs.discard(self.hub, self.user, process)
        self._redirect(self._once_finish(href("/workflows/" + process, self.user, done=f"Your draft of the {self._wf_title(process).lower()} workflow is discarded.")))

    def post_workflow_carry(self):
        self._wf_require_edit()
        user = self.user
        done = self._once_done()
        if done:
            return self._redirect(done)
        process = self._wf_process(self._f("process"))
        ref = self._f("co")
        if ref == "new":
            d = authz.check(self.bundle, user, "create", "change-order", CHANGES)
            if not d["allowed"]:
                raise ShellError(f"You cannot prepare a change order: {d['why']}", 403)
            title = self._f("title") or f"{self._wf_title(process)} workflow changes"
            co = self.hub.kernel.create("change-order", CHANGES, {"title": title, "description": f"Changes to the {self._wf_title(process).lower()} workflow.",
                                                                 "implements": [], "requested_by": user}, by=user)
        else:
            co = self._co_get(ref)
            if not (self._co_mine(co) and self._co_preparing(co)):
                raise ShellError("Only its requester or a document controller changes a change order, while it is being prepared.", 403)
        try:
            carried = wfs.carry(self.hub, co["locator"], user, process)
        except ValueError as e:
            raise ShellError(str(e)[:1].upper() + str(e)[1:] + ".") from None
        log_line(f"workflow carried process={process} on={co['number']} by={user} changes={len(carried['delta'])}")
        self._redirect(self._once_finish(self._co_href(co) + "&done=" + quote(
            f"The {self._wf_title(process).lower()} workflow is carried on {co['number']}: {wm.plural(len(carried['delta']), 'change')}. "
            f"It comes into force when {co['number']} is released.") + "#co-workflows"))

    def post_workflow_uncarry(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        co = self._co_get(self._f("co"))
        if not (self._co_mine(co) and self._co_preparing(co)):
            raise ShellError("Only its requester or a document controller changes a change order, while it is being prepared.", 403)
        process = self._f("process")
        try:
            wfs.uncarry(self.hub, co["locator"], process, by=self.user)
        except ValueError as e:
            raise ShellError(str(e)[:1].upper() + str(e)[1:] + ".") from None
        self._redirect(self._once_finish(self._co_href(co) + "&done=" + quote(f"The {process} workflow is no longer carried on {co['number']}.")))

    # ------------------------------------------------------------ elsewhere: record pages, Settings, change orders
    def _workflow_card(self, rec: dict, process: str | None) -> str:
        """The read-only drawing on a record's page: the current status highlighted and the path the item took."""
        if not process or process not in wfs.processes(self.bundle):
            return ""
        try:
            g = wfs.graph_in_force(self.hub, process, self.bundle)
        except KeyError:
            return ""
        layout = wfs.layout_in_force(self.hub, process, g)
        moves = []
        for e in self.hub.kernel.events(aggregate=rec["locator"]):
            if e["type"] == "record.transitioned":
                moves.append((e["data"].get("from"), e["data"].get("to")))
        seen, taken = path_taken(g, moves)
        if not moves:
            seen = {rec["state"]}
        svg = render(g, layout, idp="wfr", mode="view", current=rec["state"], seen=seen, taken=taken)
        legend = ("<p class='why'>The current status has a heavy outline; statuses the item has been in are shaded and the transitions it took are "
                  f"drawn in the accent colour. <a href='{href('/workflows/' + process, self.user)}'>Open the workflow</a>.</p>")
        return f"<style>{WF_CSS}</style><div class='wf-card' id='wf-diagram'>{svg}{legend}</div>"

    def _workflows_settings_html(self) -> str:
        user = self.user
        out = (f"<div class='card quiet'>Each process's workflow is drawn below as it is in force. "
               f"{'Change one in the ' if self._wf_editor() else 'Read each in the '}<a href='{href('/workflows', user)}'>workflow editor</a>"
               f"{': a draft carried on a change order, like a form change.' if self._wf_editor() else '.'}</div><style>{WF_CSS}</style>")
        return out

    def _workflow_thumb(self, process: str) -> str:
        g = wfs.graph_in_force(self.hub, process, self.bundle)
        return render(g, wfs.layout_in_force(self.hub, process, g), idp=f"wfs-{process}", mode="view")

    def _workflows_co_section(self, co: dict, mine: bool, preparing: bool) -> str:
        """What a change order carries for workflows: each process's visual diff with its linked Changes list, and the
        state map (old status, items now, where they go, what that means)."""
        user = self.user
        carried = wfs.changes_of(co)
        out = f"<h2 id='co-workflows'>{icon('workflow')}Workflows</h2>"
        if not carried:
            out += "<p class='muted'>No workflow change. Draft one in the workflow editor and carry it here.</p>"
            if preparing and mine and self._wf_editor():
                out += f"<p class='why'><a href='{href('/workflows', user)}'>{icon('workflow')}Change a workflow</a>, then carry the draft on this change order.</p>"
            return out
        for process, c in sorted(carried.items()):
            try:
                live = wfs.graph_in_force(self.hub, process, self.bundle)
                live_layout = wfs.layout_in_force(self.hub, process, live)
            except KeyError:
                live, live_layout = None, {}
            stale = wfs._base_changed(self.hub, process, c)
            rm = (f"<form method='post' action='/workflow-uncarry' class='inline'>{hidden_as(user)}{self._once()}<input type='hidden' name='co' value='{esc(co['locator'])}'>"
                  f"<input type='hidden' name='process' value='{esc(process)}'><button class='ghost'>{icon('x')}Remove</button></form>" if preparing and mine else "")
            out += (f"<h3><a href='{href('/workflows/' + process, user)}'>{esc(self._wf_title(process))} workflow</a> {rm}</h3>"
                    f"<p class='why'>Carried by {self._avatar(c.get('by') or '', size='sm', named=True)} {esc(when(c.get('at') or ''))}: "
                    f"{wm.plural(len(c.get('delta') or []), 'change')}" + (", layout only (no effect on how items move)" if c.get("layout_only") else "") + ".</p>")
            if stale:
                out += "<div class='card warn' role='status'>The workflow in force changed since this was carried: the person who carried it carries the draft again.</div>"
            base = live if (live is not None and not stale) else None
            if base is not None:
                out += diff_html(base, c["graph"], live_layout, c["layout"], idp=f"cowf-{process}", items=c.get("delta"), title=self._wf_title(process), script=False)
            else:
                out += "<ol class='wf-changes'>" + "".join(f"<li>{esc(i.get('text'))}</li>" for i in c.get("delta") or []) + "</ol>"
            smap = c.get("state_map") or {}
            removed = {k: v for k, v in smap.items() if k not in wm.status_map(c["graph"])}
            if removed and base is not None:
                rows = []
                osm = wm.status_map(base)
                now = wfs.items_by_status(self.hub, base)
                rt = base.get("record_type") or ""
                for old, new in removed.items():
                    n = int(now.get(old) or 0)
                    target = wm.status_map(c["graph"]).get(new) or {}
                    waiting = [t["name"] for a, t, b in wm.edges(base) if a == old and wm.gated(t)]
                    nxt = [t for a, t, b in wm.edges(c["graph"]) if a == new and wm.gated(t)]
                    cons = []
                    if waiting:
                        cons.append("the waiting decision (" + ", ".join(waiting) + ") is withdrawn")
                    if nxt:
                        cons.append("then waits for " + "; ".join(wm.gate_words(c["graph"], t.get("gate")) for t in nxt[:2]))
                    need = sorted({f for t in c["graph"]["transitions"] if t.get("to") == new for v in t.get("validators") or [] for f in v.get("fields_set") or []})
                    if need:
                        cons.append("items that lack " + ", ".join(need) + " move without them (accept the warning)")
                    rows.append([esc((osm.get(old) or {}).get("name") or old),
                                 (f"<a href='{href('/items/' + rt, user, state=old)}'>{n}</a>" if n and rt else str(n)),
                                 esc(target.get("name") or new), esc("; ".join(cons) or "no decision is waiting")])
                out += ("<h4>Items in removed statuses</h4>" + table(["Old status", "Items now", "Move to", "Consequences"], rows)
                        + "<p class='why'>The release moves them without running checks or actions, withdraws the waiting decisions and records the "
                          "move on each item.</p>")
        out += f"<script>{DIFF_JS}</script>"
        return out
