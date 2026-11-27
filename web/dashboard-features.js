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
  const [profile,billing,devices]=await Promise.all([api("/api/profile"),api("/billing/status"),deviceData()]);
  $("fg-overview-account").textContent=(profile.name||profile.email||"Signed-in account")+" · "+(profile.email||"");
  $("fg-overview-plan").textContent="Plan: "+(billing.plan||billing.status||"Free")+" · Premium ₹999/month";
  $("fg-overview-devices").textContent=devices.length+" registered device(s) · "+devices.filter(x=>x.online).length+" online · "+devices.filter(x=>!x.online).length+" offline";
  const enabled=devices.filter(x=>x.apps_sync_enabled||x.location_granted).length;
  $("fg-overview-consent").textContent=enabled+" device(s) report enabled app/usage or location permissions. Consent controls remain available in the Privacy section below.";
 }
 async function locations(){
  const root=$("fg-location-content");root.replaceChildren();
  const devices=await deviceData();let found=0;
  for(const d of devices){
   const card=el("article","", "border border-slate-700 rounded-xl p-4 mb-3");
   card.append(el("h3",d.deviceName+" · "+d.platform,"font-semibold"));
   if(d.location){const live=el("p","Latest: "+Number(d.location.latitude).toFixed(5)+", "+Number(d.location.longitude).toFixed(5)+" · Accuracy "+Math.round(Number(d.location.accuracyMeters||0))+" m · "+stamp(d.location.capturedAt),"text-sm text-teal-200 mt-2");const map=el("a","Open live location","text-teal-300 underline text-sm mt-1");map.href="https://www.google.com/maps/search/?api=1&query="+encodeURIComponent(d.location.latitude+","+d.location.longitude);map.target="_blank";map.rel="noopener noreferrer";live.append(document.createTextNode(" · "),map);card.append(live)}else card.append(el("p","No live location is currently shared.","muted text-sm mt-2"));
   let data;
   try{data=await api("/api/devices/"+encodeURIComponent(d.deviceId)+"/locations?limit=50");}
   catch(error){card.append(el("p","Location history is hidden because consent is OFF or unavailable.","muted text-sm mt-2"));root.append(card);continue;}
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
  const query=($("fg-app-search")?.value||"").trim().toLowerCase();let visible=0;
  for(const d of devices){
   const card=el("article","", "border border-slate-700 rounded-xl p-4 mb-3");
   card.append(el("h3",d.deviceName+" · "+d.platform,"font-semibold"));
   if(!d.apps_sync_enabled){card.append(el("p","App and usage sync is OFF. Stored app details are hidden until the device owner enables it.","muted text-sm mt-2"));root.append(card);continue;}
   const entries=Array.isArray(d.installed_apps)?d.installed_apps:[];
   const labels=entries.map(x=>typeof x==="string"?x:(x?.name||""));
   card.append(el("p","Installed apps: "+Number(d.installed_apps_count||labels.length)+" · Listed here: "+labels.length,"text-sm text-teal-200 mt-2"));
   card.append(el("p","Screen time today: "+Number(d.screen_time_minutes||0)+" minutes","muted text-sm mt-2"));
   const filtered=entries.filter(x=>{const name=typeof x==="string"?x:(x?.name||"");const pkg=typeof x==="string"?"":(x?.package||"");return (name+" "+pkg).toLowerCase().includes(query)});
   const list=el("ul","", "mt-2 space-y-2 text-sm");
   filtered.forEach(x=>{const item=document.createElement("li");item.className="flex items-center gap-3 break-words";const name=typeof x==="string"?x:(x?.name||"Unknown app");if(typeof x==="object"&&x?.icon&&String(x.icon).startsWith("data:image/webp;base64,")){const img=document.createElement("img");img.src=x.icon;img.alt="";img.width=32;img.height=32;img.loading="lazy";img.className="rounded-md shrink-0";item.append(img)}const label=el("span",name+(typeof x==="object"&&x?.package?" · "+x.package:""));item.append(label);list.append(item)});
   if(!filtered.length)list.append(el("li",query?"No matching apps.":"No installed-app list shared yet.","muted"));
   card.append(list,el("p","Last summary sync: "+stamp(d.summary_last_synced_at),"muted text-xs mt-3"));
   if(query&&!filtered.length)card.classList.add("hidden");else visible++;
   root.append(card);
  }
  if(!devices.length)root.append(el("p","No registered devices yet.","muted"));
  else if(query&&!visible)root.append(el("p","No installed apps match your search.","muted"));
 }
  const actions={"overview":overview,"location-history":locations,"installed-apps":apps,"premium":async()=>{const b=await api("/billing/status");$("premium-state").textContent=b.plan==="premium"?"Premium active":(b.plan||b.status||"Free");$("premium-detail").textContent="Premium subscription: ₹999/month";}};
 async function load(name){try{if(actions[name])await actions[name]();}catch(e){const target={"overview":"fg-overview-account","location-history":"fg-location-content","installed-apps":"fg-app-content","premium":"premium-detail"}[name];if(target&&$(target))$(target).textContent="Could not load: "+e.message;}}
 document.addEventListener("click",event=>{const btn=event.target.closest("[data-tab]");if(!btn)return;const name=btn.dataset.tab;if(actions[name])load(name);},{capture:true});
 $("fg-refresh-locations")?.addEventListener("click",()=>load("location-history"));
 $("fg-refresh-apps")?.addEventListener("click",()=>load("installed-apps"));
 $("fg-app-search")?.addEventListener("input",()=>load("installed-apps"));
 if(token())load("overview");
})();
