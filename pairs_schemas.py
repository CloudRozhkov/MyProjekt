from pydantic import BaseModel, Field
from typing import List, Optional, Literal

PairStatus = Literal["idle", "active", "stopped", "error"]

RuntimeStatus = Literal["idle", "active_cycle", "waiting_close", "error"]

class PairRuntimeResponse(BaseModel):
    pair_id: int
    runtime_status: RuntimeStatus

    # --- Базовые runtime-поля, которые уже использует UI ---
    position_qty: Optional[str] = None
    avg_price: Optional[str] = None
    open_orders: int = 0
    last_event: Optional[str] = None

    # --- Расширение v2: состояние цикла ---
    is_running: bool = False
    auto_cycle: bool = False
    stop_cycle_requested: bool = False
    oco_enabled: bool = True

    # --- Позиция / сделка ---
    session_position_qty: Optional[str] = None
    avg_entry_price: Optional[str] = None

    # --- OCO / trailing ---
    oco_active: bool = False
    oco_step_index: int = 0
    last_oco_order_list_id: Optional[int] = None
    force_trailing_refresh: bool = False

    # --- Engine state / диагностика ---
    grid_in_progress: bool = False
    oco_busy: bool = False

    # --- Тайминги / lifecycle ---
    grid_started_at_ms: Optional[int] = None
    oco_gate_since_ts: Optional[int] = None
    last_oco_set_at_ms: Optional[int] = None
    updated_at_ms: Optional[int] = None

    # --- Capability flags для UI ---
    can_start_once: bool = False
    can_start_auto: bool = False
    can_stop: bool = False
    can_delete: bool = False
    can_add_order: bool = False
    can_refresh_trailing: bool = False

    # --- Ошибки ---
    last_error: Optional[str] = None

class PairStrategyConfig(BaseModel):
    # strictly parity with GUI logic
    strategy: str = "rav_grid_v1"
    base_asset: str
    quote_asset: str = "USDC"

    qty_start_base: str
    layers_pct: List[str]
    martingale_pct: str

    tp_pct: str
    sl_pct: str
    oco_trigger_pct: str
    oco_step_pct: str
    oco_enabled: bool = True

    add_order_pct: str = "3.0"
    auto_cycle: bool = False

class PairCreateRequest(BaseModel):
    base_asset: str = Field(..., examples=["BTC"])
    config: PairStrategyConfig

class PairUpdateRequest(BaseModel):
    # allow partial update of config + flags
    config: Optional[PairStrategyConfig] = None
    is_enabled: Optional[bool] = None
    status: Optional[PairStatus] = None  # обычно лучше не давать менять руками, но полезно для админа

class PairResponse(BaseModel):
    id: int
    owner_email: str
    symbol: str
    base_asset: str
    quote_asset: str
    strategy: str
    status: PairStatus
    is_enabled: bool
    config: PairStrategyConfig
    last_error: Optional[str] = None
