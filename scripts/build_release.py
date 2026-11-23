"""Build a community install ZIP from a strict source whitelist."""

import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "main.py",
    "interop.py",
    "bridge.py",
    "desktop.py",
    "control.py",
    "relay.py",
    "protocol.py",
    "settings.py",
    "reports.py",
    "task_context.py",
    "host_agent.py",
    "codex_mcp.py",
    "_conf_schema.json",
    "requirements.txt",
    "requirements-mcp.txt",
    "metadata.yaml",
    "config.example.json",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
]


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    version = next(
        line.split(":", 1)[1].strip()
        for line in (ROOT / "metadata.yaml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version:")
    )
    archive = output / f"astrbot_plugin_codex_bridge-{version}.zip"
    files = [ROOT / name for name in FILES]
    files += sorted((ROOT / "docs").glob("*.md"))
    files += [
        ROOT / "scripts" / name
        for name in ("doctor.py", "build_release.py", "start_host.ps1")
    ]
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for path in files:
            package.write(path, path.relative_to(ROOT).as_posix())
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(
        f"{checksum}  {archive.name}\n", encoding="ascii"
    )
    print(archive.resolve())
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    build(parser.parse_args().output)
