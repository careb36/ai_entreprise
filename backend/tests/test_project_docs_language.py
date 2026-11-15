"""A documentation pack in the project's language (issue #530): every fixed
string comes from ``project_docs.texts`` in English or Spanish, both complete;
a pack in one language carries none of the other's fixed text; citations,
ids, digests and the names recorded in the architecture stay as they are; the
lint flags a pack in a language the project no longer uses.
"""

import copy
import re

import pytest
from services.planning.project_docs import c4, pack, texts
from test_project_docs import _Pack, _snapshot

PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _in(lang):
    snapshot = _snapshot()
    snapshot["language"] = lang
    return snapshot


def _fragments(value: str) -> list[str]:
    """The pieces of a template between its placeholders and Markdown marks,
    long enough (three words) to be unmistakably its language."""
    parts = re.split(r"\{[a-z_]+\}|[|*_>#\[\]()\"]|\\n", value)
    return [
        p.strip(" .,:;-") for p in parts if len(re.findall(r"[^\W\d_]{2,}", p)) >= 3
    ]


def test_both_languages_hold_the_same_keys_and_placeholders():
    assert texts.TEXTS["en"].keys() == texts.TEXTS["es"].keys()
    for key, english in texts.TEXTS["en"].items():
        spanish = texts.TEXTS["es"][key]
        assert english.strip() and spanish.strip(), key
        assert set(PLACEHOLDER.findall(english)) == set(PLACEHOLDER.findall(spanish)), (
            key
        )
    assert texts.language("es-AR") == "es" and texts.language("fr") == "en"
    assert texts.language(None) == "en"


@pytest.mark.parametrize(("lang", "other"), [("es", "en"), ("en", "es")])
def test_a_pack_carries_no_fixed_text_of_the_other_language(lang, other):
    documents, diagrams = pack.build_documents(_in(lang), prose="Una prosa. A prose.")
    text = "\n".join(d["markdown"] for d in documents)
    text += "\n".join(n["label"] + n["detail"] for d in diagrams for n in d["nodes"])
    text += "\n".join(e["label"] for d in diagrams for e in d["edges"]) + "".join(
        note for d in diagrams for note in d["notes"]
    )
    # A control: the check sees this language's own fixed text in the pack.
    own_seen = {
        key
        for key, value in texts.TEXTS[lang].items()
        for fragment in _fragments(value)
        if fragment and fragment in text
    }
    assert len(own_seen) > 60, len(own_seen)
    found = []
    for key, value in texts.TEXTS[other].items():
        own = texts.TEXTS[lang][key]
        for fragment in _fragments(value):
            if fragment and fragment not in own and fragment in text:
                found.append((key, fragment))
    assert found == []


def test_spanish_headings_statuses_and_person_templates():
    docs = {d["key"]: d for d in pack.build_documents(_in("es"))[0]}
    charter = docs["charter"]
    assert charter["title"] == "Acta de constitución del proyecto"
    assert (
        "Guía del PMBOK, 8.ª edición (PMI, 2025): dominio de desempeño Gobernanza, área de enfoque Inicio"
        in charter["markdown"]
    )
    assert charter["persons"] == [
        "Justificación de negocio",
        "Beneficios esperados",
        "Presupuesto asignado",
        "Patrocinador",
    ]
    assert charter["markdown"].count("**A completar por una persona.**") == 4
    assert "_Sin fuente registrada: no se redactó antecedentes._" in charter["markdown"]
    assert (
        "## Fuentes" in charter["markdown"] and "## Sources" not in charter["markdown"]
    )
    assert (
        "| Gobernanza | Inicio | Acta de constitución del proyecto | charter.md |"
        in docs["index"]["markdown"]
    )
    assert "T-41 terminada" in docs["scope"]["markdown"]
    assert "| verificado |" in docs["traceability"]["markdown"]
    assert docs["charter"]["domain"] == "Governance"  # data stays canonical


def test_citations_ids_and_digests_do_not_change_with_the_language():
    en_docs, en_diagrams = pack.build_documents(_in("en"))
    es_docs, es_diagrams = pack.build_documents(_in("es"))
    for en, es in zip(en_docs, es_docs, strict=True):
        assert en["key"] == es["key"] and en["input_sha256"] == es["input_sha256"]
        assert [
            (s["mark"], s["kind"], s["id"], s.get("sha256")) for s in en["sources"]
        ] == [
            (s["mark"], s["kind"], s["id"], s.get("sha256")) for s in es["sources"]
        ], en["key"]
        assert re.findall(r"\\\[S[\d, S]+\\\]", en["markdown"]) == re.findall(
            r"\\\[S[\d, S]+\\\]", es["markdown"]
        )
        for ident in ("REQ-1", "T-41", "R-1", "I-1", "C-1"):
            assert (ident in en["markdown"]) == (ident in es["markdown"]), (
                en["key"],
                ident,
            )
    for en, es in zip(en_diagrams, es_diagrams, strict=True):
        assert [n["id"] for n in en["nodes"]] == [n["id"] for n in es["nodes"]]
        assert [(e["source"], e["target"]) for e in en["edges"]] == [
            (e["source"], e["target"]) for e in es["edges"]
        ]
        assert en["input_sha256"] == es["input_sha256"]
    container = es_diagrams[1]
    labels = {n["label"] for n in container["nodes"]}
    assert {"Board API", "Store", "Web client", "Usuarios de Public API"} <= labels
    assert (
        '-->|"depende de"|' in container["mermaid"]
        and "llama a Tasks HTTP API" in container["mermaid"]
    )
    assert "núcleo de Board API" in {n["label"] for n in es_diagrams[2]["nodes"]}
    assert "Contexto del sistema" in c4.document(_in("es"), es_diagrams)["markdown"]


def test_the_lint_flags_a_pack_in_a_language_the_project_no_longer_uses():
    snapshot = _in("en")
    stored = _Pack(*pack.build_documents(snapshot), language="en")
    assert pack.lint(stored, snapshot)["ok"] is True
    changed = copy.deepcopy(snapshot)
    changed["language"] = "es"
    result = pack.lint(stored, changed)
    assert result["ok"] is False
    assert any(
        "documentation language is now es" in f["message"] for f in result["findings"]
    )
    # No document's records changed: only the language finding blocks.
    assert not any(d["stale"] for d in result["documents"])
