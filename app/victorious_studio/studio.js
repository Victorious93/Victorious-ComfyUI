function buildGraph(s) {
  if (!s.checkpoint || !s.positive.trim()) throw new Error("Select a checkpoint and enter a prompt.");
  for (const [name,min,max,step] of [["width",256,1536,64],["height",256,1536,64],["steps",1,80,1],["batch",1,4,1],["cfg",1,20,.5],["seed",0,4294967295,1]]) {
    const value=s[name];
    if (!Number.isFinite(value)||value<min||value>max||(value-min)%step!==0) throw new Error("Invalid "+name);
  }
  if(s.lora && (!Number.isFinite(s.loraStrength)||s.loraStrength<0||s.loraStrength>2)) throw new Error("Invalid LoRA strength");
  if(s.reference && (s.batch!==1||!Number.isFinite(s.denoise)||s.denoise<.05||s.denoise>1)) throw new Error("Reference mode requires batch size 1 and denoise between .05 and 1.");
  const graph={
    "1":{class_type:"CheckpointLoaderSimple",inputs:{ckpt_name:s.checkpoint}},
    "2":{class_type:"CLIPTextEncode",inputs:{clip:["1",1],text:s.positive}},
    "3":{class_type:"CLIPTextEncode",inputs:{clip:["1",1],text:s.negative}},
    "4":{class_type:"EmptyLatentImage",inputs:{width:s.width,height:s.height,batch_size:s.batch}},
    "5":{class_type:"KSampler",inputs:{model:["1",0],positive:["2",0],negative:["3",0],latent_image:["4",0],seed:s.seed,steps:s.steps,cfg:s.cfg,sampler_name:"euler",scheduler:"normal",denoise:1}},
    "6":{class_type:"VAEDecode",inputs:{samples:["5",0],vae:["1",2]}},
    "7":{class_type:"SaveImage",inputs:{images:["6",0],filename_prefix:"Victorious-Studio"}}
  };
  if(s.lora){
    graph["8"]={class_type:"LoraLoader",inputs:{model:["1",0],clip:["1",1],lora_name:s.lora,strength_model:s.loraStrength,strength_clip:s.loraStrength}};
    graph["2"].inputs.clip=["8",1];
    graph["3"].inputs.clip=["8",1];
    graph["5"].inputs.model=["8",0];
  }
  if(s.reference){
    delete graph["4"];
    graph["9"]={class_type:"LoadImage",inputs:{image:s.reference}};
    graph["10"]={class_type:"ImageScale",inputs:{image:["9",0],upscale_method:"lanczos",width:s.width,height:s.height,crop:"center"}};
    graph["11"]={class_type:"VAEEncode",inputs:{pixels:["10",0],vae:["1",2]}};
    graph["5"].inputs.latent_image=["11",0];
    graph["5"].inputs.denoise=s.denoise;
  }
  return graph;
}

const byId = id => document.getElementById(id);
const form = byId("generate");
const checkpoint = byId("checkpoint");
const status = byId("status");
const submit = byId("submit");
const cancel = byId("cancel");
const refresh = byId("refresh");
let activeId = null;
let submitting = false;
let refreshing = false;
let timer;
let missing = 0;
let cancelling = false;

function syncControls() {
  submit.disabled = submitting || refreshing || activeId !== null || !checkpoint.value;
  refresh.disabled = refreshing || submitting;
  cancel.disabled = cancelling || activeId === null;
}
function report(message) { status.textContent = message; }

async function api(path, options = {}) {
  const response = await fetch(path, {...options, cache:"no-store", signal:AbortSignal.timeout(15000)});
  if (!response.ok) {
    const data = await response.json().catch(()=>({}));
    const error = new Error(data.error?.message || (typeof data.error === "string" ? data.error : "HTTP "+response.status));
    error.status = response.status;
    if (data.node_errors && Object.keys(data.node_errors).length) error.message += "\n"+JSON.stringify(data.node_errors);
    throw error;
  }
  return response.json();
}
const post = (path, body) => api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
const gib = bytes => Number.isFinite(bytes) ? (bytes/1073741824).toFixed(1)+" GiB" : "unknown";

