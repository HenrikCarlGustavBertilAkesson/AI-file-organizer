// Exercise the actual dashboard renderer and button payload without dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag) {this.tag=tag;this.children=[];this.value='';this.classList={add(){}};}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children=children;}
  setAttribute() {}
  removeAttribute() {}
}
const nodes = new Map();
const node = id => {if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);};
const requests=[];
const context=vm.createContext({
  document:{getElementById:node,createElement:tag=>new Element(tag),querySelector:()=>({content:'csrf'}),querySelectorAll:()=>[]},
  localStorage:{getItem:()=>'',setItem(){}},setTimeout:()=>1,clearTimeout(){},console,
  fetch:async(url,options)=>{
    requests.push({url,data:JSON.parse(options.body)});
    return {ok:true,json:async()=>({job:{id:1,root:'/fixture',operation:'review-group',parameters:{},status:'queued',completed:0,total:null,failures:0}})};
  }
});
vm.runInContext(fs.readFileSync('app/static/app.js','utf8'),context);
vm.runInContext(`root='/fixture';renderGroupActions({group_actions:[{batch_id:42,token:'frozen',category:'work',file_count:3,total_bytes:123,destination:'/fixture/Work',status:'pending'}],group_action_pagination:{page:1,pages:1}})`,context);
function descendants(element){return [element,...element.children.filter(c=>typeof c==='object').flatMap(descendants)];}
const rendered=descendants(node('actions'));
assert.equal(rendered.filter(e=>e.tag==='article').length,1);
const approve=rendered.find(e=>e.textContent==='Approve & move all 3 files');
assert.ok(approve);
(async()=>{
  await approve.onclick();
  assert.equal(requests.length,1);
  assert.equal(requests[0].url,'/api/review-group');
  assert.equal(requests[0].data.batch_id,42);
  assert.equal(requests[0].data.token,'frozen');
  assert.equal(requests[0].data.decision,'y');
  assert.equal(requests[0].data.file_ids,undefined);
  assert.equal(node('resume-job').hidden,true);
  console.log('Group card and single approval payload: passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
