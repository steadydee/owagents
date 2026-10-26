"""Read either OpenClaw agent representation without changing its identity model."""
import copy
import json
from pathlib import Path
import re


def configured_agents(config):
    if not isinstance(config, dict):
        raise ValueError("Configuration must be an object.")
    agents = config.get("agents", {})
    if not isinstance(agents, dict):
        raise ValueError("Agent configuration must be an object.")
    if "entries" in agents:
        if "list" in agents:
            raise ValueError("Mixed agents.list and agents.entries is ambiguous.")
        entries = agents["entries"]
        if not isinstance(entries, dict):
            raise ValueError("agents.entries must be a keyed object.")
        for identity, agent in entries.items():
            if not isinstance(identity, str) or not identity or not isinstance(agent, dict):
                raise ValueError("Invalid keyed agent entry.")
            if "id" in agent and agent["id"] != identity:
                raise ValueError("Keyed agent identity conflicts with its entry key.")
        return entries
    entries = agents.get("list", [])
    if not isinstance(entries, list):
        raise ValueError("agents.list must be an array.")
    result = {}
    for agent in entries:
        if not isinstance(agent, dict) or not isinstance(agent.get("id"), str) or not agent["id"]:
            raise ValueError("Legacy agent entries require an ID.")
        if agent["id"] in result:
            raise ValueError("Duplicate agent identity.")
        result[agent["id"]] = agent
    return result


def single_agent_section(identity, agent, defaults, representation):
    """Build only a disposable UAT section; never migrate a live profile."""
    entry = copy.deepcopy(agent)
    result = {"defaults": copy.deepcopy(defaults)}
    if representation == "entries":
        entry.pop("id", None)
        entry.pop("default", None)
        result["entries"] = {identity: entry}
    elif representation == "list":
        entry["id"] = identity
        result["list"] = [entry]
    else:
        raise ValueError("UAT agent format must be list or entries.")
    return result


def uat_agent_format(executable, override=None):
    if override is not None:
        if override not in ("list", "entries"):
            raise ValueError("OPENCLAW_UAT_AGENT_FORMAT must be list or entries.")
        return override
    # The public executable normally resolves to openclaw.mjs in its package.
    # Wrappers can use the explicit override; no runtime command is executed.
    try:
        package = json.loads((Path(executable).resolve().parent / "package.json").read_text())
        version = re.match(r"^(\d+)\.(\d+)\.(\d+)", package.get("version", ""))
        if package.get("name") == "openclaw" and version:
            return "entries" if tuple(map(int, version.groups())) >= (2026, 10, 5) else "list"
    except (OSError, ValueError, TypeError):
        pass
    raise ValueError("Cannot identify UAT runtime schema; set OPENCLAW_UAT_AGENT_FORMAT explicitly.")
