"use strict";
(()=>{
 const $=id=>document.getElementById(id);
 const token=()=>localStorage.getItem("fg_token")||"";
 async function api(path,options={}){
  const headers={"Content-Type":"application/json","Authorization":"Bearer "+token(),...(options.headers||{})};
  const response=await fetch(location.origin+path,{...options,headers});
  const data=await response.json().catch(()=>({}));
  if(response.status===401){localStorage.removeItem("fg_token");localStorage.removeItem("fg_refresh");location.replace("/login.html");throw new Error("Session expired. Sign in again.");}
  if(!response.ok)throw new Error(data.detail||data.message||("Request failed ("+response.status+")"));
  return data;
 }
 const el=(tag,text,cls="")=>{const n=document.createElement(tag);n.textContent=text;n.className=cls;return n;};
 const stamp=value=>value?new Date(Number(value)*1000).toLocaleString():"—";
 const deviceData=async()=>{const d=await api("/devices");return d.devices||[];};
 async function overview(){
  const [profile,billing,devices]=await Promise.all([api("/account/profile"),api("/billing/status"),deviceData()]);
  $("fg-overview-account").textContent=(profile.name||profile.email||"Signed-in account")+" · "+(profile.email||"");
  $("fg-overview-plan").textContent="Plan: "+(billing.plan||billing.status||"Free")+" · Premium ₹999/month";
  $("fg-overview-devices").textContent=devices.length+" registered device(s) · "+devices.filter(x=>x.online).length+" online";
  const enabled=devices.filter(x=>x.apps_sync_enabled||x.location_granted).length;
  $("fg-overview-consent").textContent=enabled+" device(s) report enabled app/usage or location permissions. Consent controls remain available in the Privacy section below.";
 }
 async function locations(){
  const root=$("fg-location-content");root.replaceChildren();
  const devices=await deviceData();let found=0;
  for(const d of devices){
   const card=el("article","", "border border-slate-700 rounded-xl p-4 mb-3");
   card.append(el("h3",d.deviceName+" · "+d.platform,"font-semibold"));
   const data=await api("/api/devices/"+encodeURIComponent(d.deviceId)+"/locations?limit=50");
   const rows=data.locations||[];
   if(!rows.length)card.append(el("p","No location history shared for this device.","muted text-sm mt-2"));
   rows.forEach(p=>{
    found++;const line=el("p",stamp(p.capturedAt)+" · "+Number(p.latitude).toFixed(5)+", "+Number(p.longitude).toFixed(5),"muted text-sm mt-2");
    const link=el("a","Open map","text-teal-300 underline text-sm mt-1");link.href="https://www.google.com/maps/search/?api=1&query="+encodeURIComponent(p.latitude+","+p.longitude);link.target="_blank";link.rel="noopener noreferrer";line.append(document.createTextNode(" · "),link);card.append(line);
   });
   root.append(card);
  }
  if(!devices.length)root.append(el("p","No registered devices yet.","muted"));
  else if(!found)root.append(el("p","No location history is available yet. Location sharing must be explicitly enabled on the device.","muted text-sm"));
 }
 async function apps(){
  const root=$("fg-app-content");root.replaceChildren();const devices=await deviceData();
  for(const d of devices){
   const card=el("article","", "border border-slate-700 rounded-xl p-4 mb-3");
   card.append(el("h3",d.deviceName+" · "+d.platform,"font-semibold"));
   card.append(el("p","Screen time: "+Number(d.screen_time_minutes||0)+" minutes today","muted text-sm mt-2"));
   const labels=Array.isArray(d.installed_apps)?d.installed_apps:[];
   card.append(el("p",labels.length?labels.join(" · "):"No installed-app summary shared yet.","muted text-sm mt-2 break-words"));
   card.append(el("p","Last summary sync: "+stamp(d.summary_last_synced_at),"muted text-xs mt-2"));
   root.append(card);
  }
  if(!devices.length)root.append(el("p","No registered devices yet.","muted"));
 }
 async function sos(){
  const root=$("fg-sos-content");root.replaceChildren();const devices=await deviceData();let count=0;
  for(const d of devices){
   const data=await api("/api/devices/"+encodeURIComponent(d.deviceId)+"/sos");
   for(const a of (data.alerts||[])){
    count++;const card=el("article","", "border rounded-xl p-4 mb-3 "+(a.status==="active"?"border-red-500 bg-red-950/40":"border-slate-700"));
    card.append(el("h3",(a.status==="active"?"ACTIVE SOS":"RESOLVED SOS")+" · "+a.message,"font-semibold"));
    card.append(el("p",d.deviceName+" · Captured "+stamp(a.capturedAt)+" · Received "+stamp(a.createdAt),"muted text-xs mt-2"));
    if(a.status==="active"){
     const btn=el("button","Mark resolved","btn secondary mt-3");btn.type="button";
     btn.addEventListener("click",async()=>{btn.disabled=true;try{await api("/api/devices/"+encodeURIComponent(d.deviceId)+"/sos/"+encodeURIComponent(a.alertId)+"/resolve",{method:"POST"});await sos();}catch(e){btn.disabled=false;alert(e.message);}});
     card.append(btn);
    }
    root.append(card);
   }
  }
  if(!count)root.append(el("p","No SOS alerts.","muted"));
 }
 async function familyManagement(){
  const root=$("family-list");if(!root)return;
  const data=await api("/family/mine");
  if(!data.families?.length)root.textContent="No family groups yet.";
 }
 const actions={"overview":overview,"location-history":locations,"installed-apps":apps,"sos-alerts":sos,"family-management":familyManagement,"premium":async()=>{const b=await api("/billing/status");$("premium-state").textContent=b.plan==="premium"?"Premium active":(b.plan||b.status||"Free");$("premium-detail").textContent="Premium subscription: ₹999/month";}};
 async function load(name){try{if(actions[name])await actions[name]();}catch(e){const target={"overview":"fg-overview-account","location-history":"fg-location-content","installed-apps":"fg-app-content","sos-alerts":"fg-sos-content","family-management":"family-message","premium":"premium-detail"}[name];if(target&&$(target))$(target).textContent="Could not load: "+e.message;}}
 document.addEventListener("click",event=>{const btn=event.target.closest("[data-tab]");if(!btn)return;const name=btn.dataset.tab;if(actions[name])load(name);},{capture:true});
 $("fg-refresh-locations")?.addEventListener("click",()=>load("location-history"));
 $("fg-refresh-apps")?.addEventListener("click",()=>load("installed-apps"));
 $("fg-refresh-sos")?.addEventListener("click",()=>load("sos-alerts"));
 if(token())load("overview");
})();
