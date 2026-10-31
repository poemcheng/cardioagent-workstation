'use strict';
let pyodide,initialized=false,queue=Promise.resolve();
const progress=message=>postMessage({kind:'progress',message});
async function initialize(){
 progress('正在載入 Python 分析環境…');
 importScripts('https://cdn.jsdelivr.net/pyodide/v0.29.4/full/pyodide.js');
 pyodide=await loadPyodide({indexURL:'https://cdn.jsdelivr.net/pyodide/v0.29.4/full/'});
 progress('正在載入數值運算、影像與紀錄元件…');
 await pyodide.loadPackage(['numpy','pillow','sqlite3','hashlib','ssl','micropip','markupsafe']);
 const manifest=await(await fetch('browser-manifest.json',{cache:'no-cache'})).json();
 const wheels=manifest.wheels.map(name=>new URL('wheels/'+name,self.location.href).href);
 pyodide.globals.set('browser_wheels',wheels);
 await pyodide.runPythonAsync('import micropip\nawait micropip.install(list(browser_wheels), deps=False)');
 progress('正在核對來源影像並建立個人展示工作區…');
 const response=await fetch('cardio-runtime.zip?v='+manifest.runtime_sha256);if(!response.ok)throw Error('來源程式與影像未下載完成');const buffer=await response.arrayBuffer();
 const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)),b=>b.toString(16).padStart(2,'0')).join('');
 if(digest!==manifest.runtime_sha256)throw Error('來源套件完整性檢查未通過，請重新整理');
 pyodide.unpackArchive(buffer,'zip',{extractDir:'/project'});
 await pyodide.runPythonAsync("import sys\nsys.path.insert(0,'/project')\nfrom cardio.browser_runtime import BrowserRuntime\ncardio_browser = BrowserRuntime('/session')");
 initialized=true;postMessage({kind:'ready'});
}
async function handle(message){
 if(message.kind==='init'){try{await initialize();}catch(error){postMessage({kind:'fatal',error:String(error)});}return;}
 if(!initialized){postMessage({id:message.id,error:'分析環境尚未就緒'});return;}
 try{
  pyodide.globals.set('browser_request_json',JSON.stringify(message));
  const result=JSON.parse(pyodide.runPython('cardio_browser.request(browser_request_json)'));
  const raw=atob(result.base64),bytes=Uint8Array.from(raw,ch=>ch.charCodeAt(0));delete result.base64;
  postMessage({id:message.id,...result,bytes:bytes.buffer},[bytes.buffer]);
 }catch(error){postMessage({id:message.id,error:String(error)});}
}
self.onmessage=event=>{queue=queue.then(()=>handle(event.data)).catch(error=>postMessage({kind:'fatal',error:String(error)}));};
