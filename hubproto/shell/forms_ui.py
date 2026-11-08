"""Forms: each record type's form layout, edited in a drag-and-drop editor and released through a change order
(hubproto.formlayout).

``/forms`` lists every record type with the layout in force (the hub's own until a change order releases one), the
change order that released it and your draft. ``/forms/<type>`` is the editor for document controllers and policy
owners: sections of field cards to drag between and reorder (or move with the buttons and the "Move to" select), a
palette of fields not on the form and new custom fields, each field's label, help, placeholder, widget and
conditions (shown, required, read-only when ...), the start form, and live checking: errors, warnings and the
changes in words after each edit. A draft is saved per person; "Carry on a change order" puts it on one of your open
change orders (or a new one), and releasing that change order brings it into force. Everyone else sees the layout in
force and its history.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from .. import authz, eco, formlayout
from .forms import REQUIRED_EXTRA, ShellError, widget
from .handler import details, hidden_as, href, log_line, table
from .icons import icon
from .text import esc, field_label, type_name, when

EDIT_ROLES = ("document-controller", "policy-owner")
CHANGES = "Quality/Changes"
COND_KEYS = (("visible_when", "Shown", "always"), ("required_when", "Required", "only as the record type says"),
             ("readonly_when", "Read-only", "never"))

FORMS_JS = r"""(function(){
var root=document.getElementById('fe');if(!root)return;
var D=JSON.parse(document.getElementById('fe-data').textContent);var L=D.layout;
L.sections=L.sections||[];L.start=L.start||[];L.custom_fields=L.custom_fields||{};L.hidden=L.hidden||[];
var sel=null,dirty=false,timer=null,words={};
function q(s,r){return (r||root).querySelector(s)}function qa(s,r){return Array.prototype.slice.call((r||root).querySelectorAll(s))}
function h(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function customOn(f){var c=L.custom_fields[f];return c&&!c.retired}
function allFields(){var o=Object.keys(D.fields);Object.keys(L.custom_fields).forEach(function(f){if(customOn(f)&&o.indexOf(f)<0)o.push(f)});return o}
function spec(f){return D.fields[f]||(L.custom_fields[f]||{}).type||'text'}
function where(f){for(var i=0;i<L.sections.length;i++){var fs=L.sections[i].fields||[];for(var j=0;j<fs.length;j++)if(fs[j].field===f)return[i,j]}return null}
function row(f){var w=where(f);return w?L.sections[w[0]].fields[w[1]]:null}
function label(f){var r=row(f);return (r&&r.label)||(L.custom_fields[f]||{}).label||D.labels[f]||f}
function placed(){var s={};L.sections.forEach(function(x){(x.fields||[]).forEach(function(r){s[r.field]=1})});return s}
function hide(f){if(L.hidden.indexOf(f)<0)L.hidden.push(f)}function unhide(f){L.hidden=L.hidden.filter(function(x){return x!==f})}
function take(f){var w=where(f);if(!w)return null;var r=L.sections[w[0]].fields.splice(w[1],1)[0];hide(f);if(sel===f)sel=null;return r}
function put(f,si,idx){var w=where(f),r={field:f};if(w){r=L.sections[w[0]].fields.splice(w[1],1)[0];if(w[0]===si&&w[1]<idx)idx--}
 else if(customOn(f)&&L.custom_fields[f].label)r.label=L.custom_fields[f].label;
 var fs=L.sections[si].fields=L.sections[si].fields||[];if(idx==null||idx>fs.length)idx=fs.length;fs.splice(idx,0,r);unhide(f)}
function slug(t){return (t||'').toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,'').slice(0,40)}
function changed(){dirty=true;q('#fe-saved').textContent='Not saved yet.';render();clearTimeout(timer);timer=setTimeout(check,350)}
function badges(r){var b='';if(r.visible_when)b+='<span class="chip info" title="shown only sometimes">shown when…</span>';if(r.required_when)b+='<span class="chip warn" title="required sometimes">required when…</span>';
 if(r.readonly_when)b+='<span class="chip" title="read-only sometimes">read-only when…</span>';if(r.widget)b+='<span class="chip">'+h(r.widget)+'</span>';return b}
function secOptions(cur){return L.sections.map(function(s,i){return '<option value="'+i+'"'+(i===cur?' selected':'')+'>'+h(s.title||('Section '+(i+1)))+'</option>'}).join('')}
function renderCanvas(){var c=q('#fe-canvas');c.innerHTML=L.sections.map(function(s,i){
 var cards=(s.fields||[]).map(function(r,j){var f=r.field,lab=h(label(f));return '<li class="fe-card'+(sel===f?' sel':'')+'" draggable="'+(D.editable?'true':'false')+'" data-field="'+h(f)+'">'
  +'<span class="fe-grip" aria-hidden="true">⠿</span><span class="fe-what"><b>'+lab+'</b> <code>'+h(f)+'</code> <span class="chip">'+h(spec(f))+'</span> '+badges(r)+'</span>'
  +(D.editable?'<span class="fe-ctl"><button type="button" class="ghost" data-act="up" aria-label="Move '+lab+' up">↑</button><button type="button" class="ghost" data-act="down" aria-label="Move '+lab+' down">↓</button>'
  +'<select data-act="to" aria-label="Move '+lab+' to"><option value="">Move to…</option>'+secOptions(-1)+'<option value="off">off the form</option></select>'
  +'<button type="button" class="ghost" data-act="props" aria-label="Properties of '+lab+'">Properties</button></span>':'')+'</li>'}).join('');
 return '<div class="fe-sec" data-sec="'+i+'"><div class="fe-sechead">'+(D.editable?'<input class="fe-sectitle" data-sec="'+i+'" value="'+h(s.title)+'" aria-label="Section title">'
  +'<button type="button" class="ghost" data-sact="up" aria-label="Move section up">↑</button><button type="button" class="ghost" data-sact="down" aria-label="Move section down">↓</button>'
  +'<button type="button" class="ghost reject" data-sact="remove">Remove section</button>':'<h3>'+h(s.title)+'</h3>')+'</div>'
  +'<ul class="fe-drop" data-sec="'+i+'" aria-label="Fields in '+h(s.title)+'">'+(cards||'<li class="fe-empty muted">Drop fields here.</li>')+'</ul></div>'}).join('')}
function renderPalette(){var p=placed(),items=allFields().filter(function(f){return !p[f]});
 q('#fe-palette').innerHTML=items.length?items.map(function(f){var cf=L.custom_fields[f],lab=h(label(f));return '<li class="fe-pal" draggable="'+(D.editable?'true':'false')+'" data-field="'+h(f)+'"><span class="fe-grip" aria-hidden="true">⠿</span>'
  +'<span class="fe-what"><b>'+lab+'</b> <code>'+h(f)+'</code> <span class="chip">'+h(spec(f))+'</span>'+(cf?' <span class="chip info">custom</span>':'')+'</span>'
  +(D.editable?'<span class="fe-ctl"><button type="button" class="secondary" data-pact="add" aria-label="Add '+lab+' to the form">Add</button>'
  +(cf?(D.released_custom.indexOf(f)>=0?'<button type="button" class="ghost reject" data-pact="retire">Retire</button>':'<button type="button" class="ghost reject" data-pact="delete">Delete</button>'):'')+'</span>':'')+'</li>'}).join('')
  :'<li class="muted">Every field is on the form.</li>';
 var retired=Object.keys(L.custom_fields).filter(function(f){return L.custom_fields[f].retired});
 q('#fe-retired').innerHTML=retired.length?'Retired (values kept, not offered): '+retired.map(function(f){return '<code>'+h(f)+'</code>'}).join(', '):'';
 var t=q('#fe-addto');if(t)t.innerHTML=secOptions(L.sections.length-1)}
function startNames(){return L.start.map(function(r){return r.field})}
function renderStart(){var on=startNames(),cands=allFields().concat(D.start_inputs.filter(function(f){return allFields().indexOf(f)<0}));
 var rows=L.start.map(function(r,i){var need=D.needed_start.indexOf(r.field)>=0,lab=h(label(r.field));return '<li data-field="'+h(r.field)+'"><label class="check"><input type="checkbox" data-st="on" checked'+(need||!D.editable?' disabled':'')+'> '+lab+'</label>'
  +(need?' <span class="why">needed to start one</span>':'')+(D.editable?' <button type="button" class="ghost" data-st="up" aria-label="Move '+lab+' up">↑</button><button type="button" class="ghost" data-st="down" aria-label="Move '+lab+' down">↓</button>':'')+'</li>'}).join('');
 var off=cands.filter(function(f){return on.indexOf(f)<0}).map(function(f){return '<li data-field="'+h(f)+'"><label class="check"><input type="checkbox" data-st="on"'+(D.editable?'':' disabled')+'> '+h(label(f))+'</label></li>'}).join('');
 q('#fe-start').innerHTML=rows+(off?'<li class="fe-off muted">Not on the start form:</li>'+off:'')}
function condRows(c){if(!c)return{mode:'',rows:[]};var simple=function(x){return x&&!x.all&&!x.any&&!x.not};
 if((c.all||c.any)&&(c.all||c.any).every(simple))return{mode:c.all?'all':'any',rows:(c.all||c.any).slice()};if(simple(c))return{mode:'all',rows:[c]};return{mode:'raw',raw:c}}
function condFrom(mode,rows){if(!mode||!rows.length)return null;if(rows.length===1)return rows[0];var o={};o[mode]=rows;return o}
function opsFor(r){return r.state!==undefined?['in','not in']:D.ops}
function condHtml(k,title,none,r){var cr=condRows(r[k]);var subj=allFields().concat(['(the state)']);
 var head='<fieldset class="fe-cond" data-k="'+k+'"><legend>'+h(title)+'</legend>';
 if(cr.mode==='raw')return head+'<p class="why">'+h(JSON.stringify(cr.raw))+'</p><button type="button" class="ghost" data-c="clear">Clear this condition</button></fieldset>';
 var mode='<select data-c="mode"><option value=""'+(cr.mode===''?' selected':'')+'>'+h(none)+'</option><option value="all"'+(cr.mode==='all'&&cr.rows.length?' selected':'')+'>when all of these hold</option><option value="any"'+(cr.mode==='any'?' selected':'')+'>when any of these hold</option></select>';
 var rows=(cr.mode?cr.rows:[]).map(function(x,i){var isState=x.state!==undefined,s=isState?'(the state)':x.field,op=isState?x.state:x.op;
  var v=Array.isArray(x.value)?x.value.join(', '):(x.value==null?'':x.value);
  return '<div class="fe-crow" data-i="'+i+'"><select data-c="subj" aria-label="Field to test">'+subj.map(function(f){return '<option value="'+h(f)+'"'+(f===s?' selected':'')+'>'+h(f==='(the state)'?f:label(f))+'</option>'}).join('')+'</select>'
  +'<select data-c="op" aria-label="Test">'+opsFor(x).map(function(o){return '<option'+(o===op?' selected':'')+'>'+h(o)+'</option>'}).join('')+'</select>'
  +'<input data-c="value" value="'+h(v)+'" aria-label="Value" placeholder="'+(isState?'open, returned':'value')+'"'+(op==='set'||op==='empty'?' disabled':'')+'><button type="button" class="ghost" data-c="rm" aria-label="Remove this test">×</button></div>'}).join('');
 var w=(words[sel]||{})[k];return head+mode+rows+(cr.mode?'<button type="button" class="ghost" data-c="add">+ another test</button>':'')+(w?'<p class="why fe-words">'+h(title)+' when '+h(w)+'</p>':'')+'</fieldset>'}
function renderProps(){var p=q('#fe-props');if(!sel||!row(sel)){p.innerHTML='<p class="muted">Choose a field’s Properties to change its label, help, widget or conditions.</p>';return}
 var r=row(sel),cf=L.custom_fields[sel],isEnum=cf&&String(cf.type).indexOf('enum:')===0;
 p.innerHTML='<h3>'+h(label(sel))+' <code>'+h(sel)+'</code></h3><div class="fields">'
  +'<label class="f"><span>Label</span><input data-p="label" value="'+h(r.label||'')+'" placeholder="'+h(D.labels[sel]||(cf||{}).label||sel)+'"></label>'
  +'<label class="f"><span>Help</span><input data-p="help" value="'+h(r.help||'')+'"></label>'
  +'<label class="f"><span>Placeholder</span><input data-p="placeholder" value="'+h(r.placeholder||'')+'"></label>'
  +'<label class="f"><span>Widget</span><select data-p="widget">'+[['','as the field type says'],['textarea','several lines (textarea)'],['lines','one item per line']].map(function(o){return '<option value="'+o[0]+'"'+((r.widget||'')===o[0]?' selected':'')+'>'+o[1]+'</option>'}).join('')+'</select></label></div>'
  +(isEnum?'<label class="f"><span>Options <i>(options are only ever added)</i></span><input data-p="options" value="'+h(cf.type.slice(5).split(',').join(', '))+'"></label>':'')
  +D.cond_keys.map(function(c){return condHtml(c[0],c[1],c[2],r)}).join('')
  +'<div class="actions"><button type="button" class="ghost" data-p="close">Done</button></div>'}
function render(){renderCanvas();renderPalette();renderStart();renderProps();wire()}
function feedback(j){var e=q('#fe-errors'),w=q('#fe-warnings'),c=q('#fe-changes');words=j.words||{};
 e.innerHTML=(j.errors||[]).length?'<div class="card crit" role="alert"><b>Fix before it can be carried:</b><ul>'+j.errors.map(function(x){return '<li>'+h(x)+'</li>'}).join('')+'</ul></div>':'<p class="status">No problems.</p>';
 w.innerHTML=(j.warnings||[]).length?'<div class="card warn"><ul>'+j.warnings.map(function(x){return '<li>'+h(x)+'</li>'}).join('')+'</ul></div>':'';
 c.innerHTML=(j.changes||[]).length?j.changes.map(function(x){return '<li>'+h(x)+'</li>'}).join(''):'<li class="muted">No change from the form in force.</li>';
 if(sel)renderProps(),wire()}
function post(url){var b=new URLSearchParams();b.append('as',D.as);b.append('type',D.type);b.append('layout',JSON.stringify(L));
 return fetch(url,{method:'POST',body:b,credentials:'same-origin'}).then(function(r){return r.json()})}
function check(){if(!D.editable)return;post(D.urls.check).then(feedback).catch(function(){q('#fe-errors').innerHTML='<p class="err">Could not check the layout; try again.</p>'})}
function save(){return post(D.urls.save).then(function(j){feedback(j);if(j.ok){dirty=false;q('#fe-saved').textContent='Draft saved.'}else q('#fe-saved').textContent=j.error||'Not saved.';return j})}
function idxAt(list,y){var cards=qa('.fe-card',list);for(var i=0;i<cards.length;i++){var b=cards[i].getBoundingClientRect();if(y<b.top+b.height/2)return i}return cards.length}
function wire(){if(!D.editable)return;
 qa('.fe-card,.fe-pal').forEach(function(c){c.ondragstart=function(e){e.dataTransfer.setData('text/plain',c.getAttribute('data-field'));e.dataTransfer.effectAllowed='move';c.classList.add('dragging')};c.ondragend=function(){c.classList.remove('dragging')}});
 qa('.fe-drop').forEach(function(u){u.ondragover=function(e){e.preventDefault();u.classList.add('over')};u.ondragleave=function(){u.classList.remove('over')};
  u.ondrop=function(e){e.preventDefault();u.classList.remove('over');var f=e.dataTransfer.getData('text/plain');if(!f)return;put(f,+u.getAttribute('data-sec'),idxAt(u,e.clientY));changed()}});
 var pal=q('#fe-palette');pal.ondragover=function(e){e.preventDefault();pal.classList.add('over')};pal.ondragleave=function(){pal.classList.remove('over')};
 pal.ondrop=function(e){e.preventDefault();pal.classList.remove('over');var f=e.dataTransfer.getData('text/plain');if(f&&where(f)){take(f);changed()}};
 qa('.fe-card [data-act]').forEach(function(b){var f=b.closest('.fe-card').getAttribute('data-field'),a=b.getAttribute('data-act');
  if(a==='to'){b.onchange=function(){var v=b.value;if(v==='off')take(f);else if(v!=='')put(f,+v,null);changed()};return}
  b.onclick=function(){var w=where(f);if(a==='props'){sel=f;render();var p=q('#fe-props');if(p.scrollIntoView)p.scrollIntoView({block:'nearest'});return}
   var fs=L.sections[w[0]].fields;if(a==='up'&&w[1]>0){fs.splice(w[1]-1,0,fs.splice(w[1],1)[0])}else if(a==='down'&&w[1]<fs.length-1){fs.splice(w[1]+1,0,fs.splice(w[1],1)[0])}
   else if(a==='up'&&w[0]>0){put(f,w[0]-1,null)}else if(a==='down'&&w[0]<L.sections.length-1){put(f,w[0]+1,0)}changed()}});
 qa('.fe-sectitle').forEach(function(i){i.oninput=function(){L.sections[+i.getAttribute('data-sec')].title=i.value;dirty=true;clearTimeout(timer);timer=setTimeout(check,500)};i.onchange=function(){changed()}});
 qa('[data-sact]').forEach(function(b){var i=+b.closest('.fe-sec').getAttribute('data-sec'),a=b.getAttribute('data-sact');b.onclick=function(){
  if(a==='up'&&i>0)L.sections.splice(i-1,0,L.sections.splice(i,1)[0]);else if(a==='down'&&i<L.sections.length-1)L.sections.splice(i+1,0,L.sections.splice(i,1)[0]);
  else if(a==='remove'){if(L.sections.length<2){alert('A form keeps at least one section.');return}(L.sections[i].fields||[]).forEach(function(r){hide(r.field)});L.sections.splice(i,1)}changed()}});
 qa('#fe-palette [data-pact]').forEach(function(b){var f=b.closest('.fe-pal').getAttribute('data-field'),a=b.getAttribute('data-pact');b.onclick=function(){
  if(a==='add'){var t=q('#fe-addto');put(f,t?+t.value:L.sections.length-1,null)}else if(a==='retire'){L.custom_fields[f].retired=true;take(f);unhide(f)}else if(a==='delete'){delete L.custom_fields[f];take(f);unhide(f);L.start=L.start.filter(function(r){return r.field!==f})}changed()}});
 qa('#fe-start [data-st]').forEach(function(b){var f=b.closest('li').getAttribute('data-field'),a=b.getAttribute('data-st');
  if(a==='on'){b.onchange=function(){if(b.checked){var r={field:f};var fr=row(f);if(fr&&fr.widget)r.widget=fr.widget;L.start.push(r)}else L.start=L.start.filter(function(r){return r.field!==f});changed()};return}
  b.onclick=function(){var i=startNames().indexOf(f);if(a==='up'&&i>0)L.start.splice(i-1,0,L.start.splice(i,1)[0]);else if(a==='down'&&i<L.start.length-1)L.start.splice(i+1,0,L.start.splice(i,1)[0]);changed()}});
 var p=q('#fe-props');qa('[data-p]',p).forEach(function(i){var k=i.getAttribute('data-p');var r=row(sel);if(!r)return;
  if(k==='close'){i.onclick=function(){sel=null;render()};return}
  i.onchange=function(){if(k==='options'){var cf=L.custom_fields[sel],old=cf.type.slice(5).split(',');var nv=i.value.split(',').map(function(x){return x.trim()}).filter(Boolean);
    old.forEach(function(o){if(nv.indexOf(o)<0)nv.unshift(o)});cf.type='enum:'+nv.filter(function(x,j){return nv.indexOf(x)===j}).join(',')}
   else if(i.value)r[k]=i.value;else delete r[k];changed()}});
 qa('.fe-cond',p).forEach(function(fs){var k=fs.getAttribute('data-k'),r=row(sel);if(!r)return;var cr=condRows(r[k]);
  function store(mode,rows){var c=condFrom(mode,rows);if(c)r[k]=c;else delete r[k];changed()}
  function readRows(){return qa('.fe-crow',fs).map(function(d){var s=q('[data-c=subj]',d).value,op=q('[data-c=op]',d).value,v=q('[data-c=value]',d).value;
   var list=v.split(',').map(function(x){return x.trim()}).filter(Boolean);
   if(s==='(the state)')return{state:(op==='not in'?'not in':'in'),value:list};var o={field:s,op:op};if(op==='in'||op==='not in')o.value=list;else if(op!=='set'&&op!=='empty')o.value=v.trim();return o})}
  qa('[data-c]',fs).forEach(function(c){var a=c.getAttribute('data-c');
   if(a==='clear')c.onclick=function(){delete r[k];changed()};
   else if(a==='mode')c.onchange=function(){if(!c.value){store('',[])}else{var rows=cr.mode?readRows():[{field:allFields()[0],op:'set'}];store(c.value,rows)}};
   else if(a==='add')c.onclick=function(){var rows=readRows();rows.push({field:allFields()[0],op:'set'});store(cr.mode||'all',rows)};
   else if(a==='rm')c.onclick=function(){var i=+c.closest('.fe-crow').getAttribute('data-i');var rows=readRows();rows.splice(i,1);store(rows.length?(cr.mode||'all'):'',rows)};
   else c.onchange=function(){var rows=readRows();if(a==='subj'){var d=c.closest('.fe-crow'),i=+d.getAttribute('data-i');rows[i]=c.value==='(the state)'?{state:'in',value:[]}:{field:c.value,op:'set'}}store(cr.mode||'all',rows)}})})}
var nf=q('#fe-new');if(nf){var tsel=q('[name=type]',nf),opt=q('.fe-enum',nf);tsel.onchange=function(){opt.hidden=tsel.value!=='enum'};
 q('[name=label]',nf).oninput=function(){var n=q('[name=name]',nf);if(!n.dataset.touched)n.value='x_'+slug(q('[name=label]',nf).value)};q('[name=name]',nf).oninput=function(){this.dataset.touched='1'};
 nf.onsubmit=function(e){e.preventDefault();var lab=q('[name=label]',nf).value.trim(),name=q('[name=name]',nf).value.trim()||('x_'+slug(lab)),t=tsel.value;
  if(t==='enum'){var o=q('[name=options]',nf).value.split(',').map(function(x){return x.trim()}).filter(Boolean);if(!o.length){alert('Give the options, separated by commas.');return}t='enum:'+o.join(',')}
  if(L.custom_fields[name]||D.fields[name]){alert('There is already a field named '+name+'.');return}
  L.custom_fields[name]={type:t,label:lab||name};put(name,L.sections.length-1,null);nf.reset();q('[name=name]',nf).dataset.touched='';opt.hidden=true;changed()}}
var sb=q('#fe-save');if(sb)sb.onclick=function(){save()};
var addsec=q('#fe-addsec');if(addsec)addsec.onclick=function(){var t=q('#fe-newsec').value.trim();if(!t){q('#fe-newsec').focus();return}var id=slug(t)||('section_'+(L.sections.length+1)),n=1,base=id;
 while(L.sections.some(function(s){return s.id===id}))id=base+'_'+(++n);L.sections.push({id:id,title:t,fields:[]});q('#fe-newsec').value='';changed()};
qa('[data-preview]',document).forEach(function(b){b.onclick=function(){var what=b.getAttribute('data-preview'),rs=document.getElementById('fe-record'),rec=rs?rs.value:'';var go=function(){
  var u=D.urls.preview+(D.urls.preview.indexOf('?')<0?'?':'&')+'what='+what+'&record='+encodeURIComponent(rec);
  fetch(u,{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(j){document.getElementById('fe-preview').innerHTML=j.html||('<p class="err">'+h(j.error||'No preview.')+'</p>')})};
  if(dirty&&D.editable)save().then(go);else go()}});
window.addEventListener('beforeunload',function(e){if(dirty){e.preventDefault();e.returnValue=''}});
render();if(D.editable)feedback(D.check)})();"""


def _json_in_script(data) -> str:
    return json.dumps(data, default=str).replace("</", "<\\/")


class FormsMixin:
    # ------------------------------------------------------------ who, which
    def _forms_editor(self) -> bool:
        return bool(set(EDIT_ROLES) & set(authz.roles_of(self.bundle or {}, self.user)))

    def _forms_types(self) -> list[str]:
        return sorted(t for t, d in self.hub.kernel.types.items() if isinstance(d, dict) and d.get("fields"))

    def _forms_type(self, rt: str) -> str:
        rt = (rt or "").strip("/")
        if rt not in self._forms_types():
            raise ShellError(f"There is no record type '{rt}'.", 404)
        return rt

    def _forms_member(self) -> None:
        self._need_release()
        if not self._is_member():
            raise ShellError("Only members of the organization see the forms.", 403)

    def _forms_require_edit(self) -> None:
        self._forms_member()
        if not self._forms_editor():
            raise ShellError("Only document controllers and policy owners change forms; anyone may read them.", 403)

    def _forms_layout_in(self) -> dict:
        try:
            lay = json.loads(self._f("layout") or "")
        except json.JSONDecodeError as e:
            raise ShellError(f"The layout does not parse: {e}.") from None
        if not isinstance(lay, dict) or not isinstance(lay.get("sections"), list):
            raise ShellError("A layout has a list of sections.")
        for s in lay["sections"]:
            if not isinstance(s, dict) or not isinstance(s.get("fields", []), list):
                raise ShellError("Each section has a list of field rows.")
        return lay

    def _forms_words(self, lay: dict) -> dict:
        out = {}
        for s in lay.get("sections") or []:
            for r in s.get("fields") or []:
                w = {}
                for k, _, _ in COND_KEYS:
                    if r.get(k):
                        try:
                            w[k] = formlayout.describe(r[k])
                        except Exception:  # a malformed condition: validate() says what is wrong with it
                            w[k] = "(not a valid condition)"
                if w:
                    out.setdefault(r["field"], {}).update(w)
        return out

    def _forms_my_cos(self) -> list[dict]:
        """Open change orders this person may change: theirs, or any when they are a document controller."""
        out = []
        for co in self.hub.kernel.query(type_="change-order"):
            if co["state"] in eco.OPEN_STATES and self._co_mine(co) and self._co_preparing(co):
                out.append(co)
        return sorted(out, key=lambda r: r.get("created_at") or "", reverse=True)

    # ------------------------------------------------------------ the list
    def page_forms(self):
        self._forms_member()
        if self.arg:
            return self._forms_editor_page(self._forms_type(self.arg))
        user = self.user
        active = self.hub.forms.active()
        drafts = self.hub.forms.drafts(user)
        rows = []
        for rt in self._forms_types():
            entry = active.get(rt)
            inforce = (f"v{int(entry['version'])} <span class='why'>released with {self._co_link(entry.get('released_with'))}, "
                       f"{esc(when(entry.get('at') or ''))}</span>") if entry else "<span class='muted'>the hub's own</span>"
            mine = ""
            if rt in drafts:
                d = drafts[rt]
                n = len(formlayout.changes(formlayout.active_layout(self.hub, rt), d))
                mine = f"<span class='chip warn'>draft with {n} change(s)</span>"
            rows.append([f"<a href='{href('/forms/' + rt, user)}'>{esc(type_name(rt))}</a> <code>{esc(rt)}</code>", inforce, mine,
                         f"<a class='button ghost' href='{href('/forms/' + rt, user)}'>{icon('edit' if self._forms_editor() else 'eye')}{'Edit' if self._forms_editor() else 'View'}</a>"])
        body = (f"<h1>{icon('form', cls='lg')} Forms</h1><p class='why'>How each kind of record looks and is filled in: its sections, the fields "
                "on them, the start form, and fields shown, required or read-only only when a condition holds (checked by the hub when a "
                "record is saved or moves on). A form change is drafted here and carried on a change order; it comes into force when "
                "that change order is released.</p>" + table(["Record type", "Layout in force", "Your draft", ""], rows, "No record types."))
        if not self._forms_editor():
            body += "<p class='why'>You can read the forms. Document controllers and policy owners change them.</p>"
        self._page("Forms", body, crumbs=[("Settings", href("/settings", user)), ("Forms", None)])

    def _co_link(self, number: str | None) -> str:
        if not number:
            return "-"
        co = next((r for r in self.hub.kernel.query(type_="change-order") if r["number"] == number), None)
        return f"<a href='{self._co_href(co)}'>{esc(number)}</a>" if co and self._can_read(co["folder"])["allowed"] else esc(number)

    # ------------------------------------------------------------ the editor
    def _forms_editor_page(self, rt: str):
        user = self.user
        editable = self._forms_editor()
        has_draft = rt in self.hub.forms.drafts(user)
        lay = formlayout.draft_layout(self.hub, user, rt) if editable else formlayout.active_layout(self.hub, rt)
        active = formlayout.active_layout(self.hub, rt)
        entry = formlayout.active_entry(self.hub, rt)
        cand = formlayout.type_fields(self.hub, rt, lay)
        for name, cf in (lay.get("custom_fields") or {}).items():
            cand.setdefault(name, cf.get("type") or "text")
        base_inputs = [r["field"] for r in formlayout.base_layout(self.hub, rt).get("start") or [] if r.get("input")]
        errors, warnings = formlayout.validate(self.hub, rt, lay)
        records = [r for r in sorted(self.hub.kernel.query(type_=rt), key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True)
                   if self._can_read(r["folder"])["allowed"]][:25]
        data = {"type": rt, "as": user, "editable": editable, "layout": lay,
                "fields": {k: v for k, v in formlayout.type_fields(self.hub, rt).items()},
                "labels": {k: field_label(k) for k in list(cand) + [r["field"] for r in lay.get("start") or []] + base_inputs},
                "ops": list(formlayout.OPS),
                "needed_start": list(REQUIRED_EXTRA.get(rt, [])), "start_inputs": base_inputs,
                "released_custom": sorted(((entry or {}).get("layout") or {}).get("custom_fields") or {}),
                "cond_keys": [list(c) for c in COND_KEYS],
                "check": {"errors": errors, "warnings": warnings, "changes": formlayout.changes(active, lay), "words": self._forms_words(lay)},
                "urls": {"check": "/forms-check", "save": "/forms-save", "preview": href("/forms-preview/" + rt, user)}}
        head = (f"<h1>{icon('form', cls='lg')} {esc(type_name(rt))} form <span class='muted'>"
                + (f"v{int(entry['version'])} in force, released with {self._co_link(entry.get('released_with'))}" if entry else "the hub's own layout in force")
                + "</span></h1>")
        if editable:
            head += ("<p class='why'>Drag fields between sections or use the buttons on each card. Changes are checked as you go; "
                     "save the draft, then carry it on a change order. It comes into force when that change order is released.</p>")
        else:
            head += "<div class='card quiet'>You are reading the form in force. Document controllers and policy owners change it.</div>"
        canvas = (f"<div class='fe-grid'><div class='fe-main'><h2>{icon('layers')}Sections</h2><div id='fe-canvas'></div>"
                  + (f"<div class='sform row fe-addsec'><label class='f'><span>New section</span><input id='fe-newsec' placeholder='for example Traceability'></label>"
                     f"<button type='button' class='secondary' id='fe-addsec'>{icon('plus')}Add section</button></div>" if editable else "")
                  + f"<h2>{icon('form')}Start form</h2><p class='why'>The fields asked for when someone starts a {esc(type_name(rt).lower())}, in order.</p><ol id='fe-start' class='fe-start'></ol>"
                  + "</div><aside class='fe-side'>"
                  + f"<h2>{icon('list')}Fields not on the form</h2><ul id='fe-palette' class='fe-palette' aria-label='Fields not on the form'></ul><p id='fe-retired' class='why'></p>"
                  + ((f"<label class='f fe-addto'><span>Add to section</span><select id='fe-addto'></select></label>"
                      f"<form id='fe-new' class='card fe-newfield'><h3>New custom field</h3><div class='fields'>"
                      "<label class='f'><span>Label</span><input name='label' required placeholder='for example Batch code'></label>"
                      "<label class='f'><span>Name <i>(x_…, never changes)</i></span><input name='name' placeholder='x_batch_code' pattern='x_[a-z][a-z0-9_]{1,40}'></label>"
                      "<label class='f'><span>Type <i>(never changes)</i></span><select name='type'>"
                      + "".join(f"<option value='{t}'>{t}</option>" for t in formlayout.CUSTOM_TYPES) + "<option value='enum'>choice (enum)</option></select></label>"
                      "<label class='f fe-enum' hidden><span>Options <i>(comma separated; only ever added)</i></span><input name='options'></label></div>"
                      f"<div class='actions'><button class='secondary'>{icon('plus')}Add the field</button></div></form>") if editable else "")
                  + f"<div id='fe-props' class='card fe-props'></div>"
                  + (f"<h2>{icon('check-circle')}Check</h2><div id='fe-errors'></div><div id='fe-warnings'></div><h3>Changes from the form in force</h3><ul id='fe-changes' class='fe-changes'></ul>"
                     f"<div class='actions'><button type='button' id='fe-save'>{icon('check')}Save draft</button><span id='fe-saved' class='why' role='status'>"
                     + ("Your draft." if has_draft else "Nothing saved yet.") + "</span></div>" if editable else "<span id='fe-saved' hidden></span>")
                  + "</aside></div>")
        body = head + f"<div id='fe' class='fe'>{canvas}</div>"
        if editable:
            if has_draft:
                body += (f"<form method='post' action='/forms-discard' class='inline'>{hidden_as(user)}{self._once()}<input type='hidden' name='type' value='{esc(rt)}'>"
                         f"<button class='ghost reject'>{icon('x')}Discard my draft</button></form>")
            body += self._forms_carry_form(rt, has_draft)
        rec_opts = "".join(f"<option value='{esc(r['locator'])}'>{esc(r['number'])} {esc((r['fields'].get('title') or r['fields'].get('name') or '')[:60])}</option>" for r in records)
        body += (f"<section class='card fe-previewbox'><h2>{icon('eye')}Preview</h2>"
                 + (f"<label class='f'><span>A {esc(type_name(rt).lower())} to show</span><select id='fe-record'>{rec_opts}</select></label>"
                    f"<div class='actions'><button type='button' class='secondary' data-preview='sheet'>{icon('eye')}Preview the record</button>" if records else
                    "<p class='why'>No record of this type you may read yet; the start form can still be previewed.</p><div class='actions'>")
                 + f"<button type='button' class='secondary' data-preview='start'>{icon('form')}Preview the start form</button></div><div id='fe-preview' class='fe-preview'></div></section>")
        body += self._forms_history(rt)
        body += f"<script type='application/json' id='fe-data'>{_json_in_script(data)}</script><script>{FORMS_JS}</script>"
        self._page(f"{type_name(rt)} form", body, crumbs=[("Settings", href("/settings", user)), ("Forms", href("/forms", user)), (type_name(rt), None)])

    def _forms_carry_form(self, rt: str, has_draft: bool) -> str:
        user = self.user
        if not has_draft:
            return "<p class='why'>Save a draft to carry it on a change order.</p>"
        cos = self._forms_my_cos()
        opts = "".join(f"<option value='{esc(c['locator'])}'>{esc(c['number'])} {esc((c['fields'].get('title') or '')[:60])}</option>" for c in cos)
        may_new = authz.check(self.bundle, user, "create", "change-order", CHANGES)["allowed"]
        if not opts and not may_new:
            return "<p class='why'>You have no open change order to carry it on, and may not prepare one.</p>"
        return (f"<form method='post' action='/forms-carry' class='card sform fe-carry'>{hidden_as(user)}{self._once()}<input type='hidden' name='type' value='{esc(rt)}'>"
                f"<h2>{icon('layers')}Carry on a change order</h2><div class='fields'><label class='f'><span>Change order</span><select name='co'>{opts}"
                + ("<option value='new'>a new change order…</option>" if may_new else "") + "</select></label>"
                + (f"<label class='f'><span>Title of a new change order</span><input name='title' placeholder='{esc(type_name(rt))} form changes'></label>" if may_new else "")
                + f"</div><div class='actions'><button>{icon('send')}Carry the draft</button><span class='why'>A snapshot of the saved draft: carry it again after a later edit.</span></div></form>")

    def _forms_history(self, rt: str) -> str:
        hist = self.hub.forms.history(rt)
        if not hist:
            return f"<h2>{icon('clock')}History</h2><p class='muted'>No layout released yet: the hub's own form is in force.</p>"
        rows = [[f"v{int(h.get('version') or 0)}", self._co_link(h.get("released_with")), esc(when(h.get("at") or "")), esc(h.get("by") or ""),
                 "<ul>" + "".join(f"<li>{esc(c)}</li>" for c in (h.get("changes") or [])) + "</ul>" if h.get("changes") else "<span class='muted'>-</span>"]
                for h in reversed(hist)]
        return f"<h2>{icon('clock')}History</h2>" + table(["Version", "Change order", "When", "Carried by", "What changed"], rows)

    def page_forms_preview(self):
        self._forms_member()
        rt = self._forms_type(self.arg)
        lay = formlayout.draft_layout(self.hub, self.user, rt) if self._forms_editor() else formlayout.active_layout(self.hub, rt)
        what = self.q.get("what", ["sheet"])[0]
        if what == "start":
            td = self.hub.kernel.types.get(rt, {}).get("fields", {})
            spec_of = lambda r: r.get("spec") or td.get(r["field"]) or (((lay.get("custom_fields") or {}).get(r["field"]) or {}).get("type"))
            need = set(REQUIRED_EXTRA.get(rt, []))
            inputs = "".join(widget(r["field"], spec_of(r), "", required=r["field"] in need, kind=r.get("widget"),
                                    label=(formlayout.row_of(lay, r["field"]).get("label") or ((lay.get("custom_fields") or {}).get(r["field"]) or {}).get("label")))
                             for r in lay.get("start") or [])
            return self._json({"html": f"<form class='fe-startpreview' onsubmit='return false'><div class='fields'>{inputs}</div>"
                                       "<p class='why'>The start form as the draft has it (not sent anywhere).</p></form>"})
        ref = self.q.get("record", [""])[0]
        rec = self.hub.kernel.get(ref) if ref else None
        if rec is None or rec["type"] != rt or not self._can_read(rec["folder"])["allowed"]:
            return self._json({"error": f"Choose a {type_name(rt).lower()} you may read."}, 404)
        lay, _ = formlayout.reconcile(self.hub, rt, lay)
        return self._json({"html": f"<div class='sheet fe-sheetpreview'><div class='subtitle'><b>{esc(rec['number'])}</b> "
                                   f"{esc((rec['fields'].get('title') or '')[:80])}</div>{self._layout_sheet(rec, layout=lay)}</div>"})

    # ------------------------------------------------------------ actions (JSON)
    def _forms_post_json(self, fn):
        try:
            self._forms_require_edit()
            return self._json(fn())
        except ShellError as e:
            return self._json({"ok": False, "error": str(e)}, e.status)

    def post_forms_check(self):
        def run():
            rt = self._forms_type(self._f("type"))
            lay = self._forms_layout_in()
            errors, warnings = formlayout.validate(self.hub, rt, lay)
            return {"ok": True, "errors": errors, "warnings": warnings, "changes": formlayout.changes(formlayout.active_layout(self.hub, rt), lay),
                    "words": self._forms_words(lay)}
        return self._forms_post_json(run)

    def post_forms_save(self):
        def run():
            rt = self._forms_type(self._f("type"))
            out = formlayout.save_draft(self.hub, self.user, rt, self._forms_layout_in())
            log_line(f"form draft saved type={rt} by={self.user} changes={len(out['changes'])} errors={len(out['errors'])}")
            return {"ok": True, "errors": out["errors"], "warnings": out["warnings"], "changes": out["changes"], "words": self._forms_words(out["layout"])}
        return self._forms_post_json(run)

    # ------------------------------------------------------------ actions (forms)
    def post_forms_discard(self):
        self._forms_require_edit()
        done = self._once_done()
        if done:
            return self._redirect(done)
        rt = self._forms_type(self._f("type"))
        self.hub.forms.discard_draft(self.user, rt)
        self._redirect(self._once_finish(href("/forms/" + rt, self.user, done=f"Your draft of the {type_name(rt).lower()} form is discarded.")))

    def post_forms_carry(self):
        self._forms_require_edit()
        user = self.user
        done = self._once_done()
        if done:
            return self._redirect(done)
        rt = self._forms_type(self._f("type"))
        ref = self._f("co")
        if ref == "new":
            d = authz.check(self.bundle, user, "create", "change-order", CHANGES)
            if not d["allowed"]:
                raise ShellError(f"You cannot prepare a change order: {d['why']}", 403)
            title = self._f("title") or f"{type_name(rt)} form changes"
            co = self.hub.kernel.create("change-order", CHANGES, {"title": title, "description": f"Changes to the {type_name(rt).lower()} form.",
                                                                 "implements": [], "requested_by": user}, by=user)
        else:
            co = self._co_get(ref)
            if not (self._co_mine(co) and self._co_preparing(co)):
                raise ShellError("Only its requester or a document controller changes a change order, while it is being prepared.", 403)
        try:
            carried = formlayout.carry(self.hub, co["locator"], user, rt)
        except ValueError as e:
            raise ShellError(str(e)[:1].upper() + str(e)[1:] + ".") from None
        log_line(f"form carried type={rt} on={co['number']} by={user} changes={len(carried.get('changes') or [])}")
        self._redirect(self._once_finish(self._co_href(co) + "&done=" + quote(
            f"The {type_name(rt).lower()} form is carried on {co['number']}: {len(carried.get('changes') or [])} change(s). It comes into force when {co['number']} is released.")))

    def post_forms_uncarry(self):
        self._need_release()
        done = self._once_done()
        if done:
            return self._redirect(done)
        co = self._co_get(self._f("co"))
        if not (self._co_mine(co) and self._co_preparing(co)):
            raise ShellError("Only its requester or a document controller changes a change order, while it is being prepared.", 403)
        rt = self._f("type")
        formlayout.uncarry(self.hub, co["locator"], rt, by=self.user)
        self._redirect(self._once_finish(self._co_href(co) + "&done=" + quote(f"The {type_name(rt).lower()} form is no longer carried on {co['number']}.")))

    # ------------------------------------------------------------ on a change order
    def _forms_co_section(self, co: dict, mine: bool, preparing: bool) -> str:
        user = self.user
        carried = co["fields"].get("form_changes") or {}
        rows = []
        for rt, c in sorted(carried.items()):
            changes = c.get("changes") or []
            rm = (f"<form method='post' action='/forms-uncarry' class='inline'>{hidden_as(user)}{self._once()}<input type='hidden' name='co' value='{esc(co['locator'])}'>"
                  f"<input type='hidden' name='type' value='{esc(rt)}'><button class='ghost'>{icon('x')}Remove</button></form>" if preparing and mine else "")
            rows.append([f"<a href='{href('/forms/' + rt, user)}'>{esc(type_name(rt))} form</a>",
                         ("<ul class='staged'>" + "".join(f"<li>{esc(x)}</li>" for x in changes) + "</ul>") if changes else "<span class='muted'>no change</span>",
                         f"{self._avatar(c.get('by') or '', size='sm', named=True) if c.get('by') else ''} <span class='why'>{esc(when(c.get('at') or ''))}</span>", rm])
        out = f"<h2>{icon('form')}Forms</h2>" + table(["Form", "What changes", "Carried by", ""], rows,
                                                       "No form change. Draft one on the Forms page and carry it here.")
        if preparing and mine and self._forms_editor():
            out += f"<p class='why'><a href='{href('/forms', user)}'>{icon('form')}Change a form</a>, then carry the draft on this change order.</p>"
        return out
