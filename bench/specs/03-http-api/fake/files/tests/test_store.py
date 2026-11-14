from notesapi.store import NoteStore


def test_add_and_get():
    store = NoteStore()
    assert store.add("a") == {"id": 1, "text": "a"}
    assert store.get(1) == {"id": 1, "text": "a"}
    assert store.get(2) is None


def test_delete_keeps_ids():
    store = NoteStore()
    store.add("a")
    assert store.delete(1) is True
    assert store.delete(1) is False
    assert store.add("b")["id"] == 2
    assert store.all() == [{"id": 2, "text": "b"}]
