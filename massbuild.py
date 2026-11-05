#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mass-produce no-op NeoForge mods and keep only the jars.

This is the "many thousands of mods" builder. Unlike generate.py + Gradle
(which creates one Gradle project, one build directory and ~70 MB of
ModDevGradle artifacts per mod), this script:

  1. asks Gradle once for the NeoForge compile classpath;
  2. renders the sources of all requested mods into a throw-away workspace;
  3. compiles every mod with a single javac invocation;
  4. packs each mod into its own jar inside --out;
  5. deletes the workspace, every per-module build directory and the
     Gradle intermediate output, so only the jars are left behind.

Usage
-----
    python massbuild.py                     # 10 000 mods -> dist\\
    python massbuild.py --count 100         # quick test -> dist\\
    python massbuild.py --count 20000 --out E:\\mods

The metadata (version, authors, NeoForge version, package root, ...) comes
from gradle.properties, exactly like generate.py.

Requirements: a JDK 21 (auto-detected, or set JAVA_HOME) and the generated
nomods1 module (run "python generate.py --count 10" once) so that Gradle can
resolve the NeoForge classpath.
"""

from __future__ import annotations

import argparse
import glob
import os
import random
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import generate as gen  # noqa: E402  (reuse templates + gradle.properties)

WORKSPACE = ROOT / ".massbuild"
CLASSPATH_FILE = ROOT / "build" / "massbuild" / "compile-classpath.txt"
DEFAULT_COUNT = 10_000

JAR_ENTRY_MANIFEST = "META-INF/MANIFEST.MF"
JAR_ENTRY_TOML = "META-INF/neoforge.mods.toml"


# --------------------------------------------------------------------------
# Environment helpers
# --------------------------------------------------------------------------

def find_jdk21() -> Path:
    """Locate a JDK 21 installation (Gradle and javac both need it)."""
    candidates: list[Path] = []
    if os.environ.get("JAVA_HOME"):
        candidates.append(Path(os.environ["JAVA_HOME"]))

    patterns = [
        r"C:\Program Files\Java\jdk-21*",
        r"C:\Program Files\Eclipse Adoptium\jdk-21*",
        r"C:\Program Files\Zulu\zulu-21*",
        r"C:\Program Files\Microsoft\jdk-21*",
        str(Path.home() / "AppData/Local/Programs/Eclipse Adoptium/jdk-21*"),
    ]
    for pattern in patterns:
        candidates.extend(Path(p) for p in glob.glob(pattern))

    # De-duplicate while keeping order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)

    for candidate in unique:
        javac = candidate / "bin" / "javac.exe"
        java = candidate / "bin" / "java.exe"
        if not (javac.is_file() and java.is_file()):
            continue
        release = candidate / "release"
        if release.is_file():
            try:
                if 'JAVA_VERSION="21' in release.read_text(encoding="utf-8", errors="ignore"):
                    return candidate
            except OSError:
                pass
            continue
        try:
            result = subprocess.run(
                [str(javac), "-version"], capture_output=True, text=True, timeout=30
            )
        except OSError:
            continue
        version_text = (result.stdout or "") + (result.stderr or "")
        if "javac 21." in version_text:
            return candidate

    # Last resort: a javac from PATH.
    on_path = shutil.which("javac")
    if on_path:
        return Path(on_path).resolve().parent.parent

    raise SystemExit(
        "error: JDK 21 not found. Install one or point JAVA_HOME at it."
    )


def gradle_env(jdk: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["JAVA_HOME"] = str(jdk)
    env["PATH"] = str(jdk / "bin") + os.pathsep + env.get("PATH", "")
    return env


def resolve_classpath(jdk: Path) -> str:
    """Run Gradle once to get the NeoForge compile classpath."""
    if not (ROOT / "nomods1").is_dir():
        raise SystemExit(
            "error: nomods1 is missing. Run 'python generate.py --count 10' once "
            "so Gradle can resolve the NeoForge classpath."
        )

    print("[1/5] resolving NeoForge compile classpath via Gradle ...", flush=True)
    started = time.monotonic()
    process = subprocess.run(
        [
            str(ROOT / "gradlew.bat"),
            "-q",
            "--console=plain",
            # A single-use daemon exits after the task, so it does not keep the
            # generated NeoForge jars locked when we delete the build folders.
            "--no-daemon",
            "writeCompileClasspath",
        ],
        cwd=ROOT,
        env=gradle_env(jdk),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    if process.returncode != 0:
        sys.stderr.write(process.stdout)
        sys.stderr.write(process.stderr)
        raise SystemExit("error: Gradle could not resolve the compile classpath.")
    if not CLASSPATH_FILE.is_file():
        raise SystemExit(f"error: {CLASSPATH_FILE} was not created.")

    classpath = CLASSPATH_FILE.read_text(encoding="utf-8").strip()
    entries = classpath.split(";") if classpath else []
    missing = [entry for entry in entries if entry and not Path(entry).exists()]
    if missing:
        raise SystemExit("error: classpath entries are missing, e.g. " + missing[0])
    print(
        f"      {len(entries)} entries, {len(classpath):,} chars "
        f"({time.monotonic() - started:.1f}s)",
        flush=True,
    )
    return classpath


# --------------------------------------------------------------------------
# Rendering / compiling
# --------------------------------------------------------------------------

def render_sources(
    indices: list[int], props: dict[str, str], variant: str, src_root: Path
) -> list[Path]:
    """Write every mod's .java files into src_root; return their paths."""
    java_files: list[Path] = []

    for index in indices:
        files = gen.render_module(index, props, variant)
        for relative, content in files.items():
            try:
                inner = relative.relative_to(Path("src/main"))
            except ValueError:
                continue  # build.gradle - not needed for the mass build
            target = src_root / inner
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            if target.suffix == ".java":
                java_files.append(target)

    return java_files


