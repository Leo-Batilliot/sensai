from pathlib import Path

import pytest
from pydantic import ValidationError

from sensai.core.config import Config, ConfigError, load_config

EXAMPLE = Path(__file__).resolve().parents[2] / "config.example.toml"


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run each test in an empty directory so a developer's config.toml never leaks in."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# --- sources and priority -------------------------------------------------------------


def test_defaults_without_any_file() -> None:
    assert load_config(environ={}) == Config()


def test_example_file_matches_defaults() -> None:
    # config.example.toml documents the defaults: both must change together.
    assert load_config(EXAMPLE, environ={}) == Config()


def test_default_config_toml_is_picked_up(isolated_cwd: Path) -> None:
    write(isolated_cwd / "config.toml", '[ollama]\nmodel = "from-file"\n')
    assert load_config(environ={}).ollama.model == "from-file"


def test_file_overrides_defaults_key_by_key(tmp_path: Path) -> None:
    path = write(tmp_path / "c.toml", '[ollama]\nmodel = "from-file"\n')
    config = load_config(path, environ={})
    assert config.ollama.model == "from-file"
    assert config.ollama.num_ctx == 16384  # untouched keys keep their default


def test_env_overrides_file(tmp_path: Path) -> None:
    path = write(tmp_path / "c.toml", '[ollama]\nmodel = "from-file"\n')
    config = load_config(path, environ={"SENSAI_OLLAMA_MODEL": "from-env"})
    assert config.ollama.model == "from-env"


def test_cli_overrides_env(tmp_path: Path) -> None:
    path = write(tmp_path / "c.toml", '[ollama]\nmodel = "from-file"\n')
    config = load_config(
        path,
        overrides={"ollama.model": "from-cli"},
        environ={"SENSAI_OLLAMA_MODEL": "from-env"},
    )
    assert config.ollama.model == "from-cli"


def test_none_override_is_skipped() -> None:
    config = load_config(
        overrides={"ollama.model": None, "ollama.host": None},
        environ={"SENSAI_OLLAMA_MODEL": "from-env"},
    )
    assert config.ollama.model == "from-env"
    assert config.ollama.host == "http://localhost:11434"


def test_config_path_from_env(tmp_path: Path) -> None:
    path = write(tmp_path / "c.toml", '[ollama]\nmodel = "from-env-file"\n')
    config = load_config(environ={"SENSAI_CONFIG": str(path)})
    assert config.ollama.model == "from-env-file"


def test_explicit_path_beats_config_path_from_env(tmp_path: Path) -> None:
    cli = write(tmp_path / "cli.toml", '[ollama]\nmodel = "cli-file"\n')
    env = write(tmp_path / "env.toml", '[ollama]\nmodel = "env-file"\n')
    config = load_config(cli, environ={"SENSAI_CONFIG": str(env)})
    assert config.ollama.model == "cli-file"


def test_ollama_own_host_variable_is_ignored() -> None:
    config = load_config(environ={"OLLAMA_HOST": "0.0.0.0:11434"})
    assert config.ollama.host == "http://localhost:11434"


# --- environment value parsing ---------------------------------------------------------


def test_env_values_are_coerced() -> None:
    config = load_config(
        environ={
            "SENSAI_OLLAMA_NUM_CTX": "4096",
            "SENSAI_AGENT_TEMPERATURE": "0.2",
            "SENSAI_EVAL_JUDGE_ENABLED": "false",
            "SENSAI_DATABASE_MEMORY_PATH": "/tmp/m.db",
        }
    )
    assert config.ollama.num_ctx == 4096
    assert config.agent.temperature == 0.2
    assert config.eval.judge_enabled is False
    assert config.database.memory_path == Path("/tmp/m.db")


def test_env_lists_are_comma_separated() -> None:
    config = load_config(environ={"SENSAI_HITL_DENY": "a, b,,c "})
    assert config.hitl.deny == ("a", "b", "c")


