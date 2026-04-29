import os
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from spotbot_trader import PairConfig, SpotBotTrader, BinanceGatewayAdapter


app = FastAPI(title="spotbot-trader", version="0.2.0")

TRADER_GATEWAY_URL = os.getenv("BINANCE_GATEWAY_URL", "http://binance-gateway:8001").rstrip("/")


# ----------------------------
# Request models
# ----------------------------

class PairConfigPayload(BaseModel):
    base_asset: str = Field(..., min_length=2, max_length=20)
    quote_asset: str = Field("USDC", min_length=2, max_length=20)

    qty_start_base: str = Field(..., min_length=1)
    martingale_pct: str = Field("100", min_length=1)

    layers_pct: list[str] = Field(default_factory=list)

    tp_pct: str = Field("3.0", min_length=1)
    sl_pct: str = Field("0.5", min_length=1)
    oco_trigger_pct: str = Field("1.0", min_length=1)
    oco_step_pct: str = Field("1.0", min_length=1)

    add_order_pct: str = Field("3.0", min_length=1)

    poll_sec: str = Field("1.2", min_length=1)
    soft_cutoff_ms: int = 5000
    grid_start_delay_ms: int = 5000

    oco_enabled: bool = True
    auto_cycle: bool = False
    stop_cycle_requested: bool = False

    max_open_orders: int = 50


class StartPairRequest(BaseModel):
    pair_id: int
    owner_email: str = Field(..., min_length=3, max_length=255)
    api_key: str = Field(..., min_length=5)
    api_secret: str = Field(..., min_length=8)
    config: PairConfigPayload

class StopPairRequest(BaseModel):
    pair_id: int

class AddOrderRequest(BaseModel):
    pair_id: int


# ----------------------------
# Registry
# ----------------------------

@dataclass
class TraderHandle:
    pair_id: int
    owner_email: str
    created_at_ms: int
    updated_at_ms: int
    cfg: PairConfig
    exchange: BinanceGatewayAdapter
    trader: SpotBotTrader
    last_error: Optional[str] = None


_registry_lock = threading.Lock()
_registry: Dict[int, TraderHandle] = {}


# ----------------------------
# Helpers
# ----------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)


