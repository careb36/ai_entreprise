"""The notes, kept in memory in the order they were created."""


class NoteStore:
    def __init__(self) -> None:
        self._notes: dict[int, str] = {}
        self._last_id = 0

    def add(self, text: str) -> dict:
        self._last_id += 1
        self._notes[self._last_id] = text
        return {"id": self._last_id, "text": text}

    def get(self, note_id: int) -> dict | None:
        if note_id not in self._notes:
            return None
        return {"id": note_id, "text": self._notes[note_id]}

    def all(self) -> list[dict]:
        return [{"id": i, "text": t} for i, t in self._notes.items()]

    def delete(self, note_id: int) -> bool:
        return self._notes.pop(note_id, None) is not None
