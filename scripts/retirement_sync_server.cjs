#!/usr/bin/env node
// Dedicated, authenticated API. Binds only to loopback behind an HTTPS proxy.
const http=require('node:http'),fs=require('node:fs'),crypto=require('node:crypto');
const {state,locked,operate}=require('./retirement_service.cjs');
const J=require('./retirement-journal.js');
function createServer(config,{dataDir=null}={}) {
  if(!/^[A-Za-z0-9_-]{43}$/.test(config.token))throw new Error('A private 256-bit connection key is required.');
  const origin=config.origin||'https://bskthefirst.github.io',attempts=new Map();
  return http.createServer(async(req,res)=>{
    const send=(code,value)=>{res.writeHead(code,{'Content-Type':'application/json','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(JSON.stringify(value));};
    if(req.headers.origin&&req.headers.origin!==origin)return send(403,{error:'Origin not allowed.'});
    res.setHeader('Access-Control-Allow-Origin',origin);res.setHeader('Vary','Origin');
    if(req.method==='OPTIONS') {
      res.setHeader('Access-Control-Allow-Methods','GET, PUT, OPTIONS');
      res.setHeader('Access-Control-Allow-Headers','Authorization, Content-Type');
      res.setHeader('Access-Control-Max-Age','600');return send(204,{});
    }
    const bearer=Buffer.from(req.headers.authorization||''),expected=Buffer.from('Bearer '+config.token);
    if(bearer.length!==expected.length||!crypto.timingSafeEqual(bearer,expected))return send(401,{error:'Connection key required.'});
    const url=(req.url||'').replace(/^\/retirement-sync(?=\/)/,'');
    if(url!=='/v1/plan')return send(404,{error:'Not found.'});
    if(req.method==='GET') {try{return send(200,state(config.planPath));}catch(e){return send(503,{error:'The shared record could not be read.'});}}
    if(req.method!=='PUT')return send(405,{error:'Method not allowed.'});
    if(!/^application\/json(?:;|$)/i.test(req.headers['content-type']||''))return send(415,{error:'JSON required.'});
    const address=req.socket.remoteAddress,now=Date.now(),recent=(attempts.get(address)||[]).filter(t=>now-t<60000);
    if(recent.length>=60)return send(429,{error:'Too many updates. Try again shortly.'});
    attempts.set(address,[...recent,now]);
    const chunks=[];let size=0;
    try {
      for await(const chunk of req) {size+=chunk.length;if(size>2000000)return send(413,{error:'The record is too large.'});chunks.push(chunk);}
      const input=JSON.parse(Buffer.concat(chunks).toString('utf8'));J.profile(input.profile);
      if(!/^[a-f0-9]{64}$/.test(input.expectedRevision||''))return send(400,{error:'A current revision is required.'});
      return await locked(config.planPath,async()=>{
        await operate(config.planPath,'sync',input,dataDir);return send(200,state(config.planPath));
      });
    } catch(e) {
      if(e.code==='CONFLICT')return send(409,{error:'Another device changed the shared record.',...state(config.planPath)});
      if(e.code==='BUSY')return send(503,{error:'An update is running. Try again shortly.'});
      return send(400,{error:'The update could not be saved. Check the entered values.'});
    }
  });
}
if(require.main===module) {
  const config=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const server=createServer(config);server.requestTimeout=15000;
  server.listen(config.port||8787,'127.0.0.1',()=>console.log('Retirement sync ready on loopback.'));
}
module.exports={createServer};