async function refreshModels() {
  refreshing = true; syncControls();
  try {
    const info = await api("/object_info/CheckpointLoaderSimple");
    const names = info.CheckpointLoaderSimple.input.required.ckpt_name[0];
    if (!Array.isArray(names)) throw new Error("Unexpected checkpoint list");
    const selected = checkpoint.value;
    checkpoint.replaceChildren(new Option(names.length ? "Choose an installed checkpoint" : "No checkpoints installed",""));
    for (const name of names) checkpoint.add(new Option(name,name));
    if (names.includes(selected)) checkpoint.value=selected;
    if (!activeId) report(names.length ? "Ready. Choose a compatible checkpoint." : "Install a compatible checkpoint in ComfyUI’s configured models/checkpoints directory, then refresh.");
  } catch (error) {
    checkpoint.replaceChildren(new Option("Models unavailable",""));
    if (!activeId) report("Could not load checkpoints: "+error.message);
  } finally { refreshing = false; syncControls(); }
  try {
    const stats = await api("/system_stats");
    const device = stats.devices?.[0];
    byId("hardware").textContent = "System RAM: "+gib(stats.system?.ram_total)+(device ? " | "+device.name+(device.type==="cpu" ? "" : " | VRAM: "+gib(device.vram_total)) : "");
  } catch { byId("hardware").textContent = "Hardware information unavailable."; }
}

function showImages(outputs) {
  const gallery = byId("gallery");
  gallery.replaceChildren();
  for (const output of Object.values(outputs || {})) {
    for (const image of output.images || []) {
      if (typeof image.filename !== "string" || image.type !== "output") continue;
      const url = "/view?"+new URLSearchParams({filename:image.filename,subfolder:image.subfolder || "",type:"output"});
      const link = document.createElement("a");
      link.href=url; link.target="_blank"; link.rel="noopener";
      const preview=document.createElement("img");
      preview.src=url; preview.alt="Generated image: "+image.filename; preview.loading="lazy";
      link.append(preview); gallery.append(link);
    }
  }
  return gallery.childElementCount;
}
function finish(message) {
  clearTimeout(timer); activeId=null; missing=0; report(message); syncControls();
}
function schedule() { clearTimeout(timer); if (activeId) timer=setTimeout(poll,2000); }
async function poll() {
  const id=activeId;
  if (!id) return;
  try {
    const job=await api("/api/jobs/"+encodeURIComponent(id));
    if(activeId!==id)return;
    missing=0;
    if(job.status==="completed"){
      const history=await api("/history/"+encodeURIComponent(id));
      if(activeId!==id)return;
      const count=showImages(history[id]?.outputs);
      finish(count ? "Completed. "+count+" image(s) saved." : "Completed; no image previews found. Check ComfyUI output.");
    } else if(job.status==="failed" || job.status==="cancelled"){
      finish("Job "+job.status+". Check the advanced editor or server log for details.");
    } else {
      report("Job "+job.status+" — "+id);
    }
  } catch(error){
    if(activeId!==id)return;
    if(error.status===404 && ++missing>=3) finish("Job no longer in queue or history. It may have been cancelled or removed.");
    else report("Status unavailable; retrying without resubmitting. "+error.message);
  } finally { schedule(); }
}
form.addEventListener("submit",async event=>{
  event.preventDefault();
  if(submitting || activeId || refreshing)return;
  submitting=true; syncControls();
  try {
    const settings={checkpoint:checkpoint.value,positive:byId("positive").value,negative:byId("negative").value};
    for(const id of ["width","height","steps","cfg","batch"])settings[id]=byId(id).valueAsNumber;
    settings.seed=byId("seed").value==="" ? crypto.getRandomValues(new Uint32Array(1))[0] : byId("seed").valueAsNumber;
    const description=byId("character-description").value.trim();
    if(description)settings.positive=description+", "+settings.positive;
    settings.lora=byId("lora").value;
    settings.loraStrength=byId("lora-strength").valueAsNumber;
    settings.reference=byId("reference").value;
    settings.denoise=byId("denoise").valueAsNumber;
    if(settings.lora && !availableLoras.includes(settings.lora))throw new Error("Selected LoRA is unavailable; refresh models.");
    if(settings.reference && !availableReferences.includes(settings.reference))throw new Error("Selected reference is unavailable; refresh models.");
    const prompt=buildGraph(settings);
    byId("gallery").replaceChildren();
    report("Submitting…");
    const result=await post("/prompt",{prompt});
    if(typeof result.prompt_id!=="string")throw new Error("Server did not return a job ID");
    activeId=result.prompt_id; missing=0;
    report("Queued — "+activeId+" | seed "+settings.seed);
    schedule();
  } catch(error){ report("Submission not confirmed: "+error.message+"\nCheck the advanced queue before retrying if the connection was interrupted."); }
  finally{submitting=false;syncControls();}
});
cancel.addEventListener("click",async()=>{
  const id=activeId;
  if(!id || cancelling)return;
  cancelling=true;syncControls();
  try {
    const result=await post("/api/jobs/"+encodeURIComponent(id)+"/cancel",{});
    if(activeId===id)report(result.cancelled ? "Cancellation requested; waiting for server confirmation." : "Job may already be finished; checking status.");
  } catch(error){if(activeId===id)report("Cancellation not confirmed: "+error.message);}
  finally{cancelling=false;syncControls();}
});
refresh.addEventListener("click",()=>{refreshModels();refreshCharacterAssets();});
checkpoint.addEventListener("change",syncControls);

