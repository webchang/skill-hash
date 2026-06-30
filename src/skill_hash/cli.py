import hashlib
import sys
import json
from pathlib import Path
from typing import List, Tuple
import click


def compute_file_digest(filepath: Path) -> str:
    """
    Compute SHA-256 digest of a single file.
    CRLF is always normalized to LF before hashing
    for cross-platform reproducibility.
    """
    h = hashlib.sha256()
    content = filepath.read_bytes().replace(b'\r\n', b'\n')
    h.update(content)
    return h.hexdigest()


def parse_paths(raw: str) -> List[str]:
    """
    Parse a comma-separated list of skill file/folder paths.
    Strips whitespace from each entry and drops empty entries.
    """
    return [p.strip() for p in raw.split(',') if p.strip()]


def _check_collision(
    seen: dict,
    rel_path: Path,
    abs_path: Path
) -> None:
    """Raise an error if the same relative path maps to two different files."""
    if rel_path in seen and seen[rel_path] != abs_path:
        raise click.ClickException(
            f"Relative path collision: '{rel_path}' resolves to both "
            f"'{seen[rel_path]}' and '{abs_path}'."
        )


def collect_files(
    paths: List[str],
    root: Path
) -> List[Tuple[Path, Path]]:
    """
    Expand a list of file/folder paths into a sorted, deduplicated
    list of (absolute_path, relative_path) tuples.

    All relative paths are computed relative to `root`.
    Sort is by relative path, UTF-8 encoded — locale-neutral.

    Constraints:
    - All paths must exist under `root`.
    - No two distinct files may produce the same relative path.
    - File/folder paths must not contain commas.
    - Directories must not be empty.
    """
    seen_relative = {}

    for raw in paths:
        p = Path(raw.strip()).resolve()
        if not p.exists():
            raise click.ClickException(f"Path does not exist: {p}")

        # Ensure the path is under the declared root
        try:
            p.relative_to(root)
        except ValueError:
            raise click.ClickException(
                f"Path '{p}' is not under the declared root '{root}'. "
                f"All paths must be under --root."
            )

        if p.is_dir():
            dir_files = []
            for f in p.rglob("*"):
                if f.is_file():
                    abs_path = f.resolve()
                    rel_path = abs_path.relative_to(root)
                    _check_collision(seen_relative, rel_path, abs_path)
                    seen_relative[rel_path] = abs_path
                    dir_files.append(abs_path)

            # Reject empty directories — silently ignoring them would
            # allow a digest to be computed over nothing, masking errors.
            if not dir_files:
                raise click.ClickException(
                    f"Directory contains no files: '{p}'. "
                    f"Remove it from the path list or add skill files to it."
                )

        elif p.is_file():
            abs_path = p.resolve()
            rel_path = abs_path.relative_to(root)
            _check_collision(seen_relative, rel_path, abs_path)
            seen_relative[rel_path] = abs_path

        else:
            raise click.ClickException(
                f"Path is neither file nor directory: {p}"
            )

    # Sort by relative path string, UTF-8 encoded — locale-neutral
    return sorted(
        seen_relative.items(),
        key=lambda item: str(item[0]).encode('utf-8')
    )


def build_manifest(files: List[Tuple[Path, Path]]) -> str:
    """
    Build a canonical manifest string.
    One line per file: '<sha256hex>  <relative-path>'
    Uses forward slashes regardless of OS.
    """
    lines = []
    for abs_path, rel_path in files:
        digest = compute_file_digest(abs_path)
        lines.append(f"{digest}  {rel_path.as_posix()}")
    return "\n".join(lines) + "\n"


def compute_collection_digest(manifest: str) -> str:
    """Compute SHA-256 of the manifest string itself."""
    return hashlib.sha256(manifest.encode('utf-8')).hexdigest()


def _resolve_root(root: str, root_file: str) -> Path:
    """Resolve the root path from --root or --root-file."""
    if root_file:
        root = Path(root_file).read_text(encoding='utf-8').strip()
    if not root:
        raise click.ClickException(
            "No root provided. Use --root, SKILL_ROOT env var, "
            "or --root-file."
        )
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise click.ClickException(
            f"Root path does not exist or is not a directory: {resolved}"
        )
    return resolved


def _resolve_paths(paths, paths_from_env, paths_from_file) -> List[str]:
    """Resolve skill paths from file, env var, or positional arguments."""
    if paths_from_file:
        raw = Path(paths_from_file).read_text(encoding='utf-8').strip()
        return parse_paths(raw)
    elif paths_from_env:
        return parse_paths(paths_from_env)
    else:
        resolved = []
        for p in paths:
            resolved.extend(parse_paths(p))
        return resolved


