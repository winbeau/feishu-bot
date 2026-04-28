class DeduplicationStore:
    async def mark_seen(self, message_id: str) -> bool:
        raise NotImplementedError