def compile_sources(
    jdk: Path, java_files: list[Path], classpath: str, classes_dir: Path
) -> Path:
    classes_dir.mkdir(parents=True, exist_ok=True)

    def quote(path: str) -> str:
        # javac @argfiles use double quotes for names containing spaces.
        return f'"{path}"' if " " in path else path

    args_file = classes_dir.parent / "javac.args"
    lines = [
        "-d", quote(str(classes_dir.resolve())),
        "-encoding", "UTF-8",
        "-proc:none",
        "-nowarn",
        "-g",
    ]
    lines.extend(quote(p.resolve().as_posix()) for p in java_files)
    args_file.write_text("\n".join(lines), encoding="utf-8")

    env = gradle_env(jdk)
    env["CLASSPATH"] = classpath  # keeps the long classpath out of the command line

    process = subprocess.run(
        [str(jdk / "bin" / "javac.exe"), "-J-Xmx768m", f"@{args_file}"],
        cwd=classes_dir.parent,
        env=env,
        capture_output=True,
        # javac prints localized diagnostics in the console code page (GBK on
        # this machine), so never let Python decode it as UTF-8 strictly.
        text=False,
        timeout=3600,
    )
    if process.returncode != 0:
        sys.stderr.write(process.stdout.decode("utf-8", "replace")[-8000:])
        sys.stderr.write(process.stderr.decode("utf-8", "replace")[-8000:])
        raise RuntimeError("javac failed")

    return classes_dir


# --------------------------------------------------------------------------
# Packaging
# --------------------------------------------------------------------------

def manifest_for(index: int, props: dict[str, str]) -> str:
    mod_id = f"nomods{index}"
    return (
        "Manifest-Version: 1.0\r\n"
        f"Implementation-Title: {mod_id}\r\n"
        f"Implementation-Version: {props['mod_version']}\r\n"
        "\r\n"
    )


def toml_for(index: int, props: dict[str, str], variant: str) -> str:
    relative = Path("src/main/resources") / JAR_ENTRY_TOML
    return gen.render_module(index, props, variant)[relative]


