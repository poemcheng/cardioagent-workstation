'use strict';
(async()=>{
 try{
  const query=new URLSearchParams(location.search),view=query.get('view')||'analysis';query.delete('view');
  const route=(view==='report'?'/':view==='research'?'/research':'/analysis')+(query.size?'?'+query:'');
  const response=await parent.CardioBrowser.request(route);if(!response.ok)throw Error('無法開啟工作站');
  const base=new URL('./',location.href).href;
  let html=await response.text();
  html=html.replace(/(src|href)="\/static\//g,`$1="${base}static/`);
  html=html.replace('<head>',`<head><base href="${base}"><script src="${base}bridge.js"></script>`);
  document.open();document.write(html);document.close();
 }catch(error){document.body.textContent='工作站載入失敗：'+error.message;}
})();
