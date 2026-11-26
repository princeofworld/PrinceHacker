"use strict";
(()=> {
 const $=id=>document.getElementById(id);
 const token=()=>localStorage.getItem("fg_token")||"";
 async function load(){
  const root=$("fg-media-content"); if(!root)return;
  root.textContent="Loading photos…";
  const response=await fetch("/api/media",{headers:{Authorization:"Bearer "+token(),Accept:"application/json"}});
  const data=await response.json().catch(()=>({}));
  if(response.status===401){root.textContent="Session expired. Sign in again.";return;}
  if(!response.ok)throw new Error(data.detail||"Could not load gallery.");
  root.replaceChildren();
  if(!(data.items||[]).length){root.textContent="No photos uploaded yet. Select photos in the Android app to add them.";return;}
  for(const item of data.items){
   const card=document.createElement("article");card.className="rounded-xl border border-slate-700 p-2";
   const img=document.createElement("img");img.alt=item.filename||"Shared photo";img.loading="lazy";img.className="w-full aspect-square object-cover rounded-lg bg-slate-900";
   const photo=await fetch("/api/media/"+encodeURIComponent(item.id),{headers:{Authorization:"Bearer "+token()}});
   if(photo.ok){const blob=await photo.blob();img.src=URL.createObjectURL(blob);}
   else img.alt="Photo unavailable";
   const name=document.createElement("p");name.textContent=item.filename||"Photo";name.className="text-xs mt-2 break-all";
   const meta=document.createElement("p");meta.textContent=(Number(item.sizeBytes)/1024/1024).toFixed(2)+" MB · "+new Date(Number(item.createdAt)*1000).toLocaleString();meta.className="muted text-xs mt-1";
   const del=document.createElement("button");del.type="button";del.textContent="Delete";del.className="btn secondary mt-2 w-full";
   del.addEventListener("click",async()=>{if(!confirm("Delete this photo from your account gallery?"))return;del.disabled=true;try{const r=await fetch("/api/media/"+encodeURIComponent(item.id),{method:"DELETE",headers:{Authorization:"Bearer "+token()}});if(!r.ok)throw new Error("Delete failed.");await load();}catch(e){del.disabled=false;alert(e.message);}});
   card.append(img,name,meta,del);root.append(card);
  }
 }
 $("fg-refresh-media")?.addEventListener("click",()=>load().catch(e=>{$("fg-media-content").textContent=e.message;}));
 document.addEventListener("click",e=>{if(e.target.closest('[data-tab="media-gallery"]'))load().catch(err=>{$("fg-media-content").textContent=err.message;});});
 if(token())load().catch(e=>{$("fg-media-content").textContent=e.message;});
})();