"""Explicit, owner-scoped preference consent, not inferred profile extraction."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from api.chat import ChatRoute, User, body, db_call, get_chat_store, safe_error
from memory.chat_storage import ChatError, ChatStore
from core.schemas.chat import Identifier, Strict
from memory.assistant_store import AssistantStore


def get_preference_store() -> AssistantStore:
    return AssistantStore()


router = APIRouter(prefix="/chat/conversations", tags=["assistant-preferences"], route_class=ChatRoute)


class PreferenceCommand(Strict):
    action: Literal["set", "delete"]
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    value: str | None = Field(default=None, min_length=1, max_length=500)
    expected_version: int = Field(ge=0, le=1000)
    consent: bool = False


async def owned(cid: str, owner: str, chat: ChatStore) -> None:
    # A visible conversation is required; arbitrary IDs cannot access an owner
    # profile by learning another user's conversation reference.
    await db_call(chat.get, owner, cid)


async def view(store: AssistantStore, owner: str) -> dict:
    return {"schema_version": "assistant-preferences-v1", "items": await db_call(store.list_preferences, owner),
            "versions": await db_call(store.preference_versions, owner)}


@router.get("/{conversation_id}/assistant-preferences")
async def read_preferences(conversation_id: Identifier, raw: Request, user: User,
        chat: Annotated[ChatStore, Depends(get_chat_store)],
        store: Annotated[AssistantStore, Depends(get_preference_store)]) -> dict:
    if raw.query_params:
        raise HTTPException(422, detail={"code": "assistant_preference_invalid"})
    try:
        await owned(conversation_id, user.id, chat)
        return await view(store, user.id)
    except ChatError as error:
        raise safe_error(error) from None


@router.post("/{conversation_id}/assistant-preferences")
async def change_preferences(conversation_id: Identifier, raw: Request, user: User,
        chat: Annotated[ChatStore, Depends(get_chat_store)],
        store: Annotated[AssistantStore, Depends(get_preference_store)]) -> dict:
    command = await body(raw, PreferenceCommand)
    try:
        await owned(conversation_id, user.id, chat)
        if command.action == "set":
            if command.consent is not True or command.value is None:
                raise HTTPException(422, detail={"code": "assistant_preference_consent_required"})
            await db_call(lambda: store.set_preference(user.id, command.key, command.value,
                command.expected_version, consent=True))
        else:
            if command.value is not None or command.consent:
                raise HTTPException(422, detail={"code": "assistant_preference_invalid"})
            await db_call(store.delete_preference, user.id, command.key, command.expected_version)
        return await view(store, user.id)
    except ChatError as error:
        raise safe_error(error) from None
