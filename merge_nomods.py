#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bundle many generated mods into a few NeoForge jars.

Why bundle at all?
------------------
ModLauncher creates one JPMS module per mod *jar file*. With thousands of tiny
jars the module resolution in java.lang.module.Resolver becomes quadratic and
can take many minutes before anything is loaded, so the mods are packed into
bundle jars.

Why not ONE jar with all 4000 mods?
-----------------------------------
NeoForge builds the resource-pack name/version of a mod file by joining the
ids/versions of all mods inside it with commas:

    name    = "mod/" + "nomods1,nomods2,..."
    version = "1.0.0-neoforge1.21.1,1.0.0-neoforge1.21.1,..."

Those strings are sent to the client in `select_known_packs`, and Minecraft's
Utf8String has a 32767 character limit. With ~1500+ mods in a single jar the
version string overflows and every client is kicked with:

    EncoderException: Failed to encode packet
    'clientbound/minecraft:select_known_packs'
    Caused by: String too big (was 41999 characters, max 32767)

So mods are split into chunks (default 1000 per jar), keeping both strings far
below the limit while reducing thousands of modules to a handful.

The mods themselves are not changed: every mod keeps its id, classes, channel,
version and authors. Only the packaging differs.

Usage:
    python merge_nomods.py --mods E:\\server1211\\mods-original --output E:\\server1211\\mods\\nomods-bundle.jar
    python merge_nomods.py --mods ... --output ... --limit 4000 --chunk-size 1000
    python merge_nomods.py --mods ... --output ... --chunk-size 0   # single jar
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

MODS_TOML = "META-INF/neoforge.mods.toml"
MANIFEST = "META-INF/MANIFEST.MF"
MANIFEST_BODY = "Manifest-Version: 1.0\r\n\r\n"
# Minecraft's Utf8String limit used by select_known_packs.
UTF8_LIMIT = 32_767
SAFE_LIMIT = 32_000
DEFAULT_CHUNK_SIZE = 1000


def split_toml(text: str) -> tuple[str, list[str]]:
    """Split a mods.toml into its header and its [[...]] table blocks."""
    parts = re.split(r"(?m)^(?=\[\[)", text)
    header = parts[0]
    blocks = [block for block in parts[1:] if block.strip()]
    return header, blocks


def parse_mod_entries(blocks: list[str]) -> list[tuple[str, str]]:
    """Extract (modId, version) from the [[mods]] blocks of a mods.toml."""
    entries: list[tuple[str, str]] = []
    for block in blocks:
        if not block.startswith("[[mods]]"):
            continue
        mod_id = re.search(r'(?m)^\s*modId\s*=\s*"([^"]+)"', block)
        version = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', block)
        if mod_id and version:
            entries.append((mod_id.group(1), version.group(1)))
    return entries


def load_jar(jar: Path) -> tuple[str, list[str], list[tuple[str, bytes]], list[tuple[str, str]]]:
    with zipfile.ZipFile(jar) as archive:
        toml = archive.read(MODS_TOML).decode("utf-8")
        header, blocks = split_toml(toml)
        classes = [
            (name, archive.read(name))
            for name in archive.namelist()
            if name.endswith(".class")
        ]
    return header, blocks, classes, parse_mod_entries(blocks)


def write_bundle(
    output: Path,
    header: str,
    blocks: list[str],
    classes: list[tuple[str, bytes]],
    entries: list[tuple[str, str]],
) -> None:
    name = "mod/" + ",".join(mod_id for mod_id, _ in entries)
    version = ",".join(mod_version for _, mod_version in entries)
    if len(name) > SAFE_LIMIT or len(version) > SAFE_LIMIT:
        raise SystemExit(
            f"error: {len(entries)} mods in one jar produce a {len(version)} char "
            f"version string (limit {UTF8_LIMIT}); use a smaller --chunk-size."
        )

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(MANIFEST, MANIFEST_BODY)
        for class_name, data in classes:
            archive.writestr(class_name, data)
        archive.writestr(MODS_TOML, header + "".join(blocks))

    print(
        f"  {output.name}: {len(entries)} mods, {len(classes)} classes, "
        f"name/version lengths {len(name)}/{len(version)} (limit {UTF8_LIMIT})"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Merge nomodsN jars into a few NeoForge bundle jars.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--mods", type=Path, required=True, help="directory containing the jars")
    parser.add_argument("--output", type=Path, required=True,
                        help="bundle path; with several chunks, '-01', '-02', ... is appended")
    parser.add_argument("--limit", type=int, default=0, help="merge only the first N jars (0 = all)")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE,
                        help="mods per bundle jar (0 = one single jar)")
    args = parser.parse_args(argv)

    jars = sorted(
        (
            path
            for path in args.mods.glob("nomods*.jar")
            if re.match(r"nomods\d+-", path.name)
        ),
        key=lambda p: int(re.match(r"nomods(\d+)-", p.name).group(1)),
    )
    if not jars:
        raise SystemExit(f"error: no nomods*.jar found in {args.mods}")
    if args.limit > 0:
        jars = jars[: args.limit]
    if args.chunk_size < 0:
        raise SystemExit("error: --chunk-size must be >= 0")

    chunk_size = args.chunk_size or len(jars)
    chunks = [jars[i:i + chunk_size] for i in range(0, len(jars), chunk_size)]
    multiple = len(chunks) > 1

    if multiple:
        # Remove the old single bundle and stale chunks, otherwise the same mods
        # would be loaded twice (or an old oversized bundle would stay loaded).
        if args.output.is_file():
            args.output.unlink()
        for old in args.output.parent.glob(f"{args.output.stem}-[0-9][0-9]{args.output.suffix}"):
            old.unlink()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    total_mods = 0
    for index, chunk in enumerate(chunks, start=1):
        header: str | None = None
        blocks: list[str] = []
        classes: list[tuple[str, bytes]] = []
        entries: list[tuple[str, str]] = []
        for jar in chunk:
            jar_header, jar_blocks, jar_classes, jar_entries = load_jar(jar)
            if header is None:
                header = jar_header
            blocks.extend(jar_blocks)
            classes.extend(jar_classes)
            entries.extend(jar_entries)

        target = (
            args.output.with_name(f"{args.output.stem}-{index:02d}{args.output.suffix}")
            if multiple
            else args.output
        )
        write_bundle(target, header or "", blocks, classes, entries)
        total_mods += len(entries)

    print(f"merged {total_mods} mods from {len(jars)} jars into {len(chunks)} bundle(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
