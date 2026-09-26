import json, os, sys
name, args = sys.argv[2], json.load(sys.stdin)
with open(os.environ['COBROS_UAT_CALLS'], 'a') as f:
    f.write(json.dumps({'name': name, 'args': args}) + '\n')
if name.endswith('search_gmail_threads'):
    result = {'ok': True, 'matches': []} if os.environ['COBROS_UAT_SCENARIO'] == 'missing_nit' else {'ok': False, 'error': {'code': 'source_unavailable', 'retryable': False}}
elif name.endswith('prepare'):
    result = {'ok': True, 'status': 'needs_info', 'missingFields': ['operator_legal_name_and_nit'], 'question': 'What is the operator legal name and NIT?'}
elif name.endswith('memory_log'):
    result = {'ok': True}
else:
    result = {'ok': False, 'error': {'code': 'unexpected_tool', 'retryable': False}}
print(json.dumps(result))
