// Run from any directory: node --test tests/victorious_studio_graph.test.cjs
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname,"..","app","victorious_studio","studio.js"),"utf8");
const boundary = source.indexOf("\n\nconst byId");
assert.ok(boundary > 0, "Studio graph builder boundary missing");
const buildGraph = vm.runInNewContext(source.slice(0,boundary)+"\nbuildGraph");
const base = {checkpoint:"sdxl.safetensors",positive:"portrait",negative:"",width:512,height:512,batch:1,steps:20,cfg:7,seed:123};

test("plain generation retains original graph",()=>{
  const graph=buildGraph(base);
  assert.equal(Object.keys(graph).length,7);
  assert.equal(graph["5"].inputs.latent_image[0],"4");
  assert.equal(graph["7"].inputs.images[0],"6");
});
test("LoRA connects both text encoders and sampler",()=>{
  const graph=buildGraph({...base,lora:"identity.safetensors",loraStrength:.8});
  assert.equal(graph["2"].inputs.clip[0],"8");
  assert.equal(graph["3"].inputs.clip[0],"8");
  assert.equal(graph["5"].inputs.model[0],"8");
});
test("reference editing replaces empty latent",()=>{
  const graph=buildGraph({...base,reference:"reference.png",denoise:.45});
  assert.equal(graph["4"],undefined);
  assert.equal(graph["5"].inputs.latent_image[0],"11");
  assert.equal(graph["5"].inputs.denoise,.45);
});
test("PhotoMaker uses reference conditioning but not source latent",()=>{
  const graph=buildGraph({...base,referenceMode:"identity",reference:"reference.png",photomaker:"pm.bin",lora:"a",loraStrength:1});
  assert.equal(graph["2"].class_type,"PhotoMakerEncode");
  assert.equal(graph["2"].inputs.clip[0],"8");
  assert.equal(graph["2"].inputs.text,"person photomaker portrait");
  assert.equal(graph["5"].inputs.latent_image[0],"4");
  assert.equal(graph["5"].inputs.denoise,1);
});
test("inpainting composites outside the mask before optional upscaling",()=>{
  const graph=buildGraph({...base,reference:"reference.png",mask:"mask.png",denoise:1,upscale:"model:up.pth"});
  assert.equal(graph["11"].class_type,"VAEEncodeForInpaint");
  assert.equal(graph["15"].inputs.channel,"red");
  assert.equal(graph["16"].inputs.destination[0],"10");
  assert.equal(graph["16"].inputs.mask[0],"15");
  assert.equal(graph["17"].inputs.image[0],"16");
  assert.equal(graph["18"].inputs.model_name,"up.pth");
  assert.equal(graph["7"].inputs.images[0],"17");
});
test("interpolation is distinct from learned upscale",()=>{
  const graph=buildGraph({...base,upscale:"resize"});
  assert.equal(graph["17"].class_type,"ImageScaleBy");
  assert.equal(graph["17"].inputs.scale_by,2);
  assert.equal(graph["18"],undefined);
});
test("rejects incompatible combinations and invalid dimensions",()=>{
  for(const patch of [
    {mask:"m"},{referenceMode:"identity"},
    {referenceMode:"identity",reference:"r",photomaker:"p",mask:"m"},
    {referenceMode:"identity",reference:"r",photomaker:"p",batch:2},
    {referenceMode:"identity",reference:"r",photomaker:"p",positive:"a".repeat(181)},
    {reference:"r",denoise:0},{upscale:"bad"},{lora:"l",loraStrength:3},{width:513}
  ]) assert.throws(()=>buildGraph({...base,...patch}));
});
