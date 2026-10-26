#!/usr/bin/env python3
"""Disposable, synthetic OpenClaw API test. No channels or business tools."""
import json
import argparse
import http.server
import os
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile
import time
import threading
import uuid


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, indent=2)+'\n')
    path.chmod(0o600)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--node',type=Path,required=True)
    parser.add_argument('--openclaw-cli',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--deepseek-plugin',type=Path)
    parser.add_argument('--guard-plugin',type=Path)
    parser.add_argument('--primary-credit-cooldown',action='store_true')
    parser.add_argument('--credential-env',default='OPENAI_FALLBACK_API_KEY')
    parser.add_argument('--credential-storage',choices=['env','file'],default='env')
    parser.add_argument('--mode',choices=['direct','failover','network-failover','tools'],default='direct')
    parser.add_argument('--schema', choices=['legacy','2026.10.5'], default='legacy')
    args=parser.parse_args()
    mode=args.mode
    if args.primary_credit_cooldown and (mode!='failover' or not args.guard_plugin):
        raise SystemExit('Credit-cooldown UAT requires failover mode and the candidate guard plugin.')
    tag=mode+('-guarded' if args.guard_plugin else '')
    NODE=args.node.expanduser().resolve()
    CLI=args.openclaw_cli.expanduser().resolve()
    REPORT=args.output_dir.expanduser().resolve()
    if any(part.startswith('.openclaw') for part in REPORT.parts):
        raise SystemExit('Output must be outside live OpenClaw state.')
    REPORT.mkdir(parents=True,exist_ok=True,mode=0o700)
    KEY=os.environ.get(args.credential_env)
    if not KEY:
        raise SystemExit('Supply the authorized API key in the named environment variable.')
    primary_requests=[]
    class Unavailable(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            primary_requests.append(self.path)
            body=b'{"error":{"message":"Synthetic upstream unavailable","type":"server_error"}}'
            self.send_response(503);self.send_header('Content-Type','application/json');self.send_header('Retry-After','120');self.end_headers();self.wfile.write(body)
        def log_message(self,*unused): pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Unavailable)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='openai-canary-', dir=REPORT) as temp:
        state = Path(temp)
        workspace = state/'workspace'
        workspace.mkdir()
        agent_dir = state/'agents/canary/agent'
        config = {
            'agents': {'defaults': {
                'model': {'primary':'openai/gpt-5.4-mini'},
                'modelPolicy':{'allow':['openai/gpt-5.4-mini']},
                'models': {'openai/gpt-5.4-mini': {'agentRuntime':{'id':'openclaw'}, 'params': {'maxTokens':128, 'transport':'sse'}}},
                'heartbeat':{'every':'0m'}, 'timeoutSeconds':45, 'skipBootstrap':True,
            }, 'entries':{'canary':{'workspace':str(workspace),'agentDir':str(agent_dir),'skills':[], 'tools':{'profile':'minimal','deny':['*']}}}},
            'models':{'providers':{'openai':{'baseUrl':'https://api.openai.com/v1','api':'openai-responses','agentRuntime':{'id':'openclaw'},'timeoutSeconds':25,'auth':'api-key','apiKey':{'source':'env','provider':'canary-env','id':'OPENAI_CANARY_API_KEY'},'models':[]}}},
            'secrets':{'providers':{'canary-env':{'source':'env','allowlist':['OPENAI_CANARY_API_KEY']}}},
            'plugins':{'allow':['openai'],'entries':{'openai':{'enabled':True}}},
            'skills':{'allowBundled':[]},
            'gateway':{'mode':'local'}, 'channels':{}, 'tools':{'deny':['*']},
        }
        prompt='Reply with exactly CANARY_OK. This is a synthetic availability test.'
        expected='CANARY_OK'
        if mode=='failover':
            config['agents']['defaults']['model']={'primary':'canary-primary/unavailable','fallbacks':['openai/gpt-5.4-mini']}
            config['agents']['defaults']['modelPolicy']['allow'].append('canary-primary/unavailable')
            config['models']['providers']['canary-primary']={'baseUrl':f'http://127.0.0.1:{server.server_port}/v1','api':'openai-completions','apiKey':'synthetic-test-key','timeoutSeconds':3,'models':[{'id':'unavailable','name':'Synthetic unavailable primary','contextWindow':8192,'maxTokens':128,'input':['text'],'reasoning':False,'cost':{'input':0,'output':0,'cacheRead':0,'cacheWrite':0}}]}
        if mode=='network-failover':
            if args.schema != 'legacy' and not args.deepseek_plugin:
                raise SystemExit('Network failover requires the isolated official DeepSeek plugin entry.')
            if args.deepseek_plugin:
                config['plugins']['load']={'paths':[str(args.deepseek_plugin.expanduser().resolve())]}
                config['plugins']['allow'].append('deepseek')
                config['plugins']['entries']['deepseek']={'enabled':True}
            config['agents']['defaults']['model']={'primary':'deepseek/deepseek-flash','fallbacks':['openai/gpt-5.4-mini']}
            config['agents']['defaults']['modelPolicy']['allow'].append('deepseek/deepseek-flash')
            config['models']['providers']['deepseek']={'baseUrl':'https://api.deepseek.com','api':'openai-completions','apiKey':'synthetic-noncredential','timeoutSeconds':5,'models':[]}
            if args.deepseek_plugin:
                manifest=json.loads((args.deepseek_plugin.expanduser().resolve()/'openclaw.plugin.json').read_text())
                row=next(m for m in manifest['modelCatalog']['providers']['deepseek']['models'] if m['id']=='deepseek-flash')
            else:
                row={'id':'deepseek-flash','name':'DeepSeek Flash','reasoning':True,'input':['text','image'],'contextWindow':1000000,'contextTokens':65536,'maxTokens':8192}
            row.pop('compat',None)
            config['models']['providers']['deepseek']['models']=[row]
        if mode=='tools':
            expected='fixture-'+uuid.uuid4().hex
            plugin=state/'fixture-plugin';plugin.mkdir()
            atomic(plugin/'openclaw.plugin.json',{'contracts':{'tools':['fixture_lookup']},'id':'canary-fixture','name':'Synthetic fixture','activation':{'onStartup':True},'configSchema':{'type':'object','additionalProperties':False}})
            code="""import {appendFileSync} from 'node:fs';
export default {id:'canary-fixture',name:'Synthetic fixture',register(api){api.registerTool({name:'fixture_lookup',description:'Read the synthetic test fixture.',parameters:{type:'object',properties:{id:{type:'string',enum:['test']}},required:['id'],additionalProperties:false},async execute(callId,params){const result={status:'needs_review',deliveryToken:TOKEN};appendFileSync(LOG,JSON.stringify({tool:'fixture_lookup',params})+'\\n');return {content:[{type:'text',text:JSON.stringify(result)}],details:result};}});}};
""".replace('TOKEN',json.dumps(expected)).replace('LOG',json.dumps(str(state/'fixture-calls.jsonl')))
            (plugin/'index.mjs').write_text(code)
            config['plugins']['load']={'paths':[str(plugin/'index.mjs')]}
            config['plugins']['allow'].append('canary-fixture')
            config['plugins']['entries']['canary-fixture']={'enabled':True}
            config['agents']['entries']['canary']['tools']={'profile':'minimal','alsoAllow':['fixture_lookup'],'deny':['session_status']}
            config['tools']={}
            prompt='Call fixture_lookup once with id test, then reply only with its deliveryToken. This is a synthetic tool test; no other actions.'
        if args.guard_plugin:
            guard=state/'guard-plugin';guard.mkdir()
            for name in ['openclaw-plugin.js','guard.mjs','openclaw.plugin.json']:
                shutil.copy2(args.guard_plugin.expanduser().resolve()/name,guard/name)
            (guard/'node_modules').mkdir()
            (guard/'node_modules/openclaw').symlink_to(CLI.parent,target_is_directory=True)
            config['plugins'].setdefault('load',{}).setdefault('paths',[]).append(str(guard/'openclaw-plugin.js'))
            config['plugins']['allow'].append('owlswatch-runtime-guard')
            config['plugins']['entries']['owlswatch-runtime-guard']={'enabled':True,'hooks':{'allowConversationAccess':True}}
            if args.primary_credit_cooldown:
                atomic(state/'health/provider-circuit.json',{'provider':'canary-primary','until':int(time.time()*1000)+600000,'code':'provider_credit'})
        if args.schema == 'legacy':
            defaults=config['agents']['defaults']
            for ref in defaults.pop('modelPolicy')['allow']:
                defaults['models'].setdefault(ref,{})
            defaults['contextTokens']=65536
            config['agents']['list']=[{'id':name,**entry} for name,entry in config['agents'].pop('entries').items()]
        config['agents']['defaults']['thinkingDefault']='off'
        config['models']['providers']['openai']['models']=[{'id':'gpt-5.4-mini','name':'GPT-5.4 mini','reasoning':True,'input':['text','image'],'contextWindow':400000,'contextTokens':65536,'maxTokens':4096,'cost':{'input':0.75,'output':4.5,'cacheRead':0.075,'cacheWrite':0}}]
        (state/'home').mkdir()
        if args.credential_storage == 'file':
            key_path=state/'credential.txt'
            descriptor=os.open(str(key_path),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(descriptor,'w') as stream:
                stream.write(KEY)
            config['secrets']['providers']={'canary-file':{'source':'file','path':str(key_path),'mode':'singleValue'}}
            config['models']['providers']['openai']['apiKey']={'source':'file','provider':'canary-file','id':'value'}
        atomic(state/'openclaw.json',config)
        atomic(agent_dir/'settings.json',{'retry':{'provider':{'maxRetries':0}}})
        env={'HOME':str(state/'home'),'OPENCLAW_HOME':str(state/'home'),
             'OPENCLAW_STATE_DIR':str(state),'OPENCLAW_CONFIG_PATH':str(state/'openclaw.json'),
             'PATH':str(NODE.parent)+os.pathsep+os.environ.get('PATH','/usr/bin:/bin')}
        if args.credential_storage == 'env':
            env['OPENAI_CANARY_API_KEY']=KEY
        validation=subprocess.run([str(NODE),str(CLI),'config','validate'],env=env,capture_output=True,text=True,timeout=20)
        (REPORT/f'{tag}-validation.log').write_text((validation.stdout+validation.stderr).replace(KEY,'[REDACTED]'))
        if validation.returncode:
            raise SystemExit('Disposable configuration failed validation; no provider request made.')
        if mode=='network-failover':
            catalog=subprocess.run([str(NODE),str(CLI),'models','list','--all','--provider','deepseek','--json'],env=env,capture_output=True,text=True,timeout=20)
            (REPORT/'network-catalog.log').write_text((catalog.stdout+catalog.stderr).replace(KEY,'[REDACTED]'))
            if catalog.returncode or 'deepseek/deepseek-flash' not in catalog.stdout:
                raise SystemExit('Current DeepSeek model missing from isolated catalog; not a network-failover pass.')
        started=time.monotonic()
        process=subprocess.Popen([str(NODE),str(CLI),'agent','--local','--agent','canary','--message',prompt,'--json','--timeout','45'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:
            stdout,stderr=process.communicate(timeout=60)
            code=process.returncode
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGTERM)
            try: stdout,stderr=process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);stdout,stderr=process.communicate()
            code=124
        stdout=stdout.replace(KEY,'[REDACTED]');stderr=stderr.replace(KEY,'[REDACTED]')
        (REPORT/f'{tag}-canary.stdout.log').write_text(stdout)
        (REPORT/f'{tag}-canary.stderr.log').write_text(stderr)
        result={'mode':mode,'schema':args.schema,'credentialStorage':args.credential_storage,'exitCode':code,'seconds':round(time.monotonic()-started,2),'syntheticOnly':True,'channelsEnabled':False,'businessToolsEnabled':False,'liveChanges':False,'runtimeVersion':subprocess.check_output([str(NODE),str(CLI),'--version'],text=True).strip(),'nodeVersion':subprocess.check_output([str(NODE),'--version'],text=True).strip(),'primaryAttempts':len(primary_requests)}
        try:
            payload=json.loads(stdout[stdout.index('{'):])
            result['result']=payload
            result['replyMatches']=any(p.get('text','').strip()==expected for p in payload.get('payloads',[]))
            result['provider']=payload.get('meta',{}).get('agentMeta',{}).get('provider')
            result['model']=payload.get('meta',{}).get('agentMeta',{}).get('model')
            result['fixtureCalls']=[json.loads(line) for line in (state/'fixture-calls.jsonl').read_text().splitlines()] if (state/'fixture-calls.jsonl').exists() else []
            result['networkFailureObserved']=('candidate=deepseek/deepseek-flash reason=timeout' in stderr or 'candidate=deepseek/deepseek-flash reason=network' in stderr) and 'Unknown model: deepseek/' not in stderr
            if args.guard_plugin:
                journal=state/'health/runs.jsonl'
                events=[json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
                result['guardCompletedResponse']=any(e.get('kind')=='run_end' and e.get('success') and e.get('responseCompleted') for e in events)
                result['guardAllowedCreditFallback']=any(e.get('kind')=='provider_credit_fallback_allowed' for e in events)
                result['guardCreditCircuitPreserved']=(json.loads((state/'health/provider-circuit.json').read_text()).get('provider')=='canary-primary') if args.primary_credit_cooldown else None
            result['passed']=(mode!='network-failover' or result['networkFailureObserved']) and code==0 and result['replyMatches'] and result['provider']=='openai' and (mode!='failover' or len(primary_requests)==1) and (mode!='tools' or len(result['fixtureCalls'])==1)
            if args.guard_plugin:
                result['passed']=result['passed'] and result['guardCompletedResponse'] and (not args.primary_credit_cooldown or (result['guardAllowedCreditFallback'] and result['guardCreditCircuitPreserved']))
        except (ValueError, json.JSONDecodeError): result['diagnosticsTail']=(stderr+stdout)[-3500:]
        atomic(REPORT/f'{tag}-canary.json',result)
        print(json.dumps({k:v for k,v in result.items() if k!='result'}))
    server.shutdown();server.server_close()
    if not result.get('passed'):
        raise SystemExit(1)

if __name__=='__main__': main()
