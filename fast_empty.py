#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fast builder for "empty" mods (no network channel, no hello, no events).

Why this exists
---------------
The normal massbuild.py renders one .java file per mod, runs javac over them and
then packs the class files. For a million mods that is ~85 minutes: 1M source
files + 1M class files + 1M jars.

Empty mods are all identical apart from the numeric id, so this script compiles
seven class templates (one per digit count: 1, 10, 100, ... 1000000) with javac,
then produces every other mod by patching those bytes in place. Because the
replacement ids have the same length, the class file stays byte-for-byte valid;
it is even identical to what javac would have produced for that id (verified
against the javac-built jars).

The result: no source files, no class files, no javac per mod - only the final
jars are written, in parallel.

Usage:
    python fast_empty.py --count 1000000 --out dist-empty --force
    python fast_empty.py --count 1000000 --start 10001 --out dist-empty --force --jobs 8
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import generate as gen  # noqa: E402
import massbuild as mb  # noqa: E402

TEMPLATE_IDS = (1, 10, 100, 1_000, 10_000, 100_000, 1_000_000)
TEMPLATE_WORKSPACE = ROOT / ".fastbuild"
JAR_ENTRY_MANIFEST = "META-INF/MANIFEST.MF"
JAR_ENTRY_TOML = "META-INF/neoforge.mods.toml"


def build_templates(jdk: Path, classpath: str, props: dict[str, str]) -> dict[int, tuple[int, bytes, str]]:
    """Compile one class per digit count and return {digits: (base_id, class_bytes, toml)}."""
    shutil.rmtree(TEMPLATE_WORKSPACE, ignore_errors=True)
    src_root = TEMPLATE_WORKSPACE / "src"
    classes_dir = TEMPLATE_WORKSPACE / "classes"
    java_files: list[Path] = []
    tomls: dict[int, str] = {}

    for base in TEMPLATE_IDS:
        files = gen.render_module(base, props, "empty")
        for relative, content in files.items():
            try:
                inner = relative.relative_to(Path("src/main"))
            except ValueError:
                continue
            target = src_root / inner
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
            if target.suffix == ".java":
                java_files.append(target)
            elif target.name == "neoforge.mods.toml":
                tomls[base] = content

    mb.compile_sources(jdk, java_files, classpath, classes_dir)

    templates: dict[int, tuple[int, bytes, str]] = {}
    for base in TEMPLATE_IDS:
        class_file = classes_dir / "com" / "chapple" / f"nomods{base}" / f"NoMods{base}.class"
        templates[len(str(base))] = (base, class_file.read_bytes(), tomls[base])

    shutil.rmtree(TEMPLATE_WORKSPACE, ignore_errors=True)
    return templates


def patched_class(template: bytes, base: int, target: int) -> bytes:
    data = template.replace(f"com/chapple/nomods{base}".encode(), f"com/chapple/nomods{target}".encode())
    data = data.replace(f"NoMods{base}".encode(), f"NoMods{target}".encode())
    data = data.replace(f"nomods{base}".encode(), f"nomods{target}".encode())
    return data


def worker(
    first: int,
    last: int,
    templates: dict[int, tuple[int, bytes, str]],
    props: dict[str, str],
    out_dir: Path,
    progress_every: int,
) -> int:
    version = props["mod_version"]
    written = 0
    for index in range(first, last):
        digits = len(str(index))
        base, class_template, toml_template = templates[digits]
        mod_id = f"nomods{index}"
        class_bytes = patched_class(class_template, base, index)
        toml = toml_template.replace(f"nomods{base}", mod_id)

        jar_path = out_dir / f"{mod_id}-{version}.jar"
        with zipfile.ZipFile(
            jar_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1
        ) as archive:
            archive.writestr(
                JAR_ENTRY_MANIFEST,
                "Manifest-Version: 1.0\r\n"
                f"Implementation-Title: {mod_id}\r\n"
                f"Implementation-Version: {version}\r\n"
                "\r\n",
            )
            archive.writestr(f"com/chapple/{mod_id}/NoMods{index}.class", class_bytes)
            archive.writestr(JAR_ENTRY_TOML, toml)
        written += 1
        if progress_every and written % progress_every == 0:
            print(f"      {written:,} jars (up to {mod_id})", flush=True)
    return written


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Fast builder for empty nomods jars (byte-patched class templates).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--count", type=int, required=True, help="how many mods to build")
    parser.add_argument("--start", type=int, default=1, help="first mod id")
    parser.add_argument("--out", type=Path, default=ROOT / "dist-empty", help="output directory")
    parser.add_argument("--force", action="store_true", help="delete existing files in --out first")
    parser.add_argument("--jobs", type=int, default=min(8, __import__("os").cpu_count() or 1),
                        help="parallel jar writers")
    args = parser.parse_args(argv)

    if args.count < 1 or args.start < 1:
        raise SystemExit("error: --count and --start must be >= 1")

    started = time.monotonic()
    out_dir = args.out.resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        if not args.force:
            raise SystemExit(f"error: {out_dir} is not empty; pass --force.")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jdk = mb.find_jdk21()
    print(f"JDK 21: {jdk}", flush=True)
    props = gen.load_properties()
    print("[1/3] resolving NeoForge compile classpath ...", flush=True)
    classpath = mb.resolve_classpath(jdk)

    print("[2/3] compiling 7 class templates ...", flush=True)
    templates = build_templates(jdk, classpath, props)
    print("      templates ready", flush=True)

    indices = range(args.start, args.start + args.count)
    jobs = max(1, min(args.jobs, args.count))
    chunk = (args.count + jobs - 1) // jobs
    ranges = []
    for job in range(jobs):
        first = args.start + job * chunk
        last = min(first + chunk, args.start + args.count)
        if first < last:
            ranges.append((first, last))

    print(f"[3/3] writing {args.count:,} jars with {len(ranges)} parallel writers ...", flush=True)
    total = 0
    with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
        futures = [
            pool.submit(worker, first, last, templates, props, out_dir, 50_000)
            for first, last in ranges
        ]
        for future in futures:
            total += future.result()

    elapsed = time.monotonic() - started
    print()
    print(f"done in {elapsed:.1f}s: {total:,} jars in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
