"""What the README, docs and CI files promise (fix wave 2026-10-28): install steps, AI honesty, pinning."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/mAgNiFiCeNtTaCo/tacosort"


def text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def words(name: str) -> str:
    """The file's text with line breaks folded into spaces, for phrase checks."""
    return " ".join(text(name).split())


def all_docs() -> list[Path]:
    return [ROOT / "README.md", ROOT / "SECURITY.md", ROOT / "CONTRIBUTING.md",
            ROOT / "examples" / "config.example.yaml", *sorted((ROOT / "docs").glob("*.md"))]


@pytest.mark.parametrize("doc", all_docs(), ids=lambda p: p.name)
def test_never_installs_the_pypi_name(doc: Path) -> None:
    """T-B2: TacoSort is not published on PyPI, so installing it by name would fail or, if someone
    else took the name, install a stranger's program. Docs install from GitHub only."""
    body = doc.read_text(encoding="utf-8")
    bad = re.findall(r"pipx? install (?:--upgrade |-U )?\"?tacosort(?:\[ai\])?\"?(?! @)(?=[\s`)]|$)", body)
    assert bad == [], f"{doc.name}: {bad}"
    assert "<this repository's URL>" not in body and "OWNER/REPO" not in body


def test_readme_installs_from_github() -> None:
    readme = text("README.md")
    assert f"pipx install git+{REPO_URL}" in readme
    assert f'pipx install "tacosort[ai] @ git+{REPO_URL}"' in readme
    assert f"git clone {REPO_URL}\ncd tacosort" in readme
    for system in ("Windows", "macOS", "Linux"):
        assert f"**{system}:**" in readme, system
    assert "ensurepath" in readme


def test_ai_key_docs_point_at_the_keychain_command() -> None:
    """T-S9: a key set in one terminal window is invisible to the background schedule."""
    for name in ("README.md", "docs/privacy.md"):
        body = words(name)
        assert "tacosort config ai-key" in body, name
        assert "schedule" in body and "one terminal window" in body, name


def test_privacy_is_honest_about_prompt_injection() -> None:
    """S-S4: no promise that hidden instructions can't work; say what is and isn't possible."""
    for name in ("docs/privacy.md", "SECURITY.md"):
        body = words(name)
        assert "can't change what TacoSort does" not in body, name
        assert "tacosort undo" in body, name
        assert "never sent to" in body and "person" in body, name
        assert "could" in body and "wrong folder" in body, name


def test_gmail_labels_and_missing_message_id_explained() -> None:
    readme = words("README.md")
    assert "keep in the Inbox" in readme and "never moves that" in readme
    assert "Message-ID" in readme and "leaves them in" in readme
    assert "keep in the Inbox" in words("docs/providers.md")


def test_ci_actions_pinned_to_commits() -> None:
    """S-N5: tags can be moved; a full commit SHA can't."""
    workflow = yaml.safe_load(text(".github/workflows/ci.yml"))
    uses = [step["uses"] for job in workflow["jobs"].values() for step in job["steps"] if "uses" in step]
    assert uses, "no actions found"
    for u in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u), u


def test_dependabot_watches_actions_and_pip() -> None:
    cfg = yaml.safe_load(text(".github/dependabot.yml"))
    ecosystems = {u["package-ecosystem"]: u["schedule"]["interval"] for u in cfg["updates"]}
    assert ecosystems == {"github-actions": "weekly", "pip": "weekly"}
