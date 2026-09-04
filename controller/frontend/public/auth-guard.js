(function(){
'use strict';
const API='/api',tokenKey='tccs_user_token',userKey='tccs_user';
function token(){return localStorage.getItem(tokenKey)||''}
function user(){try{return JSON.parse(localStorage.getItem(userKey)||'null')}catch{return null}}
function landing(u){if(!u)return '/';if(u.role==='ADMINISTRATOR')return '/admin.html';if(u.role==='TESTROOM')return '/testroom.html';return '/controller.html'}
async function verify(){const t=token();if(!t){location.replace('/');return null}try{const r=await fetch(API+'/v1/master/auth/me',{headers:{Authorization:'Bearer '+t}});if(!r.ok)throw Error();const u=await r.json();localStorage.setItem(userKey,JSON.stringify(u));
if(location.pathname==='/controller.html'&&u.role==='CONTROLLER'&&u.controller_id){const wanted=new URLSearchParams(location.search).get('controller_id');if(wanted!==String(u.controller_id)){location.replace('/controller.html?controller_id='+encodeURIComponent(u.controller_id));return null}}
return u}catch{localStorage.removeItem(tokenKey);localStorage.removeItem(userKey);location.replace('/');return null}}
function logout(){localStorage.removeItem(tokenKey);localStorage.removeItem(userKey);location.replace('/')}
async function controllerContext(){const u=user();if(!u)return null;let controllerId=null;if(u.role==='CONTROLLER')controllerId=u.controller_id;else if(location.pathname==='/controller.html')controllerId=new URLSearchParams(location.search).get('controller_id');if(!controllerId)return null;try{const r=await originalFetch(API+'/v1/master/available-controllers',{headers:{Authorization:'Bearer '+token()}});if(!r.ok)return null;const list=await r.json();const c=list.find(x=>String(x.id)===String(controllerId));if(!c)return null;window.TCCS_CONTROLLER_CONTEXT=c;return c}catch{return null}}
window.TCCS_AUTH={token,user,logout,verify,landing,API,controllerContext};
const originalFetch=window.fetch.bind(window);
window.fetch=async function(input,init){init=init||{};const url=typeof input==='string'?input:(input&&input.url)||'';if(url.startsWith('/api/')||url.includes(':8000')){const headers=new Headers(init.headers||((input&&input.headers)||{}));const t=token();if(t&&!headers.has('Authorization'))headers.set('Authorization','Bearer '+t);init.headers=headers}
const response=await originalFetch(input,init);const u=user();
if(u&&url.includes('/api/v1/stations')&&!url.includes('/order')&&response.ok){try{const data=await response.clone().json();let sectionId=null;if(u.role==='CONTROLLER')sectionId=u.controller&&u.controller.section_id;else if(location.pathname==='/controller.html'){const selected=await controllerContext();sectionId=selected&&selected.section_id}
if(sectionId!=null&&Array.isArray(data)){const filtered=data.filter(s=>Number(s.section_id)===Number(sectionId)).map(s=>Object.assign({},s,{section_id:1}));return new Response(JSON.stringify(filtered),{status:response.status,statusText:response.statusText,headers:new Headers(response.headers)})}}\catch{}}
return response};
})();
