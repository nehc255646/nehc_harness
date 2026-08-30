"""全量配置"""

from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# 让 {PROVIDER_SLUG}_API_KEY 等未声明变量也能从 .env 读到
_root = Path(__file__).resolve().parents[3]
load_dotenv(_root / ".env", override=False)
load_dotenv(_root / "backend" / ".env", override=False)


class Settings(BaseSettings):
    # Database — 凭据由 .env 提供，代码内不留默认密码
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "harness"
    mysql_password: str = ""
    mysql_database: str = "harness"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Security — 必须由 .env 提供（Fernet 生成命令见 .env.example），留空时启动告警
    encryption_key: str = ""

    # Server — 默认本机回环；对外暴露需显式 HOST=0.0.0.0
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    llm_timeout: int = 180

    # Agent
    workdir: str = "./workspace"
    max_rounds: int = 50
    window_n: int = 20
    summary_token_ratio: float = 0.65
    max_tool_result_tokens: int = 8192
    retry_count: int = 1
    subagent_max_concurrency: int = 3
    max_workers_per_turn: int = 2
    worker_timeout: int = 600
    approval_timeout: int = 120
    shell_timeout: int = 300
    heartbeat_interval_s: int = 30
    readonly_need_approval: bool = False
    blacklist_enabled: bool = True
    allow_rules_file: str = "./allow_rules.yaml"

    # LLM (M1 临时调试，M3 后由 DB 管理)
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def mysql_dsn(self) -> str:
        password = quote_plus(self.mysql_password or "")
        return (
            f"mysql+aiomysql://{self.mysql_user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )


settings = Settings()
