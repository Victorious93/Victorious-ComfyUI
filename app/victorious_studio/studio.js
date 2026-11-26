function buildGraph(s) {
  if (!s.checkpoint || !s.positive.trim()) throw new Error("Select a checkpoint and enter a prompt.");
  for (const [name,min,max,step] of [["width",256,1536,64],["height",256,1536,64],["steps",1,80,1],["batch",1,4,1],["cfg",1,20,.5],["seed",0,4294967295,1]]) {
    const value=s[name];
    if (!Number.isFinite(value)||value<min||value>max||(value-min)%step!==0) throw new Error("Invalid "+name);
  }
  return {
    "1":{class_type:"CheckpointLoaderSimple",inputs:{ckpt_name:s.checkpoint}},
    "2":{class_type:"CLIPTextEncode",inputs:{clip:["1",1],text:s.positive}},
    "3":{class_type:"CLIPTextEncode",inputs:{clip:["1",1],text:s.negative}},
    "4":{class_type:"EmptyLatentImage",inputs:{width:s.width,height:s.height,batch_size:s.batch}},
    "5":{class_type:"KSampler",inputs:{model:["1",0],positive:["2",0],negative:["3",0],latent_image:["4",0],seed:s.seed,steps:s.steps,cfg:s.cfg,sampler_name:"euler",scheduler:"normal",denoise:1}},
    "6":{class_type:"VAEDecode",inputs:{samples:["5",0],vae:["1",2]}},
    "7":{class_type:"SaveImage",inputs:{images:["6",0],filename_prefix:"Victorious-Studio"}}
  };
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
refresh.addEventListener("click",refreshModels);
checkpoint.addEventListener("change",syncControls);
refreshModels();
