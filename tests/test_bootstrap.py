from _project_bootstrap import project_venv_python
from offline_rag.preflight import check_local_model


def test_project_venv_python_finds_venv(tmp_path):
    executable = tmp_path / "venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.touch()

    assert project_venv_python(tmp_path) == executable


def test_direct_local_model_directory_passes_preflight(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.safetensors").touch()

    check = check_local_model(str(model), "Test model")

    assert check.ok
    assert check.name == "Test model"


def test_incomplete_local_model_directory_fails_preflight(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")

    check = check_local_model(str(model), "Test model")

    assert not check.ok
    assert "incomplete" in check.detail
