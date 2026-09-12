const $ = id => document.getElementById(id);
let root = '', state = null;
let libraryOptions={page:1,page_size:50,status:'',category:null,query:'',action_page:1};
const token = document.querySelector('meta[name="app-token"]').content;
$('root').value = localStorage.getItem('organizer-folder') || '';
function el(tag, text, className) {const node = document.createElement(tag); if(text !== undefined) node.textContent = text; if(className) node.className = className; return node;}
function notice(text, kind='') {$('notice').textContent=text; $('notice').className=kind;}
let currentJob = null, pollTimer = null;
async function api(operation, data={}) {
  const response=await fetch('/api/'+operation,{method:'POST',headers:{'Content-Type':'application/json','X-App-Token':token},body:JSON.stringify({root,...libraryOptions,...data})});
  const result=await response.json(); if(!response.ok) throw new Error(result.error||'The operation failed.'); return result;
}
function showResult(result) {
  root=result.root; state=result; $('root').value=root; localStorage.setItem('organizer-folder',root);
  if(result.inventory){libraryOptions={page:1,page_size:50,status:'',category:null,query:'',action_page:1};$('status-filter').value='';$('category-filter').value='*';$('page-size').value='50';renderInventory(result.inventory);$('workspace').hidden=true;$('report').hidden=true;$('query').value='';}
  else if(result.files){$('workspace').hidden=false;render(result);}
  notice(result.message||'Your workspace is up to date.');
}
async function request(operation, data={}, label='Working…') {
  notice(label,'busy');
  try {
    const result=await api(operation,data);
    if(result.job){root=result.job.root;localStorage.setItem('organizer-folder',root);watchJob(result.job);return null;}
    showResult(result);return result;
  } catch(error){notice(error.message,'error');return null;}
}
function showJob(job) {
  currentJob=job; $('job-card').hidden=false;
  $('job-title').textContent=job.operation+' · '+job.status;
  $('job-message').textContent=job.message;
  const usage=job.usage||{}; $('ai-usage').textContent=usage.attempts===undefined?'':`AI: ${usage.attempts}/${usage.max_attempts} attempts · ${usage.retries} retries · ${usage.input_tokens} input + ${usage.output_tokens} output tokens reported${usage.unreported_attempts?' · '+usage.unreported_attempts+' attempt(s) without usage data':''}. Reported tokens are not a billing estimate.`;
  $('job-count').textContent=`${job.completed}${job.total===null?' completed (total not yet known)':' / '+job.total+' completed'} · ${job.failures} failure(s)`;
  if(job.total===null){$('job-progress').removeAttribute('value');}else{$('job-progress').max=job.total||1;$('job-progress').value=job.completed;}
  const active=['queued','running','cancelling'].includes(job.status);
  $('cancel-job').hidden=!active; $('cancel-job').disabled=job.status==='cancelling';
  $('resume-job').hidden=!['cancelled','interrupted','failed'].includes(job.status);
}
function watchJob(job) {
  clearTimeout(pollTimer);showJob(job);
  pollTimer=setTimeout(pollJob,750);
}
async function pollJob() {
  try {
    const {job}=await api('job',{id:currentJob.id,root:currentJob.root});showJob(job);
    if(['queued','running','cancelling'].includes(job.status)){pollTimer=setTimeout(pollJob,750);return;}
    if(job.result){
      if(job.result.inventory)showResult(job.result);
      else {const fresh=await api('state',{root:job.root});showResult({...fresh,...job.result});if(job.operation==='apply')$('report').replaceChildren(el('p','Index updated. Classify pending files to make contents searchable.'));}
    }else{if(job.operation!=='inventory'){const fresh=await api('state',{root:job.root});showResult(fresh);}notice(job.message,job.status==='failed'?'error':'');}
    await refreshJobs();
  }catch(error){notice('Could not refresh job status: '+error.message,'error');}
}
async function refreshJobs() {
  if(!root)return;
  const {jobs}=await api('jobs');$('job-history').replaceChildren();
  jobs.forEach(job=>{const button=el('button',`#${job.id} ${job.operation} · ${job.status}`,'quiet');button.onclick=()=>watchJob(job);$('job-history').append(button);});
  if(jobs.length&&!currentJob)watchJob(jobs[0]);
}
$('cancel-job').onclick=async()=>{try{const result=await api('cancel-job',{id:currentJob.id,root:currentJob.root});watchJob(result.job);}catch(error){notice(error.message,'error');}};
$('resume-job').onclick=async()=>{try{const result=await api('resume-job',{id:currentJob.id,root:currentJob.root});watchJob(result.job);}catch(error){notice(error.message,'error');}};
function render(data) {
  renderPolicy(data.policy);
  $('total').textContent=data.summary.total; $('pending').textContent=data.summary.pending; $('proposals').textContent=data.action_pagination.total;
  renderFiles(data.files);
  libraryOptions.page=data.pagination.page;libraryOptions.action_page=data.action_pagination.page;
  $('page-label').textContent=`Page ${data.pagination.page} of ${data.pagination.pages}`;
  $('file-count').textContent=`${data.pagination.total} matching file(s) · showing ${data.files.length}`;
  $('previous-page').disabled=data.pagination.page<=1;$('next-page').disabled=data.pagination.page>=data.pagination.pages;
  $('actions-page-label').textContent=`Page ${data.action_pagination.page} of ${data.action_pagination.pages}`;
  $('previous-actions').disabled=data.action_pagination.page<=1;$('next-actions').disabled=data.action_pagination.page>=data.action_pagination.pages;
  $('category-filter').replaceChildren(new Option('All categories','*'));
  data.categories.forEach(category=>$('category-filter').append(new Option(category||'Uncategorized',category)));
  $('category-filter').value=libraryOptions.category===null?'*':libraryOptions.category; $('actions').replaceChildren();
  if(!data.actions.length) $('actions').append(el('p','No proposals waiting. Ask for organization suggestions above.','empty'));
  data.actions.forEach(action=>{const card=el('article',undefined,'proposal'); card.append(el('strong',action.source.split('/').pop()));
    card.append(el('p','From: '+action.source,'path'),el('p','To: '+action.destination,'path'),el('p',action.reason||'No reason supplied.'));
    if(!action.valid) card.append(el('p',action.validation_error));
    const row=el('div',undefined,'row'); const reject=el('button','Reject','quiet'); const approve=el('button','Approve & move'); approve.disabled=!action.valid;
    reject.onclick=()=>request('review',{id:action.id,decision:'n'},'Rejecting proposal…'); approve.onclick=()=>request('review',{id:action.id,decision:'y'},'Moving approved file…');
    row.append(reject,approve); card.append(row); $('actions').append(card);
  });
  if(data.report) renderReport(data.report);

}
function renderFiles(files) {
  $('files').replaceChildren(); $('file-count').textContent=`${files.length} file(s)`;
  if(!files.length){const row=el('tr'),cell=el('td','No files to show. Scan your folder and update the index to get started.','empty'); cell.colSpan=3;row.append(cell);$('files').append(row);}
  files.forEach(file=>{const row=el('tr'),name=el('td'); name.append(el('strong',file.filename),el('span',file.path,'path'));if(file.snippet||file.description)name.append(el('p',file.snippet||file.description));const status=el('td');status.append(el('span',file.is_present===0?'missing':(file.status||'Not classified'),'badge'));row.append(name,el('td',file.category||'—'),status);$('files').append(row);});
}
function renderReport(report){const box=$('report');box.hidden=false;box.replaceChildren();let count=0;
  [['New files',report.new_paths],['Missing files',report.missing_paths],['Modified files',report.modified_paths],['Probable moves',report.probable_moves.map(move=>move.old_path+' → '+move.new_path)]].forEach(([label,paths])=>{count+=paths.length; if(paths.length){box.append(el('strong',`${label} (${paths.length})`));const list=el('ul');paths.forEach(path=>list.append(el('li',path)));box.append(list);}});
  if(!count)box.append(el('p','No changes detected.'));
  else {const apply=el('button','Update index','secondary');apply.onclick=async()=>{const result=await request('apply',{},'Updating your index…');if(result){box.replaceChildren(el('p','Index updated. Classify pending files to make their contents searchable.'));}};box.append(apply,el('p','Updates the local index only. Does not move or delete files.','hint'));}
}
$('folder-form').onsubmit=async event=>{event.preventDefault();const result=await request('inventory',{root:$('root').value},'Opening folder…');if(result){$('report').hidden=true;$('query').value='';}};
$('scan').onclick=()=>request('scan',{},'Scanning your folder…');
$('classify').onclick=()=>request('classify',{retry:$('retry').checked,batch_size:Number($('batch-size').value)},'Classifying files with AI…');
$('search-form').onsubmit=event=>{event.preventDefault();libraryOptions.query=$('query').value;libraryOptions.page=1;request('state',{},'Searching…');};
$('clear').onclick=()=>{$('query').value='';$('status-filter').value='';$('category-filter').value='*';Object.assign(libraryOptions,{query:'',status:'',category:null,page:1});request('state');};
$('organize-form').onsubmit=async event=>{event.preventDefault();const result=await request('organize',{request:$('request').value,batch_size:Number($('candidate-limit').value),max_proposals:Number($('proposal-limit').value)},'Preparing organization suggestions with AI…');if(result)$('review').scrollIntoView({behavior:'smooth'});};

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

