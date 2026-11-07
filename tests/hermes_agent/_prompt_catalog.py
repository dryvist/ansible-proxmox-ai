"""Read literal prompt bodies from the role's pinned public catalog for tests."""

from functools import lru_cache
import fcntl
from io import BytesIO
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
FRONTMATTER = r"(?s)^---\n.*?\n---\n"


@lru_cache
def catalog_files(pin):
    """Cache the immutable archive; never build Nix or contact a runtime."""
    revision = re.search(r"ai-llm-prompts/([0-9a-f]{40})#", pin)[1]
    cache = ROOT / ".pytest_cache" / f"hermes-catalog-{revision}.tar"
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not cache.exists():
            checkout = cache.parent / f"hermes-catalog-{revision}.git"
            for args in (
                ["init", "--bare", str(checkout)],
                ["-C", str(checkout), "-c", "credential.helper=", "fetch", "--depth=1",
                 "https://github.com/dryvist/ai-llm-prompts.git", revision],
            ):
                subprocess.run(["git", *args], check=True, capture_output=True, timeout=120)
            cache.write_bytes(subprocess.check_output(
                ["git", "-C", str(checkout), "archive", revision, "auto-ai-agent"],
                timeout=60,
            ))
    with tarfile.open(fileobj=BytesIO(cache.read_bytes())) as archive:
        return {
            Path(member.name).name: archive.extractfile(member).read().decode()
            for member in archive.getmembers()
            if member.isfile() and member.name.startswith("auto-ai-agent/")
            and member.name.endswith(".md")
            and member.name.count("/") == 1
        }


def prompt_bodies(defaults):
    files = catalog_files(defaults["hermes_agent_prompts_flake_ref"])
    return {
        key: "".join(re.sub(FRONTMATTER, "", files[name]) for name in
                     source.get("prompt_files", [source.get("prompt_file")]))
        for key, source in defaults["hermes_agent_catalog_prompt_sources"].items()
    }