def package_jars(
    indices: list[int], props: dict[str, str], classes_dir: Path, out_dir: Path, variant: str
) -> list[Path]:
    print(f"[4/5] packing {len(indices):,} jars into {out_dir} ...", flush=True)
    started = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    jars: list[Path] = []

    for position, index in enumerate(indices, start=1):
        mod_id = f"nomods{index}"
        package_dir = classes_dir / "com" / "chapple" / mod_id
        if not package_dir.is_dir():
            raise SystemExit(f"error: no compiled classes for {mod_id}")

        jar_path = out_dir / f"{mod_id}-{props['mod_version']}.jar"
        with zipfile.ZipFile(
            jar_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            archive.writestr(JAR_ENTRY_MANIFEST, manifest_for(index, props))
            for class_file in sorted(package_dir.glob("*.class")):
                archive.write(class_file, f"com/chapple/{mod_id}/{class_file.name}")
            archive.writestr(JAR_ENTRY_TOML, toml_for(index, props, variant))
        jars.append(jar_path)

        # Auto-clean: this mod is packed, drop its classes right away.
        shutil.rmtree(package_dir, ignore_errors=True)

        if position % 2000 == 0:
            print(f"      {position:,}/{len(indices):,}", flush=True)

    print(
        f"      {len(jars):,} jars in {time.monotonic() - started:.1f}s",
        flush=True,
    )
    return jars


# --------------------------------------------------------------------------
# Verification / cleanup
# --------------------------------------------------------------------------

def validate(jars: list[Path], variant: str, sample_size: int = 25) -> None:
    print("[5/5] validating ...", flush=True)
    names = {jar.name for jar in jars}
    if len(names) != len(jars):
        raise SystemExit("error: duplicate jar names were produced.")

    random.seed(935321)
    sample = random.sample(jars, min(sample_size, len(jars)))
    for jar in sample:
        with zipfile.ZipFile(jar) as archive:
            bad = archive.testzip()
            if bad is not None:
                raise SystemExit(f"error: corrupt entry {bad} in {jar.name}")
            entries = set(archive.namelist())
            mod_id = jar.name.split("-")[0]
            expected_classes = {
                f"com/chapple/{mod_id}/NoMods{mod_id.removeprefix('nomods')}.class",
            }
            if variant == "hello":
                expected_classes.add(f"com/chapple/{mod_id}/HelloPayload.class")
            elif f"com/chapple/{mod_id}/HelloPayload.class" in entries:
                raise SystemExit(f"error: {jar.name} unexpectedly contains HelloPayload")
            if not expected_classes.issubset(entries):
                raise SystemExit(f"error: {jar.name} is missing class entries: {expected_classes - entries}")
            if JAR_ENTRY_TOML not in entries or JAR_ENTRY_MANIFEST not in entries:
                raise SystemExit(f"error: {jar.name} is missing metadata entries")
            toml = archive.read(JAR_ENTRY_TOML).decode("utf-8")
            if f'modId = "{mod_id}"' not in toml:
                raise SystemExit(f"error: {jar.name} has a mismatching modId")
            if b"\xca\xfe\xba\xbe" != archive.read(sorted(expected_classes)[0])[:4]:
                raise SystemExit(f"error: {jar.name} contains an invalid class file")

    total_bytes = sum(jar.stat().st_size for jar in jars)
    print(
        f"      {len(jars):,} jars, {total_bytes / 1024 / 1024:.1f} MB, "
        f"{len(sample)} sampled jars look valid",
        flush=True,
    )


def remove_tree(path: Path, attempts: int = 8) -> bool:
    """Delete a directory, retrying briefly in case a file handle is still open."""
    for _ in range(attempts):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        time.sleep(0.5)
    return not path.exists()


def cleanup(clean_module_builds: bool) -> None:
    remove_tree(WORKSPACE)
    leftovers: list[Path] = []
    if clean_module_builds:
        for build_dir in ROOT.glob("nomods*/build"):
            if not remove_tree(build_dir):
                leftovers.append(build_dir)
    remove_tree(ROOT / "build")
    if leftovers:
        print(
            "warning: could not delete " + ", ".join(str(p) for p in leftovers)
            + " (still in use by another process).",
            flush=True,
        )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def build_batch(
    batch_no: int,
    batch_total: int,
    indices: list[int],
    props: dict[str, str],
    variant: str,
    jdk: Path,
    classpath: str,
    out_dir: Path,
    do_validate: bool,
) -> int:
    """Render, compile and pack one batch, then delete that batch's temp files."""
    started = time.monotonic()
    batch_root = WORKSPACE / f"batch-{batch_no:04d}"
    try:
        java_files = render_sources(indices, props, variant, batch_root / "src")
        classes_dir = compile_sources(jdk, java_files, classpath, batch_root / "classes")
        shutil.rmtree(batch_root / "src", ignore_errors=True)

        jars = package_jars(indices, props, classes_dir, out_dir, variant)
        if do_validate:
            validate(jars, variant, sample_size=min(5, len(jars)))

        elapsed = time.monotonic() - started
        print(
            f"  batch {batch_no}/{batch_total}: nomods{indices[0]}..nomods{indices[-1]} "
            f"-> {len(jars):,} jars in {elapsed:.1f}s",
            flush=True,
        )
        return len(jars)
    finally:
        remove_tree(batch_root)

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build thousands of no-op NeoForge mods, leaving only jars behind.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
                        help="amount of mods to build (nomods<start> .. nomods<start+count-1>)")
    parser.add_argument("--start", type=int, default=1,
                        help="index of the first mod to build")
    parser.add_argument("--variant", choices=("hello", "empty"), default="hello",
                        help="hello = network channel + hello packet; empty = no networking at all")
    parser.add_argument("--out", type=Path, default=ROOT / "dist",
                        help="output directory for the jars")
    parser.add_argument("--force", action="store_true",
                        help="delete existing files in --out first")
    parser.add_argument("--keep-builds", action="store_true",
                        help="do not delete nomods*/build after the build")
    parser.add_argument("--classpath", type=Path,
                        help="reuse a previously dumped compile-classpath.txt")
    parser.add_argument("--no-validate", action="store_true",
                        help="skip jar validation")
    parser.add_argument("--batch-size", type=int, default=10_000,
                        help="mods per batch; keeps disk usage and javac memory bounded")
    parser.add_argument("--jobs", type=int, default=min(6, os.cpu_count() or 1),
                        help="how many batches to build in parallel")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.count < 1:
        raise SystemExit("error: --count must be >= 1")
    if args.start < 1:
        raise SystemExit("error: --start must be >= 1")
    if args.batch_size < 1:
        raise SystemExit("error: --batch-size must be >= 1")
    if args.jobs < 1:
        raise SystemExit("error: --jobs must be >= 1")

    started = time.monotonic()
    out_dir = args.out.resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        if not args.force:
            raise SystemExit(
                f"error: {out_dir} is not empty; pass --force to replace its contents."
            )
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jdk = find_jdk21()
    print(f"JDK 21: {jdk}", flush=True)
    props = gen.load_properties()
    indices = list(range(args.start, args.start + args.count))
    batches = [indices[i:i + args.batch_size] for i in range(0, len(indices), args.batch_size)]
    print(
        f"building {len(indices):,} {args.variant} mods in {len(batches):,} batches "
        f"of up to {args.batch_size:,}, {args.jobs} jobs",
        flush=True,
    )

    shutil.rmtree(WORKSPACE, ignore_errors=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    done = 0
    try:
        if args.classpath:
            classpath = args.classpath.resolve().read_text(encoding="utf-8").strip()
            print("[1/5] using supplied compile classpath", flush=True)
        else:
            classpath = resolve_classpath(jdk)

        if args.jobs == 1:
            for batch_no, batch in enumerate(batches, start=1):
                done += build_batch(
                    batch_no, len(batches), batch, props, args.variant,
                    jdk, classpath, out_dir, not args.no_validate,
                )
        else:
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                futures = [
                    pool.submit(
                        build_batch, batch_no, len(batches), batch, props, args.variant,
                        jdk, classpath, out_dir, not args.no_validate,
                    )
                    for batch_no, batch in enumerate(batches, start=1)
                ]
                for future in as_completed(futures):
                    done += future.result()
    finally:
        cleanup(clean_module_builds=not args.keep_builds)

    elapsed = time.monotonic() - started
    print()
    print(f"done in {elapsed:.1f}s: {done:,} jars in {out_dir}")
    print("workspace and build intermediates were removed; only the jars remain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
