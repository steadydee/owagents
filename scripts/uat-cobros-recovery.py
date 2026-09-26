#!/usr/bin/env python3
"""Isolated real-model UAT. Offline tools, no Telegram or application writes."""
import json, os, shutil, subprocess, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
assert os.environ.get('DEEPSEEK_API_KEY'), 'Provide the model key through environment only.'
with tempfile.TemporaryDirectory(prefix='cobros-recovery-uat-') as tmp:
    root = Path(tmp)
    workspace = root / 'workspace'
    shutil.copytree(ROOT / 'openclaw/agents/cobros', workspace)
    tool_dir = workspace / 'tools/owlswatch_cobros'
    shutil.copytree(ROOT / 'tools/owlswatch_cobros', tool_dir)
    catalog = subprocess.check_output(['python3', str(tool_dir / 'server.py'), 'catalog'], text=True)
    (tool_dir / 'catalog-fixture.json').write_text(catalog)
    shutil.copyfile(tool_dir / 'tests/fixture_server.py', tool_dir / 'server.py')
    profile = json.loads((ROOT / 'openclaw/profiles/owlswatch/openclaw.example.json').read_text())
    agent = next(a for a in profile['agents']['list'] if a['id'] == 'cobros')
    agent.update(workspace=str(workspace), agentDir=str(root / 'agent'), model='deepseek/deepseek-reasoner', models={})
    config = {'agents': {'defaults': {'model': {'primary': 'deepseek/deepseek-reasoner'}}, 'list': [agent]},
        'gateway': {'mode': 'local'},
        'models': {'providers': {'deepseek': {'baseUrl': 'https://api.deepseek.com', 'api': 'openai-completions',
            'apiKey': '${DEEPSEEK_API_KEY}', 'models': [{'id': 'deepseek-reasoner', 'name': 'DeepSeek Reasoner',
            'reasoning': True, 'input': ['text'], 'contextWindow': 128000, 'maxTokens': 8192}]}}},
        'plugins': {'allow': ['owlswatch-cobros', 'deepseek'], 'load': {'paths': [str(tool_dir)]},
                    'entries': {'owlswatch-cobros': {'enabled': True}, 'deepseek': {'enabled': True}}}}
    path = root / 'openclaw.json'; path.write_text(json.dumps(config))
    env = dict(os.environ, OPENCLAW_CONFIG_PATH=str(path), OPENCLAW_STATE_DIR=str(root / 'state'),
               OPENCLAW_HOME=str(root / 'home'), PYTHONPYCACHEPREFIX=str(root / 'pycache'))
    for key in list(env):
        if key.startswith(('TELEGRAM_', 'OPERATIONS_', 'OWLSWATCH_', 'GMAIL_', 'GOOGLE_')): del env[key]
    for scenario in ('missing_nit', 'lookup_failure'):
        calls = root / (scenario + '.jsonl')
        env.update(COBROS_UAT_CALLS=str(calls), COBROS_UAT_SCENARIO=scenario)
        result = subprocess.run(['openclaw', 'agent', '--local', '--agent', 'cobros', '--session-key',
            'agent:cobros:uat-' + scenario, '--message',
            'Necesito cuenta de cobro para Agencia Ejemplo por $390.000, pasadia con guia del 24 de septiembre de 2026. '
            'A nombre de Luz Adriana Valencia Ortiz. No tengo el NIT. Busca en Gmail y dime si falta informacion.',
            '--json', '--timeout', '180'], env=env, capture_output=True, text=True, timeout=240)
        assert result.returncode == 0, (result.stderr + result.stdout)[-2000:].replace(os.environ['DEEPSEEK_API_KEY'], '[redacted]')
        names = [json.loads(line)['name'] for line in calls.read_text().splitlines()]
        assert 1 <= names.count('owlswatch_cobros_search_gmail_threads') <= 3, names
        assert not any('create_' in n or 'send_telegram' in n for n in names), names
        payload = json.loads(result.stdout[result.stdout.index('{'):])
        reply = ' '.join(p.get('text', '') for p in payload.get('payloads', []))
        assert 'nit' in reply.lower() and '?' in reply, reply
        assert 'compaction' not in reply.lower(), reply
        transcripts = list((root / 'state/agents/cobros/sessions').glob('*.jsonl'))
        assert transcripts
        for file in transcripts:
            for line in file.read_text().splitlines():
                parts = json.loads(line).get('message', {}).get('content', [])
                for part in parts if isinstance(parts, list) else []:
                    assert not (part.get('type') == 'toolCall' and part.get('name') in ('read', 'message')), part
        print('PASS ' + scenario + ': bounded lookup, missing-info question, no document/email writes', flush=True)
