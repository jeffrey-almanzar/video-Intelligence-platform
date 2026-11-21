from pathlib import Path

import pytest

from vip_ai import config


def test_repo_env_file_is_none_outside_the_monorepo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate the container layout (/app/src/vip_ai/config.py): no workspace marker above.
    fake = tmp_path / "app" / "src" / "vip_ai" / "config.py"
    fake.parent.mkdir(parents=True)
    fake.touch()
    monkeypatch.setattr(config, "__file__", str(fake))
    assert config._repo_env_file() is None


def test_repo_env_file_found_via_workspace_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pnpm-workspace.yaml").touch()
    (tmp_path / ".env").write_text("X=1\n")
    fake = tmp_path / "services" / "ai" / "src" / "vip_ai" / "config.py"
    fake.parent.mkdir(parents=True)
    fake.touch()
    monkeypatch.setattr(config, "__file__", str(fake))
    assert config._repo_env_file() == tmp_path / ".env"
