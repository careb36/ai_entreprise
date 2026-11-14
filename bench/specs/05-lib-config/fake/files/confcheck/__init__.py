"""Parse an INI-style settings file: [section] headers and key = value lines."""

import re

_SECTION = re.compile(r"^\[([^\[\]]+)\]$")


class ConfigError(ValueError):
    """A line of the settings file that cannot be read."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line


def parse(text: str) -> dict[str, dict[str, str]]:
    config: dict[str, dict[str, str]] = {}
    section: str | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        header = _SECTION.match(line)
        if header:
            section = header.group(1).strip()
            if not section:
                raise ConfigError(number, "the section has no name")
            if section in config:
                raise ConfigError(number, f"section [{section}] is repeated")
            config[section] = {}
            continue
        if "=" not in line:
            raise ConfigError(
                number, f"cannot read {line!r}: expected [section] or key = value"
            )
        if section is None:
            raise ConfigError(number, "a key comes before any [section]")
        key, value = (part.strip() for part in line.split("=", 1))
        if not key:
            raise ConfigError(number, "the key is empty")
        if key in config[section]:
            raise ConfigError(number, f"key {key!r} is repeated in [{section}]")
        config[section][key] = value
    return config
