"""全量配置 — 对应 PLAN.md §10"""

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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
