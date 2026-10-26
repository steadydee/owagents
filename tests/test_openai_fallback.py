import copy
import importlib.util
import unittest
import tempfile
import stat
from pathlib import Path

spec = importlib.util.spec_from_file_location("fallback", Path(__file__).resolve().parents[1]/"scripts/prepare-openai-fallback.py")
fallback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fallback)


class FallbackTests(unittest.TestCase):
    def fixture(self, keyed=False):
        agent = {"model": "deepseek/deepseek-reasoner", "tools": {"alsoAllow": ["narrow_read"], "deny": ["exec"]}}
        return {"agents": {"defaults": {"model": {"primary": "deepseek/deepseek-reasoner"}, "timeoutSeconds": 180},
                           **({"entries": {"nomina": agent}} if keyed else {"list": [{"id": "nomina", **agent}]})},
                "channels": {"telegram": {"botToken": "fixture-value", "groups": {"fixture": {"allowFrom": ["owner"]}}}},
                "mcp": {"servers": {"fixture": {"env": {"APP_TOKEN": "fixture-app-token"}}}},
                "auth": {"profiles": {"existing": {"provider": "deepseek", "mode": "api_key"}}}}

    def test_both_schemas_explicit_agent_fallback_and_no_authority_changes(self):
        for keyed in [False, True]:
            with self.subTest(keyed=keyed):
                source = self.fixture(keyed); before = copy.deepcopy(source)
                result = fallback.prepare(source, "nomina")
                self.assertEqual(source, before)
                for section in ["channels", "mcp", "auth"]:
                    self.assertEqual(result[section], before[section])
                agent = fallback.agent_entries(result)["nomina"]
                self.assertEqual(agent["tools"], fallback.agent_entries(before)["nomina"]["tools"])
                self.assertEqual(agent["model"], {"primary": fallback.PRIMARY_MODEL, "fallbacks": [fallback.MODEL]})
                self.assertEqual(result["agents"]["defaults"]["timeoutSeconds"], 180)
                self.assertEqual(result["models"]["providers"]["openai"]["auth"], "api-key")

    def test_idempotent_and_preserves_existing_fallback(self):
        source = self.fixture()
        source['agents']['list'][0]['model'] = {'primary':'deepseek/deepseek-reasoner','fallbacks':['other/test']}
        source['models']={'providers':{'other':{'models':[{'id':'test','contextWindow':32000,'maxTokens':2048}]}}}
        once = fallback.prepare(source, "nomina")
        self.assertEqual(once, fallback.prepare(once, "nomina"))
        self.assertEqual(fallback.agent_entries(once)['nomina']['model']['fallbacks'], ['other/test', fallback.MODEL])

    def test_unauthorized_profiles_and_agents_rejected(self):
        for profile in ['luna', 'finca', 'other']:
            with self.assertRaises(ValueError): fallback.prepare(self.fixture(), profile)
        source = self.fixture(); source['agents']['list'][0]['id'] = 'other'
        with self.assertRaises(ValueError): fallback.prepare(source, 'nomina')

    def test_custom_endpoint_or_transport_not_overwritten(self):
        for value in [{'baseUrl':'https://example.invalid/v1'}, {'request':{'proxy':{'mode':'env-proxy'}}}]:
            source=self.fixture(); source['models']={'providers':{'openai':value}}
            with self.assertRaises(ValueError): fallback.prepare(source, 'nomina')

    def test_explicit_model_policy_retained_and_extended(self):
        source=self.fixture(True)
        source['agents']['entries']['nomina']['modelPolicy']={'allow':['deepseek/deepseek-reasoner']}
        result=fallback.prepare(source,'nomina')
        self.assertEqual(result['agents']['entries']['nomina']['modelPolicy']['allow'],[fallback.PRIMARY_MODEL,fallback.MODEL])

    def test_session_budget_preserves_zero_and_unrelated_settings(self):
        source={'retry':{'provider':{'maxRetries':0,'maxRetryDelayMs':1000},'other':'preserve'},'compaction':{'enabled':True}}
        self.assertEqual(source, fallback.prepare_session_settings(source))
        self.assertEqual(fallback.prepare_session_settings({'retry':{'provider':{'maxRetries':8}}})['retry']['provider'], {'maxRetries':0})

    def test_missing_primary_and_ambiguous_schema_fail_closed(self):
        source=self.fixture(); del source['agents']['defaults']['model']
        with self.assertRaises(ValueError):fallback.prepare(source,'nomina')
        source=self.fixture();source['agents']['entries']={}
        with self.assertRaises(ValueError):fallback.prepare(source,'nomina')

    def test_secret_candidates_reject_repos_worktrees_and_live_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            for name, git_file in [('repo',False),('worktree',True)]:
                root=base/name;root.mkdir()
                if git_file:(root/'.git').write_text('gitdir: fixture')
                else:(root/'.git').mkdir()
                with self.assertRaises(ValueError):fallback.validate_output_dir(root/'candidate',self.fixture())
            source=self.fixture();source['agents']['list'][0]['workspace']=str(base/'workspace')
            with self.assertRaises(ValueError):fallback.validate_output_dir(base/'workspace'/'candidate',source)
            with self.assertRaises(ValueError):fallback.validate_output_dir(base/'.openclaw-luna'/'candidate',source)
            with self.assertRaises(ValueError):fallback.validate_output_dir(Path.home()/'new-candidate',source)

    def test_private_candidate_mode_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            target=fallback.validate_output_dir(Path(temp)/'candidate',self.fixture())
            fallback.write_private(target/'config.json',{'fixture':'private'})
            self.assertEqual(stat.S_IMODE((target/'config.json').stat().st_mode),0o600)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o700)
            with self.assertRaises(ValueError):fallback.validate_output_dir(target,self.fixture())

    def test_retired_primary_migrates_with_bounded_context_and_same_thinking_mode(self):
        source=self.fixture(True)
        result=fallback.prepare(source,'nomina')
        self.assertNotIn('contextTokens',result['agents']['defaults'])
        row=result['models']['providers']['deepseek']['models'][0]
        self.assertEqual(row['id'],'deepseek-flash')
        self.assertEqual(row['contextTokens'],65536)
        self.assertEqual(result['models']['providers']['openai']['models'][0]['contextTokens'],65536)
        self.assertEqual(row['maxTokens'],8192)
        self.assertEqual(result['agents']['entries']['nomina']['models'][fallback.PRIMARY_MODEL]['params']['thinking'],'high')
        source['agents']['entries']['nomina']['model']='deepseek/deepseek-chat'
        result=fallback.prepare(source,'nomina')
        self.assertEqual(result['agents']['entries']['nomina']['models'][fallback.PRIMARY_MODEL]['params']['thinking'],'off')

    def test_tighter_context_limits_survive_without_retired_default_field(self):
        source=self.fixture()
        source['agents']['defaults']['contextTokens']=24000
        source['models']={'providers':{'openai':{'models':[{**fallback.OPENAI_MINI,'contextTokens':16000,'maxTokens':2048}]}}}
        result=fallback.prepare(source,'nomina')
        self.assertNotIn('contextTokens',result['agents']['defaults'])
        self.assertEqual(result['models']['providers']['openai']['models'][0]['contextTokens'],16000)
        self.assertEqual(result['models']['providers']['openai']['models'][0]['maxTokens'],2048)
        self.assertEqual(result['models']['providers']['deepseek']['models'][0]['contextTokens'],24000)
        self.assertEqual(result,fallback.prepare(result,'nomina'))

    def test_other_primary_requires_catalog_to_preserve_context_budget(self):
        source=self.fixture()
        source['agents']['list'][0]['model']='other/test'
        with self.assertRaises(ValueError):fallback.prepare(source,'nomina')


if __name__ == '__main__': unittest.main()
