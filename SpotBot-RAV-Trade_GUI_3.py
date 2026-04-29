# =========================
# ### СЕКЦИЯ 1: Подключение библиотек ###
# =========================
import sys
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from binance.client import Client
from binance.exceptions import BinanceAPIException
import time
from concurrent.futures import ThreadPoolExecutor
import random
from decimal import Decimal, ROUND_DOWN, getcontext
getcontext().prec = 28


def is_linux() -> bool:
    return sys.platform.startswith("linux")

def default_ui_font_family() -> str:
    if is_linux():
        return "Noto Sans"
    return "Segoe UI Emoji"


def emoji_font_family() -> str:
    return "Noto Color Emoji" if is_linux() else "Segoe UI Emoji"


def decimals_from_str(x_str: str) -> int:
    """
    Возвращает кол-во десятичных знаков по строке из Binance (без ошибок float).
    Работает и с '1.00000000', и с '0.01000000', и с экспонентой.
    """
    d = Decimal(x_str).normalize()
    exp = d.as_tuple().exponent
    return -exp if exp < 0 else 0

def q_step(value: float, step: float) -> Decimal:
    """Усечение количества под шаг stepSize"""
    dv, ds = Decimal(str(value)), Decimal(str(step))
    return (dv // ds) * ds

def q_tick(value: float, tick: float) -> Decimal:
    """Усечение цены под шаг tickSize"""
    dv, dt = Decimal(str(value)), Decimal(str(tick))
    return (dv / dt).to_integral_value(rounding=ROUND_DOWN) * dt

def fmt(dec: Decimal, decimals: int) -> str:
    """Формат строки под точность"""
    q = Decimal(1).scaleb(-decimals)  # 10^-decimals
    return str(dec.quantize(q, rounding=ROUND_DOWN))

# =========================
# ### СЕКЦИЯ 2: Главный класс интерфейса и логики ###
# =========================
class BinanceBotGUI:
    def __init__(self, root):
        import tkinter.font as tkfont
        base_font_family = default_ui_font_family()
        emoji_family = emoji_font_family()
        tkfont.nametofont("TkDefaultFont").configure(family=base_font_family)
        tkfont.nametofont("TkTextFont").configure(family=base_font_family)
        tkfont.nametofont("TkFixedFont").configure(family=base_font_family)
        self._button_font = (base_font_family, 10)
        self._emoji_font = (emoji_family, 10)
        self.root = root
        self.root.grid_columnconfigure(1, weight=1, minsize=320)    # Колонка с инпутами (column=1) растягивается по ширине окна
        self.root.title("RAV-Trade")
        self.client = None
        self.blink_state = True
        self._add_in_progress = False       # защита от даблклика при добавлении
        self.grid_orders_placed = False     # Флаг: выставлена ли сетка
        self._grid_in_progress = False      # идёт ли сейчас постановка сетки
        self.last_trigger_price = 0.0       # Для OCO логики
        self.oco_active = False             # Флаг: активен ли OCO
        self._last_activation_buy_ts = None # логировать ктивацию OCO 1x (только при покупке)
        self.grid_started_at = None         # timestamp старта сетки — для расчёта средней
        self.last_oco_order_list_id = None  # Отмена текущего OCO перед постановкой нового
        self.oco_gate_since_ts = None       # миллисекунды; гейт OCO не стартует раньше этой метки
        self._qty_warned_after_oco = False  # антиспам: предупреждение о qty показываем 1 раз, и только когда OCO не активен
        self.last_oco_params = None         # запомним цены TP/SL, qty на момент постановки
        self.oco_placed_at_ts = None        # время постановки OCO (мс)
        self.auto_cycle = False             # режим автоперезапуска сетки (▶️ ꝏ)
        self.stop_cycle_requested = False   # «Стоп цикл»: не стартовать заново после закрытия позиции
        self._oco_busy = False              # анти-реэнтри для OCO-цикла
        self._paused_no_buys_logged = False # однократный комент на период «без покупок»
        self._force_trailing_refresh = False   # принудительно переставить OCO по новым параметрам
        self._grid_start_delay_ms = 5000
        self._grid_start_timer = None
        self._pending_grid_start_reason = None
        self._pending_grid_start_ts = None
        self._grid_start_pending = False


        # =========================
        # ### СЕКЦИЯ 3: Поля ввода API и статуса подключения ###
        # =========================
        tk.Label(root, text="API Key:").grid(row=0, column=0, sticky="e")
        self.api_key_entry = tk.Entry(root, width=50)
        self.api_key_entry.grid(row=0, column=1, sticky="w")

        tk.Label(root, text="API Secret:").grid(row=1, column=0, sticky="e")
        self.api_secret_entry = tk.Entry(root, width=50, show="*")
        self.api_secret_entry.grid(row=1, column=1, sticky="w")

        self.connect_button = tk.Button(root, text="Подключиться", command=self.connect_to_binance)
        self.connect_button.grid(row=2, column=0, columnspan=2, pady=(12, 12))

        # =========================
        # ### СЕКЦИЯ 4: Баланс, пара и сетка ордеров ###
        # =========================
        tk.Label(root, text="Валюта:").grid(row=3, column=0, sticky="e")
        self.quote_asset = "USDC"  # константа, не меняется пользователем
        self.asset_label = tk.Label(root, text=self.quote_asset)
        self.asset_label.grid(row=3, column=1, sticky="w")

        self.balance_label = tk.Label(root, text="Баланс: -")
        self.balance_label.grid(row=4, column=0, columnspan=2, pady=5)

        self.status_label = tk.Label(root, text="Отключено", fg="red")
        self.status_label.grid(row=5, column=0, columnspan=2)

        tk.Label(root, text="Пара:").grid(row=6, column=0, sticky="e")
        pair_row = tk.Frame(root)
        pair_row.grid(row=6, column=1, sticky="w")

        self.base_var = tk.StringVar(value="BTC")
        self.base_combo = ttk.Combobox(
            pair_row,
            textvariable=self.base_var,
            width=8,
            state="normal",  # можно печатать руками; поставь "readonly", если хочешь только из списка
            values=[
                "BTC","ETH","BNB","XRP","ADA","SOL","DOGE","TRX","DOT","MATIC",
                "LINK","LTC","ATOM","AVAX","TON","NEAR","HBAR","OP","ARB","APE"
            ]
        )
        def _validate_base(newval):
            return (newval.upper().isalpha() and len(newval) <= 10) or (newval == "")
        vcmd = (root.register(_validate_base), "%P")
        self.base_combo.configure(validate="key", validatecommand=vcmd)
        self.base_combo.grid(row=0, column=0, sticky="w")
        self.base_var.trace_add("write", lambda *_: self.base_var.set((self.base_var.get() or "").upper()))
        tk.Label(pair_row, text="/USDC").grid(row=0, column=1, padx=(6,0), sticky="w")

        # =========================
        # ### СЕКЦИЯ 5: Настройки сетки и объёма ###
        # =========================

        # текст метки делаем через StringVar
        self.qty_label_text = tk.StringVar()
        self.qty_label_text.set(f"Старт. сумма в {self.base_var.get()}:")

        self.qty_label = tk.Label(root, textvariable=self.qty_label_text)
        self.qty_label.grid(row=7, column=0, sticky="e")

        qty_row = tk.Frame(root)
        qty_row.grid(row=7, column=1, sticky="w")  # тянем по ширине

        self.quantity_entry = tk.Entry(qty_row, width=10)
        self.quantity_entry.grid(row=0, column=0, padx=(0, 6), sticky="w")

        self.equivalent_label = tk.Label(qty_row, text="= - USDC")
        self.equivalent_label.grid(row=0, column=1, sticky="w")

        # --- обновление метки при смене базовой монеты ---
        def _update_qty_label(*_):
            base = (self.base_var.get() or "").upper().strip()
            if base:
                self.qty_label_text.set(f"Старт. сумма в {base}:")
            else:
                self.qty_label_text.set("Старт. сумма:")

        self.base_var.trace_add("write", _update_qty_label)


        # Старт + Шаги (%)
        tk.Label(root, text="Старт + Шаги (%):").grid(row=8, column=0, sticky="e")


        layers_frame = tk.Frame(root)
        layers_frame.grid(row=8, column=1, sticky="w") # тянем по ширине колонки


        self.layer_entries = []
        NUM_LAYERS = 8
        for i in range(NUM_LAYERS):
            e = tk.Entry(layers_frame, width=4)
            e.grid(row=0, column=i, padx=(0 if i == 0 else 4, 0), pady=0)
            self.layer_entries.append(e)


        # «Итого» сумма, которую займут ВСЕ планируемые сделки (без учёта доступного баланса)
        tk.Label(root, text="Резерв в торги (≈):").grid(row=9, column=0, sticky="e")
        self.planned_label = tk.Label(root, text="≈ -")
        self.planned_label.grid(row=9, column=1, sticky="w")
        

        # «пружина» — переносим на следующую колонку, чтобы ряд тянулся вправо
        layers_frame.grid_columnconfigure(NUM_LAYERS + 1, weight=1)
        tk.Label(layers_frame, text="").grid(row=0, column=NUM_LAYERS + 1, sticky="w")

        # Пример значений (можешь стереть)
        _defaults = ["0", "0.5", "1", "2", "4", "", "", ""]
        for i, val in enumerate(_defaults[:NUM_LAYERS]):
            if val:
                self.layer_entries[i].insert(0, val)

        # «пружина» справа, чтобы блок занял всю ширину колонки
        qty_row.grid_columnconfigure(2, weight=1)
        tk.Label(qty_row, text="").grid(row=0, column=2, sticky="w")

        # Коэф. объёма (как было)
        tk.Label(root, text="Коэф. объёма (Мартингейл %):").grid(row=10, column=0, sticky="e")
        self.martingale_entry = tk.Entry(root)
        self.martingale_entry.insert(0, "100")
        self.martingale_entry.grid(row=10, column=1, sticky="w")

        # бинды + пересчёт «Итого»
        self.quantity_entry.bind("<KeyRelease>", lambda e: (self.update_equivalent(), self.update_planned_total()))
        self.martingale_entry.bind("<KeyRelease>", lambda e: self.update_planned_total())
        for e in self.layer_entries:
            e.bind("<KeyRelease>", lambda ev: self.update_planned_total())

        # =========================
        # ### СЕКЦИЯ 6: OCO-поля (Take-Profit и Stop-Loss) ###
        # =========================
        # row=13 ist frei
        
        tk.Label(root, text="Прибыль:   Старт ОСО %:").grid(row=14, column=0, sticky="e", pady=(12, 0))
        self.oco_trigger_entry = tk.Entry(root)
        self.oco_trigger_entry.insert(0, "1.0")
        self.oco_trigger_entry.grid(row=14, column=1, sticky="w", pady=(12, 0))

        tk.Label(root, text="Шаг ОСО %:").grid(row=15, column=0, sticky="e")
        self.oco_step_entry = tk.Entry(root)
        self.oco_step_entry.insert(0, "1.0")
        self.oco_step_entry.grid(row=15, column=1, sticky="w")

        tk.Label(root, text="T/P %:").grid(row=16, column=0, sticky="e")
        self.tp_entry = tk.Entry(root)
        self.tp_entry.insert(0, "3.0")
        self.tp_entry.grid(row=16, column=1, sticky="w")

        tk.Label(root, text="S/L %:").grid(row=17, column=0, sticky="e")
        sl_row = tk.Frame(root)
        sl_row.grid(row=17, column=1, sticky="w")

        self.sl_entry = tk.Entry(sl_row)
        self.sl_entry.insert(0, "0.5")
        self.sl_entry.grid(row=0, column=0, sticky="w")

        self.refresh_trailing_btn = tk.Button(
            sl_row, text="🔄 Обновить трейлинг",
            command=self.refresh_trailing,
            font=self._button_font,
            state="disabled"   # изначально выключена
        )
        self.refresh_trailing_btn.grid(row=0, column=1, padx=(8, 0), sticky="w")


        # =========================
        # ### СЕКЦИЯ 7: Кнопки управления ###
        # =========================

        # Ряд кнопок (row=19)
        btns19 = tk.Frame(root)
        btns19.grid(row=19, column=1, columnspan=2, sticky="w", pady=10)

        self.place_grid_button = tk.Button(
            btns19,
            text=" ▶ Start х 1",
            command=lambda: self.start_grid_with_delay(5000),
            font=self._button_font,
            state="disabled",
            )
        self.place_grid_button.grid(row=0, column=0, padx=(0, 8))

        # рядом с self.place_grid_button и self.add_orders_button:
        self.auto_cycle_button = tk.Button(
            btns19,
            text="▶ Auto-Start",   # можно "▶ ∞  Авто-цикл: ВКЛ"
            command=self.toggle_auto_cycle,
            font=self._button_font,
            state="disabled",    # Windows: цветной эмодзи
        )
        self.auto_cycle_button.grid(row=0, column=1, padx=(8, 8))

        self.stop_cycle_button = tk.Button(
            btns19,
            text="⏹️ Auto-Stop",
            command=self.request_stop_cycle,
            state="disabled",
            font=self._button_font
            )
        self.stop_cycle_button.grid(row=0, column=2)

        # Строка со средней ценой и кнопкой "+ Order" + поле % (row=20)
        avg_row = tk.Frame(root)
        avg_row.grid(row=20, column=1, columnspan=2, sticky="w", pady=(8, 0))

        tk.Label(avg_row, text="Средняя цена:").grid(row=0, column=0, sticky="w")

        # сама цифра средней
        self.avg_price_label = tk.Label(avg_row, text="-")
        self.avg_price_label.grid(row=0, column=1, padx=(4, 12), sticky="w")

        # кнопка "+ Order"
        self.add_orders_button = tk.Button(
            avg_row,
            text="➕ Order",
            command=self.add_more_orders,
            state="disabled",
            font=self._button_font
        )
        self.add_orders_button.grid(row=0, column=2, padx=(0, 4), sticky="w")

        # поле для процента доп. ордера (от средней)
        self.add_order_pct_entry = tk.Entry(avg_row, width=4, justify="center")
        self.add_order_pct_entry.insert(0, "3")  # дефолт 3%
        self.add_order_pct_entry.grid(row=0, column=3, sticky="w")

        tk.Label(avg_row, text="%").grid(row=0, column=4, padx=(2, 0), sticky="w")


        # Кнопка "Лог средней (детали)" — отдельной строкой ниже (row=21)
        self.log_avg_details_btn = tk.Button(
            root,
            text="🧾 Лог средней (детали)",
            command=self.log_avg_details,
            state="disabled",   # изначально выключена
            font=self._emoji_font if is_linux() else self._button_font
        )
        self.log_avg_details_btn.grid(row=21, column=1, columnspan=2, sticky="w", pady=(4, 0))

        # Кнопка "Отменить ордера" (row=22)
        self.cancel_orders_button = tk.Button(
            root,
            text="❌ Отменить ордера",
            command=self.cancel_buy_orders,
            state="disabled"
        )
        self.cancel_orders_button.grid(row=22, column=1, columnspan=2, sticky="w", pady=(4, 0))


        # Лог событий
        tk.Label(root, text="Лог событий:").grid(row=23, column=0, sticky="ne")
        self.log_text = ScrolledText(root, height=10, width=60, state="disabled")
        self.log_text.grid(row=23, column=1, sticky="nsew", pady=(0,10))
        self.root.grid_rowconfigure(23, weight=1)
        
        self.root.after(2000, self.update_balance_and_status)
        self.root.after(1000, self.oco_monitor_loop)

        self._indent_grid_column(indent_px=50, column=1)

        self._executor = ThreadPoolExecutor(max_workers=3)
        self._symbol_info_cache = {}
        self._api_last_call = {}  # endpoint -> last_ts

        def _ui(self, fn, *a, **kw):
            """Выполнить fn на UI-потоке."""
            self.root.after(0, lambda: fn(*a, **kw))

        def _bg(self, work, done=None):
            """Выполнить тяжелую работу в фоне. done(result|exc) — на UI-потоке."""
            def _runner():
                try:
                    res = work()
                except Exception as e:
                    res = e
                if done:
                    self._ui(done, res)
            self._executor.submit(_runner)

        def _rate_ok(self, key, min_interval_ms=350):
            """Простейший локальный rate-limit (по ключу)."""
            now = int(time.time() * 1000)
            last = self._api_last_call.get(key, 0)
            if now - last >= min_interval_ms:
                self._api_last_call[key] = now
                return True
            return False

        def get_symbol_info_cached(self, symbol):
            info = self._symbol_info_cache.get(symbol)
            if info is None:
                info = self.client.get_symbol_info(symbol)
                self._symbol_info_cache[symbol] = info
            return info
        self._ui = _ui.__get__(self, type(self))
        self._bg = _bg.__get__(self, type(self))
        self._rate_ok = _rate_ok.__get__(self, type(self))
        self.get_symbol_info_cached = get_symbol_info_cached.__get__(self, type(self))

        def _on_base_change(*_):
            try:
                self._symbol_info_cache.clear()
            except Exception:
                pass
            # полезно также пересчитать эквивалент и план
            try:
                self.update_equivalent()
                self.update_planned_total()
            except Exception:
                pass
            # сброс базлайнов
            try:
                if hasattr(self, "_avg_trade_baseline"):
                    self._avg_trade_baseline.clear()
                if hasattr(self, "_avg_balance_baseline"):
                    self._avg_balance_baseline.clear()
            except Exception:
                pass

        self.base_var.trace_add("write", _on_base_change)


    # =========================
    # ### СЕКЦИЯ 8: Логирование в окно ###
    # =========================
    def log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"[{ts}] {msg}\n")
            self.log_text.see("end")
        finally:
            self.log_text.configure(state="disabled")

    # =========================
    # ### СЕКЦИЯ 9: Обновление эквивалента монеты / Обновление "Итого" ###
    # =========================
    def update_equivalent(self):
        if not self.client:
            self.equivalent_label.config(text="= - USDC")
            return
        try:
            symbol = self.get_current_symbol().upper().strip()
            qty = float((self.quantity_entry.get() or "0").replace(",", "."))
            price = float(self.client.get_symbol_ticker(symbol=symbol)['price'])
            self.equivalent_label.config(text=f"= {qty * price:.2f} USDC")
        except Exception:
            self.equivalent_label.config(text="= - USDC")

    def get_current_symbol(self) -> str:
        base = (self.base_var.get() or "").upper().strip()
        return f"{base}{self.quote_asset}"

    def update_planned_total(self):
        """
        Считает сумму (в котируемой валюте), которая будет задействована
        во ВСЕХ планируемых сделках сетки по текущим полям «Старт + Шаги»,
        стартовому количеству и мартингейлу. НЕ учитывает доступный баланс.
        Учитывает minQty и (MIN_)NOTIONAL. Цены/кол-ва усечены по фильтрам.
        """
        if not self.client:
            self.planned_label.config(text="≈ -")
            return
        try:
            symbol = self.get_current_symbol().upper().strip()
            info = self.get_symbol_info_cached(symbol)

            price_filter = next(f for f in info['filters'] if f['filterType'] == 'PRICE_FILTER')
            lot_size     = next(f for f in info['filters'] if f['filterType'] == 'LOT_SIZE')
            tick_size    = float(price_filter['tickSize'])
            step_size    = float(lot_size['stepSize'])
            min_qty      = float(lot_size['minQty'])

            notional_filter = next((f for f in info['filters']
                                    if f['filterType'] in ('NOTIONAL', 'MIN_NOTIONAL')), None)
            min_notional = float(notional_filter.get('minNotional')) if notional_filter else 0.0

            def notional_ok(qty_f: float, price_f: float) -> bool:
                return (qty_f * price_f) >= min_notional if notional_filter else True

            # параметры
            try:
                qty_start = float(self.quantity_entry.get().replace(',', '.') or 0.0)
            except Exception:
                qty_start = 0.0
            try:
                martingale_pct = float(self.martingale_entry.get().replace(',', '.') or 0.0)
            except Exception:
                martingale_pct = 0.0
            martingale_coef = 1 + martingale_pct / 100.0

            # слои (старт + шаги)
            layers = []
            for e in getattr(self, 'layer_entries', []):
                s = (e.get() or '').strip().replace(',', '.')
                if not s:
                    break
                try:
                    layers.append(abs(float(s)))
                except Exception:
                    break

            if not layers or qty_start <= 0:
                self.planned_label.config(text="≈ -")
                return

            current_price = float(self.client.get_symbol_ticker(symbol=symbol)['price'])

            # цены уровней: первый — от рынка (0 => по рынку), далее — от предыдущего
            prices_dec = []
            for i, pct in enumerate(layers):
                if i == 0:
                    target = current_price if pct == 0 else current_price * (1.0 - pct / 100.0)
                else:
                    base = float(prices_dec[-1])
                    target = base * (1.0 - pct / 100.0)
                prices_dec.append(q_tick(target, tick_size))

            # считаем план (без учёта доступного баланса)
            quote_planned = 0.0
            cur_qty = qty_start
            for price_dec in prices_dec:
                qty_dec = q_step(cur_qty, step_size)
                qty = float(qty_dec)
                price = float(price_dec)
                if qty < min_qty or not notional_ok(qty, price):
                    # уровень слишком мелкий — пропускаем и идём дальше
                    cur_qty *= martingale_coef
                    continue
                quote_planned += qty * price
                cur_qty *= martingale_coef

            quote_asset = info['quoteAsset']
            if quote_planned > 0:
                self.planned_label.config(text=f"≈ {quote_planned:.2f} {quote_asset}")
            else:
                self.planned_label.config(text="≈ -")
        except Exception:
            self.planned_label.config(text="≈ -")

    # =========================
    # ### СЕКЦИЯ 10: Обновление баланса и статуса ###
    # =========================
    def update_balance_and_status(self):
        if not self.client:
            self.root.after(3000, self.update_balance_and_status)
            return

        # снимок того, что нужно из UI
        asset = "USDC"

        def work():
            # rate-limit на баланс (не чаще ~400–500 мс)
            if not self._rate_ok(f"bal:{asset}", 450):
                # «ничего не делаем», но вернём None, чтобы просто перепланировать
                return None
            balances = self.client.get_asset_balance(asset=asset)
            price_blink = True  # вернём флаг, как мигать
            return balances, price_blink

        def done(res):
            if isinstance(res, Exception):
                self.balance_label.config(text="Баланс: -")
                self.status_label.config(text="Ошибка", fg="red", font=("Arial", 10, "bold"))
                self.log(f"Balance error: {res}")
            elif res is not None:
                balances, price_blink = res
                free_raw = (balances or {}).get('free', '0')
                free_fmt = Decimal(str(free_raw)).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
                self.balance_label.config(text=f"Баланс: {free_fmt} {asset}")
                self.blink_state = not self.blink_state
                color = "green" if self.blink_state else "darkgreen"
                self.status_label.config(text="Подключено", fg=color, font=("Arial", 12, "bold"))
            # небольшой «джиттер», чтобы не тикать строго в такт
            delay = 3000 + random.randint(-150, 150)
            self.root.after(max(1000, delay), self.update_balance_and_status)

        self._bg(work, done)

    # =========================
    # ### Дополнительно - выравнивание колонок ###
    # =========================
    def _indent_grid_column(self, indent_px: int = 50, column: int = 1) -> None:
        """
        Даёт общий левый отступ всем виджетам в указанной колонке grid.
        Работает «оптом», без правок каждой .grid(...).
        """
        for w in self.root.grid_slaves():  # все дети, у кого grid
            try:
                info = w.grid_info()
                if int(info.get("column", -1)) == column:
                    # сохраняем правый паддинг, если он был, и ставим общий левый
                    px = info.get("padx")
                    right = px[1] if isinstance(px, tuple) and len(px) == 2 else 0
                    w.grid_configure(padx=(indent_px, right))
            except Exception:
                pass

    # =========================
    # ### СЕКЦИЯ 11: Подключение к Binance ###
    # =========================
    def connect_to_binance(self):
        api_key = self.api_key_entry.get()
        api_secret = self.api_secret_entry.get()
        try:
            self.client = Client(api_key, api_secret)
            self.client.get_account()
            self.update_balance_and_status()  # сразу обновить статус/баланс
            self.update_equivalent()
            self.update_planned_total()

            # Метка для «гейта» OCO — мониторинг ждёт новую покупку
            self.oco_gate_since_ts = int(time.time() * 1000)
            self._last_activation_buy_ts = None
            self.oco_active = False
            self.last_trigger_price = 0
            self.log("Подключено к Binance. OCO-гейт установлен; мониторинг ждёт первой покупки.")
            # Активируем стартовые кнопки после подключения
            self.place_grid_button.config(state="normal")
            self.auto_cycle_button.config(state="normal")
            self.stop_cycle_button.config(state="disabled")

        except BinanceAPIException as e:
            self.client = None
            self.status_label.config(text="Ошибка", fg="red", font=("Arial", 12, "bold"))
            self.log(f"Ошибка Binance API при подключении: {e.message}")
        except Exception as e:
            self.client = None
            self.status_label.config(text="Ошибка", fg="red", font=("Arial", 12, "bold"))
            self.log(f"Ошибка подключения: {e}")
        try:
            self.refresh_trailing_btn.config(state="disabled")
        except Exception:
            pass


    # =========================
    # ### СЕКЦИЯ 12: Выставление сетки ордеров (объём в торгуемой валюте) ###
    # =========================
    def _set_session_baseline(self):
        """Фиксируем точку отсчёта ДО постановки сетки: время (по серверу), tradeId и базовый остаток."""
        try:
            srv_ts = int(self.client.get_server_time().get("serverTime"))
        except Exception:
            srv_ts = int(time.time() * 1000)

        self.grid_started_at = srv_ts
        self.oco_gate_since_ts = srv_ts

        try:
            symbol = self.get_current_symbol().upper().strip()
            info   = self.get_symbol_info_cached(symbol)
            base   = info["baseAsset"]

            try:
                trades = self.client.get_my_trades(symbol=symbol) or []
                last_id = int(trades[-1].get("id")) if trades and trades[-1].get("id") is not None else None
                if not hasattr(self, "_avg_trade_baseline"):
                    self._avg_trade_baseline = {}
                self._avg_trade_baseline[symbol] = last_id
            except Exception:
                pass

            try:
                bal = self.client.get_asset_balance(asset=base) or {}
                qty0 = float(bal.get("free", 0) or 0) + float(bal.get("locked", 0) or 0)
                if not hasattr(self, "_avg_balance_baseline"):
                    self._avg_balance_baseline = {}
                self._avg_balance_baseline[base] = qty0
            except Exception:
                pass
        except Exception:
            pass

    def start_grid_with_delay(self, delay_ms: int = 5000):
        """Ставит baseline сейчас, а сетку — через delay_ms. Защита от двойных запусков."""
        if getattr(self, "_grid_start_pending", False):
            return

        if not self.client:
            self.log("Старт: нет подключения к бирже.")
            return

        self._set_session_baseline()

        self._grid_start_pending = True
        self.log(f"Старт через {delay_ms/1000:.0f} с: фиксирую точку отсчёта и жду перед постановкой сетки.")

        try:
            if getattr(self, "place_grid_button", None):
                self.place_grid_button.config(state="disabled")
            if getattr(self, "auto_cycle_button", None) and getattr(self, "auto_cycle", False):
                self.auto_cycle_button.config(state="disabled")
        except Exception:
            pass

        def _do_start():
            self._grid_start_pending = False
            self._grid_start_timer = None
            if getattr(self, "stop_cycle_requested", False):
                self.log("Старт отменён: включён Auto-Stop.")
                return
            try:
                self.create_grid_orders()
            except Exception as e:
                self.log(f"Ошибка старта сетки: {e}")

        self._grid_start_timer = self.root.after(delay_ms, _do_start)

    def _cancel_pending_grid_schedule(self, quiet: bool = True):
        had_timer = getattr(self, "_grid_start_timer", None) is not None
        try:
            if had_timer:
                self.root.after_cancel(self._grid_start_timer)
        except Exception:
            pass
        self._grid_start_timer = None
        self._pending_grid_start_reason = None
        self._pending_grid_start_ts = None
        self._grid_start_pending = False
        if had_timer and not quiet:
            self.log("Сетка: отложенный старт отменён.")

    def _schedule_grid_start(self, reason: str = "ручной старт", delay_ms: int | None = None):
        if not self.client:
            self.log("Сетка: нет подключения к бирже.")
            return
        delay = delay_ms if delay_ms is not None else self._grid_start_delay_ms
        delay = max(0, int(delay))
        self._set_session_baseline()
        # отменяем предыдущий таймер
        if getattr(self, "_grid_start_timer", None):
            self._cancel_pending_grid_schedule(quiet=True)
            self.log("Сетка: предыдущий отложенный старт отменён.")
        self._pending_grid_start_reason = reason
        scheduled_at = int(time.time() * 1000)
        self._pending_grid_start_ts = scheduled_at
        self.log(f"Сетка: старт '{reason}' через {delay/1000:.1f}с.")

        def _start():
            self._grid_start_timer = None
            reason_local = self._pending_grid_start_reason or reason
            self._pending_grid_start_reason = None
            self.log(f"Сетка: запускаю после задержки ({reason_local}).")
            try:
                self.create_grid_orders()
            except Exception as e:
                self.log(f"Сетка: ошибка отложенного старта — {e}")

        self._grid_start_timer = self.root.after(delay, _start)

    def create_grid_orders(self):
        # запуск сетки всегда сбрасывает отложенный таймер
        self._grid_start_timer = None
        self._pending_grid_start_reason = None
        self._pending_grid_start_ts = None
        self._grid_start_pending = False
        if not self.client:
            self.log("Внимание: сначала подключитесь к Binance.")
            return
        try:
            symbol = self.get_current_symbol().upper().strip()
            # 1) Антидребезг: не чаще, чем раз в 10 секунд
            now = time.time()
            if getattr(self, "_last_grid_attempt_ts", 0) and now - self._last_grid_attempt_ts < 10:
                self.log("Сетка: повторный вызов слишком быстро — игнорирую.")
                return
            self._last_grid_attempt_ts = now

            # 2) Не ставим новую сетку, если по символу уже есть открытые BUY
            try:
                open_orders = self.client.get_open_orders(symbol=symbol) or []
                has_buy = any(o.get('side') == 'BUY' for o in open_orders)
                if has_buy:
                    self.log("Сетка: по символу уже есть открытые BUY — новая сетка не ставится.")
                    return
            except Exception:
                self.log("Сетка: не удалось проверить открытые ордера — продолжаю постановку.")

            qty_start = float((self.quantity_entry.get() or '0').replace(',', '.'))
            martingale_pct = float((self.martingale_entry.get() or '0').replace(',', '.'))
            martingale_coef = 1 + (martingale_pct / 100)  # например, 100% -> 2x

            info = self.get_symbol_info_cached(symbol)
            price_filter = next(f for f in info['filters'] if f['filterType'] == 'PRICE_FILTER')
            lot_size_filter = next(f for f in info['filters'] if f['filterType'] == 'LOT_SIZE')
            tick_size = float(price_filter['tickSize'])
            step_size = float(lot_size_filter['stepSize'])
            min_qty  = float(lot_size_filter['minQty'])

            # --- извлечь NOTIONAL/MIN_NOTIONAL и десятичности ---
            notional_filter = next((f for f in info['filters']
                                    if f['filterType'] in ('NOTIONAL', 'MIN_NOTIONAL')), None)
            min_notional = float(notional_filter.get('minNotional')) if notional_filter else 0.0

            qty_decimals   = decimals_from_str(lot_size_filter['stepSize'])
            price_decimals = decimals_from_str(price_filter['tickSize'])

            # анти-даблклик и защита от параллельных запусков
            if self._grid_in_progress:
                self.log("Сетка: постановка уже выполняется — игнорирую повторный вызов.")
                return
            self._grid_in_progress = True
            try:
                if hasattr(self, "place_grid_button") and self.place_grid_button:
                    self.place_grid_button.config(state="disabled")
            except Exception:
                pass

            # --- Лог точности ---
            self.log(f"Precision: qty_dec={qty_decimals}, price_dec={price_decimals}")

            def notional_ok(qty_f: float, price_f: float) -> bool:
                return (qty_f * price_f) >= min_notional if notional_filter else True

            # --- Собираем уровни из self.layer_entries ---
            layers = []
            for e in getattr(self, 'layer_entries', []):
                s = e.get().strip().replace(',', '.')
                if not s:
                    break  # пустое поле — дальше уровни не ставим
                try:
                    val = abs(float(s))
                except Exception:
                    break
                layers.append(val)

            if not layers:
                self.log("Сетка: не задан ни один уровень в 'Старт + Шаги'.")
                return

            current_price = float(self.client.get_symbol_ticker(symbol=symbol)['price'])

            # Цены уровней: первый — от рынка (0 => по рынку), далее — от предыдущего уровня
            prices_dec = []
            for i, pct in enumerate(layers):
                if i == 0:
                    target = current_price if pct == 0 else current_price * (1.0 - pct / 100.0)
                else:
                    base = float(prices_dec[-1])
                    target = base * (1.0 - pct / 100.0)
                prices_dec.append(q_tick(target, tick_size))

            # --- Собираем параметры уровней: qty и price ---
            orders_params = []  # список из пар (qty_str, price_str)
            quote_planned = 0.0

            current_qty = qty_start
            for price_dec in prices_dec:
                qty_dec = q_step(current_qty, step_size)
                qty = float(qty_dec)
                price = float(price_dec)
                if qty < min_qty or not notional_ok(qty, price):
                    current_qty *= martingale_coef  # ← важно: двигаем объём дальше по мартингейлу
                    continue
                orders_params.append((fmt(qty_dec, qty_decimals), fmt(price_dec, price_decimals)))
                quote_planned += qty * price
                current_qty *= martingale_coef

            if not orders_params:
                self.log("Сетка: после проверок уровни отсутствуют (minQty/notional).")
                return

            # --- Баланс-гейт: ставим уровни, пока хватает котируемой валюты ---
            quote_asset = info['quoteAsset']
            free_quote = float((self.client.get_asset_balance(asset=quote_asset) or {}).get('free', 0) or 0)

            # Лог: что именно планируем поставить
            self.log("Сетка → уровни:")
            for qty_str, price_str in orders_params:
                self.log(f"  BUY {symbol}: qty={qty_str} @ {price_str}")

            placed = 0
            spent  = 0.0
            for qty_str, price_str in orders_params:
                q = float(qty_str)
                p = float(price_str)
                notional = q * p
                if spent + notional > free_quote:
                    self.log(f"Сетка: не хватает {quote_asset} для уровня qty={qty_str} @ {price_str}; остановка постановки.")
                    break
                self.client.order_limit_buy(symbol=symbol, quantity=qty_str, price=price_str)
                placed += 1
                spent  += notional

            self.grid_orders_placed = placed > 0
            if self.grid_orders_placed and getattr(self, "add_orders_button", None):
                self.add_orders_button.config(state="disabled")
            self.cancel_orders_button.config(state="normal" if self.grid_orders_placed else "disabled")
            self.log(f"Сетка: поставлено уровней {placed}, бюджет использован ~{spent:.2f} {quote_asset} (планировалось ~{quote_planned:.2f}).")
            self.place_grid_button.config(state="disabled")


            # если включён авто-цикл — разрешим Стоп-цикл
            if getattr(self, "auto_cycle", False) and getattr(self, "stop_cycle_button", None):
                self.stop_cycle_button.config(state="normal")

            self.update_planned_total()


        except Exception as e:
            self.log(f"Ошибка при создании ордеров: {e}")
        finally:
            # если сетка не поставлена — снова разрешим кнопку
            try:
                if hasattr(self, "place_grid_button") and self.place_grid_button:
                    if not self.grid_orders_placed:
                        self.place_grid_button.config(state="normal")
            except Exception:
                pass
            self._grid_in_progress = False

    # =========================
    # ### СЕКЦИЯ 13: Авто цикл ###
    # =========================
    def toggle_auto_cycle(self):
        """Включает или выключает авто-цикл торговли."""
        if not self.client:
            self.log("Авто-цикл: нет подключения к бирже.")
            return

        # переключаем состояние автоцикла
        self.auto_cycle = not getattr(self, 'auto_cycle', False)
        self.stop_cycle_requested = False  # снимаем прежний стоп

        try:
            if self.auto_cycle:
                # включили авто-цикл
                self.auto_cycle_button.config(text="▶️ Auto-Start: ON", state="disabled")  # сама кнопка гасится
                self.stop_cycle_button.config(state="normal")
                self.log("Авто-цикл включён.")

                # если сетка ещё не стоит и нет активных BUY — стартуем сразу
                symbol = self.get_current_symbol().upper().strip()
                has_buy = False
                try:
                    open_orders = self.client.get_open_orders(symbol=symbol) or []
                    has_buy = any(o.get('side') == 'BUY' for o in open_orders)
                except Exception:
                    pass

                if not getattr(self, "grid_orders_placed", False) and not has_buy:
                    self.log("Авто-цикл: стартую с задержкой — выставлю сетку по текущим параметрам.")
                    try:
                        self.start_grid_with_delay(5000)
                    except Exception as e:
                        self.log(f"Авто-цикл: ошибка при старте сетки — {e}")
                else:
                    self.log("Авто-цикл: ВКЛ. Текущая сетка сохранена.")

            else:
                # выключили авто-цикл вручную (редкий сценарий)
                self.auto_cycle_button.config(text="▶️ Auto-Start", state="normal")
                self.stop_cycle_button.config(state="disabled")
                self._cancel_pending_grid_schedule(quiet=False)
                self.log("Авто-цикл выключен.")

        except Exception as e:
            self.log(f"Ошибка toggle_auto_cycle: {e}")


    def request_stop_cycle(self):
        # Пользователь хочет завершить текущий цикл и не начинать новый.
        self.stop_cycle_requested = True
        self.auto_cycle = False

        self._cancel_pending_grid_schedule(quiet=False)

        try:
            self.auto_cycle_button.config(text="▶️ Auto-Start", state="normal")
            self.stop_cycle_button.config(state="disabled")
        except Exception:
            pass

        self.log("Стоп цикл: новые сетки не будут ставиться после закрытия текущей позиции.")


    # =========================
    # ### СЕКЦИЯ 14: Выставление дополнительных ордеров  ###
    # =========================
    def add_more_orders(self):
        if not self.client:
            self.log("Добавление: нет соединения с биржей.")
            return

        symbol = self.get_current_symbol().upper().strip()

        # анти-даблклик
        if getattr(self, "_add_in_progress", False):
            self.log("Добавление: уже выполняется — игнорирую повторный вызов.")
            return
        self._add_in_progress = True

        try:
            info = self.get_symbol_info_cached(symbol)
            # --- фильтры ---
            price_filter = next(f for f in info['filters'] if f['filterType'] == 'PRICE_FILTER')
            lot_size     = next(f for f in info['filters'] if f['filterType'] == 'LOT_SIZE')
            tick_size    = float(price_filter['tickSize'])
            step_size    = float(lot_size['stepSize'])
            min_qty      = float(lot_size['minQty'])

            qty_decimals   = decimals_from_str(lot_size['stepSize'])
            price_decimals = decimals_from_str(price_filter['tickSize'])

            # --- notional ---
            notional_filter = next((f for f in info['filters']
                                    if f['filterType'] in ('NOTIONAL', 'MIN_NOTIONAL')), None)
            min_notional = float(notional_filter.get('minNotional')) if notional_filter else 0.0

            def notional_ok(qty_f: float, price_f: float) -> bool:
                return (qty_f * price_f) >= min_notional if notional_filter else True

            # --- параметры ---
            try:
                martingale_pct = float(self.martingale_entry.get().replace(',', '.') or 0.0)
            except Exception:
                martingale_pct = 0.0
            mcoef = 1 + martingale_pct / 100.0

            # --- параметр шага для ДОП. ордера (% от последней покупки) ---
            try:
                extra_pct = float((self.add_order_pct_entry.get() or "0").replace(",", "."))
            except Exception:
                extra_pct = 0.0

            if extra_pct <= 0:
                self.log("Доп. ордер: укажите положительный % шага.")
                return

            # --- последняя покупка В ЭТОЙ СЕССИИ (после старта сетки/гейта) ---
            cutoff_ts = max((self.grid_started_at or 0),
                            (getattr(self, "oco_gate_since_ts", 0) or 0)) or 0
            soft_cutoff_ms = cutoff_ts - 5000  # «мягкий» -5 c

            last_buy_qty   = None
            last_buy_price = None
            try:
                # используем тот же хелпер, что и для средней
                trades = self._fetch_trades_since(symbol, soft_cutoff_ms)
                for t in reversed(trades):
                    if t.get("isBuyer"):
                        last_buy_qty   = float(t.get("qty",   0) or 0.0)
                        last_buy_price = float(t.get("price", 0) or 0.0)
                        break
            except Exception:
                pass

            if not last_buy_qty or not last_buy_price:
                self.log("Доп. ордер: не удалось найти последнюю покупку в текущей сессии.")
                return

            # --- стартовый объём: СЛЕДУЮЩИЙ шаг по мартингейлу от последней покупки ---
            # mcoef уже посчитан выше в функции
            if mcoef <= 1.0:
                base_qty = last_buy_qty
            else:
                base_qty = last_buy_qty * mcoef       # классический «следующий уровень»
            start_qty_dec = q_step(base_qty, step_size)

            if float(start_qty_dec) < min_qty:
                self.log(f"Доп. ордер: расчётный объём слишком мал (qty<{min_qty}).")
                return

            # --- цена доп. ордера: extra_pct ВНИЗ от последней покупочной цены ---
            target_price = last_buy_price * (1.0 - extra_pct / 100.0)
            price_dec    = q_tick(target_price, tick_size)

            # --- свободный котируемый баланс ---
            quote_asset = info["quoteAsset"]
            try:
                bal_q = self.client.get_asset_balance(asset=quote_asset) or {}
                quote_free = float(bal_q.get("free", 0) or 0.0)
            except Exception:
                quote_free = 0.0

            # --- формируем ОДИН доп. ордер с учётом minQty / NOTIONAL / баланса ---
            orders_params = []
            spent = 0.0
            cur_qty = float(start_qty_dec)

            qty_dec  = q_step(cur_qty, step_size)
            qty_f    = float(qty_dec)
            price_f  = float(price_dec)

            if qty_f < min_qty or not notional_ok(qty_f, price_f):
                self.log("Доп. ордер: не проходит по minQty или MIN_NOTIONAL.")
                return

            need = qty_f * price_f
            if need > quote_free:
                self.log("Доп. ордер: недостаточно USDC на балансе.")
                return

            orders_params.append(
                (fmt(qty_dec,   qty_decimals),
                 fmt(price_dec, price_decimals))
            )
            spent += need


            for price_dec in prices_dec:
                qty_dec = q_step(cur_qty, step_size)
                qty_f   = float(qty_dec)
                price_f = float(price_dec)
                if qty_f < min_qty or not notional_ok(qty_f, price_f):
                    cur_qty *= mcoef
                    continue
                need = qty_f * price_f
                if spent + need > quote_free:
                    break
                orders_params.append((fmt(qty_dec, qty_decimals), fmt(price_dec, price_decimals)))
                spent += need
                cur_qty *= mcoef

            if not orders_params:
                self.log("Добавление: недостаточно средств или уровни невалидны — ничего не добавлено.")
                return

            # --- лог ---
            self.log("Добавление → уровни:")
            for qty_str, price_str in orders_params:
                self.log(f"  BUY {symbol}: qty={qty_str} @ {price_str}")

            # --- отправка ---
            placed = 0
            for qty_str, price_str in orders_params:
                try:
                    self.client.order_limit_buy(symbol=symbol, quantity=qty_str, price=price_str)
                    placed += 1
                except Exception as e:
                    self.log(f"Добавление: не удалось поставить {qty_str} @ {price_str}: {e}")

            if placed:
                self.grid_orders_placed = True
                if getattr(self, "cancel_orders_button", None):
                    self.cancel_orders_button.config(state="normal")
                if getattr(self, "add_orders_button", None):
                    self.add_orders_button.config(state="normal")  # остаётся активной
                self.log(f"Добавление: поставлено уровней {placed}, бюджет использован ~{spent:.2f} {quote_asset}.")
            else:
                self.log("Добавление: не удалось поставить ни одного уровня.")

        except Exception as e:
            self.log(f"Добавление: ошибка — {e}")
        finally:
            self._add_in_progress = False
            self.update_planned_total()

    # =========================
    # ### СЕКЦИЯ 15: Отмена лимитных ордеров на покупку - Средняя цена ###
    # =========================
    def cancel_buy_orders(self):
        if not self.client:
            return
        try:
            symbol = self.get_current_symbol().upper().strip()
            orders = self.client.get_open_orders(symbol=symbol)
            cancelled = 0
            for order in orders:
                if order['side'] == 'BUY':
                    self.client.cancel_order(symbol=symbol, orderId=order['orderId'])
                    cancelled += 1
            self.cancel_orders_button.config(state="disabled")
            self.grid_orders_placed = False
            self.update_avg_price()
            self.log(f"Сетка BUY: отменено ордеров {cancelled}.")
            
            # снова разрешаем ставить сетку
            try:
                if hasattr(self, "place_grid_button") and self.place_grid_button:
                    self.place_grid_button.config(state="disabled")
                if hasattr(self, "add_orders_button") and self.add_orders_button:
                    self.add_orders_button.config(state="disabled")  # ← отключаем дозакупку, сетки уже нет
            except Exception:
                pass


        except Exception as e:
            self.log(f"Не удалось отменить ордера: {e}")

            # Активируем кнопку "Установить сетку"
            self.grid_orders_placed = False
            try:
                if hasattr(self, "place_grid_button") and self.place_grid_button:
                    self.place_grid_button.config(state="disabled")
                if hasattr(self, "add_orders_button") and self.add_orders_button:
                    self.add_orders_button.config(state="disabled")
            except Exception:
                pass

    def update_avg_price(self):
        if not self.client:
            self.avg_price_label.config(text="-")
            return
        try:
            symbol = self.get_current_symbol().upper().strip()
            cutoff_ts = max(
                (self.grid_started_at or 0),
                (getattr(self, "oco_gate_since_ts", 0) or 0)
            ) or None
            soft_cutoff_ms = (cutoff_ts - 5000) if cutoff_ts is not None else None

            avg = self._calc_position_avg(symbol, soft_cutoff_ms)
            if avg is not None:
                self.avg_price_label.config(
                    text=f"{avg:.6f}",
                    fg="green",
                    font=("Arial", 10, "bold")
                    )
            else:
                self.avg_price_label.config(text="-")
        except Exception as e:
            self.avg_price_label.config(text=f"Ошибка: {e}")

    def _fetch_trades_since(self, symbol: str, cutoff_ts: int | None):
        """
        Загружает сделки по символу начиная с сохранённого baseline tradeId (если есть),
        чтобы не терять историю длинных сессий (лимит 500 на /myTrades).
        """
        limit = 1000
        params = {'symbol': symbol, 'limit': limit}
        last_known = None
        try:
            baseline_map = getattr(self, "_avg_trade_baseline", None) or {}
            last_known = baseline_map.get(symbol)
            if last_known is not None:
                params['fromId'] = int(last_known) + 1
        except Exception:
            pass

        trades = []
        paginate = 'fromId' in params
        while True:
            batch = self.client.get_my_trades(**params) or []
            if not batch:
                break
            trades.extend(batch)
            if not (paginate and len(batch) == limit):
                break
            last_id = batch[-1].get('id')
            if last_id is None:
                break
            next_from = int(last_id) + 1
            if params.get('fromId') == next_from:
                break
            params['fromId'] = next_from

        # Если не удалось использовать baseline по tradeId, отфильтруем старые сделки по времени
        if cutoff_ts is not None and last_known is None:
            safe_cutoff = int(cutoff_ts) - 60_000  # запас 60с на возможный дрейф локального времени
            trades = [t for t in trades if int(t.get('time', 0)) >= safe_cutoff]

        # Если раньше baseline отсутствовал, а теперь появились сделки — обновим его для будущих вызовов
        if last_known is None and trades:
            try:
                if not hasattr(self, "_avg_trade_baseline"):
                    self._avg_trade_baseline = {}
                max_id = max(int(t.get('id')) for t in trades if t.get('id') is not None)
                self._avg_trade_baseline[symbol] = max_id
            except Exception:
                pass

        trades.sort(key=lambda t: int(t.get('time', 0)))
        return trades

    def _calc_position_avg(self, symbol, cutoff_ts, debug=False):
        """
        Средняя себестоимость остатка позиции после cutoff_ts.
        Учитывает продажи и комиссию, списанную в базовой монете.
        Если debug=True — собирает пошаговые строки для _log_avg_debug.
        Возвращает float (avg) или None, если позиции нет.
        """
        info = self.get_symbol_info_cached(symbol)
        base_asset = info['baseAsset']
        trades = self._fetch_trades_since(symbol, cutoff_ts)

        pos = 0.0   # текущая позиция (в базовой монете)
        cost = 0.0  # суммарная стоимость позиции (в котируемой)
        used_rows = []  # для детального лога

        for t in trades:
            ts  = int(t.get('time', 0))
            side = 'BUY' if t.get('isBuyer') else 'SELL'
            q   = float(t['qty'])
            p   = float(t['price'])
            comm = float(t.get('commission', 0) or 0.0)
            comm_asset = t.get('commissionAsset', '')

            if side == 'BUY':
                cost += p * q
                pos  += q
                if comm_asset == base_asset and comm > 0:
                    pos = max(0.0, pos - comm)
                if debug:
                    used_rows.append((ts, side, q, p, comm, comm_asset, pos, cost))
            else:
                sell_q = min(pos, q)
                if sell_q > 0:
                    avg_before = cost / pos if pos > 0 else 0.0
                    cost -= avg_before * sell_q
                    pos  -= sell_q
                # комиссия базовой монетой уменьшает остаток
                if comm_asset == base_asset:
                    pos = max(0.0, pos - comm)
                if debug:
                    used_rows.append((ts, side, q, p, comm, comm_asset, pos, cost))

        if debug:
            self._log_avg_debug(symbol, cutoff_ts, used_rows, pos, cost)

        if pos > 0:
            return cost / pos
        return None
    
    # посчитать позицию с этой сессии
    def _calc_session_position(self, symbol: str, cutoff_ts: int | None):
        """
        Возвращает (pos, avg) только по сделкам С ПОСЛЕ cutoff_ts.
        pos — объём позиции в базовой монете, avg — средняя себестоимость или None если pos==0.
        Учитывает комиссию в базовой монете.
        """
        info = self.get_symbol_info_cached(symbol)
        base_asset = info['baseAsset']

        trades = self._fetch_trades_since(symbol, cutoff_ts)

        pos = 0.0
        cost = 0.0

        for t in trades:
            q = float(t.get('qty', 0) or 0)
            p = float(t.get('price', 0) or 0)
            is_buy = bool(t.get('isBuyer'))
            comm = float(t.get('commission', 0) or 0)
            comm_asset = t.get('commissionAsset')

            if is_buy:
                cost += q * p
                pos += q
                if comm_asset == base_asset:
                    pos = max(0.0, pos - comm)  # комиссия списана в базовой
            else:
                sell_q = min(pos, q)
                if sell_q > 0 and pos > 0:
                    avg_before = cost / pos
                    cost -= avg_before * sell_q
                    pos -= sell_q
                if comm_asset == base_asset:
                    pos = max(0.0, pos - comm)

        avg = (cost / pos) if pos > 0 else None
        return pos, avg



    def _log_avg_debug(self, symbol, cutoff_ts, rows, pos, cost):
        """
        Красиво пишет в лог сделки, попавшие в расчёт средней.
        Показываем первые 3 и последние 3 строки, чтобы не заспамить.
        """
        def _ts(ms):  # форматируем время
            try:
                return time.strftime("%H:%M:%S", time.localtime(int(ms) / 1000))
            except Exception:
                return str(ms)

        self.log(f"AVG[{symbol}] cutoff={cutoff_ts or '—'}, записей={len(rows)}")

        preview = rows[:3]
        tail    = rows[-3:] if len(rows) > 3 else []
        for ts, side, q, p, comm, comm_asset, pos_a, cost_a in preview:
            self.log(f"  {_ts(ts)}  {side:<4} q={q} p={p} fee={comm} {comm_asset or ''}  → pos={pos_a:.6f}, cost={cost_a:.6f}")

        if len(rows) > 6:
            self.log("  ... ...")

        for ts, side, q, p, comm, comm_asset, pos_a, cost_a in tail:
            self.log(f"  {_ts(ts)}  {side:<4} q={q} p={p} fee={comm} {comm_asset or ''}  → pos={pos_a:.6f}, cost={cost_a:.6f}")

        if pos > 0:
            avg = cost / pos
            self.log(f"AVG итог: pos={pos:.6f}, cost={cost:.6f}, avg={avg:.6f}")
        else:
            self.log("AVG итог: позиция=0 — средней нет")


    def log_avg_details(self):
        """Публичный хендлер для кнопки '🧾 Лог средней (детали)'."""
        if not self.client:
            self.log("AVG: нет подключения.")
            return
        symbol = self.get_current_symbol().upper().strip()
        cutoff_ts = max((self.grid_started_at or 0), (getattr(self, "oco_gate_since_ts", 0) or 0)) or None
        soft_cutoff_ms = (cutoff_ts - 5000) if cutoff_ts is not None else None
        try:
            avg = self._calc_position_avg(symbol, soft_cutoff_ms, debug=True)
            if avg is None:
                self.log("AVG: позиция отсутствует (или все сделки до cutoff).")
            else:
                self.log(f"AVG: текущая средняя цена = {avg:.6f}")
                # и заодно обновим лейбл
                self.avg_price_label.config(text=f"{avg:.6f}")
        except Exception as e:
            self.log(f"AVG: ошибка расчёта — {e}")

    # =========================
    # ### СЕКЦИЯ 16: OCO-мониторинг и автоматизация Take-Profit/Stop-Loss ###
    # =========================

    def cancel_existing_oco(self, symbol: str) -> None:
        """
        Отмена активных OCO по символу, даже если у клиента нет cancel_oco_order:
        1) если есть last_oco_order_list_id — пытаемся снять весь список,
        иначе — проходим все открытые OCO по символу;
        2) если cancel_oco_order недоступен/не сработал — снимаем дочерние заказы по orderId
        (и LIMIT, и STOP_LOСС_LIMIT).
        """
        try:
            # helper: отменить все дочерние ордера из структуры OCO
            def _cancel_children(oco_obj):
                children = []
                children += oco_obj.get("orders", []) or []
                children += oco_obj.get("orderReports", []) or []
                for ch in children:
                    oid = ch.get("orderId")
                    if oid:
                        try:
                            self.client.cancel_order(symbol=symbol, orderId=oid)
                        except Exception:
                            pass

            # 1) точечно по последнему списку
            if getattr(self, "last_oco_order_list_id", None):
                ok = False
                try:
                    if hasattr(self.client, "cancel_oco_order"):
                        self.client.cancel_oco_order(symbol=symbol, orderListId=self.last_oco_order_list_id)
                        ok = True
                except Exception:
                    pass
                if not ok:
                    # найдём нужный список и снимем детей вручную
                    try:
                        open_oco = self.client.get_open_oco_orders() or []
                        for oco in open_oco:
                            if oco.get("symbol") == symbol and oco.get("orderListId") == self.last_oco_order_list_id:
                                _cancel_children(oco)
                    except Exception:
                        pass
                self.last_oco_order_list_id = None

            # 2) подстраховка — снимем все открытые OCO по символу
            try:
                open_oco = self.client.get_open_oco_orders() or []
                for oco in open_oco:
                    if oco.get("symbol") != symbol:
                        continue
                    if hasattr(self.client, "cancel_oco_order"):
                        try:
                            self.client.cancel_oco_order(symbol=symbol, orderListId=oco["orderListId"])
                            continue
                        except Exception:
                            pass
                    _cancel_children(oco)
            except Exception:
                pass

            # 3) финальная «мётёлка»: если дочерние висят как обычные ордера
            try:
                open_orders = self.client.get_open_orders(symbol=symbol) or []
                for o in open_orders:
                    if o.get("side") == "SELL" and o.get("type") in ("LIMIT", "STOP_LOSS_LIMIT"):
                        try:
                            self.client.cancel_order(symbol=symbol, orderId=o["orderId"])
                        except Exception:
                            pass
            except Exception:
                pass

        except Exception:
            pass

    # =========================
    # ### СЕКЦИЯ Универсальный ресет сессии  ###
    # =========================   
    def _reset_session_ui_and_state(self):
        # UI
        try:
            self.avg_price_label.config(text="-", fg="black", font=("Arial", 10))
            if getattr(self, "refresh_trailing_btn", None):
                self.refresh_trailing_btn.config(state="disabled")
            if getattr(self, "log_avg_details_btn", None):
                self.log_avg_details_btn.config(state="disabled")
        except Exception:
            pass

        # внутренние маркеры/флаги
        self.grid_started_at = None
        self._last_activation_buy_ts = None
        self.oco_gate_since_ts = None
        self.last_oco_params = None
        self.last_oco_order_list_id = None
        self.oco_placed_at_ts = None
        self.oco_active = False
        self.oco_step_index = -1
        self.last_trigger_price = 0.0

        # «тихий» мониторинг — обнулить накопители
        if hasattr(self, "_last_mon_ratio"):
            self._last_mon_ratio = None
        if hasattr(self, "_last_mon_log_ts"):
            self._last_mon_log_ts = 0
            self._last_mon_bucket = None

        # baseline’ы средней
        try:
            if hasattr(self, "_avg_trade_baseline"):
                self._avg_trade_baseline.clear()
            if hasattr(self, "_avg_balance_baseline"):
                self._avg_balance_baseline.clear()
        except Exception:
            pass



    def oco_monitor_loop(self):
        """
        Неблокирующий GUI вариант:
        - вся сеть/расчёты выполняются в фоне (work)
        - UI обновляется только в done(actions)
        - локальный rate-limit сглаживает запросы
        - мягкий cutoff: -5с
        - тик + джиттер: ~1.0s ±150ms
        """
        # ▶ анти-реэнтри на уровне цикла
        if getattr(self, "_oco_busy", False):
            self.root.after(500, self.oco_monitor_loop)
            return
        self._oco_busy = True

        def _reschedule(delay=1000):
            # снимем busy-флаг и перепланируем с лёгким джиттером
            try:
                self._oco_busy = False
            except Exception:
                pass
            self.root.after(int(delay + random.randint(-150, 150)), self.oco_monitor_loop)
            return

        # если нет клиента — сразу перепланировать
        if not self.client:
            return _reschedule(2000)

        # ❗️важно: все обращения к виджетам делаем ЗДЕСЬ (UI-поток) и сохраняем снимки
        symbol = self.get_current_symbol().upper().strip()


        # ----- ФОН: ВСЯ сеть/расчёты -----
        def work():
            actions = []  # список команд для UI, например ('log', str), ('avg', 1.2345), ('state', {...}), ('reschedule', ms), ...
            try:
                # --- Стартовый гейт / cutoff ---
                cutoff_ts = max((self.grid_started_at or 0), (getattr(self, "oco_gate_since_ts", 0) or 0)) or None
                soft_cutoff_ms = (cutoff_ts - 5000) if cutoff_ts is not None else None

                # trades для гейта — с rate-limit
                if not self._rate_ok(f"trades_gate:{symbol}", 900):
                    return [('reschedule', 700)]

                try:
                    if soft_cutoff_ms is not None:
                        trades_for_gate = self._fetch_trades_since(symbol, soft_cutoff_ms)
                    else:
                        trades_for_gate = []
                except Exception:
                    trades_for_gate = []

                buy_ts = [int(t.get('time', 0)) for t in trades_for_gate if t.get('isBuyer') and float(t.get('qty', 0) or 0) > 0]
                has_filled_buy = len(buy_ts) > 0
                latest_buy_ts = max(buy_ts) if has_filled_buy else None

                if not has_filled_buy:
                    # --- Fallback по остатку на балансе ---
                    # ВАЖНО: если мы вообще ещё сетку не ставили (grid_orders_placed=False),
                    # то не надо пытаться "страховать" старые монеты, которые уже лежали на аккаунте.
                    # Иначе при старте он начнёт ставить OCO на ВСЁ, что найдёт.
                    # --- Fallback по остатку на балансе ---
                    if getattr(self, "grid_orders_placed", False):
                        info = self.get_symbol_info_cached(symbol)
                        lot  = next(f for f in info['filters'] if f['filterType'] == 'LOT_SIZE')
                        step = float(lot['stepSize'])
                        min_qty = float(lot['minQty'])
                        base = info['baseAsset']

                        bal = self.client.get_asset_balance(asset=base) or {}
                        qty_free   = float(bal.get('free', 0) or 0)
                        qty_locked = float(bal.get('locked', 0) or 0)
                        qty_total  = float(q_step(qty_free + qty_locked, step))

                        # baseline по остатку на старте сетки
                        baseline_qty = 0.0
                        try:
                            if hasattr(self, "_avg_balance_baseline"):
                                baseline_qty = float(self._avg_balance_baseline.get(base, 0.0) or 0.0)
                        except Exception:
                            pass

                        # BUY считаем обнаруженным ТОЛЬКО если текущий остаток
                        # стал значимо больше стартового (минимум на min_qty)
                        grown_qty = qty_total - baseline_qty
                        if grown_qty >= max(min_qty, step/2):
                            now_ts = int(time.time()*1000)
                            actions.append(('log', f"BUY обнаружен (по Δостатка {base}: +{grown_qty}). Активирую OCO-гейт."))
                            actions.append(('update_flags', {
                                '_last_activation_buy_ts': now_ts,
                                '_paused_no_buys_logged': False
                            }))
                        else:
                            # баланс не вырос относительно старта — значит покупок не было
                            return actions + [('reschedule', 1000)]
                    else:
                        # сетка ещё не ставилась — fallback по балансу не делаем
                        return actions + [('reschedule', 1000)]


                # Новый BUY → лог + показать среднюю
                if (latest_buy_ts is not None) and (latest_buy_ts != getattr(self, "_last_activation_buy_ts", None)):
                    actions.append(('update_flags', {'_last_activation_buy_ts': latest_buy_ts, 'oco_step_index': -1, '_paused_no_buys_logged': False}))
                    avg_now = self._calc_position_avg(symbol, soft_cutoff_ms)
                    if avg_now is not None:
                        actions.append(('avg', avg_now))
                        actions.append(('logavg_btn', 'normal'))
                        actions.append(('refresh_btn', 'normal'))
                        actions.append(('log', f"BUY обнаружен — ОСО мониторинг активирован. Средняя цена = {avg_now:.6f}"))
                        actions.append(('add_orders_btn_state', 'normal'))
                    else:
                        actions.append(('refresh_btn', 'normal'))
                        actions.append(('log', "BUY обнаружен — ОСО мониторинг активирован."))
                
                # --- Фильтры и точности (через кэш) ---
                info = self.get_symbol_info_cached(symbol)
                price_filter = next(f for f in info['filters'] if f['filterType'] == 'PRICE_FILTER')
                lot_size     = next(f for f in info['filters'] if f['filterType'] == 'LOT_SIZE')
                tick_size    = float(price_filter['tickSize'])
                step_size    = float(lot_size['stepSize'])
                min_qty      = float(lot_size['minQty'])
                base_asset   = info['baseAsset']
                qty_decimals   = decimals_from_str(lot_size['stepSize'])
                price_decimals = decimals_from_str(price_filter['tickSize'])
                notional_filter = next((f for f in info['filters'] if f['filterType'] in ('NOTIONAL', 'MIN_NOTIONAL')), None)
                min_notional = float(notional_filter.get('minNotional')) if notional_filter else 0.0
                def notional_ok(qty_f: float, price_f: float) -> bool:
                    return (qty_f * price_f) >= min_notional if notional_filter else True

                # анти-спам лог точностей
                prec_key = f"{symbol}:{qty_decimals}:{price_decimals}"
                if getattr(self, "_last_prec_key", None) != prec_key:
                    actions.append(('log', f"OCO precision: qty_dec={qty_decimals}, price_dec={price_decimals}"))
                    actions.append(('update_flags', {'_last_prec_key': prec_key}))

                # --- Проверка статуса предыдущего OCO ---
                if getattr(self, 'last_oco_order_list_id', None):
                    if not self._rate_ok(f"open_oco:{symbol}", 900):
                        return [('reschedule', 700)]
                    open_oco = self.client.get_open_oco_orders() or []
                    still_open = any(o.get('orderListId') == self.last_oco_order_list_id for o in open_oco)
                    if not still_open:
                        # попытка восстановить SL, если остался хвост LIMIT TP
                        try:
                            if not self._rate_ok(f"open_orders:{symbol}", 900):
                                return [('reschedule', 700)]
                            open_orders = self.client.get_open_orders(symbol=symbol) or []
                            last_tp_lim = float(self.last_oco_params.get("tp_limit", 0) or 0) if getattr(self, "last_oco_params", None) else 0.0
                            rem_order = None
                            if last_tp_lim:
                                for o in open_orders:
                                    if o.get("side") == "SELL" and o.get("type") == "LIMIT":
                                        op = float(o.get("price", 0) or 0)
                                        if abs(op - last_tp_lim) <= tick_size * 2:
                                            rem_order = o
                                            break
                            if rem_order:
                                o_orig = float(rem_order.get("origQty", 0) or 0)
                                o_exec = float(rem_order.get("executedQty", 0) or 0)
                                rem_qty = float(q_step(o_orig - o_exec if o_exec else o_orig, step_size))
                                if rem_qty >= min_qty and (notional_filter is None or (rem_qty * last_tp_lim) >= min_notional):
                                    try:
                                        self.client.cancel_order(symbol=symbol, orderId=rem_order["orderId"])
                                        actions.append(('log', f"Снят остаточный ОСО TP-лимит orderId={rem_order['orderId']} для восстановления SL."))
                                    except Exception as ce:
                                        actions.append(('log', f"Не удалось снять остаточный ОСО TP-лимит: {ce}"))
                                    lp = self.last_oco_params
                                    tp_stop_dec = q_tick(float(lp["tp_stop"]),  tick_size)
                                    tp_limit_dec = q_tick(float(lp["tp_limit"]), tick_size)
                                    sl_price_dec = q_tick(float(lp["sl_stop"]),  tick_size)
                                    sl_limit_dec = q_tick(float(lp["sl_limit"]), tick_size)

                                    # ограничим восстановление только сессионным остатком
                                    session_pos_now, _ = self._calc_session_position(symbol, soft_cutoff_ms)
                                    session_lim_dec = q_step(session_pos_now, step_size)
                                    session_lim = float(session_lim_dec)
                                    rem_qty = min(rem_qty, session_lim)
                                    if rem_qty < min_qty or (notional_filter and (rem_qty * last_tp_lim) < min_notional):
                                        # нечего восстанавливать в рамках сессии
                                        raise Exception("Сессионный остаток слишком мал для восстановления OCO")
                                    try:
                                        oco_resp = None
                                        try:
                                            oco_resp = self.client.create_oco_order(
                                                symbol=symbol, side="SELL",
                                                quantity=fmt(q_step(rem_qty, step_size), qty_decimals),
                                                aboveType="TAKE_PROFIT_LIMIT",
                                                aboveStopPrice=fmt(tp_stop_dec,  price_decimals),
                                                abovePrice=fmt(tp_limit_dec,     price_decimals),
                                                aboveTimeInForce="GTC",
                                                belowType="STOP_LOSS_LIMIT",
                                                belowStopPrice=fmt(sl_price_dec, price_decimals),
                                                belowPrice=fmt(sl_limit_dec,     price_decimals),
                                                belowTimeInForce="GTC",
                                            )
                                        except Exception:
                                            oco_resp = self.client.create_oco_order(
                                                symbol=symbol, side="SELL",
                                                quantity=fmt(q_step(rem_qty, step_size), qty_decimals),
                                                price=fmt(tp_limit_dec, price_decimals),
                                                stopPrice=fmt(sl_price_dec, price_decimals),
                                                stopLimitPrice=fmt(sl_limit_dec, price_decimals),
                                                stopLimitTimeInForce="GTC",
                                            )
                                        try:
                                            loi = oco_resp.get("orderListId")
                                        except Exception:
                                            loi = None
                                        actions.append(('update_flags', {'last_oco_order_list_id': loi, 'oco_active': True}))
                                        actions.append(('log', f"OCO: восстановлен на остаток qty={rem_qty} (orderListId={loi or '—'})."))
                                        actions.append(('refresh_btn', 'normal'))
                                        actions.append(('logavg_btn', 'normal'))

                                        return actions + [('reschedule', 1000)]
                                    except Exception as e_rec:
                                        actions.append(('log', f"OCO: не удалось восстановить связку на остаток: {e_rec}"))
                        except Exception:
                            pass

                        # лог о закрытии OCO + тип ноги
                        sell_note = ""
                        try:
                            cutoff_ts2 = (self.oco_placed_at_ts or getattr(self, "_last_activation_buy_ts", None) or (self.grid_started_at or 0))
                            if self._rate_ok(f"trades_sell:{symbol}", 900):
                                trades = self._fetch_trades_since(symbol, cutoff_ts2)
                                sells = [t for t in trades if not t.get('isBuyer')]
                                if sells:
                                    last = max(sells, key=lambda t: int(t.get('time', 0)))
                                    q = float(last.get('qty', 0) or 0)
                                    p = float(last.get('price', 0) or 0)
                                    leg = ""
                                    if getattr(self, "last_oco_params", None):
                                        tp_stop = float(self.last_oco_params.get('tp_stop', 0) or 0)
                                        tp_lim  = float(self.last_oco_params.get('tp_limit', 0) or 0)
                                        sl_stop = float(self.last_oco_params.get('sl_stop', 0) or 0)
                                        sl_lim  = float(self.last_oco_params.get('sl_limit', 0) or 0)
                                        tol = max(tick_size * 3, p * 0.0005)
                                        if abs(p - tp_lim) <= tol or abs(p - tp_stop) <= tol:
                                            leg = "TP"
                                        elif abs(p - sl_lim) <= tol or abs(p - sl_stop) <= tol:
                                            leg = "SL"
                                    sell_note = f" ({leg}) qty={q}, price={p:.6f}" if leg else f" qty={q}, price={p:.6f}"
                        except Exception:
                            pass

                        actions.append(('log', f"OCO исполнен.{sell_note} Перезапуск: ждём новую покупку."))
                        # Полный ресет + новый gate
                        actions.append(('update_flags', {
                            'last_oco_order_list_id': None,
                            'last_oco_params': None,
                            'oco_placed_at_ts': None,
                            'oco_active': False,
                            'oco_step_index': -1,
                            '_last_activation_buy_ts': None,
                            '_qty_warned_after_oco': False,
                            'oco_gate_since_ts': int(time.time()*1000),
                        }))
                        # Кнопки
                        actions.append(('ui_controls_after_oco_close', None))
                        actions.append(('refresh_btn', 'disabled'))
                        actions.append(('logavg_btn', 'disabled'))
                        actions.append(('reset_session_full', None))

                        # сброс базлайнов
                        try:
                            if hasattr(self, "_avg_trade_baseline"):
                                self._avg_trade_baseline.clear()
                            if hasattr(self, "_avg_balance_baseline"):
                                self._avg_balance_baseline.clear()
                        except Exception:
                            pass

                        # логика авто-цикла
                        if getattr(self, "stop_cycle_requested", False):
                            actions.append(('log', "Стоп цикл: позиция закрыта, новые сетки не выставляются."))
                            return actions + [('reschedule', 1000)]
                        if getattr(self, "auto_cycle", False):
                            actions.append(('auto_place_grid', symbol))
                            actions.append(('log', "Авто-цикл: позиция закрыта → через 5с поставлю новую сетку."))
                            return actions + [('reschedule', 1000)]
                        return actions + [('reschedule', 1000)]

                # --- Средняя и СЕССИОННЫЙ объём после "точки отсчёта" ---
                cutoff_ts = max((self.grid_started_at or 0), (getattr(self, "oco_gate_since_ts", 0) or 0)) or None
                soft_cutoff_ms = (cutoff_ts - 5000) if cutoff_ts is not None else None

                session_pos, avg_price = self._calc_session_position(symbol, soft_cutoff_ms)
                if not session_pos or avg_price is None:
                    actions.append(('avg', None))
                    actions.append(('update_flags', {
                        'oco_active': False,
                        'last_trigger_price': 0,
                        '_qty_warned_after_oco': False,
                    }))
                    return actions + [('reschedule', 1000)]

                qty_dec = q_step(session_pos, step_size)
                qty     = float(qty_dec)
                warn_already = getattr(self, '_qty_warned_after_oco', False)

                if qty < min_qty:
                    if not warn_already:
                        actions.append(('log', f"OCO: недостаточно {base_asset} для продажи (qty<{min_qty})."))
                    actions.append(('update_flags', {'_qty_warned_after_oco': True}))
                    return actions + [('reschedule', 1000)]

                actions.append(('update_flags', {'_qty_warned_after_oco': False}))
                actions.append(('avg', avg_price))

                # --- Параметры из UI ---
                try:
                    tp_pct       = max(0.0, float(self.tp_entry.get()) / 100.0)
                    sl_pct       = max(0.0, float(self.sl_entry.get()) / 100.0)
                    trigger_pct  = max(0.0, float(self.oco_trigger_entry.get()) / 100.0)
                    oco_step_pct = max(0.0, float(self.oco_step_entry.get()) / 100.0)
                except Exception:
                    return actions + [('reschedule', 1000)]

                stop_limit_extra = 0.001

                # --- Текущая цена ---
                if not self._rate_ok(f"ticker:{symbol}", 500):
                    return actions + [('reschedule', 500)]
                try:
                    current_price = float(self.client.get_symbol_ticker(symbol=symbol)['price'])
                except Exception:
                    return actions + [('reschedule', 1000)]

                ratio = (current_price / avg_price) - 1.0
                force = getattr(self, "_force_trailing_refresh", False)


                # ====== ТРОТТЛИНГ ЛОГА МОНИТОРИНГА ======
                # идея: не спамим «Мониторинг: ...» каждую секунду.
                now_ms = int(time.time() * 1000)

                # если ещё ни разу не логировали — залогируем первый
                last_log_ts = getattr(self, "_last_mon_log_ts", 0)
                last_ratio  = getattr(self, "_last_mon_ratio", None)

                should_log_mon = False

                # --- Тихий режим логов мониторинга ---
                # Логируем только если дельта заметно изменилась (≥0.5%) или прошёл 1 час
                last_ratio  = getattr(self, "_last_mon_ratio", None)
                last_log_ts = getattr(self, "_last_mon_log_ts", 0)
                now_ms = int(time.time() * 1000)

                should_log_mon = (
                    last_ratio is None
                    or abs(ratio - last_ratio) >= 0.005     # 0.5% изменение
                    or (now_ms - last_log_ts) > 3_600_000   # раз в 1 час даже без изменений
                )

                if should_log_mon:
                    actions.append((
                        'log',
                        f"Мониторинг: avg={avg_price:.6f}, price={current_price:.6f}, Δ={ratio*100:.2f}% (триггер {trigger_pct*100:.2f}%)."
                    ))
                    actions.append(('update_flags', {
                        '_last_mon_log_ts': now_ms,
                        '_last_mon_ratio': ratio,
                    }))

                # ====== КОНЕЦ ТРОТТЛИНГА ======

                if ratio < trigger_pct and not force:
                    return actions + [('reschedule', 2000)]


                k = 0
                if oco_step_pct > 0:
                    k = int((ratio - trigger_pct) // oco_step_pct)

                last_k = getattr(self, 'oco_step_index', -1)
                if force:
                    actions.append(('log', "Трейлинг: переустанавливаю OCO по новым параметрам."))
                need_place = force or (not getattr(self, 'oco_active', False) or k > last_k)

                # защита от пере-постановки
                if not need_place and k <= last_k:
                    if self._rate_ok(f"open_orders:{symbol}", 900):
                        try:
                            open_orders2 = self.client.get_open_orders(symbol=symbol) or []
                            has_sell_protect = any(
                                o.get('side') == 'SELL' and o.get('type') in ('LIMIT', 'STOP_LOSS_LIMIT')
                                for o in open_orders2
                            )
                        except Exception:
                            has_sell_protect = True
                        if has_sell_protect:
                            return [('reschedule', 1000)]
                    return [('reschedule', 1000)]

                # лог про ступени
                prev_k = last_k
                tp_eff = tp_pct + k * oco_step_pct
                sl_eff = sl_pct + k * oco_step_pct
                if prev_k >= 0:
                    prev_tp_eff = tp_pct + prev_k * oco_step_pct
                    prev_sl_eff = sl_pct + prev_k * oco_step_pct
                    actions.append(('log', f"OCO: ступень {prev_k}→{k}: TP/SL {prev_tp_eff*100:.1f}%/{prev_sl_eff*100:.1f}% → {tp_eff*100:.1f}%/{sl_eff*100:.1f}%"))
                else:
                    actions.append(('log', f"OCO: ступень k={k}: TP/SL от средней {tp_eff*100:.1f}% / {sl_eff*100:.1f}%"))

                # --- Цены ног OCO ---
                tp_stop_dec  = q_tick(avg_price * (1.0 + tp_eff), tick_size)
                tp_limit_dec = q_tick(float(tp_stop_dec) * (1.0 - 0.0002), tick_size)
                sl_price_dec = q_tick(avg_price * (1.0 + sl_eff), tick_size)
                sl_limit_dec = q_tick(float(sl_price_dec) * (1.0 - stop_limit_extra), tick_size)

                actions.append(('log',
                    f"OCO→ {symbol}: qty={fmt(qty_dec, qty_decimals)}, "
                    f"TP={fmt(tp_stop_dec, price_decimals)}|{fmt(tp_limit_dec, price_decimals)}, "
                    f"SL={fmt(sl_price_dec, price_decimals)}|{fmt(sl_limit_dec, price_decimals)} "
                    f"(от ср.: TP {tp_eff*100:.1f}% / SL {sl_eff*100:.1f}%)"
                ))

                # notional проверка
                tp_lim_f = float(tp_limit_dec)
                sl_lim_f = float(sl_limit_dec)
                if not (notional_ok(qty, tp_lim_f) and notional_ok(qty, sl_lim_f)):
                    actions.append(('log', f"OCO: объём слишком мал (notional<min) для TP={tp_lim_f} или SLlim={sl_lim_f}."))
                    actions.append(('update_flags', {'oco_step_index': k}))
                    return actions + [('reschedule', 1000)]

                # сохранить параметры текущего OCO
                actions.append(('update_flags', {
                    'last_oco_params': {
                        "tp_stop":  float(tp_stop_dec),
                        "tp_limit": float(tp_limit_dec),
                        "sl_stop":  float(sl_price_dec),
                        "sl_limit": float(sl_limit_dec),
                        "qty":      float(qty_dec),
                    },
                    'oco_placed_at_ts': int(time.time()*1000),
                    '_force_trailing_refresh': False,
                }))

                # снять текущий OCO (если был)
                try:
                    self.cancel_existing_oco(symbol)
                except Exception:
                    pass

                # перечитать доступный объём перед постановкой
                if not self._rate_ok(f"balance2:{base_asset}", 500):
                    return actions + [('reschedule', 600)]
                # Снова считаем сессионный остаток (он мог измениться)
                session_pos2, _ = self._calc_session_position(symbol, soft_cutoff_ms)
                qty_dec2 = q_step(session_pos2, step_size)
                qty2     = float(qty_dec2)

                if qty2 < min_qty:
                    actions.append(('log', f"OCO: после отмен объём сессии всё ещё мал (qty<{min_qty}) — пропускаем постановку."))
                    return actions + [('reschedule', 1000)]

                # повторная notional-проверка с qty2
                if not (notional_ok(qty2, tp_lim_f) and notional_ok(qty2, sl_lim_f)):
                    actions.append(('log', f"OCO: объём сессии слишком мал (notional<min) для TP={tp_lim_f} или SLlim={sl_lim_f}."))
                    actions.append(('update_flags', {'oco_step_index': k}))
                    return actions + [('reschedule', 1000)]

                # и ставим OCO с qty_dec2
                ...
                quantity=fmt(qty_dec2, qty_decimals),
                ...


                # постановка OCO (new → legacy)
                try:
                    oco_resp = None
                    try:
                        oco_resp = self.client.create_oco_order(
                            symbol=symbol, side='SELL',
                            quantity=fmt(qty_dec2, qty_decimals),
                            aboveType='TAKE_PROFIT_LIMIT',
                            aboveStopPrice=fmt(tp_stop_dec,  price_decimals),
                            abovePrice=fmt(tp_limit_dec,     price_decimals),
                            aboveTimeInForce='GTC',
                            belowType='STOP_LOSS_LIMIT',
                            belowStopPrice=fmt(sl_price_dec, price_decimals),
                            belowPrice=fmt(sl_limit_dec,     price_decimals),
                            belowTimeInForce='GTC',
                        )
                    except Exception as e_new:
                        oco_resp = self.client.create_oco_order(
                            symbol=symbol, side='SELL',
                            quantity=fmt(qty_dec2, qty_decimals),
                            price=fmt(tp_limit_dec, price_decimals),
                            stopPrice=fmt(sl_price_dec, price_decimals),
                            stopLimitPrice=fmt(sl_limit_dec, price_decimals),
                            stopLimitTimeInForce='GTC',
                        )
                    try:
                        loi = oco_resp.get('orderListId')
                    except Exception:
                        loi = None
                    actions.append(('update_flags', {'last_oco_order_list_id': loi}))
                    actions.append(('log', f"OCO выставлен: orderListId={loi or '—'}"))
                    actions.append(('refresh_btn', 'normal'))
                    actions.append(('logavg_btn', 'normal'))
                    actions.append(('add_orders_btn_state', 'disabled'))

                except Exception as e_place:
                    actions.append(('log', f"OCO: ошибка постановки — {e_place}"))
                    return actions + [('reschedule', 1000)]

                # снять BUY-ордера сетки после успешной постановки OCO
                cancelled = 0
                try:
                    if self._rate_ok(f"open_orders2:{symbol}", 900):
                        open_orders2 = self.client.get_open_orders(symbol=symbol) or []
                        for o in open_orders2:
                            if o.get('side') == 'BUY':
                                try:
                                    self.client.cancel_order(symbol=symbol, orderId=o['orderId'])
                                    cancelled += 1
                                    actions.append(('log', f"Сетка BUY: отменён orderId={o['orderId']} после постановки OCO."))
                                except Exception as ce:
                                    actions.append(('log', f"Сетка BUY: не удалось отменить orderId={o['orderId']}: {ce}"))
                except Exception as ge:
                    actions.append(('log', f"Сетка BUY: ошибка получения открытых ордеров: {ge}"))

                if cancelled > 0:
                    actions.append(('log', f"Сетка BUY: отмена завершена (отменено {cancelled})."))
                # кнопки + флаги после постановки
                actions.append(('ui_controls_after_grid_cancel', None))
                actions.append(('update_flags', {'oco_active': True, 'oco_step_index': k}))
                actions.append(('log', f"OCO: ступень k={k} (tp_eff={tp_eff*100:.2f}%, sl_eff={sl_eff*100:.2f}%)"))

                return actions + [('reschedule', 1000)]

            except Exception as e:
                return [('log', f"OCO: ошибка мониторинга — {e}"), ('reschedule', 1200)]

        # ----- UI: применяем actions -----
        def done(result):
            try:
                if isinstance(result, Exception):
                    self.log(f"OCO error: {result}")
                    return _reschedule(2000)

                actions = result or []
                # флажок: применялся ли reschedule
                scheduled = False

                for kind, payload in actions:
                    if kind == 'log':
                        self.log(payload)

                    elif kind == 'avg':
                        if payload is None:
                            self.avg_price_label.config(text="-")
                        else:
                            self.avg_price_label.config(text=f"{payload:.6f}")

                    elif kind == 'update_flags':
                        # безопасно выставим только ожидаемые поля
                        for k, v in (payload or {}).items():
                            setattr(self, k, v)

                    elif kind == 'ui_controls_after_oco_close':
                        try:
                            if getattr(self, "place_grid_button", None):
                                self.place_grid_button.config(state="normal")
                            if getattr(self, "add_orders_button", None):
                                self.add_orders_button.config(state="disabled")
                            if getattr(self, "auto_cycle_button", None):
                                if getattr(self, "auto_cycle", False):
                                    self.auto_cycle_button.config(state="disabled")
                                else:
                                    self.auto_cycle_button.config(state="normal")
                            if getattr(self, "stop_cycle_button", None):
                                self.stop_cycle_button.config(state="disabled")
                        except Exception:
                            pass

                    elif kind == 'auto_place_grid':
                        try:
                            self.start_grid_with_delay(5000)
                        except Exception as e_auto:
                            self.log(f"Авто-цикл: ошибка при автостарте сетки — {e_auto}")

                    elif kind == 'add_orders_btn_state':
                        if getattr(self, 'add_orders_button', None):
                            self.add_orders_button.config(state=payload)

                    elif kind == 'ui_controls_after_grid_cancel':
                        # сетка аннулирована
                        self.grid_orders_placed = False
                        try:
                            self.cancel_orders_button.config(state="disabled")
                            if getattr(self, "place_grid_button", None):
                                self.place_grid_button.config(state="disabled")
                            if getattr(self, "add_orders_button", None):
                                self.add_orders_button.config(state="disabled")
                        except Exception:
                            pass
                        
                    elif kind == 'refresh_btn':
                        try:
                            state = 'normal' if str(payload).lower() == 'normal' else 'disabled'
                            self.refresh_trailing_btn.config(state=state)
                        except Exception:
                            pass
                        
                    elif kind == 'logavg_btn':
                        try:
                            state = 'normal' if str(payload).lower() == 'normal' else 'disabled'
                            self.log_avg_details_btn.config(state=state)
                        except Exception:
                            pass

                    elif kind == 'reset_session_full':
                        # полный сброс UI и внутренних флагов сессии
                        try:
                            self._reset_session_ui_and_state()
                        except Exception:
                            pass

                    elif kind == 'reschedule':
                        scheduled = True
                        return _reschedule(int(payload))


                if not scheduled:
                    return _reschedule(2000)

            finally:
                pass

        # поехали
        self._bg(work, done)

    # =========================
    # ### СЕКЦИЯ 17: Обновление трейлинга вручную (перевыставить OCO по новым % во время торговли) ###
    # =========================
    def refresh_trailing(self):
        """
        Пометить текущую связку на перестановку под новые параметры TP/SL/шаг.
        Реальную замену сделает oco_monitor_loop при ближайшем тике.
        """
        if not self.client:
            self.log("Трейлинг: нет соединения с биржей.")
            return
        self._force_trailing_refresh = True
        self.log("Трейлинг: параметры обновлены в UI — переставлю OCO на новых уровнях.")
        # ускорим реакцию
        self.root.after(100, self.oco_monitor_loop)
        
# =========================
# ### СЕКЦИЯ 18: Точка входа и запуск GUI ###
# =========================
if __name__ == "__main__":
    root = tk.Tk()
    app = BinanceBotGUI(root)
    root.mainloop()
