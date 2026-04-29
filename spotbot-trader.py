# spotbot-trader.py
# Headless trading engine (no Tkinter) with 1:1 parity to SpotBot-RAV-Trade_GUI_3.py
# IMPORTANT: This file intentionally preserves the original GUI strategy logic and parameter semantics.
#
# Python 3.11+ recommended.

from __future__ import annotations

import os
import time
import json
import threading
from dataclasses import dataclass, asdict, field
from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable, Dict, List, Optional

import httpx


# ----------------------------
# Helpers (Decimal + ticks/steps)
# ----------------------------

def _d(x: Any) -> Decimal:
    if isinstance(x, Decimal):
        return x
    if x is None:
        return Decimal("0")
    return Decimal(str(x))


def q_step(qty: Decimal, step: Decimal) -> Decimal:
    # Quantize quantity to lot step (ROUND_DOWN like in GUI)
    if step <= 0:
        return qty
    return (qty / step).to_integral_value(rounding=ROUND_DOWN) * step


def q_tick(price: Decimal, tick: Decimal) -> Decimal:
    # Quantize price to tick size (ROUND_DOWN like in GUI)
    if tick <= 0:
        return price
    return (price / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def now_ms() -> int:
    return int(time.time() * 1000)


# ----------------------------
# Config (matches GUI inputs)
# ----------------------------

@dataclass
class PairConfig:
    """
    1:1 with Tkinter inputs.

    GUI semantics (IMPORTANT):
      - base_asset is chosen by user, quote asset was fixed to USDC in GUI.
      - qty_start_base: стартовая сумма в BASE (например BTC), НЕ в USDC.
      - layers_pct: "Старт + Шаги (%)" list.
          * first element: 0 => "по рынку", otherwise % down from CURRENT price
          * next elements: each % down from PREVIOUS level
      - martingale_pct: applied to BASE qty each next level: mcoef = 1 + martingale_pct/100
      - OCO:
          tp_pct, sl_pct, oco_trigger_pct, oco_step_pct are provided as percents (e.g. 3.0 means 3%).
          Original GUI computes:
            ratio = current/avg - 1
            if ratio < trigger -> do nothing
            k = floor((ratio - trigger)/step)
            tp_eff = tp + k*step ; sl_eff = sl + k*step
            tp_stop = avg*(1+tp_eff) ; tp_limit = tp_stop*(1-0.0002)
            sl_stop = avg*(1+sl_eff) ; sl_limit = sl_stop*(1-0.001)
          Yes, SL leg is also above avg in original code; we preserve it exactly.
      - add_order_pct: "+ Order": one extra BUY at last_buy_price*(1 - add_order_pct/100),
        qty = last_buy_qty*mcoef (next martingale step), with minQty/MIN_NOTIONAL/balance checks.
      - auto_cycle: after OCO execution, optionally re-place grid after grid_start_delay_ms.
    """

    # Pair
    base_asset: str = "BTC"
    quote_asset: str = "USDC"  # fixed in GUI

    # Grid
    qty_start_base: Decimal = Decimal("0")          # user input; no default in GUI
    martingale_pct: Decimal = Decimal("100")        # GUI default 100 (%)

    layers_pct: List[Decimal] = field(default_factory=list)  # up to 8 entries from GUI

    # OCO params (GUI defaults: TP 3.0, SL 0.5, trigger 1.0, step 1.0)
    tp_pct: Decimal = Decimal("3.0")
    sl_pct: Decimal = Decimal("0.5")
    oco_trigger_pct: Decimal = Decimal("1.0")
    oco_step_pct: Decimal = Decimal("1.0")

    # "+ Order"
    add_order_pct: Decimal = Decimal("3.0")

    # Runtime
    poll_sec: Decimal = Decimal("1.2")
    soft_cutoff_ms: int = 5000
    grid_start_delay_ms: int = 5000

    # Behaviour flags (mirrors GUI)
    oco_enabled: bool = True
    auto_cycle: bool = False
    stop_cycle_requested: bool = False

    # Safety
    max_open_orders: int = 50


# ----------------------------
# Exchange abstraction
# ----------------------------

class ExchangeAdapter:
    """
    Adapter required for exact GUI parity.

    Your binance-gateway must eventually provide these endpoints.
    Until then, missing methods should raise clearly.

    Minimum required for GRID parity:
      - get_exchange_info(symbol)
      - get_price(symbol)
      - get_open_orders(symbol)
      - get_asset_balance(asset)
      - place_limit_buy(symbol, price, qty_base)

    Required for full OCO parity:
      - get_open_oco_orders()
      - cancel_oco_order_list(order_list_id)
      - place_oco_sell(symbol, qty, tp_stop, tp_limit, sl_stop, sl_limit)
      - get_my_trades(symbol, start_time_ms)  (for session avg/qty + last buy)
    """

    def get_exchange_info(self, symbol: str) -> Dict[str, Any]:
        raise NotImplementedError

    def get_price(self, symbol: str) -> Decimal:
        raise NotImplementedError

    def get_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_asset_balance(self, asset: str) -> Dict[str, Any]:
        raise NotImplementedError

    def place_limit_buy(self, symbol: str, price: Decimal, qty_base: Decimal) -> Dict[str, Any]:
        raise NotImplementedError

    # --- OCO / trades ---
    def get_open_oco_orders(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def cancel_oco_order_list(self, symbol: str, order_list_id: int) -> Dict[str, Any]:
        raise NotImplementedError

    def place_oco_sell(
        self,
        symbol: str,
        qty_base: Decimal,
        tp_stop: Decimal,
        tp_limit: Decimal,
        sl_stop: Decimal,
        sl_limit: Decimal,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def get_my_trades(self, symbol: str, start_time_ms: int) -> List[Dict[str, Any]]:
        raise NotImplementedError


class BinanceGatewayAdapter(ExchangeAdapter):
    """
    Adapter that talks to your binance-gateway service.
    You will need to extend the gateway to support all required endpoints.

    Expected endpoints:
      - GET  /exchange-info?symbol=BTCUSDC
      - GET  /price?symbol=BTCUSDC
      - GET  /open-orders?symbol=BTCUSDC
      - GET  /balance?asset=USDC
      - POST /order/limit-buy {symbol, price, qty}
      - GET  /open-oco
      - POST /oco/cancel {orderListId}
      - POST /order/oco-sell {symbol, qty, tp_stop, tp_limit, sl_stop, sl_limit}
      - GET  /my-trades?symbol=BTCUSDC&startTime=...
    """

    def __init__(self, gateway_url: str, api_key: str, api_secret: str, timeout: float = 15.0):
        self.gateway_url = gateway_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout

    def _get_public(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as c:
            r = c.get(f"{self.gateway_url}{path}", params=params or {})
            if r.status_code != 200:
                raise RuntimeError(f"Gateway {path} failed: {r.status_code} {r.text}")
            return r.json()

    def _get_private(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        final_params = dict(params or {})
        final_params["api_key"] = self.api_key
        final_params["api_secret"] = self.api_secret

        with httpx.Client(timeout=self.timeout) as c:
            r = c.get(f"{self.gateway_url}{path}", params=final_params)
            if r.status_code != 200:
                raise RuntimeError(f"Gateway {path} failed: {r.status_code} {r.text}")
            return r.json()

    def _post_private(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        final_payload = dict(payload)
        final_payload["api_key"] = self.api_key
        final_payload["api_secret"] = self.api_secret

        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(f"{self.gateway_url}{path}", json=final_payload)
            if r.status_code != 200:
                raise RuntimeError(f"Gateway {path} failed: {r.status_code} {r.text}")
            return r.json()

    def get_exchange_info(self, symbol: str) -> Dict[str, Any]:
        return self._get_public("/exchange-info", {"symbol": symbol})

    def get_price(self, symbol: str) -> Decimal:
        out = self._get_public("/price", {"symbol": symbol})
        return _d(out["price"])

    def get_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        out = self._get_private("/open-orders", {"symbol": symbol})
        return out.get("orders", out)

    def get_asset_balance(self, asset: str) -> Dict[str, Any]:
        return self._get_private("/balance", {"asset": asset})

    def place_limit_buy(self, symbol: str, price: Decimal, qty_base: Decimal) -> Dict[str, Any]:
        return self._post_private(
            "/order/limit-buy",
            {"symbol": symbol, "price": str(price), "qty": str(qty_base)},
        )

    def get_open_oco_orders(self) -> List[Dict[str, Any]]:
        out = self._get_private("/open-oco", {})
        return out.get("orders", out)

    def cancel_oco_order_list(self, symbol: str, order_list_id: int) -> Dict[str, Any]:
        return self._post_private(
            "/oco/cancel",
            {
                "symbol": symbol,
                "orderListId": int(order_list_id),
            },
        )

    def place_oco_sell(
        self,
        symbol: str,
        qty_base: Decimal,
        tp_stop: Decimal,
        tp_limit: Decimal,
        sl_stop: Decimal,
        sl_limit: Decimal,
    ) -> Dict[str, Any]:
        return self._post_private(
            "/order/oco-sell",
            {
                "symbol": symbol,
                "qty": str(qty_base),
                "tp_stop": str(tp_stop),
                "tp_limit": str(tp_limit),
                "sl_stop": str(sl_stop),
                "sl_limit": str(sl_limit),
            },
        )

    def get_my_trades(self, symbol: str, start_time_ms: int) -> List[Dict[str, Any]]:
        out = self._get_private("/my-trades", {"symbol": symbol, "startTime": int(start_time_ms)})
        return out.get("trades", out)


# ----------------------------
# Trader engine (GUI parity)
# ----------------------------

LogFn = Callable[[str], None]


class SpotBotTrader:
    def __init__(self, cfg: PairConfig, exchange: ExchangeAdapter, log: Optional[LogFn] = None):
        self.cfg = cfg
        self.exchange = exchange
        self.log: LogFn = log or (lambda s: print(s, flush=True))

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # filters
        self._tick_size: Decimal = Decimal("0")
        self._step_size: Decimal = Decimal("0")
        self._min_qty: Decimal = Decimal("0")
        self._min_notional: Decimal = Decimal("0")
        self._has_notional_filter: bool = False

        # runtime flags (mirrors GUI state)
        self.grid_started_at_ms: Optional[int] = None
        self.oco_gate_since_ts: int = 0
        self.oco_active: bool = False
        self.oco_step_index: int = -1
        self.last_oco_order_list_id: Optional[int] = None
        self._force_trailing_refresh: bool = False

        # anti re-entry / throttles
        self._grid_in_progress: bool = False
        self._oco_busy: bool = False
        self._last_oco_set_at_ms: int = 0

    # -------- lifecycle --------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            self.log("[trader] start(): already running")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"SpotBotTrader[{self.symbol}]", daemon=True)
        self._thread.start()
        self.log(f"[trader] started for {self.symbol}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.log(f"[trader] stopped for {self.symbol}")

    @property
    def symbol(self) -> str:
        return f"{self.cfg.base_asset.upper().strip()}{self.cfg.quote_asset.upper().strip()}"

    # -------- core loop --------

    def _run(self) -> None:
        try:
            self._load_symbol_filters()
            # initial grid
            self._place_grid()
            # monitoring loop
            while not self._stop.is_set():
                try:
                    if self.cfg.oco_enabled:
                        self._oco_monitor_tick()
                except Exception as e:
                    self.log(f"[oco] loop error: {type(e).__name__}: {e}")
                time.sleep(float(max(Decimal("0.2"), _d(self.cfg.poll_sec))))
        except Exception as e:
            self.log(f"[trader] fatal: {type(e).__name__}: {e}")

    # -------- symbol filters --------

    def _load_symbol_filters(self) -> None:
        info = self.exchange.get_exchange_info(self.symbol)

        # allow both formats: info['filters'] or info['symbols'][0]['filters']
        filters = info.get("filters") or (info.get("symbols") or [{}])[0].get("filters") or []
        tick = step = min_qty = None
        notional_filter = None

        for f in filters:
            if f.get("filterType") == "PRICE_FILTER":
                tick = f.get("tickSize")
            elif f.get("filterType") == "LOT_SIZE":
                step = f.get("stepSize")
                min_qty = f.get("minQty")
            elif f.get("filterType") in ("NOTIONAL", "MIN_NOTIONAL"):
                notional_filter = f

        if not tick or not step or min_qty is None:
            raise RuntimeError("exchange-info missing PRICE_FILTER/LOT_SIZE(minQty)")

        self._tick_size = _d(tick)
        self._step_size = _d(step)
        self._min_qty = _d(min_qty)

        if notional_filter and (notional_filter.get("minNotional") is not None):
            self._has_notional_filter = True
            self._min_notional = _d(notional_filter.get("minNotional"))
        else:
            self._has_notional_filter = False
            self._min_notional = Decimal("0")

        self.log(f"[filters] tick={self._tick_size} step={self._step_size} minQty={self._min_qty} minNotional={self._min_notional}")

    def _notional_ok(self, qty: Decimal, price: Decimal) -> bool:
        if not self._has_notional_filter:
            return True
        return (qty * price) >= self._min_notional

    # -------- grid (exact GUI logic) --------

    def _build_prices_from_layers_pct(self, current_price: Decimal) -> List[Decimal]:
        layers = [abs(_d(x)) for x in (self.cfg.layers_pct or []) if str(x).strip() != ""]
        if not layers:
            raise RuntimeError("Grid: layers_pct is empty (GUI required at least one layer).")

        prices: List[Decimal] = []
        for i, pct in enumerate(layers):
            if i == 0:
                target = current_price if pct == 0 else current_price * (Decimal("1") - pct / Decimal("100"))
            else:
                base = prices[-1]
                target = base * (Decimal("1") - pct / Decimal("100"))
            prices.append(q_tick(target, self._tick_size))
        return prices

    def _place_grid(self) -> None:
        if self._grid_in_progress:
            self.log("[grid] placement already in progress; ignoring.")
            return
        self._grid_in_progress = True
        try:
            symbol = self.symbol

            # 1) cap open orders
            open_orders = self.exchange.get_open_orders(symbol) or []
            if len(open_orders) > int(self.cfg.max_open_orders):
                raise RuntimeError(f"[grid] Too many open orders ({len(open_orders)}) > max_open_orders={self.cfg.max_open_orders}")

            # 2) do not place if there is already any BUY open (GUI rule)
            try:
                has_buy = any((o.get("side") == "BUY") for o in open_orders)
                if has_buy:
                    self.log("[grid] already has open BUY orders; new grid not placed.")
                    return
            except Exception:
                self.log("[grid] could not check open BUY orders; continuing (GUI behaviour).")

            qty_start = _d(self.cfg.qty_start_base)
            if qty_start <= 0:
                raise RuntimeError("[grid] qty_start_base must be > 0 (GUI required).")

            martingale_pct = _d(self.cfg.martingale_pct)
            mcoef = Decimal("1") + martingale_pct / Decimal("100")

            current_price = self.exchange.get_price(symbol)
            prices = self._build_prices_from_layers_pct(current_price)

            # build (qty, price) list with minQty/notional filtering, advancing qty by martingale even when skipped
            orders: List[tuple[Decimal, Decimal]] = []
            quote_planned = Decimal("0")
            current_qty = qty_start

            for price in prices:
                qty_dec = q_step(current_qty, self._step_size)
                if qty_dec < self._min_qty or not self._notional_ok(qty_dec, price):
                    current_qty = current_qty * mcoef
                    continue
                orders.append((qty_dec, price))
                quote_planned += qty_dec * price
                current_qty = current_qty * mcoef

            if not orders:
                self.log("[grid] after minQty/minNotional checks, no valid levels remain.")
                return

            # balance-gate: place while quote balance is enough (GUI)
            bal = self.exchange.get_asset_balance(self.cfg.quote_asset)
            free_quote = _d((bal or {}).get("free", "0"))
            spent = Decimal("0")
            placed = 0

            self.log("[grid] planned levels:")
            for qty, price in orders:
                self.log(f"  BUY {symbol}: qty={qty} @ {price}")

            for qty, price in orders:
                if self._stop.is_set():
                    return
                need = qty * price
                if spent + need > free_quote:
                    self.log(f"[grid] insufficient {self.cfg.quote_asset} for qty={qty} @ {price}; stop placing.")
                    break
                self.exchange.place_limit_buy(symbol, price=price, qty_base=qty)
                placed += 1
                spent += need

            self.grid_started_at_ms = now_ms()
            self.oco_gate_since_ts = self.grid_started_at_ms  # GUI sets new gate point after grid start
            self.log(f"[grid] placed {placed} levels, spent~{spent:.2f} {self.cfg.quote_asset} (planned~{quote_planned:.2f}).")

        finally:
            self._grid_in_progress = False

    # -------- trades / session position (GUI logic) --------

    def _fetch_trades_since(self, symbol: str, start_ms: int) -> List[Dict[str, Any]]:
        return self.exchange.get_my_trades(symbol, start_ms)

    def _calc_session_position(self, symbol: str, start_ms: int) -> tuple[Decimal, Optional[Decimal]]:
        """
        Approximate GUI _calc_session_position:
          - session_pos: base bought - base sold since start_ms
          - avg_price: (quote_spent_buys - quote_received_sells) / session_pos
        """
        trades = self._fetch_trades_since(symbol, start_ms) or []
        base_pos = Decimal("0")
        quote_net = Decimal("0")

        for t in trades:
            is_buyer = bool(t.get("isBuyer"))  # Binance format
            qty = _d(t.get("qty") or t.get("quantity") or "0")
            price = _d(t.get("price") or "0")
            if qty <= 0 or price <= 0:
                continue
            q = qty * price
            if is_buyer:
                base_pos += qty
                quote_net += q
            else:
                base_pos -= qty
                quote_net -= q

        if base_pos <= 0:
            return Decimal("0"), None

        avg = quote_net / base_pos if base_pos > 0 else None
        return base_pos, avg

    # -------- + Order (GUI parity) --------

    def add_one_order(self) -> None:
        """
        Public method for "+ Order" button equivalent.
        Places ONE extra BUY order using GUI rules.
        """
        symbol = self.symbol

        martingale_pct = _d(self.cfg.martingale_pct)
        mcoef = Decimal("1") + martingale_pct / Decimal("100")

        extra_pct = _d(self.cfg.add_order_pct)
        if extra_pct <= 0:
            self.log("[add] specify positive add_order_pct.")
            return

        cutoff = max((self.grid_started_at_ms or 0), (self.oco_gate_since_ts or 0)) or 0
        soft_cutoff_ms = max(0, cutoff - int(self.cfg.soft_cutoff_ms))

        # last buy in this session
        last_buy_qty = None
        last_buy_price = None
        try:
            trades = self._fetch_trades_since(symbol, soft_cutoff_ms)
            for t in reversed(trades or []):
                if bool(t.get("isBuyer")):
                    last_buy_qty = _d(t.get("qty") or t.get("quantity") or "0")
                    last_buy_price = _d(t.get("price") or "0")
                    break
        except Exception:
            pass

        if not last_buy_qty or not last_buy_price or last_buy_qty <= 0 or last_buy_price <= 0:
            self.log("[add] could not find last BUY in current session.")
            return

        base_qty = last_buy_qty if mcoef <= 1 else (last_buy_qty * mcoef)
        qty_dec = q_step(base_qty, self._step_size)
        if qty_dec < self._min_qty:
            self.log(f"[add] computed qty too small (qty<{self._min_qty}).")
            return

        target_price = last_buy_price * (Decimal("1") - extra_pct / Decimal("100"))
        price_dec = q_tick(target_price, self._tick_size)

        if qty_dec < self._min_qty or not self._notional_ok(qty_dec, price_dec):
            self.log("[add] does not pass minQty or MIN_NOTIONAL.")
            return

        # quote balance
        bal = self.exchange.get_asset_balance(self.cfg.quote_asset)
        free_quote = _d((bal or {}).get("free", "0"))
        need = qty_dec * price_dec
        if need > free_quote:
            self.log(f"[add] insufficient {self.cfg.quote_asset} balance.")
            return

        self.log(f"[add] BUY {symbol}: qty={qty_dec} @ {price_dec} (need~{need:.2f} {self.cfg.quote_asset})")
        self.exchange.place_limit_buy(symbol, price=price_dec, qty_base=qty_dec)

    # -------- OCO monitor (GUI parity, core) --------

    def refresh_trailing(self) -> None:
        """Equivalent to GUI 'refresh trailing' button."""
        self._force_trailing_refresh = True
        self.log("[oco] force refresh requested (UI changed TP/SL/step).")

    def _cancel_existing_oco(self) -> None:
        if self.last_oco_order_list_id is None:
            return
        try:
            self.exchange.cancel_oco_order_list(self.symbol, int(self.last_oco_order_list_id))
        finally:
            # even if cancel failed, don't loop-cancel forever
            self.last_oco_order_list_id = None

    def _oco_monitor_tick(self) -> None:
        if self._oco_busy:
            return
        self._oco_busy = True
        try:
            symbol = self.symbol

            # --- if we had an OCO list id, check if it is still open (GUI does this) ---
            if self.last_oco_order_list_id is not None:
                try:
                    open_oco = self.exchange.get_open_oco_orders() or []
                    still_open = any(int(o.get("orderListId", -1)) == int(self.last_oco_order_list_id) for o in open_oco)
                except Exception:
                    still_open = True

                if not still_open:
                    # OCO executed/closed => reset session flags + gate
                    self.log("[oco] executed/closed. Waiting for new buys.")
                    self.last_oco_order_list_id = None
                    self.oco_active = False
                    self.oco_step_index = -1
                    self.oco_gate_since_ts = now_ms()

                    if self.cfg.stop_cycle_requested:
                        self.log("[cycle] stop_cycle_requested=true; no new grids.")
                        return

                    if self.cfg.auto_cycle:
                        self.log(f"[cycle] auto_cycle=true; placing new grid after {self.cfg.grid_start_delay_ms}ms")
                        time.sleep(max(0.0, float(self.cfg.grid_start_delay_ms) / 1000.0))
                        if not self._stop.is_set():
                            self._place_grid()
                    return

            cutoff = max((self.grid_started_at_ms or 0), (self.oco_gate_since_ts or 0)) or 0
            soft_cutoff_ms = max(0, cutoff - int(self.cfg.soft_cutoff_ms))

            session_pos, avg_price = self._calc_session_position(symbol, soft_cutoff_ms)
            if session_pos <= 0 or avg_price is None or avg_price <= 0:
                # no session position => nothing to protect
                self.oco_active = False
                return

            qty_dec = q_step(session_pos, self._step_size)
            if qty_dec < self._min_qty:
                # same as GUI: can't sell if qty < minQty
                self.oco_active = False
                return

            # --- UI params (percents) ---
            tp_pct = max(Decimal("0"), _d(self.cfg.tp_pct) / Decimal("100"))
            sl_pct = max(Decimal("0"), _d(self.cfg.sl_pct) / Decimal("100"))
            trigger_pct = max(Decimal("0"), _d(self.cfg.oco_trigger_pct) / Decimal("100"))
            step_pct = max(Decimal("0"), _d(self.cfg.oco_step_pct) / Decimal("100"))
            stop_limit_extra = Decimal("0.001")

            current_price = self.exchange.get_price(symbol)
            ratio = (current_price / avg_price) - Decimal("1")

            force = bool(self._force_trailing_refresh)

            if ratio < trigger_pct and not force:
                return

            k = 0
            if step_pct > 0:
                # floor division like GUI: int((ratio - trigger)//step)
                k = int((ratio - trigger_pct) // step_pct)

            last_k = int(self.oco_step_index)
            need_place = force or (not self.oco_active) or (k > last_k)

            if not need_place and k <= last_k:
                # GUI checks that SELL protection exists; we do a lightweight check
                try:
                    open_orders = self.exchange.get_open_orders(symbol) or []
                    has_sell_protect = any(
                        (o.get("side") == "SELL" and o.get("type") in ("LIMIT", "STOP_LOSS_LIMIT"))
                        for o in open_orders
                    )
                    if has_sell_protect:
                        return
                except Exception:
                    return

            # effective tp/sl with steps
            tp_eff = tp_pct + Decimal(str(k)) * step_pct
            sl_eff = sl_pct + Decimal(str(k)) * step_pct

            tp_stop = q_tick(avg_price * (Decimal("1") + tp_eff), self._tick_size)
            tp_limit = q_tick(tp_stop * (Decimal("1") - Decimal("0.0002")), self._tick_size)
            sl_stop = q_tick(avg_price * (Decimal("1") + sl_eff), self._tick_size)
            sl_limit = q_tick(sl_stop * (Decimal("1") - stop_limit_extra), self._tick_size)

            # notional checks (GUI checks both legs)
            if not (self._notional_ok(qty_dec, tp_limit) and self._notional_ok(qty_dec, sl_limit)):
                self.oco_step_index = k
                return

            # cancel existing OCO before placing new (GUI)
            try:
                self._cancel_existing_oco()
            except Exception:
                pass

            # recalc session qty right before placing
            session_pos2, _ = self._calc_session_position(symbol, soft_cutoff_ms)
            qty_dec2 = q_step(session_pos2, self._step_size)
            if qty_dec2 < self._min_qty:
                return

            out = self.exchange.place_oco_sell(
                symbol=symbol,
                qty_base=qty_dec2,
                tp_stop=tp_stop,
                tp_limit=tp_limit,
                sl_stop=sl_stop,
                sl_limit=sl_limit,
            )

            # store list id if present
            order_list_id = out.get("orderListId") or out.get("orderListID") or out.get("order_list_id")
            if order_list_id is not None:
                try:
                    self.last_oco_order_list_id = int(order_list_id)
                except Exception:
                    self.last_oco_order_list_id = None

            self.oco_active = True
            self.oco_step_index = k
            self._force_trailing_refresh = False
            self._last_oco_set_at_ms = now_ms()

            self.log(
                f"[oco] set k={k} qty={qty_dec2} avg={avg_price} "
                f"TP={tp_stop}|{tp_limit} SL={sl_stop}|{sl_limit} -> {order_list_id or out}"
            )

        finally:
            self._oco_busy = False

    # -------- serialization --------

    def export_config(self) -> Dict[str, Any]:
        d = asdict(self.cfg)

        def conv(x: Any) -> Any:
            if isinstance(x, Decimal):
                return str(x)
            if isinstance(x, list):
                return [conv(i) for i in x]
            if isinstance(x, dict):
                return {k: conv(v) for k, v in x.items()}
            return x

        return conv(d)


# ----------------------------
# CLI runner (for tests)
# ----------------------------

def _default_logger(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {msg}", flush=True)


def main() -> None:
    """
    Example:
      export BINANCE_GATEWAY_URL=http://127.0.0.1:8001
      export BINANCE_API_KEY=...
      export BINANCE_API_SECRET=...
      python3 spotbot-trader.py run config.json

    config.json example (GUI parity):
    {
      "base_asset":"BTC",
      "quote_asset":"USDC",
      "qty_start_base":"0.0002",
      "martingale_pct":"100",
      "layers_pct":["0","3","3","3"],
      "tp_pct":"3.0",
      "sl_pct":"0.5",
      "oco_trigger_pct":"1.0",
      "oco_step_pct":"1.0",
      "add_order_pct":"3.0",
      "oco_enabled":true,
      "auto_cycle":false,
      "stop_cycle_requested":false
    }
    """
    import sys

    if len(sys.argv) < 3 or sys.argv[1].lower().strip() != "run":
        print("Usage: spotbot-trader.py run <config.json>")
        raise SystemExit(2)

    cfg_path = sys.argv[2]
    raw = json.loads(open(cfg_path, "r", encoding="utf-8").read())

    cfg = PairConfig(
        base_asset=raw.get("base_asset", "BTC"),
        quote_asset=raw.get("quote_asset", "USDC"),
        qty_start_base=_d(raw.get("qty_start_base", "0")),
        martingale_pct=_d(raw.get("martingale_pct", "100")),
        layers_pct=[_d(x) for x in (raw.get("layers_pct") or [])],
        tp_pct=_d(raw.get("tp_pct", "3.0")),
        sl_pct=_d(raw.get("sl_pct", "0.5")),
        oco_trigger_pct=_d(raw.get("oco_trigger_pct", "1.0")),
        oco_step_pct=_d(raw.get("oco_step_pct", "1.0")),
        add_order_pct=_d(raw.get("add_order_pct", "3.0")),
        poll_sec=_d(raw.get("poll_sec", "1.2")),
        oco_enabled=bool(raw.get("oco_enabled", True)),
        auto_cycle=bool(raw.get("auto_cycle", False)),
        stop_cycle_requested=bool(raw.get("stop_cycle_requested", False)),
    )

    gw = os.getenv("BINANCE_GATEWAY_URL", "http://127.0.0.1:8001").rstrip("/")
    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    if not api_key or not api_secret:
        raise SystemExit("Set BINANCE_API_KEY and BINANCE_API_SECRET env vars.")

    ex = BinanceGatewayAdapter(gateway_url=gw, api_key=api_key, api_secret=api_secret)
    trader = SpotBotTrader(cfg=cfg, exchange=ex, log=_default_logger)
    trader.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        trader.stop()


if __name__ == "__main__":
    main()
