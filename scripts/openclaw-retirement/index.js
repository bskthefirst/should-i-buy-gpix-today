import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
const execute=promisify(execFile);
export default {
  id:'gpix-retirement',name:'Dividend Journey',
  register(api) {
    const c=api.pluginConfig;
    const commands={holdings:['holdings','Report total shares and average purchase prices'],dividends:['progress','Show your dividend journey'],expense:['expense','Choose the monthly expense dividends should cover'],retire_history:['history','Show your reported holdings changes'],retire_export:['export','Download your private holdings file for the website'],retire_import:['import','Apply an update copied from the retirement website']};
    for(const [name,[action,description]] of Object.entries(commands)) api.registerCommand({
      name,description,acceptsArgs:true,requireAuth:true,channels:['telegram'],
      handler:async ctx=> {
        // Host allowlisting alone is not enough: these commands belong to one private owner.
        if(ctx.channel!=='telegram'||!ctx.isAuthorizedSender||String(ctx.senderId)!==c.ownerId||ctx.accountId!=='default'||!['',c.ownerId,'telegram:'+c.ownerId].includes(ctx.to||'')) return {text:'These holdings commands are available only in the owner’s private Telegram chat.'};
        try {
          const {stdout}=await execute(c.nodePath,[c.servicePath,c.planPath,action,ctx.args||''],{timeout:50000,maxBuffer:2e6});
          const result=JSON.parse(stdout);
          if(action==='export'&&result.mediaUrl) {
            const mediaDir=c.mediaDir||path.join(os.homedir(),'.openclaw','media','retirement');
            await fs.mkdir(mediaDir,{recursive:true,mode:0o700});
            const destination=path.join(mediaDir,'retirement-telegram-plan.json');
            await fs.copyFile(result.mediaUrl,destination);await fs.chmod(destination,0o600);
            result.mediaUrl=destination;
          }
          return result;
        } catch(e) { return {text:'The update was not completed. '+String(e.stderr||e.message).replace(/^Retirement update stopped: /,'').trim().slice(0,240)}; }
      }
    });
  }
};
