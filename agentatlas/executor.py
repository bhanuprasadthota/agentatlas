"""Dedicated browser execution tooling for cold-start collection and internal operator workflows."""

from agentatlas.atlas import Atlas
from agentatlas.browser_runtime import AtlasBrowserRuntimeMixin


class AgentExecutor(Atlas):
    async def execute(
        self,
        site: str,
        url: str,
        task: str,
        variant: str = "loggedout",
        max_steps: int = 10,
    ):
        self._require_direct_mode("AgentExecutor.execute")
        return await AtlasBrowserRuntimeMixin.execute(
            self,
            site=site,
            url=url,
            task=task,
            variant=variant,
            max_steps=max_steps,
        )
