from pathlib import Path
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]


def test_version_is_set():
    assert settings.cmk_version == "4.3.1"


def test_owner_approval_gate_present():
    engine = (ROOT / "app/evolution/engine.py").read_text()
    assert '"can_modify_live_production_without_approval": False' in engine
    assert "owner approval" in engine
    assert "sandbox tests" in engine.lower()


def test_publisher_targets_cmk_repo_and_main():
    publisher = (ROOT / "app/evolution/publisher.py").read_text()
    config = (ROOT / "app/config.py").read_text()
    assert "coloummckeown-lab/Atlas-ai" in config
    assert 'cmk_github_branch: str = "main"' in config
    assert "ATLAS_COMPLETE_PACKAGE.zip" in publisher
    assert "RENDER_GIT_COMMIT" in publisher


def test_core_source_files_exist():
    for rel in [
        "app/config.py",
        "app/evolution/engine.py",
        "app/evolution/publisher.py",
        "app/main.py",
        "app/static/app.js",
    ]:
        assert (ROOT / rel).is_file(), rel