def _to_decimal_str(value: str, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid decimal for {field_name}: {value}")


def _build_pair_config(payload: PairConfigPayload) -> PairConfig:
    return PairConfig(
        base_asset=payload.base_asset.strip().upper(),
        quote_asset=payload.quote_asset.strip().upper(),
        qty_start_base=_to_decimal_str(payload.qty_start_base, "qty_start_base"),
        martingale_pct=_to_decimal_str(payload.martingale_pct, "martingale_pct"),
        layers_pct=[_to_decimal_str(x, "layers_pct") for x in payload.layers_pct],
        tp_pct=_to_decimal_str(payload.tp_pct, "tp_pct"),
        sl_pct=_to_decimal_str(payload.sl_pct, "sl_pct"),
        oco_trigger_pct=_to_decimal_str(payload.oco_trigger_pct, "oco_trigger_pct"),
        oco_step_pct=_to_decimal_str(payload.oco_step_pct, "oco_step_pct"),
        add_order_pct=_to_decimal_str(payload.add_order_pct, "add_order_pct"),
        poll_sec=_to_decimal_str(payload.poll_sec, "poll_sec"),
        soft_cutoff_ms=int(payload.soft_cutoff_ms),
        grid_start_delay_ms=int(payload.grid_start_delay_ms),
        oco_enabled=bool(payload.oco_enabled),
        auto_cycle=bool(payload.auto_cycle),
        stop_cycle_requested=bool(payload.stop_cycle_requested),
        max_open_orders=int(payload.max_open_orders),
    )


def _make_log_fn(pair_id: int):
    def _log(msg: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"{ts} [pair:{pair_id}] {msg}", flush=True)
    return _log


def _handle_to_dict(handle: TraderHandle) -> Dict[str, Any]:
    thread_alive = bool(handle.trader._thread and handle.trader._thread.is_alive())
    return {
        "pair_id": handle.pair_id,
        "owner_email": handle.owner_email,
        "symbol": handle.trader.symbol,
        "created_at_ms": handle.created_at_ms,
        "updated_at_ms": handle.updated_at_ms,
        "thread_alive": thread_alive,
        "last_error": handle.last_error,
        "config": handle.trader.export_config(),
    }

def _build_runtime_response(handle: TraderHandle) -> Dict[str, Any]:
    trader = handle.trader
    thread_alive = bool(trader._thread and trader._thread.is_alive())

    if thread_alive:
        if trader.cfg.stop_cycle_requested:
            runtime_status = "waiting_close"
        elif trader.grid_started_at_ms:
            runtime_status = "active_cycle"
        else:
            runtime_status = "starting"
    else:
        runtime_status = "idle"

    handle.updated_at_ms = _now_ms()

    return {
        "pair_id": handle.pair_id,
        "owner_email": handle.owner_email,
        "symbol": trader.symbol,
        "thread_alive": thread_alive,
        "runtime_status": runtime_status,
        "is_running": thread_alive,
        "auto_cycle": bool(trader.cfg.auto_cycle),
        "stop_cycle_requested": bool(trader.cfg.stop_cycle_requested),
        "oco_enabled": bool(trader.cfg.oco_enabled),
        "session_position_qty": None,
        "avg_entry_price": None,
        "open_orders": None,
        "oco_active": bool(trader.oco_active),
        "oco_step_index": int(trader.oco_step_index),
        "last_oco_order_list_id": trader.last_oco_order_list_id,
        "force_trailing_refresh": bool(trader._force_trailing_refresh),
        "grid_in_progress": bool(trader._grid_in_progress),
        "oco_busy": bool(trader._oco_busy),
        "grid_started_at_ms": trader.grid_started_at_ms,
        "oco_gate_since_ts": trader.oco_gate_since_ts,
        "last_oco_set_at_ms": trader._last_oco_set_at_ms,
        "updated_at_ms": handle.updated_at_ms,
        "last_error": handle.last_error,
    }


# ----------------------------
# Routes
# ----------------------------

@app.get("/health")
def health():
    with _registry_lock:
        active_pairs = list(_registry.keys())

    return {
        "status": "ok",
        "service": "trader",
        "engine_import_ok": True,
        "pair_config_class": PairConfig.__name__,
        "trader_class": SpotBotTrader.__name__,
        "gateway_url": TRADER_GATEWAY_URL,
        "active_pairs_count": len(active_pairs),
        "active_pair_ids": active_pairs,
    }


@app.post("/start")
def start_pair(req: StartPairRequest):
    with _registry_lock:
        existing = _registry.get(req.pair_id)
        if existing:
            thread_alive = bool(existing.trader._thread and existing.trader._thread.is_alive())
            if thread_alive:
                return {
                    "ok": True,
                    "message": "pair already running",
                    "pair": _handle_to_dict(existing),
                }
            else:
                # stale handle cleanup
                _registry.pop(req.pair_id, None)

    cfg = _build_pair_config(req.config)

    exchange = BinanceGatewayAdapter(
        gateway_url=TRADER_GATEWAY_URL,
        api_key=req.api_key,
        api_secret=req.api_secret,
    )

    trader = SpotBotTrader(
        cfg=cfg,
        exchange=exchange,
        log=_make_log_fn(req.pair_id),
    )

    # very light preflight:
    # check symbol filters before background thread starts doing work
    try:
        exchange.get_exchange_info(f"{cfg.base_asset}{cfg.quote_asset}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Trader preflight failed: {type(e).__name__}: {e}")

    handle = TraderHandle(
        pair_id=req.pair_id,
        owner_email=req.owner_email,
        created_at_ms=_now_ms(),
        updated_at_ms=_now_ms(),
        cfg=cfg,
        exchange=exchange,
        trader=trader,
        last_error=None,
    )

    try:
        trader.start()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Trader start failed: {type(e).__name__}: {e}")

    with _registry_lock:
        _registry[req.pair_id] = handle

    return {
        "ok": True,
        "message": "pair started",
        "pair": _handle_to_dict(handle),
    }

@app.get("/runtime/{pair_id}")
def get_runtime(pair_id: int):
    with _registry_lock:
        handle = _registry.get(pair_id)

    if not handle:
        raise HTTPException(status_code=404, detail=f"Runtime not found for pair_id={pair_id}")

    return {
        "ok": True,
        "runtime": _build_runtime_response(handle),
    }

@app.post("/stop")
def stop_pair(req: StopPairRequest):
    with _registry_lock:
        handle = _registry.get(req.pair_id)

        if not handle:
            raise HTTPException(status_code=404, detail=f"Runtime not found for pair_id={req.pair_id}")

        handle.trader.cfg.stop_cycle_requested = True
        handle.updated_at_ms = _now_ms()

        runtime = _build_runtime_response(handle)

    return {
        "ok": True,
        "message": "soft stop requested",
        "runtime": runtime,
    }

@app.post("/add-order")
def add_order(req: AddOrderRequest):
    with _registry_lock:
        handle = _registry.get(req.pair_id)

    if not handle:
        raise HTTPException(status_code=404, detail=f"Runtime not found for pair_id={req.pair_id}")

    thread_alive = bool(handle.trader._thread and handle.trader._thread.is_alive())
    if not thread_alive:
        raise HTTPException(status_code=409, detail=f"Trader thread is not alive for pair_id={req.pair_id}")

    try:
        handle.trader.add_one_order()
        handle.updated_at_ms = _now_ms()
    except Exception as e:
        handle.last_error = f"add_order failed: {type(e).__name__}: {e}"
        handle.updated_at_ms = _now_ms()
        raise HTTPException(status_code=400, detail=handle.last_error)

    return {
        "ok": True,
        "message": "add-order requested",
        "runtime": _build_runtime_response(handle),
    }
