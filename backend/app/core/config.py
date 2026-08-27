"""全量配置 — 对应 PLAN.md §10"""

import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "harness"
    mysql_password: str = "harness_dev"
    mysql_database: str = "harness"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Security
    encryption_key: str = "8XkJ2pLq9vN3mR4tY7wZ0aBcDeFgHiJkLmNoPqRsTuVw="

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

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
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def model_post_init(self, __context):
        # 兼容 OPENAI_* 环境变量 (不经 pydantic 校验)
        if not self.openai_api_key:
            self.openai_api_key = os.getenv("OPENAI_API_KEY")
        if not self.openai_base_url:
            self.openai_base_url = os.getenv("OPENAI_BASE_URL")
        if not self.openai_model:
            self.openai_model = os.getenv("OPENAI_MODEL") or os.getenv("MODEL_NAME")

    @property
    def mysql_dsn(self) -> str:
        return (
            f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )

    @property
    def mysql_dsn_asyncmy(self) -> str:
        return (
            f"mysql+asyncmy://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )


settings = Settings()