def test_invalid_env_value_is_reported() -> None:
    with pytest.raises(ConfigError, match=r"ollama\.num_ctx"):
        load_config(environ={"SENSAI_OLLAMA_NUM_CTX": "beaucoup"})


# --- file errors -------------------------------------------------------------------------


def test_missing_explicit_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="introuvable"):
        load_config(tmp_path / "absent.toml", environ={})


def test_missing_file_from_env_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="introuvable"):
        load_config(environ={"SENSAI_CONFIG": str(tmp_path / "absent.toml")})


def test_invalid_toml_names_the_file(tmp_path: Path) -> None:
    path = write(tmp_path / "broken.toml", "[ollama\nmodel = 1\n")
    with pytest.raises(ConfigError, match=r"broken\.toml.*TOML invalide"):
        load_config(path, environ={})


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "c.toml", '[ollama]\nmodle = "typo"\n')
    with pytest.raises(ConfigError, match=r"ollama\.modle"):
        load_config(path, environ={})


def test_unknown_override_is_rejected() -> None:
    with pytest.raises(ConfigError, match=r"ollama\.modle"):
        load_config(overrides={"ollama.modle": "typo"}, environ={})


# --- validation --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ollama.num_ctx", 1024),
        ("ollama.num_ctx", 65536),
        ("ollama.timeout_s", 0),
        ("ollama.model", ""),
        ("ollama.host", "localhost:11434"),
        ("agent.temperature", 1.5),
        ("agent.max_tool_iterations", 0),
        ("database.row_limit", 0),
        ("hitl.confirmation_timeout_s", -1),
        ("eval.numeric_tolerance", 1),
        ("web.port", 70000),
    ],
)
def test_out_of_bounds_values_are_rejected(key: str, value: object) -> None:
    with pytest.raises(ConfigError, match=key.replace(".", r"\.")):
        load_config(overrides={key: value}, environ={})


def test_host_trailing_slash_is_removed() -> None:
    config = load_config(overrides={"ollama.host": "http://gpu:11434/"}, environ={})
    assert config.ollama.host == "http://gpu:11434"


def test_judge_model_falls_back_to_agent_model() -> None:
    assert Config().ollama.effective_judge_model == "qwen3:8b"
    config = load_config(overrides={"ollama.judge_model": "llama3.1:8b"}, environ={})
    assert config.ollama.effective_judge_model == "llama3.1:8b"


# --- MCP servers ------------------------------------------------------------------------


def test_file_servers_replace_default_servers(tmp_path: Path) -> None:
    path = write(
        tmp_path / "c.toml",
        '[mcp.servers.filesystem]\ncommand = "npx"\nargs = ["-y", "server-fs", "./reports"]\n',
    )
    servers = load_config(path, environ={}).mcp.servers
    assert set(servers) == {"filesystem"}
    assert servers["filesystem"].args == ("-y", "server-fs", "./reports")


@pytest.mark.parametrize("name", ["time__v2", "my server", "_time", "time_"])
def test_invalid_server_name_is_rejected(tmp_path: Path, name: str) -> None:
    path = write(tmp_path / "c.toml", f'[mcp.servers."{name}"]\ncommand = "x"\n')
    with pytest.raises(ConfigError, match="nom de serveur invalide"):
        load_config(path, environ={})


def test_server_without_command_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "c.toml", "[mcp.servers.time]\nargs = []\n")
    with pytest.raises(ConfigError, match=r"mcp\.servers\.time\.command"):
        load_config(path, environ={})


# --- immutability ----------------------------------------------------------------------


def test_config_is_frozen() -> None:
    config = Config()
    with pytest.raises(ValidationError):
        config.ollama.model = "other"  # type: ignore[misc]


def test_model_copy_derives_a_new_config() -> None:
    config = Config()
    session = config.model_copy(
        update={"agent": config.agent.model_copy(update={"temperature": 0.2})}
    )
    assert session.agent.temperature == 0.2
    assert config.agent.temperature == 0.1
