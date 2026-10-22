"""Provider choice is pinned to a run; changing the default cannot redirect it."""
from .managed import Managed
from .gemini import Gemini

LABELS = {'claude': 'Claude Managed Agents', 'gemini': 'Gemini Interactions API'}


class Providers(Managed):
    def __init__(self, *args, gemini_client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.gemini = Gemini(self.store, self.sandbox, self.tools, self.mcp, self.config, client=gemini_client)
        self.gemini.tasks = self.tasks
        self.gemini.starting = self.starting

    def missing(self):
        return self.gemini.missing() if self.store.provider() == 'gemini' else super().missing()

    def choices(self):
        # Explicit base dispatch avoids routing Claude's readiness through the default.
        from .managed import Managed
        return [{'id': 'claude', 'label': LABELS['claude'], 'missing': Managed.missing(self)},
                {'id': 'gemini', 'label': LABELS['gemini'], 'missing': self.gemini.missing()}]

    def for_run(self, ident):
        return self.gemini if self.store.get(ident)['provider'] == 'gemini' else None

    async def start(self, run):
        if run['provider'] == 'gemini': return await self.gemini.start(run)
        return await super().start(run)

    async def reply(self, ident, *args, **kwargs):
        if self.for_run(ident): return await self.gemini.reply(ident, *args, **kwargs)
        return await super().reply(ident, *args, **kwargs)

    async def resume(self, ident):
        from .recovery import Recovery
        return await Recovery(self).resume(ident)

    async def stop(self, ident):
        if self.for_run(ident): return await self.gemini.stop(ident)
        return await super().stop(ident)

    async def refresh_usage(self, ident, release=False):
        if self.for_run(ident): return await self.gemini.refresh_usage(ident, release)
        return await super().refresh_usage(ident, release)
