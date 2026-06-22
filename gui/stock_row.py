import tkinter as tk
from core.calc import (
    display_name, BUY_GEAR_INFO, LOAD_PCT_MIN, LOAD_PCT_MAX,
    normalize_load_pct, load_pct_color, sell_pct_color, gap_color, fmt_price,
    calc_load_ladder, calc_buy_cascade, calc_sell_tiers, calc_gap_rate,
    auto_gear_params, select_auto_gear,
)
from gui.stepper import Stepper

# Readable blue for buy-trigger values (matches the chart's rescue lines)
_BUY_FG = '#3366CC'
# Readable, gear-differentiated blues for the selected buy-gear radio text
_BUY_SEL = {4: '#3A6EA5', 5: '#2C5C95', 6: '#1F4A85'}

# -- Fonts (1.3x scale for QHD) ----------------------------------------------
_F_NAME_KR = ('Segoe UI', 13, 'bold')   # Korean stocks are bold
_F_NAME_US = ('Segoe UI', 13)           # US stocks are plain
_F_LBL  = ('Segoe UI', 12)
_F_VAL  = ('Segoe UI', 12)
_F_OUT  = ('Segoe UI', 13, 'bold')
_F_SM   = ('Segoe UI', 10)
_F_SM_B = ('Segoe UI', 10, 'bold')
_F_BTN  = ('Segoe UI', 11, 'bold')

# Greys for a "muted" (state-inactive but informational) gear box
_MUTE_TITLE = '#C0C0C0'
_MUTE_BG    = '#F0F0F0'
_MUTE_FG    = '#9A9A9A'


