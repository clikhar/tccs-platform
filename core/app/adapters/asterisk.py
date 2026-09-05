from typing import Protocol


class AsteriskClient(Protocol):
    """Minimal interface expected from the Asterisk integration layer.

    The implementation will be added in S1.2 using ARI/AMI. Keeping the
    interface here prevents railway call rules from being coupled to the
    Asterisk client library.
    """

    async def originate(self, source: str, target: str) -> str: ...

    async def hangup(self, call_id: str) -> None: ...

    async def mute(self, call_id: str, participant: str) -> None: ...

    async def unmute(self, call_id: str, participant: str) -> None: ...

    async def remove_participant(self, call_id: str, participant: str) -> None: ...
