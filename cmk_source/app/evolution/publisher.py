import base64
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from app.config import settings
from app.memory.store import get_evolution_proposal, update_evolution_proposal

ROOT = Path(__file__).resolve().parents[2]


def publisher_status() -> dict:
    return {
        "configured": bool(settings.cmk_github_token and settings.cmk_github_repo),
        "repository": settings.cmk_github_repo,
        "branch": settings.cmk_github_branch,
        "package_path": settings.cmk_github_package_path,
        "current_build_commit": os.getenv("RENDER_GIT_COMMIT", ""),
        "mode": "owner-approved GitHub publish -> Render auto-deploy -> live commit verification",
    }


def _github_api(method: str, endpoint: str, payload: dict | None = None) -> dict:
    token = settings.cmk_github_token.strip()
    if not token:
        raise RuntimeError("Merlin publishing is not configured yet. Add CMK_GITHUB_TOKEN to Render once, then Merlin can publish approved upgrades directly.")
    url = "https://api.github.com" + endpoint
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "CMK-Merlin-System-Lab")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[-2000:]
        raise RuntimeError(f"GitHub publish failed ({exc.code}): {detail}") from exc


def _copy_source(destination: Path) -> None:
    def ignore(_dir: str, names: list[str]):
        blocked = {".git", ".env", "__pycache__", "atlas_memory.db", "uploads"}
        return [n for n in names if n in blocked or n.endswith(".pyc")]
    shutil.copytree(ROOT, destination, ignore=ignore)


def _build_outer_package(candidate: dict) -> bytes:
    with tempfile.TemporaryDirectory(prefix="cmk-publish-") as td:
        td = Path(td)
        source = td / "source"
        _copy_source(source)
        for item in candidate.get("files", []):
            rel = str(item.get("path", ""))
            if not rel or rel.startswith("/") or ".." in Path(rel).parts:
                raise RuntimeError(f"Unsafe candidate path: {rel}")
            target = source / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(item.get("content", "")))

        inner = td / "atlas_v1_1_pwa.zip"
        with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as z:
            for f in source.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(source).as_posix())

        outer = td / "ATLAS_COMPLETE_PACKAGE.zip"
        with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("ATLAS_COMPLETE_PACKAGE/START_HERE.txt", "CMK Merlin owner-approved deployment package.\n")
            z.write(inner, "ATLAS_COMPLETE_PACKAGE/atlas_v1_1_pwa.zip")
        return outer.read_bytes()


def publish_approved_candidate(user_id: str, proposal_id: int) -> dict:
    proposal = get_evolution_proposal(proposal_id, user_id)
    if not proposal:
        raise RuntimeError("Improvement proposal not found.")
    if proposal.get("status") != "deployment_approved":
        raise RuntimeError("Only an owner-approved, sandbox-passed candidate may be published.")
    body = proposal.get("body") or {}
    test = body.get("sandbox_test") or {}
    if not test.get("passed"):
        raise RuntimeError("Sandbox tests must pass before publishing.")
    candidate = body.get("candidate") or {}
    if not candidate.get("files"):
        raise RuntimeError("There is no candidate code to publish.")

    repo = settings.cmk_github_repo.strip()
    branch = settings.cmk_github_branch.strip() or "main"
    package_path = settings.cmk_github_package_path.strip() or "ATLAS_COMPLETE_PACKAGE.zip"
    quoted = urllib.parse.quote(package_path, safe="/")
    branch_info = _github_api("GET", f"/repos/{repo}/branches/{urllib.parse.quote(branch, safe='')}")
    before_commit = ((branch_info.get("commit") or {}).get("sha") or "")
    current = _github_api("GET", f"/repos/{repo}/contents/{quoted}?ref={urllib.parse.quote(branch, safe='')}")
    current_sha = current.get("sha")
    if not current_sha:
        raise RuntimeError("Could not resolve the current CMK deployment package on GitHub.")

    package = _build_outer_package(candidate)
    payload = {
        "message": f"Merlin approved upgrade: {proposal.get('title') or 'CMK improvement'}",
        "content": base64.b64encode(package).decode("ascii"),
        "sha": current_sha,
        "branch": branch,
    }
    result = _github_api("PUT", f"/repos/{repo}/contents/{quoted}", payload)
    commit_sha = ((result.get("commit") or {}).get("sha") or "")
    publication = {
        "repository": repo,
        "branch": branch,
        "package_path": package_path,
        "before_commit": before_commit,
        "commit": commit_sha,
        "published": bool(commit_sha),
    }
    body["publication"] = publication
    update_evolution_proposal(proposal_id, user_id, "published" if commit_sha else "publish_failed", body)
    return publication


def reconcile_live_publication(user_id: str, proposal: dict) -> dict:
    if not proposal or proposal.get("status") != "published":
        return proposal
    body = proposal.get("body") or {}
    expected = str((body.get("publication") or {}).get("commit") or "")
    current = os.getenv("RENDER_GIT_COMMIT", "")
    if expected and current and (current == expected or current.startswith(expected[:12])):
        body["live_verification"] = {"verified": True, "build_commit": current}
        update_evolution_proposal(int(proposal["id"]), user_id, "live_verified", body)
        proposal = dict(proposal)
        proposal["status"] = "live_verified"
        proposal["body"] = body
    return proposal


def rollback_publication(user_id: str, proposal_id: int) -> dict:
    proposal = get_evolution_proposal(proposal_id, user_id)
    if not proposal:
        raise RuntimeError("Improvement proposal not found.")
    body = proposal.get("body") or {}
    pub = body.get("publication") or {}
    before = str(pub.get("before_commit") or "")
    if not before:
        raise RuntimeError("No rollback commit was recorded for this upgrade.")
    repo = settings.cmk_github_repo.strip()
    branch = settings.cmk_github_branch.strip() or "main"
    package_path = settings.cmk_github_package_path.strip() or "ATLAS_COMPLETE_PACKAGE.zip"
    quoted = urllib.parse.quote(package_path, safe="/")
    old = _github_api("GET", f"/repos/{repo}/contents/{quoted}?ref={urllib.parse.quote(before, safe='')}")
    current = _github_api("GET", f"/repos/{repo}/contents/{quoted}?ref={urllib.parse.quote(branch, safe='')}")
    payload = {
        "message": f"Merlin rollback: {proposal.get('title') or 'CMK upgrade'}",
        "content": old.get("content", "").replace("\n", ""),
        "sha": current.get("sha"),
        "branch": branch,
    }
    result = _github_api("PUT", f"/repos/{repo}/contents/{quoted}", payload)
    rollback_commit = ((result.get("commit") or {}).get("sha") or "")
    body["rollback"] = {"commit": rollback_commit, "restored_from": before}
    update_evolution_proposal(proposal_id, user_id, "rolled_back", body)
    return body["rollback"]
