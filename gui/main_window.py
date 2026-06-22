import os
import tkinter as tk
from tkinter import ttk
import threading
from datetime import datetime

# Order execution mode. DRY_RUN = compute and log orders only, never call the
# broker. (LIVE wiring comes in a later, confirmed step.)
ORDER_MODE = 'DRY_RUN'
_ORDERS_LOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'logs', 'orders.log')

from core.calc import stock_sort_key, calc_volatility, fx_dev_color
from core.csv_io import load_config, save_config, load_positions, save_positions
from providers import get_provider
from gui.stock_row import StockRow
from gui.candle_chart import CandleChartWindow

# ── Fonts (1.3× scale for QHD) ──────────────────────────────────────────────
_F_SECTION = ('Segoe UI', 16, 'bold')
_F_SEC_INFO = ('Segoe UI', 13)
_F_HDR     = ('Segoe UI', 13)
_F_HDR_B   = ('Segoe UI', 13, 'bold')
_F_BTN     = ('Segoe UI', 13, 'bold')
_F_SM      = ('Segoe UI', 10)
_F_FX_BANNER   = ('Segoe UI', 11)
_F_FX_BANNER_B = ('Segoe UI', 11, 'bold')
_F_FX_BTN      = ('Segoe UI', 11, 'bold')

