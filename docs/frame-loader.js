'use strict';
(async()=>{
 try{
  const query=new URLSearchParams(location.search),view=query.get('view')||'workspace';query.delete('view');
  const route=(view==='workspace'?'/workspace':view==='report'?'/':view==='research'?'/research':'/analysis')+(query.size?'?'+query:'');
  const response=await parent.CardioBrowser.request(route);if(!response.ok)throw Error('無法開啟工作站');
  const base=new URL('./',location.href).href;
  let html=await response.text();
  html=html.replace(/(src|href)="\/static\/([^" ]+)"/g,`$1="${base}static/$2?v=2.4.0-preparation1"`);
  html=html.replace('<head>',`<head><base href="${base}"><script src="${base}bridge.js?v=2.4.0-preparation1"></script>`);
  html=html.replace('簽核是本機帳號的覆核紀錄；未使用醫院電子簽章憑證，亦未寫回院內系統。','公開版僅記錄本分頁操作者的展示確認，未驗證醫事身分，亦未寫回院內系統。').replace('CardioAgent 2.0 · 影像協作','CardioAgent 2.1.0 · 影像協作');
  document.open();document.write(html);document.close();
 }catch(error){document.body.textContent='工作站載入失敗：'+error.message;}
})();