class StockRow:
    """One unified stock card, used for both EMPTY and DEPLOYED stocks.

    The layout is identical for both states (title row + stats + 3-column buy/
    sell ladder on the left, and three gear boxes — Load | Buy | Sell — on the
    right). What differs is the strategy logic, driven by ``self.deployed``:

    * DEPLOYED — the buy ladder is the rescue cascade from the real avg cost
      (Buy 1/2/3) and the sells are real. The Load gear is greyed/disabled.
    * EMPTY — the buy ladder is the projected entry: Load (initial unit) plus
      two projected rescues (Buy 1/2), and the sells are *pseudo* (computed as
      if the load had filled). The Buy and Sell gears are muted but stay
      clickable so the pseudo lines can be explored.

    The parent grids ``self.frame``; the row does not place itself. The program
    still keeps deployed and empty rows in two separate lists.
    """

    def __init__(self, parent, row_num: int, pos: dict, deployed: bool,
                 get_unit_cash, on_graph, on_compute=None, on_order=None,
                 editable=True):
        self.deployed       = deployed
        self.editable       = editable
        self.ticker         = pos['ticker']
        self.tier           = pos.get('tier', 'Major')
        self.currency       = 'KRW' if self.ticker.endswith('.KS') else 'USD'
        self.get_unit_cash  = get_unit_cash
        self.on_graph       = on_graph
        self._on_compute_cb = on_compute
        self.on_order       = on_order
        self.current_price  = None
        self.peak_5d        = None
        self.volatility     = None
        self._army_pct      = None
        self._computing     = False

        # Chart data (filled by compute; safe defaults so a graph before the
        # first fetch still opens).
        self._anchor_label = 'Avg' if deployed else 'Load'
        self._anchor_price = None
        self._buy_lines    = []
        self._sell_lines   = []

        # -- Card frame --------------------------------------------------------
        self.frame = tk.Frame(parent, bd=1, relief='groove', padx=8, pady=3)

        # -- Input variables ---------------------------------------------------
        self.shares_var   = tk.StringVar(
            value=str(pos.get('shares', 0)) if pos.get('shares', 0) > 0 else '')
        self.avg_cost_var = tk.StringVar(value=self._fmt_init(pos.get('avg_cost', 0)))

        init_load = normalize_load_pct(pos.get('load_gear', 5))
        self.load_pct_var = tk.IntVar(value=-init_load)

        pct_init = pos.get('buy_pct', 5)
        if pct_init not in (4, 5, 6):
            pct_init = 5
        self.buy_pct_var = tk.IntVar(value=pct_init)

        # Default sell-tier activation: only T2 on; others off but clickable.
        self.t_active = [
            tk.BooleanVar(value=bool(pos.get(f't{i+1}_active', d)))
            for i, d in enumerate((False, True, False))]
        self.t_pct = [
            tk.IntVar(value=int(pos.get(f't{i+1}_pct', [4, 6, 8][i])))
            for i in range(3)]

        self.auto_var = tk.BooleanVar(value=bool(pos.get('auto_mode', True)))

        # Order-button latches (deployed only). Ephemeral UI state — not saved.
        self.buy_active  = tk.BooleanVar(value=False)
        self.sell_active = tk.BooleanVar(value=False)

        # -- Output variables --------------------------------------------------
        self.current_var  = tk.StringVar(value='--')
        self.gap_var      = tk.StringVar(value='--')
        self.cost_var     = tk.StringVar(value='--')
        self.army_pct_var = tk.StringVar(value='')
        self.vol_var      = tk.StringVar(value='V --')
        self.buy_info_var = [tk.StringVar(value='--') for _ in range(3)]
        self.t_info_var   = [tk.StringVar(value='--') for _ in range(3)]
        # Buy-ladder row labels differ by state (Load/Buy 1/Buy 2 vs Buy 1/2/3)
        self._buy_lbl_var = [tk.StringVar(value='') for _ in range(3)]

        self._build_title_row(row_num)
        self._build_body()

        # -- Reactive traces (after all widgets exist) -------------------------
        self.shares_var.trace_add('write', lambda *_: self._on_input_change())
        self.avg_cost_var.trace_add('write', lambda *_: self._on_input_change())
        self.load_pct_var.trace_add('write', lambda *_: self._on_input_change())
        for i in range(3):
            self.t_active[i].trace_add('write', lambda *_: self._on_input_change())
            self.t_pct[i].trace_add('write', lambda *_: self._on_input_change())
        self.auto_var.trace_add('write', lambda *_: self._on_input_change())

        # Initial styling pass (rows are computed on the first price fetch).
        self._refresh_gear_styles()
        self._update_auto_btn()
        self._update_vol_label()

    # ── Build: title row ──────────────────────────────────────────────────────

    def _build_title_row(self, row_num):
        r0 = tk.Frame(self.frame)
        r0.pack(fill='x', pady=(0, 2))

        name = display_name(self.ticker)
        if self.ticker.endswith('.KS'):
            name += ' (KR)'
        self._disp_name = name
        name_font = _F_NAME_KR if self.ticker.endswith('.KS') else _F_NAME_US
        self._name_lbl = tk.Label(r0, text=f"{row_num}. {name}",
                                  font=name_font, anchor='w')
        self._name_lbl.pack(side='left')

        # Army % (deployed only)
        tk.Label(r0, textvariable=self.army_pct_var,
                 font=_F_SM, fg='#888').pack(side='left', padx=(3, 6))

        tk.Button(r0, text='Graph', font=_F_SM, width=6,
                  command=lambda: self.on_graph(self.ticker)
                  ).pack(side='left', padx=(0, 6))

        self.auto_btn = tk.Checkbutton(
            r0, variable=self.auto_var, indicatoron=False, takefocus=0,
            width=7, font=_F_BTN, bd=1)
        self.auto_btn.pack(side='left', padx=(0, 6))
        tk.Label(r0, textvariable=self.vol_var, font=_F_SM, fg='#666'
                 ).pack(side='left', padx=(0, 12))

        ent_state = 'normal' if self.editable else 'readonly'
        tk.Label(r0, text='Avg Cost:', font=_F_LBL).pack(side='left')
        self.avg_entry = tk.Entry(r0, textvariable=self.avg_cost_var,
                                  width=10, justify='right', font=_F_VAL,
                                  state=ent_state)
        self.avg_entry.pack(side='left', padx=(2, 8))
        self.avg_entry.bind('<FocusOut>', lambda e: self._format_avg())

        tk.Label(r0, text='Shares:', font=_F_LBL).pack(side='left')
        tk.Entry(r0, textvariable=self.shares_var,
                 width=6, justify='right', font=_F_VAL, state=ent_state
                 ).pack(side='left', padx=(2, 8))

        if self.deployed:
            # Order toggles: latch on = "place" the ladder, latch off = withdraw.
            self.buy_btn = tk.Checkbutton(
                r0, text='Buy', variable=self.buy_active, indicatoron=False,
                width=5, font=_F_BTN, bd=1, takefocus=0,
                command=lambda: self._on_order_toggle('BUY'))
            self.buy_btn.pack(side='left', padx=(4, 2))
            self.sell_btn = tk.Checkbutton(
                r0, text='Sell', variable=self.sell_active, indicatoron=False,
                width=5, font=_F_BTN, bd=1, takefocus=0,
                command=lambda: self._on_order_toggle('SELL'))
            self.sell_btn.pack(side='left', padx=(0, 2))
            self._color_order_btns()
        elif self.editable:
            tk.Label(r0, text='(fill & Save to deploy)', font=_F_SM,
                     fg='#AAA').pack(side='left', padx=(2, 0))

    # ── Build: body (left stats+ladder | right gear boxes) ────────────────────

    def _build_body(self):
        body = tk.Frame(self.frame)
        body.pack(fill='x')

        left = tk.Frame(body)
        left.pack(side='left')

        # Stats: anchor value | current | gap
        stats = tk.Frame(left)
        stats.pack(fill='x')

        def _out(label, var, **kw):
            tk.Label(stats, text=label, fg='#888', font=_F_SM).pack(side='left')
            lbl = tk.Label(stats, textvariable=var, font=_F_OUT, **kw)
            lbl.pack(side='left', padx=(2, 12))
            return lbl

        cost_label = 'Total Cost:' if self.deployed else '5D High:'
        _out(cost_label, self.cost_var)
        _out('Current:', self.current_var)
        self.current_lbl = stats.winfo_children()[-1]  # the Current value label
        self.gap_lbl = _out('Gap:', self.gap_var, width=9)

        # Buy/sell ladder: 3 columns, buy on top, matching sell beneath.
        ladder = tk.Frame(left)
        ladder.pack(fill='x', pady=(2, 0))

        self.buy_info_lbl = []
        self.t_info_lbl   = []
        for col in range(3):
            cell = tk.Frame(ladder)
            cell.grid(row=0, column=col, sticky='w', padx=(0, 14))

            bf = tk.Frame(cell)
            bf.pack(anchor='w')
            tk.Label(bf, textvariable=self._buy_lbl_var[col], fg='#888',
                     font=_F_SM, width=7, anchor='w').pack(side='left')
            blbl = tk.Label(bf, textvariable=self.buy_info_var[col],
                            font=_F_OUT, fg=_BUY_FG)
            blbl.pack(side='left')
            self.buy_info_lbl.append(blbl)

            sf = tk.Frame(cell)
            sf.pack(anchor='w')
            tk.Label(sf, text=f'Sell T{col+1}:', fg='#888', font=_F_SM,
                     width=7, anchor='w').pack(side='left')
            slbl = tk.Label(sf, textvariable=self.t_info_var[col], font=_F_OUT)
            slbl.pack(side='left')
            self.t_info_lbl.append(slbl)

        # Gear boxes on the right: Load | Buy | Sell
        self._build_gear_boxes(body)

    def _build_gear_boxes(self, body):
        wrap = tk.Frame(body)
        wrap.pack(side='left', anchor='n', padx=(16, 0))
        self._gear_title = {}

        # -- Load gear (single stepper) ----------------------------------------
        load_box = tk.Frame(wrap)
        load_box.pack(side='left', anchor='n', padx=(0, 12))
        self._gear_title['load'] = tk.Label(load_box, text='Load',
                                            font=_F_SM, fg='#888')
        self._gear_title['load'].grid(row=0, column=0, columnspan=2)
        self.load_step = Stepper(load_box, self.load_pct_var,
                                 -LOAD_PCT_MAX, -LOAD_PCT_MIN,
                                 entry_width=4, value_font=_F_SM, btn_font=_F_SM)
        self.load_step.grid(row=1, column=0)
        tk.Label(load_box, text='%', font=_F_SM, fg='#888'
                 ).grid(row=1, column=1)

        # -- Buy gear (3 radios) -----------------------------------------------
        buy_box = tk.Frame(wrap)
        buy_box.pack(side='left', anchor='n', padx=(0, 12))
        self._gear_title['buy'] = tk.Label(buy_box, text='Buy',
                                           font=_F_SM, fg='#888')
        self._gear_title['buy'].grid(row=0, column=0, sticky='w')
        self._buy_radios = {}
        for disp, pct in enumerate([4, 5, 6]):
            ratio = BUY_GEAR_INFO[pct]['ratio']
            rb = tk.Radiobutton(
                buy_box, text=f"-{pct}%  ×{ratio}", value=pct,
                variable=self.buy_pct_var, font=_F_SM, anchor='w',
                takefocus=0, bd=0, pady=0, selectcolor='white',
                command=self._on_buy_change)
            rb.grid(row=disp + 1, column=0, sticky='w', pady=0)
            self._buy_radios[pct] = rb

        # -- Sell gear (3 tiers: toggle + stepper, T3 on top) ------------------
        sell_box = tk.Frame(wrap)
        sell_box.pack(side='left', anchor='n')
        self._gear_title['sell'] = tk.Label(sell_box, text='Sell',
                                            font=_F_SM, fg='#888')
        self._gear_title['sell'].grid(row=0, column=0, columnspan=3, sticky='w')
        self._steppers = [None, None, None]
        for disp, ti in enumerate([2, 1, 0]):
            grow = disp + 1
            tk.Label(sell_box, text=f'T{ti+1}', font=_F_SM
                     ).grid(row=grow, column=0, sticky='e', padx=(0, 1))
            tk.Checkbutton(sell_box, variable=self.t_active[ti], takefocus=0,
                           bd=0, pady=0).grid(row=grow, column=1)
            step = Stepper(sell_box, self.t_pct[ti], 1, 20,
                           entry_width=3, value_font=_F_SM, btn_font=_F_SM)
            step.grid(row=grow, column=2, sticky='w')
            self._steppers[ti] = step

    # ── Formatting ────────────────────────────────────────────────────────────

    def _fmt_init(self, avg):
        if avg is None or avg <= 0:
            return ''
        return f"{avg:,.0f}" if self.currency == 'KRW' else f"{avg:,.2f}"

    def _format_avg(self):
        try:
            v = float(self.avg_cost_var.get().replace(',', ''))
            self.avg_cost_var.set(
                f"{v:,.0f}" if self.currency == 'KRW' else f"{v:,.2f}")
        except ValueError:
            pass

    # ── Gear value getters ────────────────────────────────────────────────────

    def _get_load_pct(self) -> int:
        try:
            v = abs(self.load_pct_var.get())
        except tk.TclError:
            v = 5
        return max(LOAD_PCT_MIN, min(LOAD_PCT_MAX, v))

    def _get_buy_pct(self) -> int:
        try:
            v = int(self.buy_pct_var.get())
        except (tk.TclError, ValueError):
            v = 5
        return v if v in (4, 5, 6) else 5

    # ── Auto / manual ─────────────────────────────────────────────────────────

    def _update_auto_btn(self):
        if self.auto_var.get():
            self.auto_btn.config(text='AUTO', fg='white', bg='#2E8B57',
                                 selectcolor='#2E8B57')
        else:
            self.auto_btn.config(text='MANUAL', fg='black', bg='#E6B800',
                                 selectcolor='#E6B800')

    def _update_vol_label(self):
        vol = self.volatility
        if vol is None:
            self.vol_var.set('V --')
        elif self.auto_var.get():
            self.vol_var.set(f'V {vol:.1f}% → G{select_auto_gear(vol)}')
        else:
            self.vol_var.set(f'V {vol:.1f}%')

    def _apply_auto(self):
        """In auto mode, drive every gear from the 5-day volatility (the load
        gear too, so it is correct whether the card is empty now or later
        demotes back to empty)."""
        if self.auto_var.get() and self.volatility is not None:
            g = auto_gear_params(self.volatility)
            if self._get_load_pct() != g['load_pct']:
                self.load_pct_var.set(-g['load_pct'])
            if self._get_buy_pct() != g['buy_pct']:
                self.buy_pct_var.set(g['buy_pct'])
            for i in range(3):
                if self.t_pct[i].get() != g['tiers'][i]:
                    self.t_pct[i].set(g['tiers'][i])

    # ── Gear styling (enabled + state muting) ─────────────────────────────────

    def _refresh_gear_styles(self):
        auto = self.auto_var.get()

        # Load gear: only active for empty stocks; greyed/disabled when deployed.
        load_active = not self.deployed
        self.load_step.set_enabled(load_active and not auto)
        if load_active:
            pct = self._get_load_pct()
            self.load_step.set_value_color(load_pct_color(pct),
                                           'black' if pct <= 5 else 'white')
            self._gear_title['load'].config(fg='#888')
        else:
            self.load_step.set_value_color(_MUTE_BG, _MUTE_FG)
            self._gear_title['load'].config(fg=_MUTE_TITLE)

        # Buy gear: full color when deployed, muted (but clickable) when empty.
        buy_muted = not self.deployed
        for rb in self._buy_radios.values():
            rb.config(state='disabled' if auto else 'normal')
        self._update_buy_color(muted=buy_muted)
        self._gear_title['buy'].config(fg=_MUTE_TITLE if buy_muted else '#888')

        # Sell gear: same muting rule; tier toggles always editable.
        sell_muted = not self.deployed
        for i in range(3):
            self._steppers[i].set_enabled(not auto)
            self._color_spn(self._steppers[i], self.t_pct[i].get(),
                            muted=sell_muted)
        self._gear_title['sell'].config(fg=_MUTE_TITLE if sell_muted else '#888')

    def _update_buy_color(self, muted=False):
        sel = self._get_buy_pct()
        for pct, rb in self._buy_radios.items():
            if muted:
                rb.config(fg=_MUTE_FG, disabledforeground='#C8C8C8', font=_F_SM)
            elif pct == sel:
                rb.config(fg=_BUY_SEL[pct], disabledforeground=_BUY_SEL[pct],
                          font=_F_SM_B)
            else:
                rb.config(fg='#AAAAAA', disabledforeground='#CCCCCC',
                          font=_F_SM)

    def _color_spn(self, stepper, pct, muted=False):
        if muted:
            stepper.set_value_color(_MUTE_BG, _MUTE_FG)
        else:
            c = sell_pct_color(float(pct))
            stepper.set_value_color(c, 'white' if float(pct) >= 5 else 'black')

    def _on_buy_change(self, _=None):
        self._update_buy_color(muted=not self.deployed)
        self._on_input_change()

    # ── Compute ───────────────────────────────────────────────────────────────

    def _on_input_change(self):
        if not self._computing:
            self.compute()
            if self._on_compute_cb:
                self._on_compute_cb()

    def compute(self):
        if self._computing:
            return
        self._computing = True
        try:
            self._compute_impl()
        finally:
            self._computing = False

    def _enforce_order(self):
        try:
            p = [self.t_pct[i].get() for i in range(3)]
        except Exception:
            return
        if p[1] <= p[0]:
            self.t_pct[1].set(p[0] + 1); p[1] = p[0] + 1
        if p[2] <= p[1]:
            self.t_pct[2].set(p[1] + 1)

    def _compute_impl(self):
        self._apply_auto()
        self._enforce_order()
        self._refresh_gear_styles()
        self._update_auto_btn()
        self._update_vol_label()

        ccy  = self.currency
        pcts = [self.t_pct[i].get() for i in range(3)]
        acts = [self.t_active[i].get() for i in range(3)]
        buy_pct = self._get_buy_pct()

        try:
            shares = int(self.shares_var.get().replace(',', '') or 0)
        except ValueError:
            shares = 0
        try:
            avg_cost = float(self.avg_cost_var.get().replace(',', '') or 0)
        except ValueError:
            avg_cost = 0.0

        if self.deployed:
            buy_labels = ['Buy 1', 'Buy 2', 'Buy 3']
            anchor_price = avg_cost if avg_cost > 0 else None
            if shares > 0 and avg_cost > 0:
                self.cost_var.set(fmt_price(shares * avg_cost, ccy))
                buy_lines  = calc_buy_cascade(shares, avg_cost, buy_pct, 3)
                sell_lines = calc_sell_tiers(shares, avg_cost, pcts, acts)
            else:
                self.cost_var.set('--')
                buy_lines  = [{'price': None, 'qty': None} for _ in range(3)]
                sell_lines = [{'price': None, 'qty': None} for _ in range(3)]
        else:
            buy_labels = ['Load', 'Buy 1', 'Buy 2']
            if self.peak_5d and self.peak_5d > 0:
                buy_lines, load_price, load_shares = calc_load_ladder(
                    self.peak_5d, self._get_load_pct(), buy_pct,
                    self.get_unit_cash(), rescues=2)
                anchor_price = load_price if load_price > 0 else None
                sell_lines = calc_sell_tiers(load_shares, load_price, pcts, acts)
                self.cost_var.set(fmt_price(self.peak_5d, ccy))
            else:
                anchor_price = None
                buy_lines  = [{'price': None, 'qty': None} for _ in range(3)]
                sell_lines = [{'price': None, 'qty': None} for _ in range(3)]
                self.cost_var.set('--')

        # Current price + gap vs the anchor (avg cost or load price)
        if self.current_price:
            self.current_var.set(fmt_price(self.current_price, ccy))
        else:
            self.current_var.set('--')

        if self.current_price and anchor_price:
            gap = calc_gap_rate(self.current_price, anchor_price)
            self.gap_var.set(f"{gap:+.2f}%")
            self.gap_lbl.config(fg=gap_color(gap))
        else:
            self.gap_var.set('--')
            self.gap_lbl.config(fg='black')

        # Empty stocks turn the Current value green once price is at/below the
        # load trigger (the entry condition is met).
        if (not self.deployed and anchor_price and self.current_price
                and self.current_price <= anchor_price):
            self.current_lbl.config(fg='#006400')
        else:
            self.current_lbl.config(fg='black')

        # Fill the ladder
        for i in range(3):
            self._buy_lbl_var[i].set(f'{buy_labels[i]}:')
            e = buy_lines[i]
            if e['price'] is not None:
                self.buy_info_var[i].set(f"{fmt_price(e['price'], ccy)} × {e['qty']}")
                self.buy_info_lbl[i].config(fg=_BUY_FG)
            else:
                self.buy_info_var[i].set('--')
                self.buy_info_lbl[i].config(fg='#CCC')
            s = sell_lines[i]
            if s['price'] is not None:
                self.t_info_var[i].set(f"{fmt_price(s['price'], ccy)} × {s['qty']}")
                self.t_info_lbl[i].config(fg='black')
            else:
                self.t_info_var[i].set('--')
                self.t_info_lbl[i].config(fg='#CCC')

        # Stash chart data
        self._anchor_label = 'Avg' if self.deployed else 'Load'
        self._anchor_price = anchor_price
        self._buy_lines = [(buy_labels[i], buy_lines[i]['price'],
                            buy_lines[i]['qty']) for i in range(3)]
        self._sell_lines = [(f"+{pcts[i]}%", sell_lines[i]['price'],
                             sell_lines[i]['qty'])
                            for i in range(3) if sell_lines[i]['price'] is not None]

    # ── Public API ────────────────────────────────────────────────────────────

    def set_row_num(self, n: int):
        self._name_lbl.config(text=f"{n}. {self._disp_name}")

    # -- Order buttons (deployed only) ----------------------------------------

    def _color_order_btns(self):
        """Buy latches blue, Sell latches red when active; grey when off."""
        if not self.deployed:
            return
        self.buy_btn.config(
            bg=('#3366CC' if self.buy_active.get() else 'SystemButtonFace'),
            fg=('white' if self.buy_active.get() else 'black'),
            selectcolor='#3366CC')
        self.sell_btn.config(
            bg=('#CC3333' if self.sell_active.get() else 'SystemButtonFace'),
            fg=('white' if self.sell_active.get() else 'black'),
            selectcolor='#CC3333')

    def _on_order_toggle(self, side: str):
        self._color_order_btns()
        if self.on_order:
            active = (self.buy_active if side == 'BUY' else self.sell_active).get()
            self.on_order(self, side, active)

    def set_order_active(self, side: str, active: bool):
        """Let the controller force a latch state (e.g. block a 0-share sell)."""
        (self.buy_active if side == 'BUY' else self.sell_active).set(bool(active))
        self._color_order_btns()

    def order_intents(self, side: str) -> list:
        """The orders this card's current ladder represents on the given side:
        BUY = the 3 buy lines, SELL = the active sell tiers. Each is
        {ticker, side, label, price, qty, currency}."""
        lines = self._buy_lines if side == 'BUY' else self._sell_lines
        out = []
        for label, price, qty in lines:
            if price is None or not qty:
                continue
            out.append({'ticker': self.ticker, 'side': side, 'label': label,
                        'price': price, 'qty': int(qty), 'currency': self.currency})
        return out

    def current_shares(self) -> int:
        try:
            return int(self.shares_var.get().replace(',', '') or 0)
        except ValueError:
            return 0

    def set_army_pct(self, pct):
        self._army_pct = pct
        self.army_pct_var.set(
            f"({pct:.1f}%)" if (pct is not None and self.deployed) else '')

    def update_live(self, price=None, peak_5d=None, closes_5d=None,
                    volatility=None):
        if price is not None:
            self.current_price = price
        if peak_5d is not None:
            self.peak_5d = peak_5d
        if volatility is not None:
            self.volatility = volatility

    def chart_data(self) -> dict:
        return {
            'anchor_label': self._anchor_label,
            'anchor_price': self._anchor_price,
            'buy_lines':    list(self._buy_lines),
            'sell_lines':   list(self._sell_lines),
        }

    def get_state(self) -> dict:
        try:
            shares = int(self.shares_var.get().replace(',', '') or 0)
        except ValueError:
            shares = 0
        try:
            avg_cost = float(self.avg_cost_var.get().replace(',', '') or 0)
        except ValueError:
            avg_cost = 0.0
        return {
            'ticker':     self.ticker,
            'tier':       self.tier,
            'is_deployed': self.deployed,
            'shares':     shares,
            'avg_cost':   avg_cost,
            'cost_basis': shares * avg_cost,
            'load_gear':  self._get_load_pct(),
            'buy_pct':    self._get_buy_pct(),
            't1_pct':     self.t_pct[0].get(),
            't2_pct':     self.t_pct[1].get(),
            't3_pct':     self.t_pct[2].get(),
            't1_active':  self.t_active[0].get(),
            't2_active':  self.t_active[1].get(),
            't3_active':  self.t_active[2].get(),
            'auto_mode':  self.auto_var.get(),
        }
