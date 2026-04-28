class SessionStore:
    async def get_session_id(self, key: str) -> str | None:
        raise NotImplementedError

    async def set_session_id(self, key: str, session_id: str) -> None:
        raise NotImplementedError
