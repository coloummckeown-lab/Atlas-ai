import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from openai import AsyncOpenAI
from app.config import settings
from app.schemas import ImprovementProposal
from app.memory.store import save_evolution_proposal, get_evolution_proposal, update_evolution_proposal

ROOT = Path(__file__).resolve().parents[2]
PROTECTED = {".env", ".git", "atlas_memory.db"}


def source_manifest() -> list[str]:
    allowed = {".py", ".js", ".html", ".css", ".json", ".webmanifest"}
    files = []
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in allowed and not any(part in PROTECTED for part in p.parts):
            files.append(str(p.relative_to(ROOT)))
    return sorted(files)


def evolution_contract() -> dict:
    return {
        "can_read_own_source": True,
        "can_propose_patch": True,
        "can_generate_tests": True,
        "can_run_tests_in_sandbox": True,
        "can_measure_candidate_vs_baseline": True,
        "can_learn_from_feedback": True,
        "continuous_review_on_owner_open": True,
        "can_prepare_approved_upgrade_end_to_end_in_sandbox": True,
        "can_modify_live_production_without_approval": False,
        "can_disable_safety_gates": False,
        "can_access_secrets_for_self_modification": False,
        "promotion_path": "continuous review -> proposal -> owner approval -> candidate -> sandbox tests -> GitHub publish -> Render auto-deploy -> verify",
    }


def validate_proposal(proposal: ImprovementProposal) -> tuple[bool, list[str]]:
    reasons = []
    if not proposal.requires_human_approval:
        reasons.append("Production code changes require human approval.")
    if not proposal.test_plan:
        reasons.append("Every code change requires a test plan.")
    if any(f.startswith("/") or ".." in f for f in proposal.target_files):
        reasons.append("Target files must remain inside the repository.")
    return not reasons, reasons


async def propose_improvement(user_id: str, objective: str) -> dict:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required before CMK can generate evolution proposals.")
    snippets = []
    for rel in source_manifest()[:45]:
        p = ROOT / rel
        try:
            txt = p.read_text(errors="ignore")[:6000]
            snippets.append(f"\n--- {rel} ---\n{txt}")
        except Exception:
            continue
    prompt = f"""You are CMK's Evolution Reviewer. Review this application source and propose ONE high-value improvement.
Objective: {objective}
Do not propose removing approval, safety, auth, logging, evaluation, or secret boundaries.
Return JSON with: title, problem, rationale, target_files, proposed_changes, test_plan, risk, expected_benefit, requires_human_approval=true.
SOURCE:\n{''.join(snippets)}"""
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    r = await client.responses.create(model=settings.openai_model, input=prompt, reasoning={"effort": "high"}, text={"verbosity": "medium"}, store=False)
    raw = r.output_text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n",1)[1].rsplit("```",1)[0]
    proposal = ImprovementProposal(**json.loads(raw))
    valid, reasons = validate_proposal(proposal)
    body = proposal.model_dump()
    body["valid"] = valid
    body["validation_reasons"] = reasons
    save_evolution_proposal(user_id, proposal.title, body)
    return body


async def build_candidate(user_id: str, proposal_id: int) -> dict:
    proposal = get_evolution_proposal(proposal_id, user_id)
    if not proposal:
        raise RuntimeError("Improvement proposal not found.")
    if proposal["status"] not in {"approved_for_sandbox", "sandbox_failed", "candidate_ready"}:
        raise RuntimeError("The owner must approve this proposal before CMK can write candidate code.")
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required before CMK can write candidate code.")
    body = proposal["body"]
    targets = [x for x in body.get("target_files", []) if x in source_manifest()][:8]
    if not targets:
        raise RuntimeError("Proposal has no valid target files.")
    source = []
    for rel in targets:
        txt = (ROOT / rel).read_text(errors="ignore")
        source.append(f"\n--- {rel} ---\n{txt[:24000]}")
    prompt = f"""You are CMK's sandbox code author. The owner has APPROVED writing a candidate implementation for this proposal.
You are NOT modifying production. Produce complete replacement contents only for files that genuinely need changing.
Never remove authentication, approval gates, safety boundaries, secret handling, or logging.
PROPOSAL:
{json.dumps(body, ensure_ascii=False)}
TARGET SOURCE:
{''.join(source)}
Return JSON only: {{"summary":str,"files":[{{"path":str,"content":str}}],"tests_added_or_changed":[str],"risk_notes":[str]}}
"""
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    r = await client.responses.create(model=settings.openai_model, input=prompt, reasoning={"effort":"high"}, text={"verbosity":"medium"}, store=False)
    raw = r.output_text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n",1)[1].rsplit("```",1)[0]
    candidate = json.loads(raw)
    allowed = set(targets) | {x for x in source_manifest() if x.startswith("tests/")}
    clean_files = []
    for item in candidate.get("files", []):
        rel = str(item.get("path", ""))
        if rel in allowed and ".." not in rel and not rel.startswith("/"):
            clean_files.append({"path": rel, "content": str(item.get("content", ""))})
    candidate["files"] = clean_files
    body["candidate"] = candidate
    update_evolution_proposal(proposal_id, user_id, "candidate_ready", body)
    return candidate


def run_candidate_tests(user_id: str, proposal_id: int) -> dict:
    proposal = get_evolution_proposal(proposal_id, user_id)
    if not proposal or proposal["status"] not in {"candidate_ready", "sandbox_failed", "sandbox_passed"}:
        raise RuntimeError("A candidate must be generated before sandbox testing.")
    body = proposal["body"]
    candidate = body.get("candidate") or {}
    with tempfile.TemporaryDirectory(prefix="cmk-evolution-") as td:
        sandbox = Path(td) / "repo"
        shutil.copytree(ROOT, sandbox, ignore=shutil.ignore_patterns(".git", ".env", "__pycache__", "*.pyc", "atlas_memory.db", "uploads"))
        for item in candidate.get("files", []):
            path = sandbox / item["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item["content"])
        proc = subprocess.run(["python", "-m", "pytest", "-q"], cwd=sandbox, capture_output=True, text=True, timeout=90)
        result = {"passed": proc.returncode == 0, "returncode": proc.returncode, "output": (proc.stdout + "\n" + proc.stderr)[-12000:]}
    body["sandbox_test"] = result
    update_evolution_proposal(proposal_id, user_id, "sandbox_passed" if result["passed"] else "sandbox_failed", body)
    return result


def approve_candidate_writing(user_id: str, proposal_id: int, approve: bool) -> dict:
    proposal = get_evolution_proposal(proposal_id, user_id)
    if not proposal:
        raise RuntimeError("Improvement proposal not found.")
    status = "approved_for_sandbox" if approve else "rejected"
    update_evolution_proposal(proposal_id, user_id, status, proposal["body"])
    return {"ok": True, "status": status}


def approve_deployment(user_id: str, proposal_id: int, approve: bool) -> dict:
    proposal = get_evolution_proposal(proposal_id, user_id)
    if not proposal:
        raise RuntimeError("Improvement proposal not found.")
    if approve and proposal["status"] != "sandbox_passed":
        raise RuntimeError("Deployment cannot be approved until the sandbox tests pass.")
    status = "deployment_approved" if approve else "deployment_rejected"
    body = proposal["body"]
    body["deployment_note"] = "Owner approved deployment. Merlin may publish only through the configured GitHub publisher after sandbox tests pass." if approve else "Owner rejected deployment."
    update_evolution_proposal(proposal_id, user_id, status, body)
    return {"ok": True, "status": status, "requires_source_control_connector": bool(approve)}
