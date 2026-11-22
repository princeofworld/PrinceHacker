// Keep browser and Android authentication on the exact same API origin.
const API_BASE="https://princehacker.onrender.com",$=id=>document.getElementById(id);
let token=null,emailAddress="";
function readableApiError(data,status){
  const detail=data?.detail??data?.message??data?.error;
  if(typeof detail==="string"&&detail.trim()) return detail;
  if(Array.isArray(detail)){
    const messages=detail.map(item=>{
      if(typeof item==="string") return item;
      if(item&&typeof item==="object") return item.msg||item.message||item.detail||"";
      return "";
    }).filter(Boolean);
    if(messages.length) return messages.join(" ");
  }
  if(detail&&typeof detail==="object"){
    const message=detail.message||detail.msg||detail.error_description||detail.error;
    if(typeof message==="string"&&message.trim()) return message;
  }
  if(status===409) return "An account with this email already exists. Please sign in instead.";
  if(status===401) return "Email or password is incorrect. Check your details and try again.";
  if(status===422) return "Please enter a valid email address and a password with at least 10 characters.";
  if(status===429) return "Too many attempts. Please wait a moment and try again.";
  if(status>=500) return "FamilyGuard service is temporarily unavailable. Please try again later.";
  return status ? "Request failed (HTTP "+status+"). Please check your details and try again." : "Something went wrong. Please try again.";
}
async function api(path,opts={}){
  const headers={Accept:"application/json",...(opts.headers||{})};
  if(opts.body) headers["Content-Type"]="application/json";
  if(token) headers.Authorization="Bearer "+token;
  let res;
  try { res=await fetch(API_BASE+path,{...opts,headers}); }
  catch(e){ throw new Error("Could not connect to FamilyGuard. Check your internet connection and try again."); }
  const raw=await res.text();
  let data={};
  if(raw){
    try { data=JSON.parse(raw); }
    catch { data={message:raw.slice(0,240)}; }
  }
  if(!res.ok) throw new Error(readableApiError(data,res.status));
  if(!data||typeof data!=="object") throw new Error("FamilyGuard returned an unexpected response. Please try again.");
  return data;
}
function showAuth(){
  $("auth").classList.remove("hidden");
  $("auth").scrollIntoView({behavior:"smooth",block:"center"});
}
function showDash(){
  $("auth").classList.add("hidden");
  $("dashboard").classList.remove("hidden");
  $("account-label").textContent=emailAddress;
  history.replaceState(null,"","#dashboard");
  $("dashboard").scrollIntoView({behavior:"smooth",block:"start"});
}
["nav-login","hero-login","free-login"].forEach(id=>$(id).addEventListener("click",showAuth));$("close-auth").onclick=()=>$("auth").classList.add("hidden");
async function authAction(create){
  const message=$("auth-message"),email=$("email").value.trim().toLowerCase(),password=$("password").value;
  const submit=$("auth-form").querySelector('button[type="submit"]'),register=$("register");
  if(!email){message.textContent="Enter your email address.";return}
  if(!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)){message.textContent="Enter a valid email address.";return}
  if(password.length<10){message.textContent="Use a password with at least 10 characters.";return}
  message.textContent=create?"Creating your account…":"Signing in…";
  submit.disabled=true;register.disabled=true;
  try{
    const d=await api(create?"/auth/register":"/auth/login",{method:"POST",body:JSON.stringify({email,password})});
    if(!d.idToken||!d.localId) throw new Error("Authentication response was incomplete. Please try again.");
    token=d.idToken;emailAddress=email;
    sessionStorage.setItem("fg_token",token);sessionStorage.setItem("fg_email",emailAddress);
    $("password").value="";
    showDash();
    await refreshBilling();
  }catch(e){
    message.textContent=e instanceof Error&&e.message?e.message:"Unable to complete authentication. Please try again.";
  }finally{
    submit.disabled=false;register.disabled=false;
  }
}
$("auth-form").onsubmit=e=>{e.preventDefault();authAction(false)};$("register").onclick=()=>authAction(true);
function signOut(){token=null;emailAddress="";sessionStorage.removeItem("fg_token");sessionStorage.removeItem("fg_email");$("dashboard").classList.add("hidden");$("locations").innerHTML='<p class="muted">Enter your Family ID to load reports.</p>'}
$("logout").onclick=signOut;$("clear-session").onclick=signOut;
async function refreshBilling(){try{const s=await api("/billing/status");$("plan-status").textContent=s.premium?"Premium active"+(s.expiresAt?" · expires "+new Date(s.expiresAt*1000).toLocaleDateString():""):"Free plan";$("dashboard-upgrade").textContent=s.premium?"Premium active":"Upgrade • ₹999 / 30 days";$("dashboard-upgrade").disabled=s.premium;await refreshUtrStatus()}catch(e){$("plan-status").textContent=e.message}}
async function refreshUtrStatus(){try{const d=await api("/payment/my-utr");if(d.payments?.length){const p=d.payments[0];$("utr-message").textContent="Latest UTR "+p.utr+": "+p.status.toUpperCase()+(p.review_note?" · "+p.review_note:"")} }catch(e){}}
async function loadUpiCheckout(){try{const c=await api("/payment/upi-config");$("upi-payment").classList.remove("hidden");$("upi-payment").scrollIntoView({behavior:"smooth",block:"center"});if(!c.enabled){$("upi-details").textContent=c.message||"UPI payments are not configured.";return} $("upi-details").textContent="Pay ₹999 to "+c.payeeName+" · UPI ID: "+c.upiId+" · Access period: 30 days after manual approval.";const link=$("upi-pay-link");link.href=c.upiUri;link.classList.remove("hidden");$("upi-qr").replaceChildren();if(window.QRCode)new QRCode($("upi-qr"),{text:c.upiUri,width:190,height:190});else $("upi-qr").textContent="QR generator unavailable. Use the UPI payment link."; }catch(e){$("upi-details").textContent=e.message}}
async function checkout(){if(!token){showAuth();$("auth-message").textContent="Sign in before paying.";return}try{const upi=await api("/payment/upi-config");if(upi.enabled){await loadUpiCheckout();return}const c=await api("/billing/config");if(!c.configured){$("payment-status").textContent=upi.message||"UPI and Razorpay are not configured. No payment has been taken.";return}const s=await api("/payment/create-order",{method:"POST",body:"{}"});const script=document.createElement("script");script.src="https://checkout.razorpay.com/v1/checkout.js";script.onload=()=>{const rz=new Razorpay({key:s.keyId,subscription_id:s.subscriptionId,name:"FamilyGuard",description:"₹999/month Premium",prefill:{email:emailAddress},theme:{color:"#49d6b0"},handler:async r=>{try{const v=await api("/payment/verify",{method:"POST",body:JSON.stringify({razorpay_subscription_id:r.razorpay_subscription_id,razorpay_payment_id:r.razorpay_payment_id,razorpay_signature:r.razorpay_signature})});$("payment-status").textContent=v.message;await refreshBilling()}catch(e){$("payment-status").textContent="Payment callback received; verification pending: "+e.message}}});rz.on("payment.failed",r=>$("payment-status").textContent=r.error?.description||"Payment did not complete.");rz.open()};script.onerror=()=>$("payment-status").textContent="Secure checkout could not load.";document.head.appendChild(script)}catch(e){$("payment-status").textContent=e.message}}
$("submit-utr").onclick=async()=>{const utr=$("utr-input").value.trim();if(!/^\d{12}$/.test(utr)){$("utr-message").textContent="Enter exactly 12 digits.";return}try{const r=await api("/payment/submit-utr",{method:"POST",body:JSON.stringify({utr})});$("utr-message").textContent=r.message;$("utr-input").value="";await refreshUtrStatus()}catch(e){$("utr-message").textContent=e.message}};
$("premium-cta").onclick=checkout;$("dashboard-upgrade").onclick=checkout;
$("create-family").onclick=async()=>{const name=$("family-name").value.trim()||"My Family";$("pairing-message").textContent="Creating family…";try{const r=await api("/family/create",{method:"POST",body:JSON.stringify({displayName:name})});$("family-id").value=r.familyId;$("invite-code").value=r.inviteCode;$("pairing-message").textContent="Family created. Invite code "+r.inviteCode+" expires in "+Math.round(r.expiresInSeconds/60)+" minutes."}catch(e){$("pairing-message").textContent=e.message}};
$("join-family").onclick=async()=>{const code=$("invite-code").value.replace(/\D/g,"").slice(0,6);if(code.length!==6){$("pairing-message").textContent="Enter the 6-digit invite code.";return}try{const r=await api("/family/join",{method:"POST",body:JSON.stringify({inviteCode:code})});$("family-id").value=r.familyId;$("pairing-message").textContent="Joined "+(r.familyName||"family")+". Location sharing remains off until each member opts in on their device."}catch(e){$("pairing-message").textContent=e.message}};
$("refresh").onclick=async()=>{const id=$("family-id").value.trim();if(!id){$("location-message").textContent="Enter your Family ID first.";return}try{const d=await api("/location/family?familyId="+encodeURIComponent(id));$("locations").replaceChildren();if(!(d.locations||[]).length)$("locations").innerHTML='<p class="muted">No current opted-in locations.</p>';(d.locations||[]).forEach(v=>{const card=document.createElement("div");card.className="location-item";const title=document.createElement("strong");title.textContent="Family member • "+String(v.uid).slice(0,8);const a=document.createElement("p");a.textContent="Location: "+Number(v.latitude).toFixed(5)+", "+Number(v.longitude).toFixed(5);const b=document.createElement("p");b.textContent="Status: "+(v.isOnline?"Recent check-in":"Offline / stale check-in")+" • Battery: "+(Number.isInteger(v.batteryPercent)?v.batteryPercent+"%":"Not reported");const t=document.createElement("p");t.textContent="Last check-in: "+new Date(v.capturedAt).toLocaleString();card.append(title,a,b,t);$("locations").append(card)});$("location-message").textContent="Reports refreshed."}catch(e){$("location-message").textContent=e.message}};
(async()=>{try{await api("/health");$("connection").textContent="Service online"}catch{$("connection").textContent="Service unavailable"}if("serviceWorker"in navigator)navigator.serviceWorker.register("/sw.js").catch(()=>{});const saved=sessionStorage.getItem("fg_token");if(saved){token=saved;emailAddress=sessionStorage.getItem("fg_email")||"";try{await api("/health");showDash();await refreshBilling()}catch{signOut()}}})();