const profileKey="victorious-studio.characters.v1";
const profileSelect=byId("character");
const characterStatus=byId("character-status");
const loraSelect=byId("lora");
const referenceSelect=byId("reference");
let profiles=[];
let availableLoras=[];
let availableReferences=[];
let assetsLoading=false;
let uploading=false;
let profileStorageOK=true;

function characterReport(message){characterStatus.textContent=message;}
function validProfile(p){
  return p && typeof p.id==="string" && typeof p.name==="string" && p.name.length>0 && p.name.length<=80 &&
    typeof p.description==="string" && p.description.length<=2000 && typeof p.lora==="string" &&
    typeof p.reference==="string" && Number.isFinite(p.strength) && p.strength>=0 && p.strength<=2 &&
    Number.isFinite(p.denoise) && p.denoise>=.05 && p.denoise<=1;
}
function renderProfiles(selected=""){
  profileSelect.replaceChildren(new Option("No saved profile",""));
  for(const p of profiles)profileSelect.add(new Option(p.name,p.id));
  profileSelect.value=selected;
}
function setAssetOptions(select, values, empty, selected=select.value){
  select.replaceChildren(new Option(empty,""));
  for(const value of values)select.add(new Option(value,value));
  if(selected && !values.includes(selected))select.add(new Option(selected+" (unavailable)",selected));
  select.value=selected;
}
function previewReference(){
  const image=byId("reference-preview");
  const name=referenceSelect.value;
  image.hidden=!name || !availableReferences.includes(name);
  if(image.hidden)image.removeAttribute("src");
  else image.src="/view?"+new URLSearchParams({filename:name,type:"input"});
}
function updateReference(){
  previewReference();
  if(referenceSelect.value)byId("batch").value="1";
}
function storeProfiles(next){
  if(!profileStorageOK)throw new Error("Profile storage is unavailable or invalid; existing data was not overwritten.");
  localStorage.setItem(profileKey,JSON.stringify(next));
  profiles=next;
}
try{
  const raw=localStorage.getItem(profileKey);
  if(raw!==null){
    const value=JSON.parse(raw);
    if(!Array.isArray(value)||value.length>100||!value.every(validProfile))throw new Error("Invalid profile storage");
    profiles=value;
  }
}catch(error){profileStorageOK=false;characterReport("Saved profiles unavailable: "+error.message);}
renderProfiles();
profileSelect.addEventListener("change",()=>{
  const p=profiles.find(item=>item.id===profileSelect.value);
  byId("character-name").value=p?.name||"";
  byId("character-description").value=p?.description||"";
  byId("lora-strength").value=p?.strength??.8;
  byId("denoise").value=p?.denoise??.45;
  setAssetOptions(loraSelect,availableLoras,"No LoRA",p?.lora||"");
  setAssetOptions(referenceSelect,availableReferences,"No reference",p?.reference||"");
  updateReference();
});
byId("save-character").addEventListener("click",()=>{
  try{
    const name=byId("character-name").value.trim();
    const selected=profileSelect.value;
    const id=selected||Array.from(crypto.getRandomValues(new Uint32Array(4)),n=>n.toString(16).padStart(8,"0")).join("");
    const p={id,name,description:byId("character-description").value.trim(),lora:loraSelect.value,reference:referenceSelect.value,strength:byId("lora-strength").valueAsNumber,denoise:byId("denoise").valueAsNumber};
    if(!validProfile(p))throw new Error("Enter a name and valid character settings.");
    if(!selected && profiles.length>=100)throw new Error("Profile limit reached (100).");
    storeProfiles([...profiles.filter(item=>item.id!==id),p]);
    renderProfiles(id);characterReport("Profile saved in this browser.");
  }catch(error){characterReport("Could not save: "+error.message);}
});
byId("delete-character").addEventListener("click",()=>{
  const id=profileSelect.value;
  if(!id)return;
  if(!confirm("Delete this character profile? Uploaded reference files will remain on the server."))return;
  try{
    storeProfiles(profiles.filter(p=>p.id!==id));
    renderProfiles();profileSelect.dispatchEvent(new Event("change"));characterReport("Profile deleted.");
  }catch(error){characterReport("Could not delete: "+error.message);}
});
referenceSelect.addEventListener("change",updateReference);