# Switch-tracker buttons: 0 is always green (the neutral/reset rung); a done
# switch lights red on the sell (+) side and blue on the buy (-) side.
_FX_GREEN = '#2E8B57'
_FX_RED   = '#CC3333'
_FX_BLUE  = '#3366CC'


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AI Seesaw Mini-Calculator")
        self.root.minsize(1440, 700)
        self._center_window(1800, 1320)

        self.config    = load_config()
        self.positions = load_positions()

        # Market-data provider. Toss is the default; fall back to Yahoo if Toss
        # can't initialize (e.g. missing credentials) so launch never breaks.
        self._provider_init_note = ''
        try:
            self._provider = get_provider(self.config)
        except Exception as e:
            from providers import YahooMarketProvider
            self._provider = YahooMarketProvider()
            self._provider_init_note = f"Toss unavailable ({e}); using Yahoo."
        self.provider_var = tk.StringVar(
            value=getattr(self._provider, 'name', 'yahoo').title())

        # Sort positions in fixed order on load
        self.positions.sort(key=lambda p: stock_sort_key(p['ticker']))

        self._fx_rate        = None
        self._fx_avg_3m      = None
        self._toss_acct_seq  = None   # cached Toss accountSeq for order/account reads
        # How many dollar-switch steps have been done: + = sold USD (FX high),
        # - = bought USD (FX low). Range -3..+3, tracked manually on the panel.
        self.fx_switch_level = int(self.config.get('fx_switch_level', 0))
        self._current_prices = {}
        self._current_peaks  = {}
        self._ohlc_data      = {}
        self._closes_data    = {}
        self._volatility     = {}
        self._last_data      = {}

        # Header vars
        self.N_var            = tk.StringVar(value=str(self.config['N']))
        self.unit_krw_var     = tk.StringVar(value=f"{self.config['unit_cash_krw']:,}")
        self.unit_usd_var     = tk.StringVar(value=str(self.config['unit_cash_usd']))
        self.fx_var           = tk.StringVar(value='--')
        self.fx_pct_var       = tk.StringVar(value='')
        self.fx_avg_var       = tk.StringVar(value='--')
        self.fx_pool_var      = tk.StringVar(value='')
        # Per-threshold (-3..+3) rung price and trade-amount labels.
        self._fx_rung_vars    = {k: tk.StringVar(value='') for k in range(-3, 4)}
        self._fx_amt_vars     = {k: tk.StringVar(value='') for k in range(-3, 4)}
        self.last_refresh_var = tk.StringVar(value='--')
        self.status_var       = tk.StringVar(value='Initializing...')
        self.deploy_info_var  = tk.StringVar(value='')

        # ── Build layout ────────────────────────────────────────────────────
        self._build_header()
        self._build_fx_panel()

        # Scrollable content area
        outer = tk.Frame(self.root)
        outer.pack(fill='both', expand=True, padx=5, pady=2)

        vbar = tk.Scrollbar(outer, orient='vertical')
        vbar.pack(side='right', fill='y')

        self._canvas = tk.Canvas(outer, yscrollcommand=vbar.set,
                                 highlightthickness=0)
        self._canvas.pack(side='left', fill='both', expand=True)
        vbar.config(command=self._canvas.yview)

        self.content_frame = tk.Frame(self._canvas)
        self._content_win = self._canvas.create_window(
            (0, 0), window=self.content_frame, anchor='nw')
        self.content_frame.bind(
            '<Configure>',
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox('all')))
        # Stretch the content to the canvas width so the 2-column card grids
        # divide the full width evenly instead of sitting at natural width.
        self._canvas.bind(
            '<Configure>',
            lambda e: self._canvas.itemconfig(self._content_win, width=e.width))

        self._canvas.bind('<Enter>',
            lambda e: self._canvas.bind_all('<MouseWheel>', self._mwheel))
        self._canvas.bind('<Leave>',
            lambda e: self._canvas.unbind_all('<MouseWheel>'))

        self._build_footer()
        self._rebuild_sections()

        # Auto-derive USD when KRW changes (header only)
        self.unit_krw_var.trace_add('write', lambda *_: self._update_unit_usd())

        if self._provider_init_note:
            self.status_var.set(self._provider_init_note)

        # Auto-refresh on launch
        self.root.after(300, self._on_save_refresh)

    def _mwheel(self, event):
        self._canvas.yview_scroll(-1 * (event.delta // 120), 'units')

    def _center_window(self, w, h):
        """Size the window (clamped to the screen) and center it on launch so
        nothing is clipped off-screen."""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w  = min(w, sw - 40)
        h  = min(h, sh - 80)
        x  = max(0, (sw - w) // 2)
        y  = max(0, (sh - h) // 2 - 20)
        self.root.geometry(f'{w}x{h}+{x}+{y}')

    # ── Unit cash ────────────────────────────────────────────────────────────

    def _get_unit_cash(self, currency: str) -> float:
        try:
            if currency == 'KRW':
                return float(self.unit_krw_var.get().replace(',', ''))
            return float(self.unit_usd_var.get().replace(',', ''))
        except ValueError:
            return 0.0

    def _update_fx_display(self):
        """Refresh the FX panel: current rate + deviation from the 3-month
        average, the per-rung target prices and switch amounts, and the marker
        on the rung the current rate currently sits at."""
        rate = self._fx_rate
        avg  = self._fx_avg_3m

        if not rate:
            self.fx_var.set('N/A')
            self.fx_pct_var.set('')
            self.fx_avg_var.set('--')
            self.fx_pool_var.set('')
            for k in range(-3, 4):
                self._fx_rung_vars[k].set('')
                self._fx_amt_vars[k].set('')
            return

        self.fx_var.set(f"{rate:,.2f}")

        if not (avg and avg > 0):
            self.fx_pct_var.set('')
            self.fx_avg_var.set('--')
            self.fx_pool_var.set('')
            for k in range(-3, 4):
                self._fx_rung_vars[k].set('')
                self._fx_amt_vars[k].set('')
            return

        pct = (rate - avg) / avg * 100.0
        self.fx_pct_var.set(f"({pct:+.1f}%)")
        self._fx_pct_lbl.config(fg=fx_dev_color(pct))
        self.fx_avg_var.set(f"{avg:,.0f}")

        pool   = self._switch_pool_krw()         # 1/3 of total capital, in KRW
        cur    = self._fx_threshold()            # rung the current rate sits at

        for k in range(-3, 4):
            v   = avg * (1 + k / 100.0)
            tag = '3m avg' if k == 0 else f'{k:+d}%'
            self._fx_rung_vars[k].set(f"{v:,.0f}\n({tag})")
            # Mark the rung the live rate currently sits at.
            if k == cur:
                self._fx_rung_lbls[k].config(font=_F_FX_BANNER_B, fg='black')
            else:
                self._fx_rung_lbls[k].config(font=_F_FX_BANNER, fg='#555')

            if k == 0:
                self._fx_amt_vars[k].set('reset')
            else:
                # Marginal step = |k|/6 of the pool, sized in USD at the neutral
                # (3-month average) rate so it stays stable as the rate moves.
                amt_usd = abs(k) / 6.0 * pool / avg
                verb = 'sell' if k > 0 else 'buy'
                self._fx_amt_vars[k].set(f"{verb}\n${amt_usd:,.0f}")

        if pool > 0:
            self.fx_pool_var.set(
                f"(switch pool 1/3 ≈ ${pool / avg:,.0f} / "
                f"₩{pool:,.0f};  steps = |k|/6 of pool)")
        else:
            self.fx_pool_var.set('')

    # ── FX dollar-switch tracker ──────────────────────────────────────────────

    def _switch_pool_krw(self) -> float:
        """The switchable third of total capital, in KRW (= N × unit_krw / 3)."""
        try:
            n        = int(self.N_var.get())
            unit_krw = float(self.unit_krw_var.get().replace(',', ''))
            return n * unit_krw / 3.0
        except (ValueError, ZeroDivisionError):
            return 0.0

    def _fx_threshold(self):
        """Which rung (-3..+3) the current rate sits at, truncated toward zero.
        None when the rate or average is unknown."""
        if not (self._fx_rate and self._fx_avg_3m):
            return None
        pct = (self._fx_rate - self._fx_avg_3m) / self._fx_avg_3m * 100.0
        return max(-3, min(3, int(pct)))

    def _on_fx_switch_click(self, k):
        """Record that the dollar switch has been done up to threshold k
        (0 clears it). Lights all rungs between 0 and k on that side."""
        self.fx_switch_level = k
        self._update_fx_buttons()
        self._persist_fx_level()

    def _update_fx_buttons(self):
        """Color the switch buttons: the 0 rung is always green; a recorded
        switch lights red on the + (sell) side and blue on the - (buy) side."""
        if not hasattr(self, 'fx_switch_btns'):
            return
        lvl = self.fx_switch_level
        for k, b in self.fx_switch_btns.items():
            if k == 0:
                color = _FX_GREEN
            elif (0 < k <= lvl):
                color = _FX_RED
            elif (lvl <= k < 0):
                color = _FX_BLUE
            else:
                color = None

            if color:
                b.config(bg=color, fg='white',
                         activebackground=color, activeforeground='white')
            else:
                b.config(bg=self._fx_btn_default_bg, fg='black',
                         activebackground=self._fx_btn_default_bg,
                         activeforeground='black')

    def _persist_fx_level(self):
        self.config['fx_switch_level'] = self.fx_switch_level
        try:
            save_config(self.config)
        except Exception:
            pass

    def _update_unit_usd(self):
        """Auto-derive USD unit cash = KRW unit cash / FX rate."""
        if not self._fx_rate:
            return
        try:
            krw = float(self.unit_krw_var.get().replace(',', ''))
            self.unit_usd_var.set(f"{krw / self._fx_rate:.2f}")
        except (ValueError, ZeroDivisionError):
            pass

    # ── Position helpers ─────────────────────────────────────────────────────

    def _find_pos(self, ticker):
        return next(p for p in self.positions if p['ticker'] == ticker)

    # ── Header ───────────────────────────────────────────────────────────────

    def _build_header(self):
        f = tk.Frame(self.root, bd=1, relief='ridge', padx=12, pady=8)
        f.pack(fill='x', padx=5, pady=(5, 2))

        c = 0
        def _lbl(text, **kw):
            nonlocal c
            tk.Label(f, text=text, font=_F_HDR, **kw).grid(
                row=0, column=c, sticky='e', padx=(12, 2)); c += 1

        def _entry(var, w):
            nonlocal c
            tk.Entry(f, textvariable=var, width=w, justify='right',
                     font=_F_HDR_B).grid(row=0, column=c, padx=2); c += 1

        def _val(var, **kw):
            nonlocal c
            tk.Label(f, textvariable=var, font=_F_HDR_B,
                     anchor='w', **kw).grid(row=0, column=c, padx=2); c += 1

        _lbl('Total Units:');    _entry(self.N_var, 4)
        _lbl('1 Unit (KRW):');   _entry(self.unit_krw_var, 12)
        _lbl('1 Unit (USD):');   _val(self.unit_usd_var, width=8, fg='#555')

        # Market-data source selector. Toss is the default (listed first and
        # shown in bold); switching re-fetches.
        _lbl('Data:')
        self._provider_om = tk.OptionMenu(
            f, self.provider_var, 'Toss', 'Yahoo',
            command=lambda v: self._switch_provider(v))
        self._provider_om.config(width=6)
        menu = self._provider_om['menu']
        try:
            menu.entryconfig(menu.index('Toss'), font=_F_HDR_B)
            menu.entryconfig(menu.index('Yahoo'), font=_F_HDR)
        except tk.TclError:
            pass
        self._provider_om.grid(row=0, column=c, padx=2); c += 1
        self._refresh_provider_button()

        tk.Button(f, text='Account Info', font=_F_HDR,
                  command=self._on_account_info
                  ).grid(row=0, column=c, padx=(8, 2)); c += 1

        tk.Label(f, text=f'Orders: {ORDER_MODE}', font=_F_SM,
                 fg=('#888' if ORDER_MODE == 'DRY_RUN' else '#CC0000')
                 ).grid(row=0, column=c, padx=(8, 2)); c += 1

    def _refresh_provider_button(self):
        """Bold the selector when Toss (the default) is active."""
        is_toss = self.provider_var.get().lower() == 'toss'
        self._provider_om.config(font=_F_HDR_B if is_toss else _F_HDR)

    def _switch_provider(self, name: str):
        """Swap the live market-data provider at runtime. Reverts cleanly if the
        new provider can't be created (e.g. Toss credentials missing)."""
        name = name.lower()
        current = getattr(self._provider, 'name', 'yahoo')
        if name == current:
            return
        try:
            prov = get_provider({'market_provider': name})
        except Exception as e:
            self.status_var.set(f"Cannot use {name.title()}: {e}")
            self.provider_var.set(current.title())
            self._refresh_provider_button()
            return
        self._provider = prov
        self.config['market_provider'] = name
        self._refresh_provider_button()
        self.status_var.set(f"Data source → {name.title()}; refreshing…")
        self._on_save_refresh()

    # ── Account info (Toss, read-only) ──────────────────────────────────────────

    def _on_account_info(self):
        """Read holdings + cash from Toss in the background and show them in a
        popup. Read-only; needs Toss credentials regardless of the selected
        market-data provider."""
        self.status_var.set('Reading Toss account…')
        threading.Thread(target=self._account_bg, daemon=True).start()

    def _account_bg(self):
        try:
            from providers.toss_market_provider import TossMarketProvider
            snap = TossMarketProvider.from_env().account_snapshot()
        except Exception as e:
            self.root.after(0, lambda: self.status_var.set(
                f'Account read failed: {e}'))
            return
        self.root.after(0, self._show_account_window, snap)

    def _show_account_window(self, snap):
        self.status_var.set('Ready')
        if not snap:
            self.status_var.set('No Toss account found.')
            return

        win = tk.Toplevel(self.root)
        win.title('Toss Account — Holdings (read-only)')
        win.geometry('940x540')

        tk.Label(win, text='Toss Holdings  (read-only snapshot — for checking '
                           'against your manual data)',
                 font=_F_HDR_B).pack(anchor='w', padx=12, pady=(10, 4))

        cols = ('name', 'mkt', 'shares', 'avg', 'last', 'value', 'pl')
        tv = ttk.Treeview(win, columns=cols, show='headings', height=15)
        for cid, txt, w, anc in (
                ('name', 'Stock', 200, 'w'), ('mkt', 'Mkt', 50, 'center'),
                ('shares', 'Shares', 90, 'e'), ('avg', 'Avg Cost', 120, 'e'),
                ('last', 'Current', 120, 'e'), ('value', 'Value', 140, 'e'),
                ('pl', 'P/L %', 80, 'e')):
            tv.heading(cid, text=txt)
            tv.column(cid, width=w, anchor=anc)
        tv.pack(fill='both', expand=True, padx=12, pady=4)

        from core.calc import fmt_price
        for it in snap['items']:
            ccy = it['currency']
            pl = it['pl_rate']
            tv.insert('', 'end', values=(
                f"{it['name']} ({it['symbol']})",
                it['country'],
                f"{it['shares']:,.0f}" if ccy == 'KRW' else f"{it['shares']:,.4f}".rstrip('0').rstrip('.'),
                fmt_price(it['avg'], ccy),
                fmt_price(it['last'], ccy),
                fmt_price(it['value'], ccy) if it['value'] is not None else '--',
                f"{pl*100:+.2f}" if pl is not None else '--'))

        # Summary: cash + reserve/army size cross-check
        fx = self._fx_rate or 0
        cash_krw = snap['cash_krw'] or 0
        cash_usd = snap['cash_usd'] or 0
        reserve_krw = cash_krw + (cash_usd * fx if fx else 0)
        try:
            unit_krw = float(self.unit_krw_var.get().replace(',', ''))
            n_units = int(self.N_var.get())
        except (ValueError, ZeroDivisionError):
            unit_krw, n_units = 0, 0

        deployed_krw = sum(
            (it['value'] or 0) * (fx if it['currency'] == 'USD' and fx else 1)
            for it in snap['items'])

        lines = [
            f"Cash:  ₩{cash_krw:,.0f}   +   ${cash_usd:,.2f}"
            + (f"   ≈ ₩{reserve_krw:,.0f}" if fx else "  (FX unknown)"),
        ]
        if unit_krw > 0:
            lines.append(
                f"Reserve army:  {reserve_krw / unit_krw:,.1f} units"
                f"      Deployed (Toss): ₩{deployed_krw:,.0f} = "
                f"{deployed_krw / unit_krw:,.1f} units"
                + (f"  /  {n_units}" if n_units else ""))
        if fx:
            lines.append(f"(converted at current FX {fx:,.2f}; "
                         f"Toss holdings only — other brokers not included)")

        tk.Label(win, text='\n'.join(lines), font=_F_SEC_INFO, fg='#333',
                 justify='left', anchor='w').pack(anchor='w', padx=12, pady=(6, 10))

    # ── FX panel ───────────────────────────────────────────────────────────────

    def _build_fx_panel(self):
        """FX rate, its deviation from the 3-month average, and the dollar-switch
        tracker: a 7-rung ladder (-3%..+3%) with a clickable button per rung that
        records how many switch steps have been done, plus the trade amount."""
        f = tk.Frame(self.root, bd=1, relief='ridge', padx=12, pady=6)
        f.pack(fill='x', padx=5, pady=(0, 2))

        # Top line: rate, deviation, 3-month average, switch-pool summary.
        top = tk.Frame(f)
        top.pack(fill='x')
        tk.Label(top, text='FX Rate:', font=_F_HDR).pack(side='left', padx=(0, 2))
        tk.Label(top, textvariable=self.fx_var, font=_F_HDR_B).pack(side='left')
        self._fx_pct_lbl = tk.Label(top, textvariable=self.fx_pct_var,
                                    font=_F_HDR_B, width=8, anchor='w')
        self._fx_pct_lbl.pack(side='left', padx=(4, 12))
        tk.Label(top, text='3-Month Avg:', font=_F_SM, fg='#888').pack(side='left')
        tk.Label(top, textvariable=self.fx_avg_var, font=_F_HDR_B
                 ).pack(side='left', padx=(2, 16))
        tk.Label(top, textvariable=self.fx_pool_var, font=_F_SM, fg='#888'
                 ).pack(side='left')

        # Ladder grid: a left label column + 7 rung columns.
        grid = tk.Frame(f)
        grid.pack(fill='x', pady=(5, 0))
        tk.Label(grid, text='Target FX', font=_F_SM, fg='#888', anchor='e'
                 ).grid(row=0, column=0, sticky='e', padx=(0, 8))
        tk.Label(grid, text='Switched', font=_F_SM, fg='#888', anchor='e'
                 ).grid(row=1, column=0, sticky='e', padx=(0, 8))
        tk.Label(grid, text='Amount', font=_F_SM, fg='#888', anchor='e'
                 ).grid(row=2, column=0, sticky='e', padx=(0, 8))

        self.fx_switch_btns = {}
        self._fx_rung_lbls  = {}
        for col, k in enumerate(range(-3, 4), start=1):
            grid.grid_columnconfigure(col, weight=1, uniform='fx')
            rl = tk.Label(grid, textvariable=self._fx_rung_vars[k],
                          font=_F_FX_BANNER, fg='#555')
            rl.grid(row=0, column=col, padx=2, pady=(0, 2))
            self._fx_rung_lbls[k] = rl

            label = '0' if k == 0 else f'{k:+d}'
            b = tk.Button(grid, text=label, font=_F_FX_BTN, width=4,
                          command=lambda kk=k: self._on_fx_switch_click(kk))
            b.grid(row=1, column=col, padx=2, pady=1)
            self.fx_switch_btns[k] = b

            tk.Label(grid, textvariable=self._fx_amt_vars[k],
                     font=_F_SM, fg='#888').grid(row=2, column=col,
                                                 padx=2, pady=(2, 0))

        self._fx_btn_default_bg = self.fx_switch_btns[0].cget('bg')
        self._update_fx_buttons()

    # ── Rebuild ──────────────────────────────────────────────────────────────

    def _rebuild_sections(self):
        for w in self.content_frame.winfo_children():
            w.destroy()

        self.deployed_rows = []
        self.empty_rows    = []

        # Sort positions
        self.positions.sort(key=lambda p: stock_sort_key(p['ticker']))

        deployed = [p for p in self.positions if p.get('is_deployed')]
        empty    = [p for p in self.positions if not p.get('is_deployed')]

        # Deployed: biggest position first, by size in a common currency (USD
        # cost basis is converted to KRW via the FX rate). KR stocks are no
        # longer forced to the front — they sit wherever their size lands.
        # _apply_live re-grids these once the FX rate is known.
        deployed.sort(key=lambda p: self._norm_krw(
            p.get('shares', 0) * p.get('avg_cost', 0),
            'KRW' if p['ticker'].endswith('.KS') else 'USD'),
            reverse=True)

        # Empty: most volatile first (more volatile = more profitable under the
        # current strategy). Falls back to the fixed catalogue order for stocks
        # whose volatility is not yet known (e.g. before the first price fetch).
        # _apply_live re-grids these once fresh volatility arrives.
        empty.sort(key=lambda p: self._vol_order_key(
            p['ticker'], self._volatility.get(p['ticker'])))

        self._build_deployed(deployed)
        self._build_empty(empty)

    def _norm_krw(self, amount, currency):
        """Normalize a cash amount to KRW for cross-currency size comparison.
        Falls back to the raw amount when the FX rate is not yet known."""
        if currency == 'USD' and self._fx_rate:
            return amount * self._fx_rate
        return amount

    def _reorder_deployed(self):
        """Re-grid the deployed cards by size (largest first) once the FX rate
        is known, so KR and US positions interleave by true value. Skips while
        the FX rate is unknown so a transient fetch failure can't flip the order
        back to KR-first (USD sizes can't be normalized without FX)."""
        if not self.deployed_rows or not self._fx_rate:
            return
        ordered = sorted(
            self.deployed_rows,
            key=lambda r: self._norm_krw(_cb(r), r.currency), reverse=True)
        if ordered == self.deployed_rows:
            return
        for i, row in enumerate(ordered):
            r, c = divmod(i, 2)
            row.frame.grid_configure(row=r, column=c)
            row.set_row_num(i + 1)
        self.deployed_rows = ordered

    def _vol_order_key(self, ticker, vol):
        """Sort key for empty cards: highest 5-day volatility first, unknown
        volatility last, ties broken by the fixed catalogue order."""
        return (-vol if vol is not None else float('inf'),
                stock_sort_key(ticker))

    def _reorder_empty(self):
        """Re-grid the empty cards in place by current volatility so the most
        volatile sits on top. Avoids a full rebuild (keeps focus/entries)."""
        if not self.empty_rows:
            return
        ordered = sorted(
            self.empty_rows,
            key=lambda r: self._vol_order_key(r.ticker, r.volatility))
        if ordered == self.empty_rows:
            return
        for i, row in enumerate(ordered):
            r, c = divmod(i, 2)
            row.frame.grid_configure(row=r, column=c)
            row.set_row_num(i + 1)
        self.empty_rows = ordered

    # ── Deployed section ─────────────────────────────────────────────────────

    def _build_deployed(self, deployed):
        sec = tk.Frame(self.content_frame)
        sec.pack(fill='x', pady=(0, 6))

        hdr = tk.Frame(sec)
        hdr.pack(fill='x', pady=(4, 2))
        tk.Label(hdr, text='DEPLOYED STOCKS',
                 font=_F_SECTION).pack(side='left', padx=4)
        self._deploy_info_lbl = tk.Label(hdr, textvariable=self.deploy_info_var,
                                         font=_F_SEC_INFO, fg='#555')
        self._deploy_info_lbl.pack(side='left', padx=(12, 0))

        box = tk.Frame(sec, bd=1, relief='sunken', padx=4, pady=4)
        box.pack(fill='x', padx=2)

        if not deployed:
            tk.Label(box, text='(no deployed positions \u2014 fill Avg Cost & Shares below, then Save)',
                     fg='gray', font=_F_SM, pady=10).pack()
            return

        box.grid_columnconfigure(0, weight=1, uniform='dcol')
        box.grid_columnconfigure(1, weight=1, uniform='dcol')
        for i, pos in enumerate(deployed):
            ccy = 'KRW' if pos['ticker'].endswith('.KS') else 'USD'
            row = StockRow(
                parent=box, row_num=i + 1, pos=pos, deployed=True,
                get_unit_cash=lambda c=ccy: self._get_unit_cash(c),
                on_graph=self._on_graph,
                on_compute=self._on_row_compute,
                on_order=self._on_row_order)
            r, c = divmod(i, 2)
            row.frame.grid(row=r, column=c, sticky='nsew', padx=3, pady=2)
            self.deployed_rows.append(row)

    # ── Empty section ────────────────────────────────────────────────────────

    def _build_empty(self, empty):
        sec = tk.Frame(self.content_frame)
        sec.pack(fill='x', pady=(0, 6))

        hdr = tk.Frame(sec)
        hdr.pack(fill='x', pady=(4, 2))
        tk.Label(hdr, text='EMPTY STOCKS',
                 font=_F_SECTION).pack(side='left', padx=4)

        box = tk.Frame(sec, bd=1, relief='sunken', padx=4, pady=2)
        box.pack(fill='x', padx=2)

        box.grid_columnconfigure(0, weight=1, uniform='ecol')
        box.grid_columnconfigure(1, weight=1, uniform='ecol')
        for i, pos in enumerate(empty):
            ccy = 'KRW' if pos['ticker'].endswith('.KS') else 'USD'
            row = StockRow(
                parent=box, row_num=i + 1, pos=pos, deployed=False,
                get_unit_cash=lambda c=ccy: self._get_unit_cash(c),
                on_graph=self._on_graph,
                on_compute=self._on_row_compute)
            r, c = divmod(i, 2)
            row.frame.grid(row=r, column=c, sticky='nsew', padx=3, pady=3)
            self.empty_rows.append(row)
            row.compute()

    # ── Footer ───────────────────────────────────────────────────────────────

    def _build_footer(self):
        f = tk.Frame(self.root, bd=1, relief='ridge', padx=10, pady=6)
        f.pack(fill='x', padx=5, pady=(2, 5), side='bottom')
        tk.Button(f, text='Save & Refresh', command=self._on_save_refresh,
                  width=16, font=_F_BTN).pack(side='left', padx=4)
        tk.Label(f, text='Last Refresh:', font=_F_SM,
                 fg='#888').pack(side='left', padx=(16, 2))
        tk.Label(f, textvariable=self.last_refresh_var, font=_F_SM
                 ).pack(side='left', padx=(0, 16))
        tk.Label(f, textvariable=self.status_var, font=_F_SM,
                 anchor='w').pack(side='left', padx=4)

    # ── Graph ────────────────────────────────────────────────────────────────

    def _on_graph(self, ticker):
        """Open the chart popup using the row's already-computed ladder, so the
        empty (pseudo) and deployed charts draw through one unified path."""
        ohlc = self._ohlc_data.get(ticker, [])
        ccy  = 'KRW' if ticker.endswith('.KS') else 'USD'
        current_price = self._current_prices.get(ticker)

        ordered = self._toss_open_order_lines(ticker)
        for row in self.deployed_rows + self.empty_rows:
            if row.ticker == ticker:
                cd = row.chart_data()
                CandleChartWindow(
                    self.root, ticker, ohlc, ccy,
                    anchor_label=cd['anchor_label'],
                    anchor_price=cd['anchor_price'],
                    buy_lines=cd['buy_lines'],
                    sell_lines=cd['sell_lines'],
                    current_price=current_price,
                    ordered_lines=ordered)
                return

        # Fallback (ticker has no row yet)
        CandleChartWindow(self.root, ticker, ohlc, ccy,
                          current_price=current_price, ordered_lines=ordered)

    def _toss_provider(self):
        """A Toss provider for account/order reads (reuses the active provider
        when it's Toss; otherwise builds one). Returns None if unavailable."""
        if getattr(self._provider, 'name', '') == 'toss':
            return self._provider
        try:
            from providers.toss_market_provider import TossMarketProvider
            return TossMarketProvider.from_env()
        except Exception:
            return None

    def _toss_open_order_lines(self, ticker):
        """Live working orders for a ticker as chart-ready dicts
        {side, price, qty, status}. Empty list if Toss is unavailable."""
        prov = self._toss_provider()
        if prov is None:
            return []
        try:
            if self._toss_acct_seq is None:
                accts = prov.get_accounts()
                if not accts:
                    return []
                self._toss_acct_seq = accts[0]['accountSeq']
            orders = prov.get_open_orders(self._toss_acct_seq, ticker)
        except Exception:
            return []
        out = []
        for o in orders:
            try:
                price = float(o.get('price')) if o.get('price') not in (None, '') else None
            except (TypeError, ValueError):
                price = None
            if price is None:
                continue
            try:
                qty = int(float(o.get('quantity') or 0))
            except (TypeError, ValueError):
                qty = 0
            out.append({'side': o.get('side'), 'price': price, 'qty': qty,
                        'status': o.get('status')})
        return out

    def _on_row_compute(self):
        """Called when any deployed row recomputes — update army% across all."""
        if self._fx_rate:
            self._update_army(self._fx_rate)

    # ── Orders (DRY-RUN: compute + log only, no broker calls) ───────────────────

    def _on_row_order(self, row, side, active):
        """Handle a [Buy]/[Sell] latch on a deployed card. In DRY_RUN we only
        log the orders/cancels that WOULD be sent — nothing reaches Toss."""
        if not active:
            self._log_order(row, side, [], withdraw=True)
            self.status_var.set(
                f"DRY-RUN: withdraw {side} orders for {row.ticker} (logged)")
            return

        if side == 'SELL' and row.current_shares() <= 0:
            self.status_var.set(f"{row.ticker}: no shares to sell — blocked")
            row.set_order_active('SELL', False)
            return

        intents = row.order_intents(side)
        if not intents:
            self.status_var.set(f"{row.ticker}: no {side} lines to place")
            row.set_order_active(side, False)
            return

        # Toss rejects a buy and a sell pending on the same stock at once.
        opp = 'SELL' if side == 'BUY' else 'BUY'
        opp_active = (row.sell_active if side == 'BUY' else row.buy_active).get()
        warn = "  [warn: opposite-side latch also on — Toss would reject]" if opp_active else ""

        self._log_order(row, side, intents)
        self.status_var.set(
            f"DRY-RUN: would place {len(intents)} {side} order(s) for "
            f"{row.ticker} — see logs/orders.log{warn}")

    def _log_order(self, row, side, intents, withdraw=False):
        from core.calc import fmt_price
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        lines = [f"[{ts}] {ORDER_MODE} "
                 + (f"WITHDRAW {side} orders for {row.ticker}"
                    if withdraw else
                    f"PLACE {side} ({len(intents)}) for {row.ticker}:")]
        for it in intents:
            lines.append(
                f"    {it['side']} {it['label']}: {it['qty']} @ "
                f"{fmt_price(it['price'], it['currency'])} {it['currency']} "
                f"(LIMIT, DAY)")
        try:
            os.makedirs(os.path.dirname(_ORDERS_LOG), exist_ok=True)
            with open(_ORDERS_LOG, 'a', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
        except OSError:
            pass

    # ── Save & Refresh (the single main button) ─────────────────────────────

    def _on_save_refresh(self):
        """Collect inputs, auto-promote/demote, save, fetch prices, recompute."""
        self._collect()

        changed = False
        for pos in self.positions:
            # Auto-demote: deployed with shares=0 → empty
            if pos.get('is_deployed') and pos.get('shares', 0) <= 0:
                pos['is_deployed'] = False
                pos['shares'] = 0
                pos['avg_cost'] = 0.0
                pos['cost_basis'] = 0.0
                changed = True
            # Auto-promote: empty with shares>0 and avg_cost>0 → deployed
            elif (not pos.get('is_deployed')
                  and pos.get('shares', 0) > 0
                  and pos.get('avg_cost', 0) > 0):
                pos['is_deployed'] = True
                pos['cost_basis'] = pos['shares'] * pos['avg_cost']
                changed = True

        self._rebuild_sections()
        self._reapply()

        # Save to disk
        self._save_to_disk()

        # Fetch fresh prices
        self.status_var.set('Fetching prices...')
        threading.Thread(target=self._fetch_bg, daemon=True).start()

    # ── State collection ─────────────────────────────────────────────────────

    def _collect(self):
        states = {r.ticker: r.get_state() for r in self.deployed_rows}
        states.update({r.ticker: r.get_state() for r in self.empty_rows})
        for pos in self.positions:
            s = states.get(pos['ticker'])
            if s:
                for k in ('is_deployed', 'shares', 'avg_cost', 'cost_basis',
                          'buy_pct', 't1_pct', 't2_pct', 't3_pct',
                          't1_active', 't2_active', 't3_active', 'load_gear',
                          'auto_mode'):
                    if k in s:
                        pos[k] = s[k]

    def _reapply(self):
        if self._last_data:
            self._apply_live(self._last_data, self._fx_rate,
                             self._fx_avg_3m, quiet=True)

    # ── Live data ────────────────────────────────────────────────────────────

    def _fetch_bg(self):
        tickers = [p['ticker'] for p in self.positions]
        try:
            data, fx, fx_avg = self._provider.fetch_all(
                tickers, self.config.get('fx_ticker', 'USDKRW=X'))
        except Exception as e:
            self.root.after(0, lambda: self.status_var.set(f'Error: {e}'))
            return
        self.root.after(0, self._apply_live, data, fx, fx_avg)

    def _apply_live(self, data, fx_rate, fx_avg=None, quiet=False):
        self._last_data = data
        self._fx_rate   = fx_rate
        self._fx_avg_3m = fx_avg

        for t, d in data.items():
            if d.get('price'):      self._current_prices[t] = d['price']
            if d.get('5d_high'):    self._current_peaks[t]  = d['5d_high']
            if d.get('5d_ohlc'):    self._ohlc_data[t]      = d['5d_ohlc']
            if d.get('5d_closes'):  self._closes_data[t]    = d['5d_closes']
            vol = calc_volatility(d.get('5d_high'), d.get('5d_low'))
            if vol is not None:     self._volatility[t]     = vol

        self._update_fx_display()
        if fx_rate:
            self._update_unit_usd()

        # Update every row through the one unified signature.
        for row in self.deployed_rows + self.empty_rows:
            d = data.get(row.ticker, {})
            row.update_live(
                d.get('price'),
                d.get('5d_high'),
                d.get('5d_closes', []),
                volatility=calc_volatility(d.get('5d_high'), d.get('5d_low')))
            row.compute()

        # Re-order cards now that fresh data is known: deployed by size (FX
        # normalized), empty by volatility.
        self._reorder_deployed()
        self._reorder_empty()

        self._update_army(fx_rate)

        if not quiet:
            self.last_refresh_var.set(
                datetime.now().strftime('%Y-%m-%d  %H:%M:%S'))
            self.status_var.set('Ready')

    def _update_army(self, fx_rate):
        total = 0.0
        for r in self.deployed_rows:
            cb = _cb(r)
            total += cb * fx_rate if (r.currency == 'USD' and fx_rate) else cb

        # Denominator = full army (deployed + reserved), not just deployed
        try:
            n = int(self.N_var.get())
            unit_krw = float(self.unit_krw_var.get().replace(',', ''))
            full_army = n * unit_krw
        except (ValueError, ZeroDivisionError):
            full_army = 0.0

        for r in self.deployed_rows:
            cb = _cb(r)
            if full_army <= 0:
                r.set_army_pct(None); continue
            krw = cb * fx_rate if (r.currency == 'USD' and fx_rate) else cb
            r.set_army_pct(krw / full_army * 100.0)

        # Update section header deployed info
        try:
            n = int(self.N_var.get())
            unit_krw = float(self.unit_krw_var.get().replace(',', ''))
            if total > 0 and unit_krw > 0 and n > 0:
                units = total / unit_krw
                pct = units / n * 100
                self.deploy_info_var.set(
                    f"(Deployed: \u20a9{total:,.0f}    "
                    f"Ratio: {units:.1f}/{n} = {pct:.1f}%)")
            else:
                self.deploy_info_var.set('')
        except (ValueError, ZeroDivisionError):
            self.deploy_info_var.set('')

    # ── Save ─────────────────────────────────────────────────────────────────

    def _save_to_disk(self):
        save_positions(self.positions)
        try:
            cfg = {
                'N':                  int(self.N_var.get()),
                'unit_cash_krw':      int(float(self.unit_krw_var.get().replace(',', ''))),
                'unit_cash_usd':      float(self.unit_usd_var.get().replace(',', '')),
                'fx_ticker':          self.config.get('fx_ticker', 'USDKRW=X'),
                'peak_lookback_days': self.config.get('peak_lookback_days', 5),
                'fx_switch_level':    self.fx_switch_level,
                'market_provider':    self.config.get('market_provider', 'yahoo'),
            }
            save_config(cfg)
            self.config = cfg
        except Exception:
            pass


def _cb(row) -> float:
    try:
        return float(row.shares_var.get().replace(',', '')) \
             * float(row.avg_cost_var.get().replace(',', ''))
    except (ValueError, TypeError):
        return 0.0
