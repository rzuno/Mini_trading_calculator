import tkinter as tk
from core.calc import (
    STOCK_NAMES, LOAD_GEARS, LOAD_GEAR_LABELS, LOAD_LABEL_TO_KEY,
    load_gear_color, fmt_price,
    calc_load_price, calc_load_shares,
)

# ── Fonts (1.5× scale for QHD) ──────────────────────────────────────────────
_F_LBL = ('Segoe UI', 14)
_F_VAL = ('Segoe UI', 14)
_F_OUT = ('Segoe UI', 15, 'bold')
_F_SM  = ('Segoe UI', 12)
_F_ARR = ('Segoe UI', 11)


class EmptyRow:
    """Compact single-row for an empty (undeployed) stock."""

    def __init__(self, parent, row_num: int, pos: dict,
                 get_unit_cash, on_deploy, on_move_up, on_move_down, on_graph):
        self.ticker        = pos['ticker']
        self.tier          = pos['tier']
        self.currency      = 'KRW' if self.ticker.endswith('.KS') else 'USD'
        self.get_unit_cash = get_unit_cash
        self.current_price = None
        self.peak_5d       = None

        # ── Frame ────────────────────────────────────────────────────────────
        self.frame = tk.Frame(parent, padx=8, pady=5)
        self.frame.pack(fill='x')

        # ── Variables ────────────────────────────────────────────────────────
        init_label = LOAD_GEARS[pos.get('load_gear', 'L2')]['label']
        self.load_label_var = tk.StringVar(value=init_label)
        self.peak_var       = tk.StringVar(value='--')
        self.current_var    = tk.StringVar(value='--')
        self.load_info_var  = tk.StringVar(value='--')

        # ── Single row: main info ────────────────────────────────────────────
        r0 = tk.Frame(self.frame)
        r0.pack(fill='x')

        name = STOCK_NAMES.get(self.ticker, self.ticker)
        if self.ticker.endswith('.KS'):
            name += '  (KR)'
        tk.Label(r0, text=f"{row_num}. {name}",
                 font=_F_LBL, width=28, anchor='w').pack(side='left')

        tk.Label(r0, text=self.tier, font=_F_SM, width=5,
                 fg='#666').pack(side='left', padx=(0, 8))

        tk.Label(r0, text='5D High:', font=_F_SM, fg='#888').pack(side='left')
        tk.Label(r0, textvariable=self.peak_var,
                 font=_F_VAL, width=10, anchor='e').pack(side='left', padx=(2, 8))

        tk.Label(r0, text='Current:', font=_F_SM, fg='#888').pack(side='left')
        tk.Label(r0, textvariable=self.current_var,
                 font=_F_VAL, width=10, anchor='e').pack(side='left', padx=(2, 8))

        # Load gear dropdown
        self.gear_om = tk.OptionMenu(r0, self.load_label_var,
                                     *LOAD_GEAR_LABELS,
                                     command=self._on_gear_change)
        self.gear_om.config(font=_F_SM, width=13, relief='raised')
        self.gear_om.pack(side='left', padx=4)
        self._update_gear_color()

        tk.Label(r0, text='Load Target:', font=_F_SM, fg='#888').pack(side='left')
        tk.Label(r0, textvariable=self.load_info_var,
                 font=_F_OUT).pack(side='left', padx=(2, 8))

        # Right side — buttons
        btn_frame = tk.Frame(r0)
        btn_frame.pack(side='right', padx=2)

        tk.Button(btn_frame, text='Deploy', font=_F_SM, width=7,
                  command=lambda: on_deploy(self.ticker)).pack(side='left', padx=2)
        tk.Button(btn_frame, text='\u25b2', font=_F_ARR, width=2,
                  command=lambda: on_move_up(self.ticker)).pack(side='left')
        tk.Button(btn_frame, text='\u25bc', font=_F_ARR, width=2,
                  command=lambda: on_move_down(self.ticker)).pack(side='left')
        tk.Button(btn_frame, text='Graph', font=_F_SM, width=6,
                  command=lambda: on_graph(self.ticker)).pack(side='left', padx=(4, 0))

        # Trace for load gear (reactive)
        self.load_label_var.trace_add('write', lambda *_: self.compute())

    # ── Gear helpers ─────────────────────────────────────────────────────────

    def _get_gear_key(self) -> str:
        return LOAD_LABEL_TO_KEY.get(self.load_label_var.get(), 'L2')

    def _update_gear_color(self):
        key = self._get_gear_key()
        c   = load_gear_color(key)
        fg  = 'black' if key in ('L1', 'L2') else 'white'
        self.gear_om.config(bg=c, fg=fg, activebackground=c, activeforeground=fg)

    def _on_gear_change(self, _=None):
        self._update_gear_color()

    # ── Public API ───────────────────────────────────────────────────────────

    def update_live(self, price: float, peak_5d: float, closes_5d: list = None):
        self.current_price = price
        self.peak_5d       = peak_5d

    def compute(self):
        ccy = self.currency
        self.current_var.set(
            fmt_price(self.current_price, ccy) if self.current_price else '--')

        if self.peak_5d and self.peak_5d > 0:
            gear = self._get_gear_key()
            self.peak_var.set(fmt_price(self.peak_5d, ccy))
            lp = calc_load_price(self.peak_5d, gear)
            n  = calc_load_shares(self.peak_5d, gear, self.tier,
                                  self.get_unit_cash())
            self.load_info_var.set(f"{fmt_price(lp, ccy)} \u00d7 {n}")
        else:
            self.peak_var.set('--')
            self.load_info_var.set('--')

    def get_state(self) -> dict:
        return {
            'ticker':     self.ticker,
            'tier':       self.tier,
            'is_deployed': False,
            'shares':     0,
            'avg_cost':   0.0,
            'cost_basis': 0.0,
            'load_gear':  self._get_gear_key(),
            'buy_pct':    5,
            't1_pct':     4.0,  't2_pct': 6.0,  't3_pct': 8.0,
            't1_active':  True, 't2_active': True, 't3_active': True,
        }
