"""Operator-only creation of the dedicated tool-restricted agent and environment.

Requires ANTHROPIC_API_KEY in the process environment. Prints resource IDs only.
No inference session or recurring automation is started.
"""
import asyncio
import os

from anthropic import AsyncAnthropic

from .managed import SYSTEM


async def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEYを試用プロセス環境に設定してください。キーをチャットやGitへ保存しないでください。")
    async with AsyncAnthropic(max_retries=0) as client:
        agent = await client.beta.agents.create(name="SAWA isolated trial", model="claude-sonnet-5-5", system=SYSTEM, tools=[], mcp_servers=[])
        environment = await client.beta.environments.create(name="SAWA isolated trial", config={"type": "cloud", "networking": {"type": "limited", "allowed_hosts": [], "allow_package_managers": False}})
    print("CLAUDE_AGENT_ID="+agent.id)
    print("CLAUDE_ENVIRONMENT_ID="+environment.id)


if __name__ == "__main__":
    asyncio.run(main())
