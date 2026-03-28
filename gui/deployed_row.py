import tkinter as tk
from core.calc import (
    STOCK_NAMES, BUY_GEAR_PCTS, BUY_GEAR_INFO, BUY_GEAR_LABELS, BUY_LABEL_TO_PCT,
    buy_pct_color, sell_pct_color, gap_color, fmt_price,
    calc_buy_trigger, calc_buy_shares,
    calc_sell_tiers, calc_gap_rate,
)

# ── Fonts (1.5× scale for QHD) ──────────────────────────────────────────────
_F_NAME = ('Segoe UI', 15, 'bold')
_F_LBL  = ('Segoe UI', 14)
_F_VAL  = ('Segoe UI', 14)
_F_OUT  = ('Segoe UI', 15, 'bold')
_F_SM   = ('Segoe UI', 12)
_F_ARR  = ('Segoe UI', 11)


class DeployedRow:
    """Card frame for one deployed stock. Reactive traces recompute on input
    change; compute() is also called externally on Save & Refresh."""

    def __init__(self, parent, row_num: int, pos: dict,
                 on_move_up, on_move_down, on_graph,
                 on_undeploy=None, on_compute=None):
        self.ticker         = pos['ticker']
        self.tier           = pos['tier']
        self.currency       = 'KRW' if self.ticker.endswith('.KS') else 'USD'
        self.current_price  = None
        self._army_pct      = None
        self._on_compute_cb = on_compute
        self._computing     = False

        # ── Card frame ───────────────────────────────────────────────────────
        self.frame = tk.Frame(parent, bd=1, relief='groove', padx=8, pady=5)
        self.frame.pack(fill='x', padx=2, pady=3)

        # ── Variables — inputs ───────────────────────────────────────────────
        self.shares_var    = tk.StringVar(
            value=str(pos['shares']) if pos['shares'] > 0 else '')
        self.avg_cost_var  = tk.StringVar(
            value=self._fmt_init(pos['avg_cost']))

        pct_init = pos.get('buy_pct', 5)
        self.buy_label_var = tk.StringVar(
            value=BUY_GEAR_INFO.get(pct_init, BUY_GEAR_INFO[5])['label'])

        self.t_active = [
            tk.BooleanVar(value=bool(pos.get(f't{i+1}_active', True)))
            for i in range(3)]
        self.t_pct = [
            tk.IntVar(value=int(pos.get(f't{i+1}_pct', [4, 6, 8][i])))
            for i in range(3)]

        # ── Variables — outputs ──────────────────────────────────────────────
        self.current_var   = tk.StringVar(value='--')
        self.gap_var       = tk.StringVar(value='--')
        self.cost_var      = tk.StringVar(value='--')
        self.buy_info_var  = tk.StringVar(value='--')
        self.t_info_var    = [tk.StringVar(value='--') for _ in range(3)]
        self.army_pct_var  = tk.StringVar(value='--')

        # ═════════════════════════════════════════════════════════════════════
        # ROW 0 — inputs
        # ═════════════════════════════════════════════════════════════════════
        r0 = tk.Frame(self.frame)
        r0.pack(fill='x', pady=(0, 3))

        # Name — fixed width for alignment across rows
        name = STOCK_NAMES.get(self.ticker, self.ticker)
        if self.ticker.endswith('.KS'):
            name += '  (KR)'
        tk.Label(r0, text=f"{row_num}. {name}",
                 font=_F_NAME, anchor='w', width=28).pack(side='left')

        # Avg cost
        tk.Label(r0, text='Avg Cost:', font=_F_LBL).pack(side='left')
        self.avg_entry = tk.Entry(r0, textvariable=self.avg_cost_var,
                                  width=11, justify='right', font=_F_VAL)
        self.avg_entry.pack(side='left', padx=(2, 10))
        self.avg_entry.bind('<FocusOut>', lambda e: self._format_avg())

        # Shares
        tk.Label(r0, text='Shares:', font=_F_LBL).pack(side='left')
        tk.Entry(r0, textvariable=self.shares_var,
                 width=6, justify='right', font=_F_VAL
                 ).pack(side='left', padx=(2, 10))

        # Buy gear
        tk.Label(r0, text='Buy Gear:', font=_F_LBL).pack(side='left')
        self.buy_om = tk.OptionMenu(r0, self.buy_label_var, *BUY_GEAR_LABELS,
                                    command=self._on_buy_change)
        self.buy_om.config(font=_F_SM, width=14, relief='raised')
        self.buy_om.pack(side='left', padx=(2, 10))
        self._update_buy_color()

        # Sell tier selectors (T1 T2 T3)
        self._spn = []
        for i in range(3):
            tk.Label(r0, text=f'T{i+1}:', font=_F_LBL).pack(side='left', padx=(4, 0))
            tk.Checkbutton(r0, variable=self.t_active[i]).pack(side='left')
            spn = tk.Spinbox(r0, textvariable=self.t_pct[i], from_=1, to=20,
                             width=3, font=_F_VAL, justify='right')
            spn.pack(side='left', padx=(0, 2))
            self._spn.append(spn)
            self._color_spn(spn, self.t_pct[i].get())

        # Right side — arrows, graph, remove, army%
        btn_frame = tk.Frame(r0)
        btn_frame.pack(side='right', padx=4)

        tk.Button(btn_frame, text='\u25b2', font=_F_ARR, width=2,
                  command=lambda: on_move_up(self.ticker)).pack(side='left')
        tk.Button(btn_frame, text='\u25bc', font=_F_ARR, width=2,
                  command=lambda: on_move_down(self.ticker)).pack(side='left')
        tk.Button(btn_frame, text='Graph', font=_F_SM, width=6,
                  command=lambda: on_graph(self.ticker)).pack(side='left', padx=(4, 0))
        if on_undeploy:
            tk.Button(btn_frame, text='Remove', font=_F_SM, width=7,
                      command=lambda: on_undeploy(self.ticker)).pack(side='left', padx=(4, 0))

        tk.Label(r0, textvariable=self.army_pct_var,
                 font=_F_OUT, width=7, anchor='e').pack(side='right', padx=(0, 4))
        tk.Label(r0, text='Army:', font=_F_SM, fg='#888').pack(side='right')

        # ═════════════════════════════════════════════════════════════════════
        # ROW 1 — computed outputs
        # ═════════════════════════════════════════════════════════════════════
        r1 = tk.Frame(self.frame)
        r1.pack(fill='x')

        def _out(label, var, **kw):
            tk.Label(r1, text=label, fg='#888', font=_F_SM).pack(side='left')
            lbl = tk.Label(r1, textvariable=var, font=_F_OUT, **kw)
            lbl.pack(side='left', padx=(2, 12))
            return lbl

        _out('Total Cost:', self.cost_var)
        _out('Current:', self.current_var)
        self.gap_lbl = _out('Gap:', self.gap_var, width=9)
        _out('Buy Trigger:', self.buy_info_var)
        self.t_info_lbl = [
            _out(f'Sell Tier {i+1}:', self.t_info_var[i]) for i in range(3)]

        # ── Reactive traces (added after all widgets are built) ──────────────
        self.shares_var.trace_add('write', lambda *_: self._on_input_change())
        self.avg_cost_var.trace_add('write', lambda *_: self._on_input_change())
        for i in range(3):
            self.t_active[i].trace_add('write', lambda *_: self._on_input_change())
            self.t_pct[i].trace_add('write', lambda *_: self._on_input_change())

    # ── Input change handler ─────────────────────────────────────────────────

    def _on_input_change(self):
        if not self._computing:
            self.compute()
            if self._on_compute_cb:
                self._on_compute_cb()

    # ── Formatting ───────────────────────────────────────────────────────────

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

    # ── Color helpers ────────────────────────────────────────────────────────

    def _get_buy_pct(self) -> int:
        return BUY_LABEL_TO_PCT.get(self.buy_label_var.get(), 5)

    def _update_buy_color(self):
        pct = self._get_buy_pct()
        c = buy_pct_color(pct)
        fg = 'white' if pct >= 5 else 'black'
        self.buy_om.config(bg=c, fg=fg, activebackground=c, activeforeground=fg)

    def _on_buy_change(self, _=None):
        self._update_buy_color()
        self._on_input_change()

    def _color_spn(self, spn, pct):
        c = sell_pct_color(float(pct))
        fg = 'white' if float(pct) >= 5 else 'black'
        spn.config(bg=c, fg=fg)

    # ── Public API ───────────────────────────────────────────────────────────

    def update_live(self, price: float):
        self.current_price = price

    def set_army_pct(self, pct):
        self._army_pct = pct
        self.army_pct_var.set(f"{pct:.1f}%" if pct is not None else '--')

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
        # Enforce T1 < T2 < T3
        self._enforce_order()
        for i in range(3):
            self._color_spn(self._spn[i], self.t_pct[i].get())

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

        # Buy trigger
        buy_pct = self._get_buy_pct()
        trigger = calc_buy_trigger(avg_cost, buy_pct)
        n_buy   = calc_buy_shares(shares, buy_pct)
        self.buy_info_var.set(f"{fmt_price(trigger, ccy)} \u00d7 {n_buy}")

        # Sell tiers
        pcts = [self.t_pct[i].get() for i in range(3)]
        acts = [self.t_active[i].get() for i in range(3)]
        for i, res in enumerate(calc_sell_tiers(shares, avg_cost, pcts, acts)):
            if res['price'] is not None:
                self.t_info_var[i].set(
                    f"{fmt_price(res['price'], ccy)} \u00d7 {res['qty']}")
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
        for v in (self.current_var, self.gap_var, self.cost_var, self.buy_info_var):
            v.set('--')
        self.gap_lbl.config(fg='black')
        for i in range(3):
            self.t_info_var[i].set('--')
            self.t_info_lbl[i].config(fg='#CCC')

    def get_state(self) -> dict:
        try:    shares = int(self.shares_var.get().replace(',', ''))
        except: shares = 0
        try:    avg_cost = float(self.avg_cost_var.get().replace(',', ''))
        except: avg_cost = 0.0
        return {
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
        }
