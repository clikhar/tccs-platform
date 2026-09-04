(function(){
'use strict';
const API=location.protocol+'//'+location.hostname+':8000';
const tokenKey='tccs_user_token';
const userKey='tccs_user';
function token(){return localStorage.getItem(tokenKey)||''}
function user(){try{return JSON.parse(localStorage.getItem(userKey)||'null')}catch{return null}}
function landing(u){if(!u)return '/';if(u.role==='ADMINISTRATOR')return '/admin.html';if(u.role==='TESTROOM')return '/testroom.html';return '/controller.html'}
async function verify(){const t=token();if(!t){location.replace('/');return null}try{const r=await fetch(API+'/api/v1/master/auth/me',{headers:{Authorization:'Bearer '+t}});if(!r.ok)throw Error();const u=await r.json();localStorage.setItem(userKey,JSON.stringify(u));return u}catch{localStorage.removeItem(tokenKey);localStorage.removeItem(userKey);location.replace('/');return null}}
function logout(){localStorage.removeItem(tokenKey);localStorage.removeItem(userKey);location.replace('/')}
window.TCCS_AUTH={token,user,logout,verify,landing,API};
const originalFetch=window.fetch.bind(window);
window.fetch=function(input,init){init=init||{};const url=typeof input==='string'?input:(input&&input.url)||'';if(url.includes(':8000')||url.startsWith('/api/')){const headers=new Headers(init.headers||((input&&input.headers)||{}));const t=token();if(t&&!headers.has('Authorization'))headers.set('Authorization','Bearer '+t);init.headers=headers}return originalFetch(input,init)};
})();
