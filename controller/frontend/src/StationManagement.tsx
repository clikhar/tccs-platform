import React, { useEffect, useMemo, useState } from 'react';

type Station = { id:number; station_number:string; name:string; location:string|null; section_id:number; sip_extension:string; station_type:string; enabled:boolean; priority:number };
type Section = { id:number; code:string; name:string; enabled:boolean };

const API_BASE=import.meta.env.VITE_API_BASE_URL||'';

export default function StationManagement({onMessage}:{onMessage?:(message:string)=>void}){
 const[stations,setStations]=useState<Station[]>([]);
 const[sections,setSections]=useState<Section[]>([]);
 const[search,setSearch]=useState('');
 const[editing,setEditing]=useState<Station|null>(null);
 const[showForm,setShowForm]=useState(false);
 const[loading,setLoading]=useState(false);
 const[form,setForm]=useState({station_number:'',name:'',location:'',section_id:'',sip_extension:'',station_type:'WAY_STATION',enabled:true});

 const load=async()=>{setLoading(true);try{const[r,s]=await Promise.all([fetch(`${API_BASE}/api/v1/station-management`),fetch(`${API_BASE}/api/v1/sections`)]);if(!r.ok)throw new Error(`Station API HTTP ${r.status}`);if(!s.ok)throw new Error(`Section API HTTP ${s.status}`);setStations(await r.json());setSections(await s.json());}catch(e){onMessage?.(e instanceof Error?e.message:'Unable to load stations');}finally{setLoading(false);}};
 useEffect(()=>{load();},[]);
 const filtered=useMemo(()=>{const q=search.trim().toLowerCase();return stations.filter(s=>!q||[s.station_number,s.name,s.location||'',s.sip_extension].join(' ').toLowerCase().includes(q));},[stations,search]);
 const reset=()=>{setEditing(null);setShowForm(false);setForm({station_number:'',name:'',location:'',section_id:sections[0]?.id?.toString()||'',sip_extension:'',station_type:'WAY_STATION',enabled:true});};
 const edit=(s:Station)=>{setEditing(s);setForm({station_number:s.station_number,name:s.name,location:s.location||'',section_id:String(s.section_id),sip_extension:s.sip_extension,station_type:s.station_type,enabled:s.enabled});setShowForm(true);};
 const save=async()=>{try{const payload={...form,section_id:Number(form.section_id)};const url=editing?`${API_BASE}/api/v1/station-management/${editing.id}`:`${API_BASE}/api/v1/station-management`;const response=await fetch(url,{method:editing?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||`HTTP ${response.status}`);onMessage?.(editing?'Subscriber updated successfully':'Subscriber added successfully');await load();reset();}catch(e){onMessage?.(e instanceof Error?e.message:'Unable to save subscriber');}};
 const toggle=async(s:Station)=>{try{const response=await fetch(`${API_BASE}/api/v1/station-management/${s.id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!s.enabled})});const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||`HTTP ${response.status}`);onMessage?.(`${s.station_number} ${!s.enabled?'enabled':'disabled'}`);await load();}catch(e){onMessage?.(e instanceof Error?e.message:'Unable to change station state');}};
 const remove=async(s:Station)=>{if(!window.confirm(`Disable subscriber ${s.station_number} — ${s.name}?`))return;try{const response=await fetch(`${API_BASE}/api/v1/station-management/${s.id}`,{method:'DELETE'});const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||`HTTP ${response.status}`);onMessage?.(`${s.station_number} removed from active subscribers`);await load();}catch(e){onMessage?.(e instanceof Error?e.message:'Unable to remove subscriber');}};
 return <div className="station-management">
  <div className="sm-toolbar"><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search station / name / SIP"/><button onClick={()=>{reset();setShowForm(true);}}>ADD SUBSCRIBER</button><button onClick={load}>REFRESH</button></div>
  {showForm&&<div className="sm-form"><h4>{editing?'EDIT SUBSCRIBER':'ADD SUBSCRIBER'}</h4><div className="sm-form-grid">
   <label>Station Number<input value={form.station_number} onChange={e=>setForm({...form,station_number:e.target.value})}/></label>
   <label>Station Name<input value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></label>
   <label>Location<input value={form.location} onChange={e=>setForm({...form,location:e.target.value})}/></label>
   <label>SIP Extension<input value={form.sip_extension} onChange={e=>setForm({...form,sip_extension:e.target.value})} placeholder="10xx"/></label>
   <label>Section<select value={form.section_id} onChange={e=>setForm({...form,section_id:e.target.value})}>{sections.map(s=><option key={s.id} value={s.id}>{s.code} — {s.name}</option>)}</select></label>
   <label>Type<select value={form.station_type} onChange={e=>setForm({...form,station_type:e.target.value})}><option>WAY_STATION</option><option>CABIN</option><option>CONTROL_POINT</option><option>OTHER</option></select></label>
  </div><label className="sm-enabled"><input type="checkbox" checked={form.enabled} onChange={e=>setForm({...form,enabled:e.target.checked})}/> Enabled</label><div className="sm-form-actions"><button onClick={save}>{editing?'SAVE CHANGES':'CREATE'}</button><button onClick={reset}>CANCEL</button></div></div>}
  <div className="sm-count">{filtered.length} subscriber(s){loading?' • loading...':''}</div>
  <div className="sm-table"><div className="sm-head"><b>STATION</b><b>NAME</b><b>SECTION</b><b>SIP</b><b>STATUS</b><b>ACTIONS</b></div>{filtered.map(s=><div className="sm-row" key={s.id}><strong>{s.station_number}</strong><span>{s.name}</span><span>{sections.find(x=>x.id===s.section_id)?.code||s.section_id}</span><span>{s.sip_extension}</span><span className={s.enabled?'sm-active':'sm-disabled'}>{s.enabled?'ENABLED':'DISABLED'}</span><span className="sm-actions"><button onClick={()=>edit(s)}>EDIT</button><button onClick={()=>toggle(s)}>{s.enabled?'DISABLE':'ENABLE'}</button><button className="sm-danger" onClick={()=>remove(s)}>REMOVE</button></span></div>)}</div>
 </div>;
}
