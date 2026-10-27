"""Exercise installer behavior only in temporary homes with fake launchctl."""
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('monitor_install', ROOT / 'scripts/install-operations-observability.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class QuietMonitorInstallTests(unittest.TestCase):
    def test_local_monitor_and_daily_balance_have_one_shared_budget(self):
        jobs = installer.job_specs(Path('/fixture/home'), '/fixture/python')
        local = jobs['ai.openclaw.operations-observability']
        daily = jobs['ai.openclaw.operations-balance-daily']
        self.assertNotIn('--check-balance', local['ProgramArguments'])
        self.assertEqual(local['StartInterval'], 300)
        self.assertEqual(daily['ProgramArguments'], local['ProgramArguments'] + ['--check-balance'])
        self.assertEqual(daily['StartCalendarInterval'], {'Hour': 8, 'Minute': 15})
        self.assertNotIn('StartInterval', daily)
        self.assertFalse(daily['RunAtLoad'])
        self.assertIn('monitor-http-budget.py', installer.FILES)
        self.assertTrue(all('finca' not in label and 'luna' not in label for label in jobs))
        self.assertNotIn('nomina', local['ProgramArguments'])
        self.assertEqual(local['ProgramArguments'].count('--profile'), 3)

    def test_monitor_only_plan_does_not_include_backup_job(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/install-operations-observability.py'), '--monitor-only'], capture_output=True, text=True, check=True)
        plan = json.loads(result.stdout)
        self.assertEqual(set(plan['jobs']), {'ai.openclaw.operations-observability', 'ai.openclaw.operations-balance-daily'})
        self.assertFalse(plan['messages'])
        self.assertFalse(plan['gatewayRestarts'])

    def fixture(self, base):
        home, binary = base / 'home', base / 'bin'
        home.mkdir(); binary.mkdir()
        log = base / 'commands.jsonl'
        fake = '''#!{python}
import json,os,sys
from pathlib import Path
name=Path(sys.argv[0]).name
args=sys.argv[1:]
with open(os.environ['FAKE_COMMAND_LOG'],'a') as f:f.write(json.dumps([name,*args])+'\\n')
if name=='launchctl' and args[:1]==['print']:
 sys.exit(0 if args[1]=='gui/502' or (os.environ.get('FAKE_SYSTEM_OBSERVER')=='1' and args[1]=='system/ai.openclaw.hotel.telegram-observer') else 1)
if name=='id':
 print('staff' if '-gn' in args else ('0' if args==['-u'] and os.environ.get('FAKE_ROOT')=='1' else '502'))
'''.format(python=sys.executable)
        for name in ('launchctl', 'id', 'chown', 'openclaw'):
            p = binary / name; p.write_text(fake); p.chmod(0o700)
        env = dict(os.environ, HOME=str(home), PATH=str(binary) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                   FAKE_COMMAND_LOG=str(log), OPENCLAW_BIN=str(binary / 'openclaw'))
        for key in ('OPENCLAW_PROFILE_DIR', 'HOTEL_PROFILE_DIR', 'OPENCLAW_SERVICE_USER', 'SUDO_USER'):
            env.pop(key, None)
        return home, env, log

    def commands(self, log):
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_observer_install_stays_disabled_without_bootstrap(self):
        with tempfile.TemporaryDirectory() as directory:
            home, env, log = self.fixture(Path(directory))
            env['OPENCLAW_PROFILE'] = 'owlswatch'
            subprocess.run(['bash', str(ROOT / 'scripts/install-hotel-telegram-observer.sh'), 'install'], env=env, check=True, capture_output=True)
            commands = self.commands(log)
            self.assertFalse(any(c[:2] in (['launchctl', 'enable'], ['launchctl', 'bootstrap']) for c in commands))
            self.assertIn(['launchctl', 'disable', 'gui/502/ai.openclaw.owlswatch.telegram-observer'], commands)
            config = plistlib.loads((home / 'Library/LaunchAgents/ai.openclaw.owlswatch.telegram-observer.plist').read_bytes())
            self.assertTrue(config['Disabled'])

    def test_retired_finca_observer_cannot_install(self):
        with tempfile.TemporaryDirectory() as directory:
            home, env, log = self.fixture(Path(directory)); env['OPENCLAW_PROFILE'] = 'finca'
            result = subprocess.run(['bash', str(ROOT / 'scripts/install-hotel-telegram-observer.sh'), 'install'], env=env, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertFalse((home / '.openclaw-finca').exists())
            self.assertFalse(any(c[0] == 'launchctl' for c in self.commands(log)))

    def test_active_system_observer_blocks_install_without_false_quiet_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            home, env, log = self.fixture(Path(directory))
            env.update(OPENCLAW_PROFILE='hotel', FAKE_SYSTEM_OBSERVER='1')
            result = subprocess.run(['bash', str(ROOT / 'scripts/install-hotel-telegram-observer.sh'), 'install'], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('System observer is still active', result.stderr)
            self.assertFalse((home / '.openclaw-hotel').exists())
            self.assertFalse(any(c[:2] in (['launchctl', 'disable'], ['launchctl', 'bootout']) for c in self.commands(log)))

    def test_quiet_observer_action_never_touches_gateway_or_business_schedules(self):
        with tempfile.TemporaryDirectory() as directory:
            home, env, log = self.fixture(Path(directory))
            env.update(FAKE_ROOT='1', FAKE_SYSTEM_OBSERVER='1', OPENCLAW_SERVICE_USER='fixture', OPENCLAW_SERVICE_HOME=str(home))
            subprocess.run(['bash', str(ROOT / 'scripts/install-headless-hotel-services.sh'), 'quiet-observer'], env=env, check=True, capture_output=True)
            calls = [c for c in self.commands(log) if c[0] == 'launchctl']
            self.assertTrue(calls)
            self.assertTrue(all(any('telegram-observer' in part for part in c) for c in calls))
            self.assertIn(['launchctl', 'bootout', 'system/ai.openclaw.hotel.telegram-observer'], calls)

    def test_headless_install_and_uninstall_preserve_quiet_observer(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); home, env, log = self.fixture(base)
            source, dest = base / 'source', base / 'daemons'; source.mkdir()
            labels = ('ai.openclaw.hotel', 'ai.openclaw.hotel.telegram-observer', 'ai.openclaw.hotel.daily-summary', 'ai.openclaw.hotel.registro-pickup')
            for label in labels:
                (source / (label + '.plist')).write_bytes(plistlib.dumps({'Label': label, 'ProgramArguments': ['/fixture/no-run']}))
            env.update(FAKE_ROOT='1', OPENCLAW_SERVICE_USER='fixture', OPENCLAW_SERVICE_HOME=str(home),
                       OPENCLAW_LAUNCHAGENT_DIR=str(source), OPENCLAW_LAUNCHDAEMON_DIR=str(dest))
            script = ROOT / 'scripts/install-headless-hotel-services.sh'
            for action in ('install', 'uninstall'):
                subprocess.run(['bash', str(script), action], env=env, check=True, capture_output=True)
            commands = self.commands(log)
            for c in commands:
                if c[:2] in (['launchctl', 'enable'], ['launchctl', 'bootstrap']):
                    self.assertFalse(any('telegram-observer' in part for part in c), c)
            self.assertIn(['launchctl', 'enable', 'system/ai.openclaw.hotel'], commands)
            self.assertIn(['launchctl', 'enable', 'gui/502/ai.openclaw.hotel'], commands)


if __name__ == '__main__':
    unittest.main()
