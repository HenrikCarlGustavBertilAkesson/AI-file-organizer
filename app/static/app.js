const $ = id => document.getElementById(id);
let root = '', state = null;
const token = document.querySelector('meta[name="app-token"]').content;
$('root').value = localStorage.getItem('organizer-folder') || '';
function el(tag, text, className) {const node = document.createElement(tag); if(text !== undefined) node.textContent = text; if(className) node.className = className; return node;}
function notice(text, kind='') {$('notice').textContent=text; $('notice').className=kind;}
async function request(operation, data={}, label='Working…') {
  const controls=[...document.querySelectorAll('button, input, textarea')]; const disabled=controls.map(node=>node.disabled);
  controls.forEach(node=>node.disabled=true); notice(label, 'busy');
  const started=Date.now(); const timer=setInterval(()=>notice(`${label} ${Math.floor((Date.now()-started)/1000)}s elapsed.`, 'busy'),1000);
  try {const response=await fetch('/api/'+operation,{method:'POST',headers:{'Content-Type':'application/json','X-App-Token':token},body:JSON.stringify({root,...data})});
    const result=await response.json(); if(!response.ok) throw new Error(result.error||'The operation failed.');
    root=result.root; state=result; $('root').value=root; localStorage.setItem('organizer-folder',root); $('workspace').hidden=false;
    $('logs').textContent=result.details||''; $('details').hidden=!result.details;
    if(result.inventory){renderInventory(result.inventory);$('workspace').hidden=true;}else{render(result);} notice(result.message||'Your workspace is up to date.'); return result;
  } catch(error) {notice(error.message,'error'); return null;} finally {clearInterval(timer); controls.forEach((node,i)=>node.disabled=disabled[i]);}
}
function render(data) {
  const present=data.files.filter(file=>file.is_present);
  $('total').textContent=present.length; $('pending').textContent=present.filter(file=>file.status==='pending').length; $('proposals').textContent=data.actions.length;
  renderFiles(present); $('actions').replaceChildren();
  if(!data.actions.length) $('actions').append(el('p','No proposals waiting. Ask for organization suggestions above.','empty'));
  data.actions.forEach(action=>{const card=el('article',undefined,'proposal'); card.append(el('strong',action.source.split('/').pop()));
    card.append(el('p','From: '+action.source,'path'),el('p','To: '+action.destination,'path'),el('p',action.reason||'No reason supplied.'));
    if(!action.valid) card.append(el('p',action.validation_error));
    const row=el('div',undefined,'row'); const reject=el('button','Reject','quiet'); const approve=el('button','Approve & move'); approve.disabled=!action.valid;
    reject.onclick=()=>request('review',{id:action.id,decision:'n'},'Rejecting proposal…'); approve.onclick=()=>request('review',{id:action.id,decision:'y'},'Moving approved file…');
    row.append(reject,approve); card.append(row); $('actions').append(card);
  });
  if(data.report) renderReport(data.report);
  if(data.results) {renderFiles(data.results.map(result=>({filename:result.path.split('/').pop(),path:result.path,description:result.snippet,status:'match'}))); $('file-count').textContent=`${data.results.length} search result(s) · matches all search words`;}
}
function renderFiles(files) {
  $('files').replaceChildren(); $('file-count').textContent=`${files.length} file(s)`;
  if(!files.length){const row=el('tr'),cell=el('td','No files to show. Scan your folder and update the index to get started.','empty'); cell.colSpan=3;row.append(cell);$('files').append(row);}
  files.forEach(file=>{const row=el('tr'),name=el('td'); name.append(el('strong',file.filename),el('span',file.path,'path'));if(file.description)name.append(el('p',file.description));const status=el('td');status.append(el('span',file.status||'Not classified','badge'));row.append(name,el('td',file.category||'—'),status);$('files').append(row);});
}
function renderReport(report){const box=$('report');box.hidden=false;box.replaceChildren();let count=0;
  [['New files',report.new_paths],['Missing files',report.missing_paths],['Modified files',report.modified_paths],['Probable moves',report.probable_moves.map(move=>move.old_path+' → '+move.new_path)]].forEach(([label,paths])=>{count+=paths.length; if(paths.length){box.append(el('strong',`${label} (${paths.length})`));const list=el('ul');paths.forEach(path=>list.append(el('li',path)));box.append(list);}});
  if(!count)box.append(el('p','No changes detected.'));
  else {const apply=el('button','Update index','secondary');apply.onclick=async()=>{const result=await request('apply',{},'Updating your index…');if(result){box.replaceChildren(el('p','Index updated. Classify pending files to make their contents searchable.'));}};box.append(apply,el('p','Updates the local index only. Does not move or delete files.','hint'));}
}
$('folder-form').onsubmit=async event=>{event.preventDefault();const result=await request('inventory',{root:$('root').value},'Opening folder…');if(result){$('report').hidden=true;$('query').value='';}};
$('scan').onclick=()=>request('scan',{},'Scanning your folder…');
$('classify').onclick=()=>request('classify',{retry:$('retry').checked},'Classifying files with AI…');
$('search-form').onsubmit=event=>{event.preventDefault();request('search',{query:$('query').value},'Searching…');};
$('clear').onclick=()=>{$('query').value='';if(state)renderFiles(state.files.filter(file=>file.is_present));};
$('organize-form').onsubmit=async event=>{event.preventDefault();const result=await request('organize',{request:$('request').value},'Preparing organization suggestions with AI…');if(result)$('review').scrollIntoView({behavior:'smooth'});};

function renderInventory(data){
  $('scope-card').hidden=false; $('scope-groups').replaceChildren();
  const total=data.groups.reduce((sum,group)=>sum+group.files,0);
  const bytes=data.groups.reduce((sum,group)=>sum+group.bytes,0);
  $('inventory-summary').textContent=`${total} eligible files · ${(bytes/1048576).toFixed(1)} MB${data.complete?'':' · Incomplete: some locations could not be read'}`;
  data.groups.forEach(group=>{
    const label=el('label',undefined,'check'); const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=group.name;
    checkbox.checked=data.scope?(group.name==='.'?data.scope.loose_files:data.scope.folders.includes(group.name)):!group.project;
    label.append(checkbox,document.createTextNode(` ${group.name==='.'?'Files directly in this folder':group.name} — ${group.files} files · ${(group.bytes/1048576).toFixed(1)} MB${group.project?' · Project detected: review before including':''}`));
    $('scope-groups').append(label);
  });
  $('exclusions').value=data.exclusions.join(', ');$('inventory-details').replaceChildren();
  data.skipped.forEach(item=>$('inventory-details').append(el('p',item.path+' — '+item.reason,'path')));
  data.errors.forEach(item=>$('inventory-details').append(el('p',item.path+' — '+item.error,'path')));
  if(!data.skipped.length&&!data.errors.length)$('inventory-details').append(el('p','No excluded locations or errors.'));
}
$('save-scope').onclick=async()=>{
  const selected=[...$('scope-groups').querySelectorAll('input:checked')].map(input=>input.value);
  const result=await request('save-scope',{folders:selected.filter(name=>name!=='.'),loose_files:selected.includes('.'),exclusions:$('exclusions').value.split(',').map(name=>name.trim()).filter(Boolean)},'Saving workspace scope…');
  if(result){$('scope-card').hidden=true;$('report').hidden=true;}
};
