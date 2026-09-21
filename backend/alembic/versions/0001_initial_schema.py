"""Initial schema with all 22 system tables

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-19 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. runtime_config
    op.create_table(
        "runtime_config",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 2. config_history
    op.create_table(
        "config_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("old_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changed_by", sa.String(length=64), nullable=False),
    )
    op.create_index("ix_config_history_key", "config_history", ["key"])

    # 3. universe
    op.create_table(
        "universe",
        sa.Column("signal_symbol", sa.String(length=16), primary_key=True),
        sa.Column("execution_symbol", sa.String(length=16), nullable=False),
        sa.Column("cluster", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 4. daily_bars
    op.create_table(
        "daily_bars",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("high", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("low", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("close", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("volume", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("adjusted", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("feed", sa.String(length=32), nullable=False, server_default="sip_delayed"),
        sa.UniqueConstraint("symbol", "date", "feed", name="uq_daily_bars_symbol_date_feed"),
    )
    op.create_index("ix_daily_bars_symbol", "daily_bars", ["symbol"])
    op.create_index("ix_daily_bars_date", "daily_bars", ["date"])

    # 5. regime_snapshots
    op.create_table(
        "regime_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("regime", sa.String(length=32), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
    )
    op.create_index("ix_regime_snapshots_ts", "regime_snapshots", ["ts"])

    # 6. events
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=True),
        sa.Column("ts_et", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_events_type", "events", ["type"])
    op.create_index("ix_events_symbol", "events", ["symbol"])

    # 7. news_items
    op.create_table(
        "news_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("url", sa.String(length=512), unique=True, nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finbert_label", sa.String(length=16), nullable=True),
        sa.Column("finbert_score", sa.Float(), nullable=True),
        sa.Column("whitelisted", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_news_items_symbol", "news_items", ["symbol"])
    op.create_index("ix_news_items_published_at", "news_items", ["published_at"])

    # 8. news_features
    op.create_table(
        "news_features",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("window", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("sentiment_mean", sa.Float(), nullable=False),
        sa.Column("sentiment_min", sa.Float(), nullable=False),
        sa.Column("negative_share", sa.Float(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("top_topics", sa.JSON(), nullable=False),
    )
    op.create_index("ix_news_features_symbol", "news_features", ["symbol"])
    op.create_index("ix_news_features_ts", "news_features", ["ts"])

    # 9. signals
    op.create_table(
        "signals",
        sa.Column("signal_id", sa.String(length=64), primary_key=True),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("variant", sa.String(length=32), nullable=False, server_default="A"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_signals_strategy_id", "signals", ["strategy_id"])
    op.create_index("ix_signals_symbol", "signals", ["symbol"])
    op.create_index("ix_signals_created_at", "signals", ["created_at"])

    # 10. llm_calls
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("response_raw", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])

    # 11. veto_decisions
    op.create_table(
        "veto_decisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.String(length=64), nullable=False),
        sa.Column("variant", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("size_multiplier", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("reason_code", sa.String(length=32), nullable=False),
        sa.Column("llm_call_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_veto_decisions_signal_id", "veto_decisions", ["signal_id"])

    # 12. risk_decisions
    op.create_table(
        "risk_decisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.String(length=64), nullable=False),
        sa.Column("variant", sa.String(length=32), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=False),
        sa.Column("sizing", sa.JSON(), nullable=True),
        sa.Column("portfolio_state", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_risk_decisions_signal_id", "risk_decisions", ["signal_id"])

    # 13. orders
    op.create_table(
        "orders",
        sa.Column("client_order_id", sa.String(length=64), primary_key=True),
        sa.Column("broker_order_id", sa.String(length=64), nullable=True),
        sa.Column("owner", sa.String(length=16), nullable=False),
        sa.Column("signal_id", sa.String(length=64), nullable=True),
        sa.Column("signal_symbol", sa.String(length=16), nullable=False),
        sa.Column("execution_symbol", sa.String(length=16), nullable=False),
        sa.Column("leg", sa.String(length=16), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("tif", sa.String(length=16), nullable=False),
        sa.Column("qty", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("notional", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("limit_price", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("stop_price", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "filled_qty", sa.Numeric(precision=12, scale=4), nullable=False, server_default="0"
        ),
        sa.Column("avg_fill_price", sa.Numeric(precision=12, scale=4), nullable=True),
    )
    op.create_index("ix_orders_broker_order_id", "orders", ["broker_order_id"])
    op.create_index("ix_orders_signal_id", "orders", ["signal_id"])
    op.create_index("ix_orders_status", "orders", ["status"])

    # 14. position_meta
    op.create_table(
        "position_meta",
        sa.Column("symbol", sa.String(length=16), primary_key=True),
        sa.Column("execution_symbol", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=16), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=True),
        sa.Column("entry_price", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("stop_price", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("tp_price", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("max_holding", sa.String(length=32), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("path", sa.String(length=32), nullable=False),
        sa.Column("exit_at_close", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("unmanaged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    # 15. trades
    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=16), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=True),
        sa.Column("entry_price", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("exit_price", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("qty", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("pnl", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("pnl_r", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("fees_est", sa.Numeric(precision=8, scale=2), nullable=False, server_default="0"),
        sa.Column(
            "slippage_est", sa.Numeric(precision=8, scale=2), nullable=False, server_default="0"
        ),
        sa.Column("exit_reason", sa.String(length=64), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trades_symbol", "trades", ["symbol"])
    op.create_index("ix_trades_closed_at", "trades", ["closed_at"])

    # 16. equity_snapshots
    op.create_table(
        "equity_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("cash", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("bot_exposure", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("manual_exposure", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("pnl_day_bot", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("pnl_day_manual", sa.Numeric(precision=12, scale=2), nullable=False),
    )
    op.create_index("ix_equity_snapshots_ts", "equity_snapshots", ["ts"])

    # 17. intents
    op.create_table(
        "intents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_intents_status", "intents", ["status"])
    op.create_index("ix_intents_created_at", "intents", ["created_at"])

    # 18. approvals
    op.create_table(
        "approvals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_approvals_expires_at", "approvals", ["expires_at"])

    # 19. alerts
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False, server_default="email"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_alerts_sent_at", "alerts", ["sent_at"])

    # 20. reports
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_reports_period_start", "reports", ["period_start"])

    # 21. quota_usage
    op.create_table(
        "quota_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("minute_bucket", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_quota_usage_provider", "quota_usage", ["provider"])
    op.create_index("ix_quota_usage_date", "quota_usage", ["date"])
    op.create_index("ix_quota_usage_minute", "quota_usage", ["minute_bucket"])

    # 22. audit_log
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    tables = [
        "audit_log",
        "quota_usage",
        "reports",
        "alerts",
        "approvals",
        "intents",
        "equity_snapshots",
        "trades",
        "position_meta",
        "orders",
        "risk_decisions",
        "veto_decisions",
        "llm_calls",
        "signals",
        "news_features",
        "news_items",
        "events",
        "regime_snapshots",
        "daily_bars",
        "universe",
        "config_history",
        "runtime_config",
    ]
    for table in tables:
        op.drop_table(table)
