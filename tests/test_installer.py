from __future__ import annotations

from pathlib import Path

from susanoo.adapters.claude_code.installer import (
    apply_install,
    apply_uninstall,
    plan_install,
    plan_uninstall,
    skill_template,
)
from tests.conftest import write

SKILL = ".claude/skills/susanoo/SKILL.md"


def install(root: Path, **kwargs) -> dict[str, str]:
    actions = plan_install(root, **kwargs)
    apply_install(root, actions, with_agents=kwargs.get("with_agents", True))
    return {a.path: a.action for a in actions}


def test_fresh_install_and_idempotence(tmp_path: Path):
    first = install(tmp_path)
    assert set(first.values()) == {"create"}
    assert (tmp_path / SKILL).read_text(encoding="utf-8") == skill_template()
    assert (tmp_path / ".susanoo" / "install.json").is_file()
    assert set(install(tmp_path).values()) == {"unchanged"}


def test_existing_user_files_are_never_overwritten(tmp_path: Path):
    write(tmp_path, SKILL, "my own skill")
    write(tmp_path, ".claude/settings.json", '{"permissions": {}}')
    write(tmp_path, "CLAUDE.md", "project notes")
    actions = install(tmp_path)
    assert actions[SKILL] == "conflict"
    assert (tmp_path / SKILL).read_text(encoding="utf-8") == "my own skill"
    assert (tmp_path / ".claude/settings.json").read_text(encoding="utf-8") == '{"permissions": {}}'
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "project notes"


def test_force_writes_backup_first(tmp_path: Path):
    write(tmp_path, SKILL, "my own skill")
    actions = plan_install(tmp_path, force=True)
    apply_install(tmp_path, actions)
    backups = list((tmp_path / ".claude/skills/susanoo").glob("SKILL.md.susanoo-backup-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "my own skill"
    assert (tmp_path / SKILL).read_text(encoding="utf-8") == skill_template()


def test_unmodified_previous_install_is_updated(tmp_path: Path):
    install(tmp_path)
    # Simulate an older Susanoo version: rewrite the file and its manifest hash.
    import json

    from susanoo.adapters.claude_code.installer import sha256

    old = "---\nname: susanoo\ndescription: old\n---\nold body\n"
    (tmp_path / SKILL).write_text(old, encoding="utf-8")
    manifest_path = tmp_path / ".susanoo" / "install.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][SKILL] = sha256(old)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert install(tmp_path)[SKILL] == "update"
    assert (tmp_path / SKILL).read_text(encoding="utf-8") == skill_template()


def test_dry_run_plan_writes_nothing(tmp_path: Path):
    plan_install(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_no_agents_option(tmp_path: Path):
    actions = install(tmp_path, with_agents=False)
    assert not any("agents" in p for p in actions)


def test_uninstall_removes_only_unmodified_files(tmp_path: Path):
    install(tmp_path)
    agent = tmp_path / ".claude/agents/susanoo-reviewer.md"
    agent.write_text(agent.read_text(encoding="utf-8") + "\nlocal tweak\n", encoding="utf-8")
    write(tmp_path, ".susanoo/records/routes.jsonl", "{}\n")
    write(tmp_path, ".claude/agents/other.md", "unrelated")
    actions = {a.path: a.action for a in plan_uninstall(tmp_path)}
    apply_uninstall(tmp_path, plan_uninstall(tmp_path))
    assert actions[SKILL] == "remove"
    assert actions[".claude/agents/susanoo-reviewer.md"] == "keep-modified"
    assert not (tmp_path / ".claude/skills").exists()
    assert agent.exists()
    assert (tmp_path / ".claude/agents/other.md").exists()
    assert (tmp_path / ".susanoo/records/routes.jsonl").exists()
