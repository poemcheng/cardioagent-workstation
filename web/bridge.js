'use strict';
(()=>{
 const originalFetch=window.fetch.bind(window),api=parent.CardioBrowser;
 const route=value=>{try{const url=new URL(String(value),location.href);return url.origin===location.origin&&/^\/(api|reference)\//.test(url.pathname)?url.pathname+url.search:null;}catch{return null;}};
 window.fetch=(input,options={})=>{const value=typeof input==='string'?input:input.url;const internal=route(value);return internal?api.request(internal,options):originalFetch(input,options);};
 const descriptor=Object.getOwnPropertyDescriptor(HTMLImageElement.prototype,'src'),tokens=new WeakMap(),objectURLs=new WeakMap();
 function setImage(image,value){const internal=route(value);if(!internal){descriptor.set.call(image,value);return;}const token={};tokens.set(image,token);api.request(internal).then(async response=>{if(!response.ok)throw Error('影像來源無法讀取');const blob=await response.blob();if(tokens.get(image)!==token)return;const old=objectURLs.get(image);if(old)URL.revokeObjectURL(old);const url=URL.createObjectURL(blob);objectURLs.set(image,url);descriptor.set.call(image,url);}).catch(()=>{if(tokens.get(image)===token)image.dispatchEvent(new Event('error'));});}
 Object.defineProperty(HTMLImageElement.prototype,'src',{configurable:true,get:descriptor.get,set(value){setImage(this,value);}});
 const setAttribute=Element.prototype.setAttribute;Element.prototype.setAttribute=function(name,value){if(this instanceof HTMLImageElement&&name.toLowerCase()==='src'&&route(value)){setImage(this,value);return;}return setAttribute.call(this,name,value);};
 document.addEventListener('click',async event=>{
  const link=event.target.closest('a');if(!link||link.target==='_blank')return;const href=link.getAttribute('href');if(!href||href.startsWith('#')||href.startsWith('blob:'))return;
  const url=new URL(href,location.href);if(url.origin!==location.origin)return;
  if(link.getAttribute('aria-disabled')==='true'){event.preventDefault();return;}
  const internal=route(url.href);if(internal?.startsWith('/api/demo/sources/'))return;
  if(internal){event.preventDefault();try{
   const response=await api.request(internal);if(!response.ok){const data=await response.json();throw Error(data.error||'匯出失敗');}
   const blob=await response.blob(),download=document.createElement('a'),objectURL=URL.createObjectURL(blob),dialog=document.createElement('dialog'),heading=document.createElement('h2'),detail=document.createElement('p'),close=document.createElement('button');
   download.href=objectURL;download.download=(response.headers.get('Content-Disposition')||'').match(/filename="([^"]+)"/)?.[1]||'CardioAgent-result.json';download.textContent='下載 '+download.download;download.className='primary';
   heading.textContent='匯出檔案已準備完成';detail.textContent=`${download.download} · ${(blob.size/1024).toFixed(1)} KB · 由本分頁產生`;
   close.textContent='關閉';close.onclick=()=>dialog.close();dialog.id='browserExportDialog';dialog.append(heading,detail,download,document.createTextNode('　'),close);document.body.append(dialog);dialog.addEventListener('close',()=>{dialog.remove();setTimeout(()=>URL.revokeObjectURL(objectURL),60000);},{once:true});dialog.showModal();
  }catch(error){window.alert(error.message);}return;}
  if(['/','/analysis','/research'].includes(url.pathname)){event.preventDefault();api.navigate(url.pathname+url.search);}
 },true);
 document.addEventListener('DOMContentLoaded',()=>{for(const id of ['usersBtn','logout']){const button=document.getElementById(id);if(button)button.hidden=true;}const strip=document.querySelector('.environment-strip');if(strip)strip.firstChild.textContent='資料環境：公開研究展示 · 本分頁獨立運算 ';});
})();
