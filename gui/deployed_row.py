import tkinter as tk
from core.calc import (
    display_name, BUY_GEAR_INFO,
    sell_pct_color, gap_color, fmt_price,
    calc_buy_cascade, calc_sell_tiers, calc_gap_rate,
    select_auto_gear, auto_gear_params,
)
from gui.stepper import Stepper

# Readable blue for buy-trigger values (matches the chart's rescue lines)
_BUY_FG = '#3366CC'
# Readable, gear-differentiated blues for the selected buy-gear radio text
_BUY_SEL = {4: '#3A6EA5', 5: '#2C5C95', 6: '#1F4A85'}

# -- Fonts (1.3x scale for QHD) ----------------------------------------------
_F_NAME_MAJOR = ('Segoe UI', 13, 'bold')
_F_NAME_MINOR = ('Segoe UI', 13)
_F_LBL  = ('Segoe UI', 12)
_F_VAL  = ('Segoe UI', 12)
_F_OUT  = ('Segoe UI', 13, 'bold')
_F_SM   = ('Segoe UI', 10)
_F_SM_B = ('Segoe UI', 10, 'bold')
_F_BTN  = ('Segoe UI', 11, 'bold')


class DeployedRow:
    """Card frame for one deployed stock. Reactive traces recompute on
    input change; compute() is also called externally on Save & Refresh.
    The parent grids self.frame (2-column card layout)."""

    def __init__(self, parent, row_num: int, pos: dict,
                 on_graph, on_compute=None):
        self.ticker         = pos['ticker']
        self.tier           = pos['tier']
        self.currency       = 'KRW' if self.ticker.endswith('.KS') else 'USD'
        self.current_price  = None
        self.volatility     = None
        self._army_pct      = None
        self._on_compute_cb = on_compute
        self._computing     = False

        # -- Card frame (parent grids this; the row does not self-place) -------
        self.frame = tk.Frame(parent, bd=1, relief='groove', padx=8, pady=3)

        # -- Variables - inputs ------------------------------------------------
        self.shares_var    = tk.StringVar(
            value=str(pos['shares']) if pos['shares'] > 0 else '')
        self.avg_cost_var  = tk.StringVar(
            value=self._fmt_init(pos['avg_cost']))

        pct_init = pos.get('buy_pct', 5)
        if pct_init not in (4, 5, 6):
            pct_init = 5
        self.buy_pct_var = tk.IntVar(value=pct_init)

        self.t_active = [
            tk.BooleanVar(value=bool(pos.get(f't{i+1}_active', True)))
            for i in range(3)]
        self.t_pct = [
            tk.IntVar(value=int(pos.get(f't{i+1}_pct', [4, 6, 8][i])))
            for i in range(3)]

        self.auto_var = tk.BooleanVar(value=bool(pos.get('auto_mode', True)))

        # -- Variables - outputs -----------------------------------------------
        self.current_var   = tk.StringVar(value='--')
        self.gap_var       = tk.StringVar(value='--')
        self.cost_var      = tk.StringVar(value='--')
        self.buy_info_var  = [tk.StringVar(value='--') for _ in range(3)]
        self.t_info_var    = [tk.StringVar(value='--') for _ in range(3)]
        self.army_pct_var  = tk.StringVar(value='')
        self.vol_var       = tk.StringVar(value='V --')

        # -- ROW 0: title (+ army%), Graph, Avg Cost, Shares -------------------
        r0 = tk.Frame(self.frame)
        r0.pack(fill='x', pady=(0, 2))

        name = display_name(self.ticker)
        if self.ticker.endswith('.KS'):
            name += ' (KR)'
        name_font = _F_NAME_MAJOR if self.tier == 'Major' else _F_NAME_MINOR
        tk.Label(r0, text=f"{row_num}. {name}",
                 font=name_font, anchor='w').pack(side='left')
        tk.Label(r0, textvariable=self.army_pct_var,
                 font=_F_SM, fg='#888').pack(side='left', padx=(3, 6))
        tk.Button(r0, text='Graph', font=_F_SM, width=6,
                  command=lambda: on_graph(self.ticker)).pack(side='left', padx=(0, 6))

        # Auto / Manual lock — when AUTO the buy/sell gear is chosen from
        # volatility and locked; click to switch to MANUAL editing.
        self.auto_btn = tk.Checkbutton(
            r0, variable=self.auto_var, indicatoron=False, takefocus=0,
            width=7, font=_F_BTN, bd=1)
        self.auto_btn.pack(side='left', padx=(0, 6))
        tk.Label(r0, textvariable=self.vol_var, font=_F_SM, fg='#666'
                 ).pack(side='left', padx=(0, 12))

        tk.Label(r0, text='Avg Cost:', font=_F_LBL).pack(side='left')
        self.avg_entry = tk.Entry(r0, textvariable=self.avg_cost_var,
                                  width=10, justify='right', font=_F_VAL)
        self.avg_entry.pack(side='left', padx=(2, 8))
        self.avg_entry.bind('<FocusOut>', lambda e: self._format_avg())

        tk.Label(r0, text='Shares:', font=_F_LBL).pack(side='left')
        tk.Entry(r0, textvariable=self.shares_var,
                 width=6, justify='right', font=_F_VAL
                 ).pack(side='left', padx=(2, 8))

        # -- BODY: left computed ladder | right unified gear box ---------------
        body = tk.Frame(self.frame)
        body.pack(fill='x')

        # Right: gear box. Buy radios (one selectable) | sell tiers (toggle +
        # stepper). Higher level on top: -4% over -6%, T3 over T1. In auto mode
        # the gear is chosen from volatility and the buy radios / sell steppers
        # are locked, but the tier toggles stay editable so you can show only
        # the tiers you want. (The Auto/Manual lock + volatility readout live
        # up on the title row.)
        gear = tk.Frame(body)

        tk.Label(gear, text='Buy Gear', font=_F_SM, fg='#888'
                 ).grid(row=0, column=0, sticky='w', pady=0)
        tk.Label(gear, text='Sell Gear', font=_F_SM, fg='#888'
                 ).grid(row=0, column=2, columnspan=3, sticky='w', padx=(10, 0), pady=0)

        self._buy_radios = {}
        for disp, pct in enumerate([4, 5, 6]):
            ratio = BUY_GEAR_INFO[pct]['ratio']
            rb = tk.Radiobutton(
                gear, text=f"-{pct}%  ×{ratio}", value=pct,
                variable=self.buy_pct_var, font=_F_SM, anchor='w',
                takefocus=0, bd=0, pady=0, selectcolor='white',
                command=self._on_buy_change)
            rb.grid(row=disp + 1, column=0, sticky='w', pady=0)
            self._buy_radios[pct] = rb

        tk.Frame(gear, width=1, bg='#D0D0D0'
                 ).grid(row=1, column=1, rowspan=3, sticky='ns', padx=6)

        self._steppers = [None, None, None]
        for disp, ti in enumerate([2, 1, 0]):   # T3 top ... T1 bottom
            grow = disp + 1
            tk.Label(gear, text=f'T{ti+1}', font=_F_SM
                     ).grid(row=grow, column=2, sticky='e', padx=(10, 1), pady=0)
            tk.Checkbutton(gear, variable=self.t_active[ti], takefocus=0,
                           bd=0, pady=0
                           ).grid(row=grow, column=3, pady=0)
            step = Stepper(gear, self.t_pct[ti], 1, 20,
                           entry_width=3, value_font=_F_SM, btn_font=_F_SM)
            step.grid(row=grow, column=4, sticky='w', pady=0)
            self._steppers[ti] = step
            self._color_spn(step, self.t_pct[ti].get())

        self._update_buy_color()

        # Left: summary stats + buy/sell ladder. Packed before the gear box so
        # the gear sits just to its right (small gap) instead of far-right.
        left = tk.Frame(body)
        left.pack(side='left')
        gear.pack(side='left', anchor='n', padx=(20, 0))

        def _out(parent, label, var, **kw):
            tk.Label(parent, text=label, fg='#888', font=_F_SM).pack(side='left')
            lbl = tk.Label(parent, textvariable=var, font=_F_OUT, **kw)
            lbl.pack(side='left', padx=(2, 12))
            return lbl

        stats = tk.Frame(left)
        stats.pack(fill='x')
        _out(stats, 'Total Cost:', self.cost_var)
        _out(stats, 'Current:', self.current_var)
        self.gap_lbl = _out(stats, 'Gap:', self.gap_var, width=9)

        # Buy/sell ladder: cascading buy triggers on top, the matching sell
        # tier directly beneath each, aligned in 3 columns.
        ladder = tk.Frame(left)
        ladder.pack(fill='x', pady=(2, 0))

        self.buy_info_lbl = []
        self.t_info_lbl   = []
        for col in range(3):
            cell = tk.Frame(ladder)
            cell.grid(row=0, column=col, sticky='w', padx=(0, 16))

            bf = tk.Frame(cell)
            bf.pack(anchor='w')
            tk.Label(bf, text=f'Buy {col+1}:', fg='#888', font=_F_SM,
                     width=8, anchor='w').pack(side='left')
            blbl = tk.Label(bf, textvariable=self.buy_info_var[col],
                            font=_F_OUT, fg=_BUY_FG)
            blbl.pack(side='left')
            self.buy_info_lbl.append(blbl)

            sf = tk.Frame(cell)
            sf.pack(anchor='w')
            tk.Label(sf, text=f'Sell T{col+1}:', fg='#888', font=_F_SM,
                     width=8, anchor='w').pack(side='left')
            slbl = tk.Label(sf, textvariable=self.t_info_var[col], font=_F_OUT)
            slbl.pack(side='left')
            self.t_info_lbl.append(slbl)

        # -- Reactive traces (added after all widgets are built) ---------------
        self.shares_var.trace_add('write', lambda *_: self._on_input_change())
        self.avg_cost_var.trace_add('write', lambda *_: self._on_input_change())
        for i in range(3):
            self.t_active[i].trace_add('write', lambda *_: self._on_input_change())
            self.t_pct[i].trace_add('write', lambda *_: self._on_input_change())
        self.auto_var.trace_add('write', lambda *_: self._on_input_change())

        # Set the lock/button/label state now; deployed rows are not computed
        # until the first price fetch arrives.
        self._apply_auto()

    # -- Input change handler --------------------------------------------------

    def _on_input_change(self):
        if not self._computing:
            self.compute()
            if self._on_compute_cb:
                self._on_compute_cb()

    # -- Formatting ------------------------------------------------------------

    def _fmt_init(self, avg):
        if avg <= 0:
            return ''
        return f"{avg:,.0f}" if self.currency == 'KRW' else f"{avg:,.2f}"

    def _format_avg(self):
        try:
            v = float(self.avg_cost_var.get().replace(',', ''))
            self.avg_cost_var.set(
                f"{v:,.0f}" if self.currency == 'KRW' else f"{v:,.2f}")
        except ValueError:
            pass

    # -- Color helpers ---------------------------------------------------------

    def _get_buy_pct(self) -> int:
        try:
            v = int(self.buy_pct_var.get())
        except (tk.TclError, ValueError):
            v = 5
        return v if v in (4, 5, 6) else 5

    def _update_buy_color(self):
        """Selected buy gear shows in a bright gear-blue, the rest are greyed.
        disabledforeground is kept in sync so the selection stays legible when
        the radios are locked in auto mode."""
        sel = self._get_buy_pct()
        for pct, rb in self._buy_radios.items():
            if pct == sel:
                rb.config(fg=_BUY_SEL[pct], disabledforeground=_BUY_SEL[pct],
                          font=_F_SM_B)
            else:
                rb.config(fg='#AAAAAA', disabledforeground='#CCCCCC',
                          font=_F_SM)

    def _on_buy_change(self, _=None):
        self._update_buy_color()
        self._on_input_change()

    # -- Auto-mode helpers -----------------------------------------------------

    def _update_auto_btn(self):
        """Reflect the auto/manual state on the toggle button."""
        if self.auto_var.get():
            self.auto_btn.config(text='AUTO', fg='white', bg='#2E8B57',
                                 selectcolor='#2E8B57')
        else:
            self.auto_btn.config(text='MANUAL', fg='black', bg='#E6B800',
                                 selectcolor='#E6B800')

    def _update_vol_label(self):
        # The adjacent AUTO/MANUAL button shows the mode, so the label only
        # carries the volatility (and the gear it maps to in auto).
        vol = self.volatility
        if vol is None:
            self.vol_var.set('V --')
        elif self.auto_var.get():
            self.vol_var.set(f'V {vol:.1f}% → G{select_auto_gear(vol)}')
        else:
            self.vol_var.set(f'V {vol:.1f}%')

    def _set_gear_enabled(self, enabled: bool):
        """Lock (auto) or unlock (manual) the buy radios and sell-tier
        steppers. The tier on/off checkboxes always stay editable."""
        state = 'normal' if enabled else 'disabled'
        for rb in self._buy_radios.values():
            rb.config(state=state)
        for step in self._steppers:
            step.set_enabled(enabled)

    def _apply_auto(self):
        """In auto mode, drive the buy gear and sell-tier percents from the
        5-day volatility and lock those controls; manual mode leaves them be."""
        auto = self.auto_var.get()
        if auto and self.volatility is not None:
            g = auto_gear_params(self.volatility)
            if self._get_buy_pct() != g['buy_pct']:
                self.buy_pct_var.set(g['buy_pct'])
            for i in range(3):
                if self.t_pct[i].get() != g['tiers'][i]:
                    self.t_pct[i].set(g['tiers'][i])
        self._update_buy_color()
        self._set_gear_enabled(not auto)
        self._update_auto_btn()
        self._update_vol_label()

    def _color_spn(self, stepper, pct):
        c = sell_pct_color(float(pct))
        fg = 'white' if float(pct) >= 5 else 'black'
        stepper.set_value_color(c, fg)

    # -- Public API ------------------------------------------------------------

    def update_live(self, price: float = None, volatility: float = None):
        if price is not None:
            self.current_price = price
        if volatility is not None:
            self.volatility = volatility

    def set_army_pct(self, pct):
        self._army_pct = pct
        self.army_pct_var.set(f"({pct:.1f}%)" if pct is not None else '')

    def compute(self):
        """Recompute all output fields. Guarded against recursive calls."""
        if self._computing:
            return
        self._computing = True
        try:
            self._compute_impl()
        finally:
            self._computing = False

    def _compute_impl(self):
        # Auto mode picks the gear from volatility and locks the controls.
        self._apply_auto()
        # Enforce T1 < T2 < T3
        self._enforce_order()
        for i in range(3):
            self._color_spn(self._steppers[i], self.t_pct[i].get())

        try:
            shares   = int(self.shares_var.get().replace(',', ''))
            avg_cost = float(self.avg_cost_var.get().replace(',', ''))
            if shares <= 0 or avg_cost <= 0:
                raise ValueError
        except (ValueError, TypeError):
            self._clear()
            return

        ccy = self.currency

        # Current / gap
        if self.current_price:
            self.current_var.set(fmt_price(self.current_price, ccy))
            gap = calc_gap_rate(self.current_price, avg_cost)
            self.gap_var.set(f"{gap:+.2f}%")
            self.gap_lbl.config(fg=gap_color(gap))
        else:
            self.current_var.set('--')
            self.gap_var.set('--')
            self.gap_lbl.config(fg='black')

        # Total cost
        self.cost_var.set(fmt_price(shares * avg_cost, ccy))

        # Buy trigger cascade (each level catches the prior ones)
        buy_pct = self._get_buy_pct()
        for i, res in enumerate(calc_buy_cascade(shares, avg_cost, buy_pct)):
            if res['price'] is not None:
                self.buy_info_var[i].set(
                    f"{fmt_price(res['price'], ccy)} × {res['qty']}")
                self.buy_info_lbl[i].config(fg=_BUY_FG)
            else:
                self.buy_info_var[i].set('--')
                self.buy_info_lbl[i].config(fg='#CCC')

        # Sell tiers
        pcts = [self.t_pct[i].get() for i in range(3)]
        acts = [self.t_active[i].get() for i in range(3)]
        for i, res in enumerate(calc_sell_tiers(shares, avg_cost, pcts, acts)):
            if res['price'] is not None:
                self.t_info_var[i].set(
                    f"{fmt_price(res['price'], ccy)} × {res['qty']}")
                self.t_info_lbl[i].config(fg='black')
            else:
                self.t_info_var[i].set('--')
                self.t_info_lbl[i].config(fg='#CCC')

    def _enforce_order(self):
        try:
            p = [self.t_pct[i].get() for i in range(3)]
        except Exception:
            return
        if p[1] <= p[0]:
            self.t_pct[1].set(p[0] + 1); p[1] = p[0] + 1
        if p[2] <= p[1]:
            self.t_pct[2].set(p[1] + 1)

    def _clear(self):
        for v in (self.current_var, self.gap_var, self.cost_var):
            v.set('--')
        self.gap_lbl.config(fg='black')
        for i in range(3):
            self.buy_info_var[i].set('--')
            self.buy_info_lbl[i].config(fg='#CCC')
            self.t_info_var[i].set('--')
            self.t_info_lbl[i].config(fg='#CCC')

    def get_state(self) -> dict:
        try:    shares = int(self.shares_var.get().replace(',', ''))
        except: shares = 0
        try:    avg_cost = float(self.avg_cost_var.get().replace(',', ''))
        except: avg_cost = 0.0
        auto = self.auto_var.get()
        state = {
            'ticker':     self.ticker,
            'tier':       self.tier,
            'is_deployed': True,
            'shares':     shares,
            'avg_cost':   avg_cost,
            'cost_basis': shares * avg_cost,
            'buy_pct':    self._get_buy_pct(),
            't1_pct':     self.t_pct[0].get(),
            't2_pct':     self.t_pct[1].get(),
            't3_pct':     self.t_pct[2].get(),
            't1_active':  self.t_active[0].get(),
            't2_active':  self.t_active[1].get(),
            't3_active':  self.t_active[2].get(),
            'auto_mode':  auto,
        }
        # Keep the load gear in sync in auto mode so it is correct if the
        # position later demotes back to an empty card.
        if auto and self.volatility is not None:
            state['load_gear'] = auto_gear_params(self.volatility)['load_pct']
        return state
