"""仅从显式项目 .env / 服务端环境读取模型配置，不产生网络请求。"""

from io import StringIO
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from typing import Literal


_OLD_HOSTS = frozenset({
    "dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com", "dashscope-us.aliyuncs.com",
})
_WORKSPACE_HOST = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\."
    r"(?:cn-beijing|ap-southeast-1|ap-northeast-1)\.maas\.aliyuncs\.com"
)
_ENV_FILE_MAX_BYTES = 65536


class LLMSettings(BaseModel):
    """服务端配置；密钥不进入 repr、JSON 或 public_config。"""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    provider: Literal["qwen"]
    base_url: str = Field(min_length=1, max_length=400)
    model: str = Field(min_length=1, max_length=128)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), exclude=True, repr=False)
    max_rounds: int = Field(default=6, ge=1, le=20)
    max_tool_calls: int = Field(default=8, ge=1, le=32)
    request_timeout_s: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    total_timeout_s: float = Field(default=90, gt=0, le=300, allow_inf_nan=False)
    max_retries: int = Field(default=1, ge=0, le=3)
    max_corrections: int = Field(default=1, ge=0, le=2)
    max_history_turns: int = Field(default=4, ge=0, le=20)
    max_output_tokens: int = Field(default=2000, ge=1, le=8192)
    max_context_chars: int = Field(default=30000, ge=1, le=100000)
    max_tool_result_chars: int = Field(default=12000, ge=1, le=30000)
    max_events: int = Field(default=20, ge=1, le=100)

    @field_validator("base_url")
    @classmethod
    def safe_qwen_url(cls, value: str) -> str:
        # 不根据消息、模型返回值或重定向选择目的地址。
        if value != value.strip() or any(c.isspace() for c in value):
            raise ValueError("服务地址格式无效")
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("服务地址端口无效") from None
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https" or parsed.username is not None
            or parsed.password is not None or port not in (None, 443)
            or "?" in value or "#" in value or "\\" in value
            or parsed.path.rstrip("/") != "/compatible-mode/v1"
            or (host not in _OLD_HOSTS and _WORKSPACE_HOST.fullmatch(host) is None)
        ):
            raise ValueError("只支持已核实的百炼 HTTPS 兼容接口地址")
        return value.rstrip("/")

    @field_validator("model")
    @classmethod
    def model_identifier(cls, value: str) -> str:
        if value != value.strip() or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError("模型标识无效")
        return value

    @field_validator("api_key")
    @classmethod
    def key_format(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) > 4096 or any(c.isspace() or ord(c) < 32 for c in secret):
            raise ValueError("密钥格式无效")
        return value

    @property
    def ready(self) -> bool:
        return bool(self.api_key.get_secret_value())

    def public_config(self) -> dict:
        return self.model_dump(mode="json", exclude={"api_key"})


def _read_project_env(project_dir: str | Path) -> tuple[dict[str, str | None], str | None]:
    """只读显式项目文件，限制字节数；返回受控错误，不输出原配置。"""
    env_values: dict[str, str | None] = {}
    try:
        env_path = Path(project_dir) / ".env"
        if env_path.exists():
            with env_path.open("rb") as source:
                raw = source.read(_ENV_FILE_MAX_BYTES + 1)
            if len(raw) > _ENV_FILE_MAX_BYTES:
                return {}, "模型配置不可用：项目 .env 超过 64 KiB。"
            env_values = dotenv_values(stream=StringIO(raw.decode("utf-8-sig")), interpolate=False)
    except (OSError, UnicodeError, ValueError):
        return {}, "模型配置不可用：无法读取项目 .env，请检查文件格式和权限。"
    return env_values, None


def configured_secrets(project_dir: str | Path) -> tuple[str, ...]:
    """供导出脱敏使用，不要求其它LLM字段有效，不表示模型可以调用。

    环境变量优先且空值覆盖项目文件；读取失败时仍可取得显式环境密钥。
    返回值只交给脱敏器，调用方不得显示、序列化或记录。
    """
    if "LLM_API_KEY" in os.environ:
        value = os.environ["LLM_API_KEY"]
    else:
        values, _ = _read_project_env(project_dir)
        value = values.get("LLM_API_KEY")
    return (value,) if isinstance(value, str) and value else ()


def read_settings(project_dir: str | Path) -> tuple[LLMSettings | None, str]:
    """环境变量优先（包括空值）；不查找父目录、不修改进程环境、不展开变量。"""
    env_values, error = _read_project_env(project_dir)
    if error:
        return None, error

    names = {field: "LLM_" + field.upper() for field in LLMSettings.model_fields}
    values = {
        field: os.environ[name] if name in os.environ else env_values[name]
        for field, name in names.items() if name in os.environ or name in env_values
    }
    missing = [names[field] for field in ("provider", "base_url", "model", "api_key")
               if not values.get(field)]
    if missing:
        return None, "模型未配置：缺少 " + "、".join(missing) + "。"
    try:
        settings = LLMSettings.model_validate(values)
    except ValidationError as error:
        # ValidationError 默认含输入值；只使用受控字段名，绝不输出原文。
        invalid = sorted({names[item["loc"][0]] for item in error.errors(include_input=False)
                          if item["loc"] and item["loc"][0] in names})
        return None, "模型配置不可用：请检查 " + "、".join(invalid) + "。"
    return settings, "模型配置已读取；尚未验证远程连接和当前账号的模型权限。"
