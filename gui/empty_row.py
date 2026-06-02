import tkinter as tk
from core.calc import (
    display_name, LOAD_PCT_MIN, LOAD_PCT_MAX,
    normalize_load_pct, load_pct_color, fmt_price,
    calc_load_price, calc_load_shares,
)
from gui.stepper import Stepper

# ── Fonts (1.3× scale for QHD) ──────────────────────────────────────────────
_F_NAME_MAJOR = ('Segoe UI', 13, 'bold')
_F_NAME_MINOR = ('Segoe UI', 13)
_F_LBL  = ('Segoe UI', 12)
_F_VAL  = ('Segoe UI', 12)
_F_OUT  = ('Segoe UI', 13, 'bold')
_F_SM   = ('Segoe UI', 10)
_F_BTN  = ('Segoe UI', 11, 'bold')


class EmptyRow:
    """Row for an empty (undeployed) stock with load monitoring and
    optional avg cost / shares entries for auto-promotion on Save & Refresh."""

    def __init__(self, parent, row_num: int, pos: dict,
                 get_unit_cash, on_graph):
        self.ticker        = pos['ticker']
        self.tier          = pos['tier']
        self.currency      = 'KRW' if self.ticker.endswith('.KS') else 'USD'
        self.get_unit_cash = get_unit_cash
        self.current_price = None
        self.peak_5d       = None

        # ── Card frame (parent grids this; the row does not self-place) ───────
        self.frame = tk.Frame(parent, bd=1, relief='groove', padx=6, pady=3)

        # ── Variables ────────────────────────────────────────────────────────
        # Load gear held as a negative drop percent (e.g. -8 shown as -8%).
        init_pct = normalize_load_pct(pos.get('load_gear', 5))
        self.load_pct_var   = tk.IntVar(value=-init_pct)
        self.peak_var       = tk.StringVar(value='--')
        self.current_var    = tk.StringVar(value='--')
        self.load_info_var  = tk.StringVar(value='--')

        # Avg cost / shares for auto-promotion
        self.avg_cost_var = tk.StringVar(
            value=self._fmt_init(pos.get('avg_cost', 0)))
        self.shares_var = tk.StringVar(
            value=str(pos.get('shares', 0)) if pos.get('shares', 0) > 0 else '')

        # ── Row 0: name, Graph, live load monitoring ─────────────────────────
        r0 = tk.Frame(self.frame)
        r0.pack(fill='x')

        name = display_name(self.ticker)
        if self.ticker.endswith('.KS'):
            name += ' (KR)'
        name_font = _F_NAME_MAJOR if self.tier == 'Major' else _F_NAME_MINOR
        tk.Label(r0, text=f"{row_num}. {name}",
                 font=name_font, anchor='w').pack(side='left')
        tk.Button(r0, text='Graph', font=_F_SM, width=6,
                  command=lambda: on_graph(self.ticker)).pack(side='left', padx=(4, 10))

        tk.Label(r0, text='5D High:', font=_F_SM, fg='#888').pack(side='left')
        tk.Label(r0, textvariable=self.peak_var,
                 font=_F_VAL, width=9, anchor='e').pack(side='left', padx=(2, 8))

        tk.Label(r0, text='Current:', font=_F_SM, fg='#888').pack(side='left')
        self.current_lbl = tk.Label(r0, textvariable=self.current_var,
                                    font=_F_VAL, width=9, anchor='e')
        self.current_lbl.pack(side='left', padx=(2, 8))

        # Load gear stepper — click the arrows to step 1% (-4% ... -15%)
        tk.Label(r0, text='Load Gear:', font=_F_SM, fg='#888').pack(side='left')
        self.gear_step = Stepper(
            r0, self.load_pct_var, -LOAD_PCT_MAX, -LOAD_PCT_MIN,
            entry_width=4, value_font=_F_VAL, btn_font=_F_BTN)
        self.gear_step.pack(side='left', padx=(2, 0))
        tk.Label(r0, text='%', font=_F_SM, fg='#888').pack(side='left')
        self._update_gear_color()

        # ── Row 1: load target + avg cost / shares for auto-deploy ───────────
        r1 = tk.Frame(self.frame)
        r1.pack(fill='x', pady=(2, 0))

        tk.Label(r1, text='Load Target:', font=_F_SM, fg='#888').pack(side='left')
        tk.Label(r1, textvariable=self.load_info_var,
                 font=_F_OUT).pack(side='left', padx=(2, 14))

        tk.Label(r1, text='Avg Cost:', font=_F_SM, fg='#888').pack(side='left')
        self.avg_entry = tk.Entry(r1, textvariable=self.avg_cost_var,
                                  width=10, justify='right', font=_F_VAL)
        self.avg_entry.pack(side='left', padx=(2, 8))
        self.avg_entry.bind('<FocusOut>', lambda e: self._format_avg())

        tk.Label(r1, text='Shares:', font=_F_SM, fg='#888').pack(side='left')
        tk.Entry(r1, textvariable=self.shares_var,
                 width=6, justify='right', font=_F_VAL
                 ).pack(side='left', padx=(2, 8))

        tk.Label(r1, text='(fill & Save to deploy)', font=_F_SM,
                 fg='#AAA').pack(side='left', padx=(4, 0))

        # Trace for load gear (reactive)
        self.load_pct_var.trace_add('write', lambda *_: self.compute())

    # ── Formatting ───────────────────────────────────────────────────────────

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

    # ── Gear helpers ─────────────────────────────────────────────────────────

    def _get_load_pct(self) -> int:
        """Current load gear as a positive drop percent, clamped to range."""
        try:
            v = abs(self.load_pct_var.get())
        except tk.TclError:
            v = 5
        return max(LOAD_PCT_MIN, min(LOAD_PCT_MAX, v))

    def _update_gear_color(self):
        pct = self._get_load_pct()
        c   = load_pct_color(pct)
        fg  = 'black' if pct <= 5 else 'white'
        self.gear_step.set_value_color(c, fg)

    # ── Public API ───────────────────────────────────────────────────────────

    def update_live(self, price: float, peak_5d: float, closes_5d: list = None):
        self.current_price = price
        self.peak_5d       = peak_5d

    def compute(self):
        ccy = self.currency
        load_price = None
        self._update_gear_color()

        if self.peak_5d and self.peak_5d > 0:
            pct = self._get_load_pct()
            self.peak_var.set(fmt_price(self.peak_5d, ccy))
            load_price = calc_load_price(self.peak_5d, pct)
            n  = calc_load_shares(self.peak_5d, pct, self.tier,
                                  self.get_unit_cash())
            self.load_info_var.set(f"{fmt_price(load_price, ccy)} \u00d7 {n}")
        else:
            self.peak_var.set('--')
            self.load_info_var.set('--')

        # Current price with green highlight when load condition met
        if self.current_price:
            self.current_var.set(fmt_price(self.current_price, ccy))
            if load_price and self.current_price <= load_price:
                self.current_lbl.config(fg='#006400')  # dark green
            else:
                self.current_lbl.config(fg='black')
        else:
            self.current_var.set('--')
            self.current_lbl.config(fg='black')

    def get_state(self) -> dict:
        try:    shares = int(self.shares_var.get().replace(',', ''))
        except: shares = 0
        try:    avg_cost = float(self.avg_cost_var.get().replace(',', ''))
        except: avg_cost = 0.0
        return {
            'ticker':     self.ticker,
            'tier':       self.tier,
            'is_deployed': False,
            'shares':     shares,
            'avg_cost':   avg_cost,
            'cost_basis': shares * avg_cost,
            'load_gear':  self._get_load_pct(),
            'buy_pct':    5,
            't1_pct':     4.0,  't2_pct': 6.0,  't3_pct': 8.0,
            't1_active':  True, 't2_active': True, 't3_active': True,
        }
