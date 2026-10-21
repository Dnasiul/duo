"""Build and check Duo's release files for one commit.

  python3 scripts/release.py <commit> [--out DIR] [--python PY]

From a clean clone of the commit, this builds the wheel and sdist with the
pinned tools in scripts/build-requirements.txt, installs each into a fresh
environment with constraints.txt, runs the offline suite from the installed
package (strictly: no skips), runs `duo --help` and `duo-clean --help`, and
checks the installed version and that the wheel's package files match the
commit. It then writes the release files and SHA256SUMS to DIR/final and a
release-notes template to DIR/notes.md.

It needs Python 3.11 or newer, git, and network access to install packages; it
never needs credentials, calls a model, or publishes anything. The release
workflow (.github/workflows/release.yml) runs the same script on a tag and
publishes its files as a draft release.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "duo_plan"


def run(*argv: str | Path, cwd: Path | None = None, env: dict[str, str] | None = None,
        capture: bool = False) -> str:
    print("+", " ".join(map(str, argv)), flush=True)
    result = subprocess.run([str(arg) for arg in argv], cwd=cwd, env=env, check=True,
                            text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def environment(path: Path, python: str) -> Path:
    run(python, "-m", "venv", path)
    return path / "bin" / "python"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def wheel_matches(wheel: Path, clone: Path) -> None:
    """The wheel's package files are exactly the commit's src/duo files."""
    tracked = run("git", "ls-files", "src/duo", cwd=clone, capture=True).splitlines()
    expected = {name.removeprefix("src/"): (clone / name).read_bytes() for name in tracked}
    with zipfile.ZipFile(wheel) as archive:
        packaged = {name: archive.read(name) for name in archive.namelist()
                    if name.startswith("duo/") and not name.endswith("/")}
    if packaged != expected:
        differ = sorted(set(packaged) ^ set(expected)
                        | {n for n in set(packaged) & set(expected) if packaged[n] != expected[n]})
        raise SystemExit(f"The wheel does not match the commit: {', '.join(differ)}")


def notes(version: str, commit: str, files: list[Path], sums: dict[str, str]) -> str:
    wheel = f"{PROJECT}-{version}-py3-none-any.whl"
    rows = "\n".join(f"| `{path.name}` | `{sums[path.name]}` |" for path in files)
    return f"""Duo {version} SUMMARY. See `CHANGELOG.md` for details.

## Changes

- CHANGES

## Artifacts

Built from `{commit}` (tag `v{version}`) in a clean clone.

| File | SHA-256 |
| --- | --- |
{rows}

```sh
sha256sum --ignore-missing -c SHA256SUMS      # shasum -a 256 -c on macOS
python3 -m venv ~/.venvs/duo
~/.venvs/duo/bin/python -m pip install -c constraints.txt {wheel}
```

Released under the MIT License.

## Release evidence

| Check | Result |
| --- | --- |
| CI (Ubuntu and macOS, Python 3.10–3.14, wheel and sdist installed with `constraints.txt`, dependency audit, newest dependencies) | CI_RESULT |
| Installation test of these exact files | INSTALL_RESULT |
| Drift check against recorded CLI fixtures | DRIFT_RESULT |
| Live runs on the release candidate | LIVE_RESULT |

## Known limitations

- LIMITATIONS
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("commit", help="Commit, tag, or branch to build")
    parser.add_argument("--out", type=Path, help="Empty or new output directory (default: dist/release-<version>)")
    parser.add_argument("--python", default=sys.executable,
                        help="Python used for the install tests (default: this one)")
    args = parser.parse_args()
    import tomllib  # Python 3.11+; imported here so tests load this on 3.10


    commit = run("git", "rev-parse", "--verify", f"{args.commit}^{{commit}}", cwd=ROOT, capture=True)
    metadata = run("git", "show", f"{commit}:pyproject.toml", cwd=ROOT, capture=True)
    version = tomllib.loads(metadata)["project"]["version"]
    out = (args.out or ROOT / "dist" / f"release-{version}").resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty; remove it or pass --out")
    out.mkdir(parents=True, exist_ok=True)

    clone = out / "clone"
    run("git", "clone", "--quiet", "--no-checkout", ROOT, clone)
    run("git", "checkout", "--quiet", "--detach", commit, cwd=clone)

    build_python = environment(out / "buildenv", sys.executable)
    run(build_python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
        "-r", clone / "scripts" / "build-requirements.txt")
    # File times in the artifacts come from the commit, not the build machine.
    epoch = run("git", "log", "-1", "--format=%ct", commit, cwd=clone, capture=True)
    run(build_python, "-m", "build", "--no-isolation", "--outdir", out / "dist", clone,
        env={**os.environ, "SOURCE_DATE_EPOCH": epoch})

    wheel = out / "dist" / f"{PROJECT}-{version}-py3-none-any.whl"
    sdist = out / "dist" / f"{PROJECT}-{version}.tar.gz"
    built = sorted(path.name for path in (out / "dist").iterdir())
    if built != sorted([wheel.name, sdist.name]):
        raise SystemExit(f"Unexpected build output: {', '.join(built)}")
    wheel_matches(wheel, clone)

    for artifact in (wheel, sdist):
        python = environment(out / f"env-{artifact.name}", args.python)
        run(python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
            "-c", clone / "constraints.txt", artifact)
        # From the tests directory, so the installed package is what runs.
        run(python, "-B", "../scripts/run_tests.py", cwd=clone / "tests")
        installed = run(python, "-c", "import duo; print(duo.__version__)", capture=True)
        if installed != version:
            raise SystemExit(f"{artifact.name} installs version {installed}, not {version}")
        for command in ("duo", "duo-clean"):
            run(python.parent / command, "--help", capture=True)

    final = out / "final"
    final.mkdir()
    files = [final / wheel.name, final / sdist.name, final / "constraints.txt"]
    for source, target in zip((wheel, sdist, clone / "constraints.txt"), files):
        shutil.copy2(source, target)
    sums = {path.name: sha256(path) for path in files}
    (final / "SHA256SUMS").write_text("".join(f"{sums[p.name]}  {p.name}\n" for p in files))
    (out / "notes.md").write_text(notes(version, commit, files, sums))

    print(f"\nDuo {version} from {commit}: all checks passed.")
    print(f"Release files: {final}")
    print(f"Notes template: {out / 'notes.md'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        sys.exit(f"Failed ({exc.returncode}): {' '.join(map(str, exc.cmd))}")