async function refreshCharacterAssets(){
  if(assetsLoading)return;
  assetsLoading=true;
  const results=await Promise.allSettled([api("/object_info/LoraLoader"),api("/object_info/LoadImage")]);
  const errors=[];
  try{
    const names=results[0].value?.LoraLoader?.input?.required?.lora_name?.[0];
    if(!Array.isArray(names))throw new Error("LoRA list unavailable");
    availableLoras=names.filter(n=>typeof n==="string");
  }catch(error){availableLoras=[];errors.push(error.message);}
  try{
    const names=results[1].value?.LoadImage?.input?.required?.image?.[0];
    if(!Array.isArray(names))throw new Error("Reference list unavailable");
    availableReferences=names.filter(n=>typeof n==="string");
  }catch(error){availableReferences=[];errors.push(error.message);}
  setAssetOptions(loraSelect,availableLoras,"No LoRA");
  setAssetOptions(referenceSelect,availableReferences,"No reference");
  previewReference();
  assetsLoading=false;
  if(errors.length)characterReport(errors.join(". "));
}
byId("upload-reference").addEventListener("click",async()=>{
  if(uploading)return;
  const file=byId("reference-file").files[0];
  if(!file||!["image/png","image/jpeg"].includes(file.type)||file.size>10*1024*1024||!file.size){
    characterReport("Choose a PNG or JPEG file up to 10 MiB.");return;
  }
  uploading=true;byId("upload-reference").disabled=true;
  try{
    const bitmap=await createImageBitmap(file);
    try{
      if(bitmap.width*bitmap.height>24000000)throw new Error("Reference exceeds 24 megapixels.");
      const ratio=Math.min(1,2048/Math.max(bitmap.width,bitmap.height));
      const canvas=document.createElement("canvas");
      canvas.width=Math.max(1,Math.round(bitmap.width*ratio));
      canvas.height=Math.max(1,Math.round(bitmap.height*ratio));
      canvas.getContext("2d").drawImage(bitmap,0,0,canvas.width,canvas.height);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/png"));
      if(!blob||blob.size>10*1024*1024)throw new Error("Converted image exceeds upload limit.");
      const name="victorious-ref-"+Array.from(crypto.getRandomValues(new Uint32Array(4)),n=>n.toString(16).padStart(8,"0")).join("")+".png";
      const data=new FormData();
      data.append("image",blob,name);data.append("type","input");
      const result=await api("/upload/image",{method:"POST",body:data});
      if(result.type!=="input"||result.subfolder||typeof result.name!=="string")throw new Error("Unexpected upload response.");
      availableReferences=[...new Set([...availableReferences,result.name])];
      setAssetOptions(referenceSelect,availableReferences,"No reference",result.name);
      updateReference();byId("reference-file").value="";
      characterReport("Reference uploaded. Save the profile to remember this selection.");
    }finally{bitmap.close();}
  }catch(error){characterReport("Upload not confirmed: "+error.message);}
  finally{uploading=false;byId("upload-reference").disabled=false;}
});
refreshModels();
refreshCharacterAssets();
