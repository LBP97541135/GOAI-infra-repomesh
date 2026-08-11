"""Small Git value validators shared by domain and adapter boundaries."""


def normalize_full_sha(value: str, *, field: str = "sha") -> str:
    normalized = value.strip().lower()
    if len(normalized) != 40 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field} must be a full Git object id")
    return normalized
