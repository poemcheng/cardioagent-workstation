'use strict';
const frame=document.getElementById('workspace'),boot=document.getElementById('boot'),status=document.getElementById('bootStatus');
let worker,sequence=0,ready=false;const pending=new Map();
function call(payload){return new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});worker.postMessage({id,...payload});});}
function base64(bytes){let s='';for(let i=0;i<bytes.length;i+=16384)s+=String.fromCharCode(...bytes.subarray(i,i+16384));return btoa(s);}
async function request(url,options={}){
 if(!ready)throw Error('分析環境尚未就緒');
 const target=new URL(url,'https://cardio.invalid');
 if(target.origin!=='https://cardio.invalid')throw Error('僅接受工作站內部要求');
 const payload={kind:'request',path:target.pathname+target.search,method:options.method||'GET',headers:Object.fromEntries(new Headers(options.headers||{}))};
 if(Object.prototype.toString.call(options.body)==='[object FormData]'){payload.multipart=[];let total=0;for(const[name,value]of options.body.entries()){if(typeof value!=='string'&&typeof value.arrayBuffer==='function'){total+=value.size;if(total>24*1024*1024)throw Error('公開版每次匯入上限24 MB，資料只在本分頁處理');payload.multipart.push({name,filename:value.name,type:value.type,base64:base64(new Uint8Array(await value.arrayBuffer()))});}else payload.multipart.push({name,value});}}
 else if(options.body!==undefined)payload.body=options.body;
 const result=await call(payload);return new Response(result.bytes,{status:result.status,headers:result.headers});
}
function navigate(path,replace=false){
 const route=new URL(path,'https://cardio.invalid');if(route.origin!=='https://cardio.invalid')throw Error('Invalid route');
 const query=new URLSearchParams(route.search);query.set('view',route.pathname==='/analysis'?'analysis':route.pathname==='/research'?'research':'report');
 const address=new URL(location.href);address.search=query.toString();history[replace?'replaceState':'pushState']({},'',address);
 frame.src='frame.html?'+query+'&build=2.1.0-public2';frame.hidden=false;boot.hidden=true;
}
window.CardioBrowser={request,navigate};
function fail(error){status.textContent='載入未完成，請檢查網路後重試。';document.getElementById('spinner').hidden=true;document.getElementById('retry').hidden=false;document.getElementById('failure').hidden=false;document.getElementById('failureText').textContent=String(error);}
function launch(){
 ready=false;frame.hidden=true;boot.hidden=false;document.getElementById('retry').hidden=true;document.getElementById('failure').hidden=true;document.getElementById('spinner').hidden=false;
 worker?.terminate();for(const item of pending.values())item.reject(Error('展示環境已重新啟動'));pending.clear();
 worker=new Worker('python-worker.js');
 worker.onmessage=event=>{const m=event.data;if(m.kind==='progress'){status.textContent=m.message;return;}if(m.kind==='ready'){ready=true;const q=new URLSearchParams(location.search),view=q.get('view')||'analysis';q.delete('view');navigate((view==='report'?'/':view==='research'?'/research':'/analysis')+(q.size?'?'+q:''),true);return;}if(m.kind==='fatal'){fail(m.error);return;}const task=pending.get(m.id);if(!task)return;pending.delete(m.id);m.error?task.reject(Error(m.error)):task.resolve(m);};
 worker.onerror=event=>fail(event.message||'無法載入分析元件');worker.postMessage({kind:'init'});
}
document.getElementById('retry').onclick=launch;
document.getElementById('restart').onclick=()=>{if(window.confirm('重新啟動會清除本分頁的影像、分析與報告，請先下載需要的結果。確定重新啟動？'))launch();};
window.addEventListener('popstate',()=>{if(!ready)return;frame.src='frame.html'+location.search;});
launch();
