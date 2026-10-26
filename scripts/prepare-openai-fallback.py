#!/usr/bin/env python3
"""Prepare private fallback candidates; never modify a live profile or start services."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import tempfile

MODEL = "openai/gpt-5.4-mini"
PRIMARY_MODEL = "deepseek/deepseek-flash"
RETIRED_MODELS = {"deepseek/deepseek-reasoner", "deepseek/deepseek-chat"}
# Explicit catalog seed allows fallback even when primary discovery is offline.
# Prices are peak estimates from the provider's 2026-10-26 documentation.
DEEPSEEK_FLASH = {"id": "deepseek-flash", "name": "DeepSeek Flash", "reasoning": True,
                  "input": ["text", "image"], "contextWindow": 1000000,
                  "contextTokens": 65536, "maxTokens": 8192,
                  "cost": {"input": 0.30, "output": 1.20, "cacheRead": 0.006, "cacheWrite": 0}}
OPENAI_MINI = {"id": "gpt-5.4-mini", "name": "GPT-5.4 mini", "reasoning": True,
               "input": ["text", "image"], "contextWindow": 400000,
               "contextTokens": 65536, "maxTokens": 4096,
               "cost": {"input": 0.75, "output": 4.50, "cacheRead": 0.075, "cacheWrite": 0}}
SECRET_PROVIDER = "openai-fallback-env"
FILE_SECRET_PROVIDER = "openai-fallback-file"
RUNTIME_GENERATIONS = ("legacy", "2026.10.5")
ALLOWED_AGENTS = {
    "owlswatch": {"main", "cuenta", "cotiza", "correo", "cobros", "registro"},
    "hotel": {"hotel"}, "bailey-finance": {"ledger"}, "nomina": {"nomina"},
}


def agent_entries(config):
    agents = config.get("agents", {})
    if "entries" in agents and "list" in agents:
        raise ValueError("Mixed agent schemas must be reconciled before preparing fallback.")
    if "entries" in agents:
        return agents["entries"]
    rows = agents.get("list", [])
    result = {a["id"]: a for a in rows}
    if len(result) != len(rows):
        raise ValueError("Duplicate agent ids.")
    return result


def add_model(owner, inherited=None, runtime_generation="legacy"):
    selected = owner.get("model", inherited)
    if not selected:
        raise ValueError("An explicit primary model is required.")
    model = {"primary": selected} if isinstance(selected, str) else copy.deepcopy(selected)
    if not model.get("primary"):
        raise ValueError("An explicit primary model is required.")
    if runtime_generation == "2026.10.5" and model['primary'] in RETIRED_MODELS:
        old = model['primary']
        model['primary'] = PRIMARY_MODEL
        settings = owner.setdefault('models', {}).pop(old, {})
        settings.setdefault('params', {})['thinking'] = 'high' if old.endswith('reasoner') else 'off'
        owner['models'].setdefault(PRIMARY_MODEL, settings)
    fallbacks = model.setdefault("fallbacks", [])
    fallbacks[:] = list(dict.fromkeys(PRIMARY_MODEL if runtime_generation == "2026.10.5" and ref in RETIRED_MODELS else ref for ref in fallbacks))
    if MODEL not in fallbacks and model["primary"] != MODEL:
        fallbacks.append(MODEL)
    owner["model"] = model
    entry = owner.setdefault("models", {}).setdefault(MODEL, {})
    entry["agentRuntime"] = {"id": "openclaw"}
    params = entry.setdefault("params", {})
    params.update({"transport": "sse", "maxTokens": min(params.get('maxTokens') or 4096, 4096), "thinking": "low"})
    policy = owner.get("modelPolicy", {}).get("allow")
    if policy:
        policy[:] = list(dict.fromkeys(PRIMARY_MODEL if runtime_generation == "2026.10.5" and ref in RETIRED_MODELS else ref for ref in policy))
    if policy and MODEL not in policy:
        policy.append(MODEL)


def credential_file_path(path, profile):
    candidate = Path(path)
    root = Path.home() / (".openclaw-" + profile) / "credentials"
    if (not candidate.is_absolute() or candidate.resolve() != candidate or root.resolve() != root
            or not candidate.is_relative_to(root) or candidate == root):
        raise ValueError("Credential file must be a canonical absolute path under this profile's credentials directory.")
    # Path validation only. Provisioning and SDK owner/mode checks are separate;
    # never read, create, chmod or copy a credential here.
    return str(candidate)


def prepare(config, profile, env_name="OPENAI_FALLBACK_API_KEY", runtime_generation="legacy", credential_file=None):
    if runtime_generation not in RUNTIME_GENERATIONS:
        raise ValueError("Unsupported runtime generation.")
    if profile not in ALLOWED_AGENTS:
        raise ValueError("Profile is outside the authorized non-Luna active fleet.")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", env_name):
        raise ValueError("Credential environment name is invalid.")
    secret_provider = FILE_SECRET_PROVIDER if credential_file is not None else SECRET_PROVIDER
    if credential_file is not None:
        secret = {"source": "file", "path": credential_file_path(credential_file, profile), "mode": "singleValue"}
        secret_ref = {"source": "file", "provider": secret_provider, "id": "value"}
    else:
        secret = {"source": "env", "allowlist": [env_name]}
        secret_ref = {"source": "env", "provider": secret_provider, "id": env_name}
    result = copy.deepcopy(config)
    entries = agent_entries(result)
    if not entries or set(entries) - ALLOWED_AGENTS[profile]:
        raise ValueError("Unexpected or empty agent inventory; inspect ownership first.")
    defaults = result["agents"].setdefault("defaults", {})
    if runtime_generation == "legacy" and ("entries" in result["agents"] or
            any("modelPolicy" in owner for owner in [defaults, *entries.values()])):
        raise ValueError("Legacy preparation requires agents.list and models allowlists; refusing a reverse state/config migration.")
    add_model(defaults, runtime_generation=runtime_generation)
    defaults.setdefault("heartbeat", {})["every"] = "0m"
    # Preserve tighter existing limits, including Nomina's 180-second budget.
    defaults["timeoutSeconds"] = min(defaults.get("timeoutSeconds") or 600, 600)
    # The 10.5 migration drops the old default context limit. Transfer it to
    # explicit catalog rows, where the new runtime enforces it.
    context_limit = min(defaults.get('contextTokens') or 65536, 65536)
    if runtime_generation == "2026.10.5":
        defaults.pop('contextTokens', None)
    else:
        defaults['contextTokens'] = context_limit
    for agent in entries.values():
        add_model(agent, defaults["model"], runtime_generation)
        if "heartbeat" in agent:
            agent["heartbeat"]["every"] = "0m"
    providers = result.setdefault("models", {}).setdefault("providers", {})
    current = providers.setdefault("openai", {})
    if current.get("baseUrl", "https://api.openai.com/v1").rstrip("/") != "https://api.openai.com/v1":
        raise ValueError("Existing OpenAI custom endpoint requires explicit reconciliation.")
    if current.get("request") or current.get("headers"):
        raise ValueError("Existing OpenAI request overrides require explicit reconciliation.")
    current.update({"baseUrl": "https://api.openai.com/v1", "api": "openai-responses",
                    "auth": "api-key", "agentRuntime": {"id": "openclaw"},
                    "timeoutSeconds": 60,
                    "apiKey": secret_ref})
    current.setdefault("models", [])
    if not any(row.get('id') == OPENAI_MINI['id'] for row in current['models']):
        current['models'].append(copy.deepcopy(OPENAI_MINI))
    # Bound the primary attempt; this does not retry tool side effects.
    if "deepseek" in providers:
        providers["deepseek"]["timeoutSeconds"] = min(providers["deepseek"].get("timeoutSeconds") or 30, 30)
    else:
        primary_refs = [defaults["model"]["primary"]] + [a["model"]["primary"] for a in entries.values()]
        if any(ref.startswith("deepseek/") for ref in primary_refs):
            providers["deepseek"] = {"baseUrl": "https://api.deepseek.com", "api": "openai-completions",
                                     "timeoutSeconds": 30, "models": []}
    if 'deepseek' in providers and any(a['model']['primary'] == PRIMARY_MODEL for a in entries.values()):
        rows = providers['deepseek'].setdefault('models', [])
        if not any(row.get('id') == 'deepseek-flash' for row in rows):
            rows.append(copy.deepcopy(DEEPSEEK_FLASH))
    selected_refs = {MODEL}
    for owner in [defaults, *entries.values()]:
        selected_refs.add(owner['model']['primary'])
        selected_refs.update(owner['model'].get('fallbacks', []))
    for ref in selected_refs:
        provider, model_id = ref.split('/', 1)
        row = next((r for r in providers.get(provider, {}).get('models', []) if r.get('id') == model_id), None)
        if row is None and runtime_generation == "legacy" and ref in RETIRED_MODELS:
            # Preserve the installed runtime's alias and conservative catalog
            # limits. The provider still accepted reasoner on 2026-10-26.
            row = {'id': model_id, 'name': 'DeepSeek ' + model_id.removeprefix('deepseek-').title(),
                   'reasoning': model_id == 'deepseek-reasoner', 'input': ['text'],
                   'contextWindow': 128000, 'maxTokens': 8192,
                   'cost': copy.deepcopy(DEEPSEEK_FLASH['cost'])}
            providers[provider].setdefault('models', []).append(row)
        if row is None:
            raise ValueError('An explicit catalog row is required to preserve the context limit for ' + ref)
        row['contextTokens'] = min(row.get('contextTokens') or context_limit, context_limit,
                                   row.get('contextWindow') or context_limit)
        if ref == MODEL:
            row['maxTokens'] = min(row.get('maxTokens') or 4096, 4096)
        elif ref == PRIMARY_MODEL or ref in RETIRED_MODELS:
            row['maxTokens'] = min(row.get('maxTokens') or 8192, 8192)
        if runtime_generation == "legacy":
            defaults.setdefault('models', {}).setdefault(ref, {})
    secrets = result.setdefault("secrets", {}).setdefault("providers", {})
    if secret_provider in secrets and secrets[secret_provider] != secret:
        raise ValueError("Existing fallback secret-provider definition conflicts.")
    secrets[secret_provider] = secret
    plugins = result.setdefault("plugins", {})
    plugins.setdefault("entries", {}).setdefault("openai", {})["enabled"] = True
    if runtime_generation == "2026.10.5" and any(a['model']['primary'].startswith('deepseek/') for a in entries.values()):
        plugins['entries'].setdefault('deepseek', {})['enabled'] = True
        if isinstance(plugins.get('allow'), list) and 'deepseek' not in plugins['allow']:
            plugins['allow'].append('deepseek')
    if isinstance(plugins.get("allow"), list) and "openai" not in plugins["allow"]:
        plugins["allow"].append("openai")
    return result


def prepare_session_settings(settings):
    result = copy.deepcopy(settings)
    provider = result.setdefault("retry", {}).setdefault("provider", {})
    provider["maxRetries"] = 0
    return result


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".candidate-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def validate_output_dir(path, config):
    target = path.expanduser().resolve()
    private_roots = [Path(tempfile.gettempdir()).resolve(), Path('/private/tmp').resolve()]
    if not any(target.is_relative_to(root) and target != root for root in private_roots):
        raise ValueError("Secret-bearing candidates must be in a new private temporary directory.")
    if any(part.startswith('.openclaw') for part in target.parts):
        raise ValueError("Candidates must be outside live OpenClaw paths.")
    if any((parent / '.git').exists() for parent in [target, *target.parents]):
        raise ValueError("Candidates must be outside every Git repository/worktree.")
    owners = [config.get('agents', {}).get('defaults', {}), *agent_entries(config).values()]
    for owner in owners:
        for key in ['workspace', 'agentDir']:
            if owner.get(key) and target.is_relative_to(Path(owner[key]).expanduser().resolve()):
                raise ValueError("Candidates must be outside configured workspaces and agent state.")
    if target.exists():
        raise ValueError("Candidate output directory must be new; refusing to overwrite existing artifacts.")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=ALLOWED_AGENTS, required=True)
    credentials = parser.add_mutually_exclusive_group()
    credentials.add_argument("--credential-env", default="OPENAI_FALLBACK_API_KEY")
    credentials.add_argument("--credential-file", type=Path)
    parser.add_argument("--runtime-generation", choices=RUNTIME_GENERATIONS, default="legacy")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    state = Path.home() / (".openclaw-" + args.profile)
    config = json.loads((state / "openclaw.json").read_text())
    candidate = prepare(config, args.profile, args.credential_env, args.runtime_generation, args.credential_file)
    agents = agent_entries(config)
    settings = {}
    for name, agent in agents.items():
        directory = Path(agent.get("agentDir", str(state / "agents" / name / "agent"))).expanduser().resolve()
        if not directory.is_relative_to(state.resolve()):
            raise SystemExit("Agent auth/settings directory is outside its profile; inspect ownership.")
        path = directory / "settings.json"
        settings[name] = prepare_session_settings(json.loads(path.read_text()) if path.exists() else {})
    if args.output_dir:
        try:
            target = validate_output_dir(args.output_dir, config)
        except ValueError as exc:
            raise SystemExit(str(exc))
        target.mkdir(parents=True, mode=0o700)
        write_private(target / "openclaw.candidate.json", candidate)
        for name, value in settings.items():
            write_private(target / name / "settings.candidate.json", value)
    print(json.dumps({"profile": args.profile, "mode": "prepare-only", "model": MODEL,
                      "agents": sorted(agents), "changeRequired": candidate != config,
                      "credentialStorage": "file" if args.credential_file else "env",
                      "credentialEnv": None if args.credential_file else args.credential_env,
                      "credentialPresentInThisProcess": None if args.credential_file else bool(os.environ.get(args.credential_env)),
                      "primaryRequestSeconds": candidate["models"]["providers"].get("deepseek", {}).get("timeoutSeconds"),
                      "backupRequestSeconds": candidate["models"]["providers"]["openai"]["timeoutSeconds"],
                      "agentProviderRetries": {name: value["retry"]["provider"]["maxRetries"] for name, value in settings.items()},
                      "runtimeGeneration": args.runtime_generation,
                      "requiresRuntime": "2026.7.1-2" if args.runtime_generation == "legacy" else "2026.10.5",
                      "requiresConfigAndStateMigration": args.runtime_generation != "legacy",
                      "liveChanges": False, "candidateWritten": bool(args.output_dir)}, indent=2))


if __name__ == "__main__":
    main()
