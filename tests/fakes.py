from possessed_shell.brain import BrainError


class FakeBrain:
    def __init__(self, reply=None, error=None, usage=None):
        self.reply = reply or {"lament": "Alas, poor process.", "diagnosis": "It is dead.", "fix": "ls -la /"}
        self.error = error
        self.usage = usage or {}
        self.prompts = []

    async def haunt(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise BrainError(self.error)
        return {**self.reply, "usage": dict(self.usage)}

    async def warm_up(self):
        pass


class FakeMemory:
    enabled = True

    def __init__(self, sins=None):
        self.sins = sins or []
        self.inscribed = []

    async def recall(self, name, limit=5):
        return list(self.sins)

    async def inscribe(self, failure, haunting):
        self.inscribed.append((failure, haunting))
        return True

    async def aclose(self):
        pass
