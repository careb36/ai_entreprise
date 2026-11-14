"""Check a parsed settings file against a schema {section: {key: type}}."""

_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}


def _convert(raw: str, kind: str):
    if kind == "str":
        return raw
    if kind == "int":
        return int(raw.strip())
    if kind == "bool":
        word = raw.strip().lower()
        if word in _TRUE:
            return True
        if word in _FALSE:
            return False
        raise ValueError(word)
    raise ValueError(f"unknown type {kind!r}")


def check(config: dict, schema: dict) -> tuple[dict, list[str]]:
    values: dict[str, dict] = {}
    problems: list[str] = []
    for section, keys in schema.items():
        given = config.get(section) or {}
        out = values.setdefault(section, {})
        for key, kind in keys.items():
            if key not in given:
                problems.append(f"{section}.{key}: missing")
                continue
            try:
                out[key] = _convert(given[key], kind)
            except ValueError:
                problems.append(f"{section}.{key}: expected {kind}")
        problems.extend(f"{section}.{key}: unknown" for key in given if key not in keys)
    problems.extend(
        f"{section}: unknown section" for section in config if section not in schema
    )
    return values, problems
