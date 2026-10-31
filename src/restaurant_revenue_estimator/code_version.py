import functools
import hashlib
from pathlib import Path

EXCLUDED_DIRS: tuple[str, ...] = ("routes", "alembic")
EXCLUDED_FILES: tuple[str, ...] = (
    "app.py",
    "cli.py",
    "database.py",
    "config.py",
    "logging_config.py",
    "models.py",
)


def compute_code_sha256(package_root: Path) -> str:
    """Compute sha256 hash of calculation-related source files in package_root."""
    target_entries: list[tuple[str, Path]] = []

    for path in package_root.rglob("*.py"):
        if not path.is_file():
            continue
        rel_path = path.relative_to(package_root)
        if len(rel_path.parts) > 1 and rel_path.parts[0] in EXCLUDED_DIRS:
            continue
        rel_posix = rel_path.as_posix()
        if rel_posix in EXCLUDED_FILES:
            continue
        target_entries.append((rel_posix, path))

    target_entries.sort(key=lambda entry: entry[0])

    hasher = hashlib.sha256()
    for rel_posix, path in target_entries:
        hasher.update(rel_posix.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")

    return hasher.hexdigest()


@functools.cache
def current_code_sha256() -> str:
    """Return the cached sha256 hash of the current package source code."""
    return compute_code_sha256(Path(__file__).parent)
