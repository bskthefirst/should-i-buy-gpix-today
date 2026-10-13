// The business record uses a separate private file from retirement holdings.
const fs=require('node:fs'),crypto=require('node:crypto');
const B=require('./business-data.js');
const {locked}=require('./retirement_service.cjs');
function state(file){
  const raw=fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')):{schemaVersion:1,records:[]};
  const book=B.book(raw),revision=crypto.createHash('sha256').update(JSON.stringify(book)).digest('hex');
  return {book,revision,updatedAt:raw.updatedAt||null};
}
async function update(file,input){
  const book=B.book(input.book);
  if(!/^[a-f0-9]{64}$/.test(input.expectedRevision||''))throw new Error('A current revision is required.');
  return locked(file,async()=>{
    const current=state(file);
    if(input.expectedRevision!==current.revision){const e=new Error('The shared business record changed.');e.code='CONFLICT';throw e;}
    const temporary=file+'.'+process.pid+'.tmp';
    fs.writeFileSync(temporary,JSON.stringify({...book,updatedAt:new Date().toISOString()},null,2),{mode:0o600});
    fs.renameSync(temporary,file);fs.chmodSync(file,0o600);
    return state(file);
  });
}
module.exports={state,update};