# ── CLI group ──────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """skill-hash: compute and verify integrity digests
    for Kagenti skill file collections.

    File/folder paths are comma-separated and must not contain commas.
    All paths must reside under the declared --root directory.
    Empty directories are rejected.
    """
    pass


# ── compute ────────────────────────────────────────────────────────────────────

@cli.command("compute")
@click.argument("paths", nargs=-1, required=False)
@click.option(
    "--root",
    envvar="SKILL_ROOT",
    default=None,
    help="Root directory for relative path computation. "
         "All skill paths must be under this root. "
         "Can be set via SKILL_ROOT env var.",
)
@click.option(
    "--root-file",
    type=click.Path(exists=True),
    default=None,
    help="Read root path from a file (e.g. Downward API volume mount). "
         "Takes precedence over --root / SKILL_ROOT.",
)
@click.option(
    "--output", "-o",
    type=click.Choice(["digest", "manifest", "json"]),
    default="digest",
    show_default=True,
    help=(
        "digest:   print only the collection digest\n"
        "manifest: print the full per-file manifest\n"
        "json:     print both as JSON"
    ),
)
@click.option(
    "--paths-from-env",
    envvar="SKILL_PATHS",
    default=None,
    help="Read comma-separated paths from SKILL_PATHS env var.",
)
@click.option(
    "--paths-from-file",
    type=click.Path(exists=True),
    default=None,
    help="Read comma-separated paths from a file "
         "(e.g. Downward API volume mount). "
         "Takes precedence over --paths-from-env / SKILL_PATHS.",
)
def compute(paths, root, root_file, output, paths_from_env, paths_from_file):
    """
    Compute a canonical digest for a collection of skill files/folders.

    All paths must be under --root, which anchors relative path
    computation in the manifest. Empty directories are rejected.

    \b
    Examples:
        skill-hash compute --root /skills /skills/core/,/skills/tools/fetch.py
        SKILL_ROOT=/skills SKILL_PATHS=/skills/core/ skill-hash compute
    """
    root_path = _resolve_root(root, root_file)
    resolved_paths = _resolve_paths(paths, paths_from_env, paths_from_file)

    if not resolved_paths:
        raise click.ClickException(
            "No paths provided. Use arguments, --paths-from-env / "
            "SKILL_PATHS, or --paths-from-file."
        )

    files = collect_files(resolved_paths, root_path)
    manifest = build_manifest(files)
    collection_digest = f"sha256:{compute_collection_digest(manifest)}"

    if output == "digest":
        click.echo(collection_digest)

    elif output == "manifest":
        click.echo(manifest)

    elif output == "json":
        result = {
            "collectionDigest": collection_digest,
            "root": str(root_path),
            "files": [
                {
                    "relativePath": rel_path.as_posix(),
                    "digest": f"sha256:{compute_file_digest(abs_path)}"
                }
                for abs_path, rel_path in files
            ],
        }
        click.echo(json.dumps(result, indent=2))


# ── verify ─────────────────────────────────────────────────────────────────────

@cli.command("verify")
@click.argument("paths", nargs=-1, required=False)
@click.option(
    "--root",
    envvar="SKILL_ROOT",
    default=None,
    help="Root directory for relative path computation. "
         "Must match the root used during compute. "
         "Can be set via SKILL_ROOT env var.",
)
@click.option(
    "--root-file",
    type=click.Path(exists=True),
    default=None,
    help="Read root path from a file (e.g. Downward API volume mount). "
         "Takes precedence over --root / SKILL_ROOT.",
)
@click.option(
    "--paths-from-env",
    envvar="SKILL_PATHS",
    default=None,
    help="Read comma-separated paths from SKILL_PATHS env var.",
)
@click.option(
    "--paths-from-file",
    type=click.Path(exists=True),
    default=None,
    help="Read comma-separated paths from a file "
         "(e.g. Downward API volume mount). "
         "Takes precedence over --paths-from-env / SKILL_PATHS.",
)
@click.option(
    "--expected-digest",
    envvar="EXPECTED_DIGEST",
    default=None,
    help="Expected collection digest (sha256:...). "
         "Can be set via EXPECTED_DIGEST env var.",
)
@click.option(
    "--expected-digest-file",
    type=click.Path(exists=True),
    default=None,
    help="Read expected digest from a file "
         "(e.g. Downward API volume mount). "
         "Takes precedence over --expected-digest / EXPECTED_DIGEST.",
)
@click.option(
    "--output-manifest",
    type=click.Path(),
    default=None,
    help="Write the verified manifest to this path on success.",
)
def verify(
    paths,
    root,
    root_file,
    paths_from_env,
    paths_from_file,
    expected_digest,
    expected_digest_file,
    output_manifest,
):
    """
    Verify that a skill collection matches an expected digest.

    --root must match the root used during compute.
    Empty directories are rejected.
    Exits with code 0 on match, code 1 on mismatch.

    \b
    Examples:
        skill-hash verify --root /skills --expected-digest sha256:abc123... \\
            /skills/core/,/skills/tools/fetch.py
        skill-hash verify --root-file /pod-meta/skill-root \\
            --paths-from-file /pod-meta/skill-paths \\
            --expected-digest-file /pod-meta/skill-collection-digest \\
            --output-manifest /skill-signatures/skill-manifest.sha256
    """
    root_path = _resolve_root(root, root_file)

    # Resolve expected digest — file takes precedence over env var
    if expected_digest_file:
        expected_digest = Path(expected_digest_file).read_text(
            encoding='utf-8'
        ).strip()
    if not expected_digest:
        raise click.ClickException(
            "No expected digest provided. Use --expected-digest, "
            "EXPECTED_DIGEST env var, or --expected-digest-file."
        )

    resolved_paths = _resolve_paths(paths, paths_from_env, paths_from_file)

    if not resolved_paths:
        raise click.ClickException(
            "No paths provided. Use arguments, --paths-from-env / "
            "SKILL_PATHS, or --paths-from-file."
        )

    files = collect_files(resolved_paths, root_path)
    manifest = build_manifest(files)
    computed_digest = f"sha256:{compute_collection_digest(manifest)}"

    click.echo(f"Expected : {expected_digest}", err=True)
    click.echo(f"Computed : {computed_digest}", err=True)

    if computed_digest != expected_digest:
        click.echo("FAILED: digest mismatch", err=True)
        sys.exit(1)

    click.echo("PASSED: digests match", err=True)

    if output_manifest:
        Path(output_manifest).write_text(manifest, encoding='utf-8')
        click.echo(
            f"Manifest written to: {output_manifest}", err=True
        )

    sys.exit(0)


def main():
    cli()
