from uuid import uuid4

from .models import CallRequest, CallState, CallStatus


def initiate_call(request: CallRequest) -> CallStatus:
    return CallStatus(
        call_id=str(uuid4()),
        state=CallState.INITIATED,
        source=request.source,
        target=request.target,
    )