// Reconnect to saved job status after a browser reload without starting work.
if($('root').value){root=$('root').value;refreshJobs().catch(error=>notice(error.message,'error'));}

$('status-filter').onchange=()=>{libraryOptions.status=$('status-filter').value;libraryOptions.page=1;request('state');};
$('category-filter').onchange=()=>{libraryOptions.category=$('category-filter').value==='*'?null:$('category-filter').value;libraryOptions.page=1;request('state');};
$('page-size').onchange=()=>{libraryOptions.page_size=Number($('page-size').value);libraryOptions.page=1;request('state');};
$('previous-page').onclick=()=>{libraryOptions.page--;request('state');};
$('next-page').onclick=()=>{libraryOptions.page++;request('state');};
$('previous-actions').onclick=()=>{libraryOptions.action_page--;request('state');};
$('next-actions').onclick=()=>{libraryOptions.action_page++;request('state');};

let policyRoot='', policyVersion=null;
function addPolicyRule(category='',folder='') {
  const row=el('div',undefined,'row');row.classList.add('policy-rule');
  const categoryInput=document.createElement('input');categoryInput.value=category;categoryInput.placeholder='Category';categoryInput.setAttribute('aria-label','Category');categoryInput.className='rule-category';
  const folderInput=document.createElement('input');folderInput.value=folder;folderInput.placeholder='SelectedFolder/Category';folderInput.setAttribute('aria-label','Destination folder relative to workspace');folderInput.className='rule-folder';
  const remove=el('button','Remove','quiet');remove.type='button';remove.onclick=()=>row.remove();row.append(categoryInput,folderInput,remove);$('policy-rules').append(row);
}
function renderPolicy(policy) {
  if(!policy)return;
  $('policy-status').textContent=policy.version?`Saved policy · version ${policy.version}. Reused across organization batches.`:'Draft only. Edit and save these rules before requesting organization.';
  if(policyRoot===policy.root&&policyVersion===policy.version)return;
  policyRoot=policy.root;policyVersion=policy.version;$('policy-rules').replaceChildren();
  Object.entries(policy.destinations).forEach(([category,folder])=>addPolicyRule(category,folder));
  $('protected-folders').value=policy.protected_folders.join('\n');
}
$('add-rule').onclick=()=>addPolicyRule();
$('save-policy').onclick=()=>{
  const rules=[...document.querySelectorAll('.policy-rule')].map(row=>({category:row.querySelector('.rule-category').value,folder:row.querySelector('.rule-folder').value}));
  request('save-policy',{rules,protected_folders:$('protected-folders').value.split('\n').map(value=>value.trim()).filter(Boolean)},'Saving organization rules…');
};
