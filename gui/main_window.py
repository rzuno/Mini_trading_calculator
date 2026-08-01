import tkinter as tk
import threading
from datetime import datetime

from core.calc import (DEFAULT_EXIT_TIER, stock_sort_key, calc_volatility,
                       fx_dev_color)
from core.csv_io import load_config, save_config, load_positions, save_positions
from providers import get_provider
from gui.stock_row import StockRow
from gui.autopilot_ctrl import AutopilotController

# ── Fonts (1.3× scale for QHD) ──────────────────────────────────────────────
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
        self._place_window()

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
        # Toss (auto): numbers come from the Toss account (read-only cards).
        # Yahoo (manual): the old CSV/typed workflow.
        self._auto = (getattr(self._provider, 'name', 'yahoo') == 'toss')
        self.provider_var = tk.StringVar(value=self._mode_label())

        # Full watchlist + per-ticker gear preferences (auto mode overlays Toss
        # shares/avg onto these).
        self._catalogue = [p['ticker'] for p in self.positions]
        self._gear_prefs = {
            p['ticker']: {k: p.get(k) for k in (
                'tier', 'gear', 'exit_tier', 'load_gear', 'buy_pct',
                't1_pct', 't2_pct', 't3_pct',
                't1_active', 't2_active', 't3_active', 'auto_mode')}
            for p in self.positions}
        self._last_account = None
        self._last_open_orders = []

        # Sort positions in fixed order on load
        self.positions.sort(key=lambda p: stock_sort_key(p['ticker']))

        self._fx_rate        = None
        self._fx_avg_3m      = None
        self._toss_acct_seq  = None   # cached Toss accountSeq for order/account reads
        self._full_army_krw  = 0.0     # deployed + cash + reserved buy orders
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
        self.last_refresh_var = tk.StringVar(value='--')
        self.status_var       = tk.StringVar(value='Initializing...')
        self.deploy_info_var  = tk.StringVar(value='')
        self.banner_var       = tk.StringVar(value='')   # cash / army summary

        # Autopilot (always OFF at launch; armed from the card button).
        self.deployed_rows = []
        self.empty_rows    = []
        self.autopilot = AutopilotController(self)

        # ── Build layout ────────────────────────────────────────────────────
        self._build_header()

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

        # Wheel scrolls the list from anywhere in the main window (not only when
        # the cursor is over the bare canvas / scrollbar).
        self.root.bind_all('<MouseWheel>', self._mwheel)

        self._rebuild_sections()

        # Auto-derive USD when KRW changes (header only)
        self.unit_krw_var.trace_add('write', lambda *_: self._update_unit_usd())

        if self._provider_init_note:
            self.status_var.set(self._provider_init_note)

        # Auto-refresh on launch
        self.root.after(300, self._on_save_refresh)

    def _mwheel(self, event):
        # Ignore wheel events that belong to a popup (Toplevel) so they don't
        # scroll the main list underneath.
        try:
            if event.widget.winfo_toplevel() is not self.root:
                return
        except Exception:
            pass
        self._canvas.yview_scroll(-1 * (event.delta // 120), 'units')

    def _place_window(self):
        """Top-anchored, near-full-height, wide — so all 16 cards fit at once
        without scrolling, and wide enough that long KR numbers aren't clipped."""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w  = min(1980, sw - 20)
        h  = sh - 70                 # leave room for the taskbar
        x  = max(0, (sw - w) // 2)
        self.root.geometry(f'{w}x{h}+{x}+0')   # y=0: top edge at screen top

    # ── Unit cash ────────────────────────────────────────────────────────────

    def _get_unit_cash(self, currency: str) -> float:
        try:
            if currency == 'KRW':
                return float(self.unit_krw_var.get().replace(',', ''))
            return float(self.unit_usd_var.get().replace(',', ''))
        except ValueError:
            return 0.0

    def _get_total_units(self) -> float:
        try:
            return float(self.N_var.get().replace(',', ''))
        except ValueError:
            return 0.0

    def _update_fx_header(self):
        """Compact header FX: rate + deviation from the 3-month average. The
        full ladder/switch tracker lives in the FX ▸ popup."""
        rate = self._fx_rate
        avg  = self._fx_avg_3m
        if not rate:
            self.fx_var.set('N/A')
            self.fx_pct_var.set('')
            return
        self.fx_var.set(f"{rate:,.0f}")
        if avg and avg > 0:
            pct = (rate - avg) / avg * 100.0
            self.fx_pct_var.set(f"({pct:+.1f}%)")
            self._fx_pct_lbl.config(fg=fx_dev_color(pct))
        else:
            self.fx_pct_var.set('')

    def _show_fx_detail(self):
        """Popup with the 3-month average, the −3%…+3% target ladder, the
        dollar-switch buttons, and the per-rung trade amounts."""
        rate = self._fx_rate
        avg  = self._fx_avg_3m
        win = tk.Toplevel(self.root)
        win.title('FX detail')
        win.geometry('660x250')
        if not rate:
            tk.Label(win, text='No FX data yet — refresh first.',
                     font=_F_HDR).pack(padx=20, pady=20)
            return
        head = tk.Frame(win)
        head.pack(anchor='w', padx=12, pady=(10, 2))
        tk.Label(head, text=f"FX Rate: {rate:,.2f}",
                 font=_F_HDR_B).pack(side='left')
        if avg and avg > 0:
            pct = (rate - avg) / avg * 100.0
            tk.Label(head, text=f"({pct:+.1f}%)", font=_F_HDR_B,
                     fg=fx_dev_color(pct)).pack(side='left', padx=(6, 12))
            tk.Label(head, text=f"3-Month Avg: {avg:,.0f}",
                     font=_F_HDR_B).pack(side='left')
        if not (avg and avg > 0):
            tk.Label(win, text='(3-month average unavailable)',
                     fg='#888').pack(anchor='w', padx=12)
            return

        pool = self._switch_pool_krw()
        cur  = self._fx_threshold()
        tk.Label(win, text=f"Switch pool ⅓ ≈ ${pool / avg:,.0f} / ₩{pool:,.0f}"
                           f"   (each step = |k|/6 of pool)",
                 font=_F_SM, fg='#888').pack(anchor='w', padx=12)

        grid = tk.Frame(win)
        grid.pack(fill='x', padx=12, pady=10)
        for r, t in enumerate(('Target', 'Switched', 'Amount')):
            tk.Label(grid, text=t, font=_F_SM, fg='#888', anchor='e'
                     ).grid(row=r, column=0, sticky='e', padx=(0, 8))

        self.fx_switch_btns = {}
        for col, k in enumerate(range(-3, 4), start=1):
            grid.grid_columnconfigure(col, weight=1, uniform='fx')
            v   = avg * (1 + k / 100.0)
            tag = '3m avg' if k == 0 else f'{k:+d}%'
            tk.Label(grid, text=f"{v:,.0f}\n({tag})",
                     font=(_F_FX_BANNER_B if k == cur else _F_FX_BANNER),
                     fg=('black' if k == cur else '#555')
                     ).grid(row=0, column=col, padx=2)
            b = tk.Button(grid, text=('0' if k == 0 else f'{k:+d}'),
                          font=_F_FX_BTN, width=4,
                          command=lambda kk=k: self._on_fx_switch_click(kk))
            b.grid(row=1, column=col, padx=2, pady=1)
            self.fx_switch_btns[k] = b
            if k == 0:
                amt = 'reset'
            else:
                amt_usd = abs(k) / 6.0 * pool / avg
                amt = f"{'sell' if k > 0 else 'buy'}\n${amt_usd:,.0f}"
            tk.Label(grid, text=amt, font=_F_SM, fg='#888'
                     ).grid(row=2, column=col, padx=2)

        self._fx_btn_default_bg = self.fx_switch_btns[0].cget('bg')
        self._update_fx_buttons()
        win.protocol('WM_DELETE_WINDOW',
                     lambda: (self.fx_switch_btns.clear(), win.destroy()))

    # ── FX dollar-switch tracker ──────────────────────────────────────────────

    def _switch_pool_krw(self) -> float:
        """The switchable third of total capital, in KRW (= N × unit_krw / 3)."""
        try:
            n        = self._get_total_units()
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

        _lbl('Total Units:')
        self._n_entry = tk.Entry(f, textvariable=self.N_var, width=7,
                                 justify='right', font=_F_HDR_B)
        self._n_entry.grid(row=0, column=c, padx=2); c += 1
        _lbl('1 Unit (KRW):');   _entry(self.unit_krw_var, 12)
        _lbl('1 Unit (USD):');   _val(self.unit_usd_var, width=8, fg='#555')

        # Compact FX: rate + deviation; the ladder/switch detail is a popup.
        _lbl('FX:')
        tk.Label(f, textvariable=self.fx_var, font=_F_HDR_B
                 ).grid(row=0, column=c, padx=(2, 0)); c += 1
        self._fx_pct_lbl = tk.Label(f, textvariable=self.fx_pct_var,
                                    font=_F_HDR_B, width=8, anchor='w')
        self._fx_pct_lbl.grid(row=0, column=c, padx=(0, 2)); c += 1
        tk.Button(f, text='FX status', font=_F_SM, command=self._show_fx_detail
                  ).grid(row=0, column=c, padx=(0, 4)); c += 1

        # Data mode: Toss (auto) = numbers from the Toss account, read-only
        # cards, live orders; Yahoo (manual) = typed CSV workflow.
        _lbl('Data:')
        self._provider_om = tk.OptionMenu(
            f, self.provider_var, 'Toss (auto)', 'Yahoo (manual)',
            command=lambda v: self._switch_provider(v))
        self._provider_om.config(width=13)
        menu = self._provider_om['menu']
        try:
            menu.entryconfig(menu.index('Toss (auto)'), font=_F_HDR_B)
            menu.entryconfig(menu.index('Yahoo (manual)'), font=_F_HDR)
        except tk.TclError:
            pass
        self._provider_om.grid(row=0, column=c, padx=2); c += 1
        self._refresh_provider_button()

        # Save & Refresh pinned to the top-right corner (spacer column expands).
        f.grid_columnconfigure(c, weight=1)
        tk.Button(f, text='Save & Refresh', command=self._on_save_refresh,
                  width=16, font=_F_BTN).grid(row=0, column=c + 1, sticky='e',
                                              padx=(8, 2))
        span = c + 2

        # Second header row: cash/army banner (left) + last-refresh & status
        # (right). Replaces the bottom footer so all 16 cards fit without scroll.
        info = tk.Frame(f)
        info.grid(row=1, column=0, columnspan=span, sticky='ew', pady=(4, 0))
        tk.Label(info, textvariable=self.banner_var, font=_F_SEC_INFO,
                 fg='#333', anchor='w').pack(side='left', padx=(12, 2))
        tk.Label(info, textvariable=self.status_var, font=_F_SM, anchor='e'
                 ).pack(side='right', padx=(6, 2))
        tk.Label(info, textvariable=self.last_refresh_var, font=_F_SM
                 ).pack(side='right')
        tk.Label(info, text='Last:', font=_F_SM, fg='#888'
                 ).pack(side='right', padx=(0, 2))
        self._apply_mode_ui()

    def _mode_label(self):
        return 'Toss (auto)' if self._auto else 'Yahoo (manual)'

    def _apply_mode_ui(self):
        """Reflect the data mode: N is auto-computed (read-only) in Toss mode,
        typed in manual mode."""
        self._n_entry.config(state='readonly' if self._auto else 'normal')
        self._refresh_provider_button()

    def _refresh_provider_button(self):
        """Bold the selector when Toss (auto) — the default — is active."""
        self._provider_om.config(font=_F_HDR_B if self._auto else _F_HDR)

    def _switch_provider(self, label: str):
        """Switch data mode at runtime. Reverts cleanly if Toss can't init."""
        name = 'toss' if str(label).lower().startswith('toss') else 'yahoo'
        current = getattr(self._provider, 'name', 'yahoo')
        if name == current:
            return
        try:
            prov = get_provider({'market_provider': name})
        except Exception as e:
            self.status_var.set(f"Cannot use {name.title()}: {e}")
            self.provider_var.set(self._mode_label())
            return
        self._provider = prov
        self._auto = (name == 'toss')
        self.config['market_provider'] = name
        self.provider_var.set(self._mode_label())
        self._apply_mode_ui()
        self.status_var.set(f"Mode → {self._mode_label()}; refreshing…")
        self._on_save_refresh()

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

        # Until live gaps are computed, keep deployed in the catalogue order.
        # _apply_live re-grids deployed and empty by gap after row.compute().
        deployed.sort(key=lambda p: stock_sort_key(p['ticker']))

        # Empty rows still get a useful pre-live fallback: most volatile first,
        # then _apply_live re-grids them by gap once fresh prices compute.
        empty.sort(key=lambda p: self._vol_order_key(
            p['ticker'], self._volatility.get(p['ticker'])))

        # One continuous 2-column grid — no section headers or boundary. Deployed
        # cards (bold + "DEPLOYED" tag) first, then empty. Live refresh sorts
        # both groups by gap.
        box = tk.Frame(self.content_frame)
        box.pack(fill='both', expand=True, padx=2, pady=2)
        box.grid_columnconfigure(0, weight=1, uniform='col')
        box.grid_columnconfigure(1, weight=1, uniform='col')
        for pos in deployed:
            self.deployed_rows.append(self._make_card(box, pos, True))
        for pos in empty:
            row = self._make_card(box, pos, False)
            self.empty_rows.append(row)
            row.compute()
        self._grid_all_cards()

    def _make_card(self, box, pos, deployed):
        ccy = 'KRW' if pos['ticker'].endswith('.KS') else 'USD'
        return StockRow(
            parent=box, row_num=0, pos=pos, deployed=deployed,
            get_unit_cash=lambda c=ccy: self._get_unit_cash(c),
            on_compute=self._on_row_compute,
            editable=not self._auto,
            on_autopilot=self._open_autopilot)

    def _grid_all_cards(self):
        """Deployed cards first, then empty, in one 2-column grid; renumber."""
        for i, row in enumerate(self.deployed_rows + self.empty_rows):
            r, c = divmod(i, 2)
            row.frame.grid(row=r, column=c, sticky='nsew', padx=3, pady=3)
            row.set_row_num(i + 1)

    def _vol_order_key(self, ticker, vol):
        """Sort key for empty cards: highest 5-day volatility first, unknown
        volatility last, ties broken by the fixed catalogue order."""
        return (-vol if vol is not None else float('inf'),
                stock_sort_key(ticker))

    def _gap_order_key(self, row):
        """Highest gap first: +1% above -1%, then -2%, and so on."""
        return (-row._gap if row._gap is not None else float('inf'),
                stock_sort_key(row.ticker))

    def _reorder_cards(self):
        """Re-sort + re-grid all cards on fresh data — **everything by gap**,
        deployed and flat alike, highest first.

        For a deployed card the gap is price vs the average cost, so the ones
        closest to their exit rise. For a flat card it is price vs the vantage,
        so the ones closest to their LOAD rise. Either way the top of the
        screen is what is about to happen."""
        self.deployed_rows.sort(key=self._gap_order_key)
        self.empty_rows.sort(key=self._gap_order_key)
        self._grid_all_cards()

    # ── Autopilot window (big card button) ────────────────────────────────────

    def _open_autopilot(self, ticker):
        """The card's V-COMMANDOS button: start watching the stock (bare WATCH
        mode — polling only, no orders) and pop its campaign cockpit.
        Reuses an already-open window instead of stacking duplicates."""
        if not self._auto:
            self.status_var.set('Autopilot needs Toss (auto) mode.')
            return
        if not hasattr(self, '_ap_windows'):
            self._ap_windows = {}
        win = self._ap_windows.get(ticker)
        if win is not None:
            try:
                if win.win.winfo_exists():
                    win.win.lift()
                    return
            except tk.TclError:
                pass
            self._ap_windows.pop(ticker, None)
        ok, msg = self.autopilot.watch(ticker)
        if not ok:
            self.status_var.set(f'Autopilot: {msg}')
            return
        from gui.campaign_window import CampaignWindow
        ccy = 'KRW' if ticker.endswith('.KS') else 'USD'
        self._ap_windows[ticker] = CampaignWindow(
            self.root, ticker, ccy, self.autopilot.graph_context(ticker))

    def _account_seq(self, prov):
        if self._toss_acct_seq is None:
            accts = prov.get_accounts()
            if not accts:
                return None
            self._toss_acct_seq = accts[0]['accountSeq']
        return self._toss_acct_seq

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

    def _order_ticker(self, order):
        sym = order.get('symbol') or ''
        ccy = order.get('currency')
        country = order.get('marketCountry')
        if country == 'KR' or ccy == 'KRW' or sym.isdigit():
            return sym + '.KS'
        return sym

    def _order_open_qty(self, order) -> float:
        def num(v, default=0.0):
            try:
                if v in (None, ''):
                    return default
                return float(v)
            except (TypeError, ValueError):
                return default

        qty = num(order.get('quantity'))
        execution = order.get('execution') or {}
        filled = num(execution.get('filledQuantity'))
        return max(0.0, qty - filled)

    def _buy_orders_krw(self, fx_rate=None) -> float:
        """Capital reserved by live BUY orders, normalized to KRW."""
        fx = fx_rate if fx_rate is not None else self._fx_rate
        total = 0.0
        for o in (self._last_open_orders or []):
            if o.get('side') != 'BUY':
                continue
            try:
                price = float(o.get('price')) if o.get('price') not in (None, '') else 0.0
            except (TypeError, ValueError):
                price = 0.0
            if price <= 0:
                price = self._current_prices.get(self._order_ticker(o)) or 0.0
            qty = self._order_open_qty(o)
            if price <= 0 or qty <= 0:
                continue
            amount = price * qty
            ccy = o.get('currency') or ('KRW' if self._order_ticker(o).endswith('.KS') else 'USD')
            if ccy == 'KRW':
                total += amount
            elif fx:
                total += amount * fx
        return total

    def _on_row_compute(self):
        """Called when any row recomputes (gear change, tier switch, typed
        input) — update army% across the cards and push every card's gear
        config to the autopilot. The card IS the campaign strategy, so a gear
        the commander changes here reaches the bot on its next poll."""
        if self._fx_rate:
            self._update_army(self._fx_rate)
        for row in self.deployed_rows + self.empty_rows:
            if self.autopilot.is_enabled(row.ticker):
                self.autopilot.set_card_config(row.ticker, row.line_config())

    # ── Save & Refresh (the single main button) ─────────────────────────────

    def _on_save_refresh(self):
        """Collect inputs, auto-promote/demote, save, fetch prices, recompute."""
        self._collect()

        # Manual mode promotes/demotes from typed shares; Toss mode derives
        # deployment from the account instead (see _reconcile_from_toss).
        if not self._auto:
            for pos in self.positions:
                if pos.get('is_deployed') and pos.get('shares', 0) <= 0:
                    pos['is_deployed'] = False
                    pos['shares'] = 0
                    pos['avg_cost'] = 0.0
                    pos['cost_basis'] = 0.0
                elif (not pos.get('is_deployed')
                      and pos.get('shares', 0) > 0
                      and pos.get('avg_cost', 0) > 0):
                    pos['is_deployed'] = True
                    pos['cost_basis'] = pos['shares'] * pos['avg_cost']
                    pos['exit_tier'] = DEFAULT_EXIT_TIER

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
                          'gear', 'exit_tier',
                          'buy_pct', 't1_pct', 't2_pct', 't3_pct',
                          't1_active', 't2_active', 't3_active', 'load_gear',
                          'auto_mode'):
                    if k in s:
                        pos[k] = s[k]

    def _reapply(self):
        if self._last_data:
            self._apply_live(self._last_data, self._fx_rate, self._fx_avg_3m,
                             self._last_account, self._last_open_orders,
                             quiet=True)

    def _apply_order_states(self):
        """Tag each card with its live Toss order side (BUY/SELL/None) so the
        title shows 'buy/sell ordered' and the gear locks — even for orders
        placed in the web. A stock can't have both sides pending."""
        by_ticker = {}
        for o in (self._last_open_orders or []):
            sym = o.get('symbol') or ''
            ticker = (sym + '.KS') if sym.isdigit() else sym
            by_ticker.setdefault(ticker, o.get('side'))
        for row in self.deployed_rows + self.empty_rows:
            row.set_order_state(by_ticker.get(row.ticker))

    # ── Live data ────────────────────────────────────────────────────────────

    def _fetch_bg(self):
        tickers = [p['ticker'] for p in self.positions]
        try:
            data, fx, fx_avg = self._provider.fetch_all(
                tickers, self.config.get('fx_ticker', 'USDKRW=X'))
        except Exception as e:
            self.root.after(0, lambda: self.status_var.set(f'Error: {e}'))
            return
        # In Toss(auto) mode also read the account (holdings + cash) and the
        # live open orders so the cards/army size and the order-state tags come
        # straight from the broker.
        account, open_orders = None, []
        if self._auto:
            try:
                prov = self._toss_provider()
                if prov:
                    account = prov.account_snapshot()
                    seq = self._account_seq(prov)
                    open_orders = prov.get_open_orders(seq) if seq else []
            except Exception:
                account, open_orders = None, []
        self.root.after(0, self._apply_live, data, fx, fx_avg, account,
                        open_orders)

    def _reconcile_from_toss(self, account):
        """Overlay Toss holdings onto the catalogue: held tickers become
        deployed with the broker's shares/avg; everything else is empty. Gear
        preferences already on each position are preserved."""
        held = {it['ticker']: it for it in (account.get('items') or [])}
        for pos in self.positions:
            it = held.get(pos['ticker'])
            if it:
                was_deployed = bool(pos.get('is_deployed'))
                pos['is_deployed'] = True
                pos['shares'] = int(round(it.get('shares') or 0))
                pos['avg_cost'] = it.get('avg') or 0.0
                pos['cost_basis'] = pos['shares'] * pos['avg_cost']
                if not was_deployed:
                    # A fresh campaign starts on the standing default exit.
                    pos['exit_tier'] = DEFAULT_EXIT_TIER
            else:
                pos['is_deployed'] = False
                pos['shares'] = 0
                pos['avg_cost'] = 0.0
                pos['cost_basis'] = 0.0

    def _apply_live(self, data, fx_rate, fx_avg=None, account=None,
                    open_orders=None, quiet=False):
        self._last_data = data
        self._fx_rate   = fx_rate
        self._fx_avg_3m = fx_avg
        if account is not None:
            self._last_account = account
        if open_orders is not None:
            self._last_open_orders = open_orders

        for t, d in data.items():
            if d.get('price'):      self._current_prices[t] = d['price']
            if d.get('5d_high'):    self._current_peaks[t]  = d['5d_high']
            if d.get('5d_ohlc'):    self._ohlc_data[t]      = d['5d_ohlc']
            if d.get('5d_closes'):  self._closes_data[t]    = d['5d_closes']
            vol = calc_volatility(d.get('5d_high'), d.get('5d_low'))
            if vol is not None:     self._volatility[t]     = vol

        # Toss mode: derive deployment/shares/avg from the account, then rebuild.
        if self._auto and self._last_account is not None:
            self._reconcile_from_toss(self._last_account)
            self._rebuild_sections()

        self._update_fx_header()
        if fx_rate:
            self._update_unit_usd()

        # Update every row through the one unified signature. The VANTAGE is
        # High5 — the highest completed-session high of the previous five
        # trading days (Gearbox manual §9.1) — with the previous close as the
        # fallback when the 5-day high is not known yet. The campaign bot
        # watches exactly the lines these cards draw.
        for row in self.deployed_rows + self.empty_rows:
            d = data.get(row.ticker, {})
            high5 = d.get('5d_high')
            row.update_live(
                d.get('price'),
                vantage=high5 or d.get('prev_close'),
                vantage_src='high5' if high5 else 'close',
                volatility=calc_volatility(d.get('5d_high'), d.get('5d_low')))

        for row in self.deployed_rows + self.empty_rows:
            row.compute()

        # Re-order all cards now that fresh data is known.
        self._reorder_cards()

        # Tag each card with its live order side (and lock the gear) from Toss.
        self._apply_order_states()

        self._update_banner()      # sets auto N before army% uses it
        self._update_army(fx_rate)

        # Cards were rebuilt — re-apply autopilot badges + cached units.
        self.autopilot.on_rows_rebuilt()

        if not quiet:
            self.last_refresh_var.set(
                datetime.now().strftime('%Y-%m-%d  %H:%M:%S'))
            self.status_var.set('Ready')

    def _update_army(self, fx_rate):
        total = 0.0
        for r in self.deployed_rows:
            cb = _cb(r)
            total += cb * fx_rate if (r.currency == 'USD' and fx_rate) else cb

        # Denominator = full army (deployed + cash + reserved orders), not just deployed.
        try:
            n = self._get_total_units()
            unit_krw = float(self.unit_krw_var.get().replace(',', ''))
            full_army = self._full_army_krw or (n * unit_krw)
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
            n = self._get_total_units()
            unit_krw = float(self.unit_krw_var.get().replace(',', ''))
            if total > 0 and unit_krw > 0 and n > 0:
                units = total / unit_krw
                pct = units / n * 100
                self.deploy_info_var.set(
                    f"(Deployed: \u20a9{total:,.0f}    "
                    f"Ratio: {units:.2f}/{n:.2f} = {pct:.1f}%)")
            else:
                self.deploy_info_var.set('')
        except (ValueError, ZeroDivisionError):
            self.deploy_info_var.set('')

    def _army_snapshot(self):
        fx = self._fx_rate

        def val_krw(ticker, shares):
            p = self._current_prices.get(ticker)
            if not p or not shares:
                return 0.0
            if ticker.endswith('.KS'):
                return p * shares
            return p * shares * fx if fx else 0.0

        deployed_krw = sum(val_krw(r.ticker, r.current_shares())
                           for r in self.deployed_rows)

        acct = self._last_account or {}
        cash_krw = acct.get('cash_krw') or 0
        cash_usd = acct.get('cash_usd') or 0
        reserve_krw = cash_krw + (cash_usd * fx if fx else 0)
        ordered_krw = self._buy_orders_krw(fx)
        total_krw = deployed_krw + reserve_krw + ordered_krw

        try:
            unit_krw = float(self.unit_krw_var.get().replace(',', ''))
        except ValueError:
            unit_krw = 0.0

        return {
            'deployed_krw': deployed_krw,
            'cash_krw': cash_krw,
            'cash_usd': cash_usd,
            'reserve_krw': reserve_krw,
            'ordered_krw': ordered_krw,
            'total_krw': total_krw,
            'unit_krw': unit_krw,
        }

    def _update_banner(self):
        """Top-line cash + army summary. In Toss(auto) mode it also computes the
        total unit count from deployed + cash + reserved buy orders."""
        if not self._auto:
            self._full_army_krw = 0.0
            self.banner_var.set('')
            return
        snap = self._army_snapshot()
        deployed_krw = snap['deployed_krw']
        cash_krw = snap['cash_krw']
        cash_usd = snap['cash_usd']
        reserve_krw = snap['reserve_krw']
        ordered_krw = snap['ordered_krw']
        total_krw = snap['total_krw']
        unit_krw = snap['unit_krw']
        self._full_army_krw = total_krw

        if unit_krw > 0 and total_krw > 0:
            self.N_var.set(f"{total_krw / unit_krw:.2f}")

        def u(x):
            return f"{x / unit_krw:,.2f}u" if unit_krw > 0 else "--"

        dep_pct = (deployed_krw / total_krw * 100) if total_krw > 0 else 0
        self.banner_var.set(
            f"Cash: ₩{cash_krw:,.0f} + ${cash_usd:,.0f}     "
            f"Deployed: {u(deployed_krw)} ({dep_pct:.0f}%)     "
            f"Ordered: {u(ordered_krw)}     "
            f"Reserve: {u(reserve_krw)}     "
            f"Total: ₩{total_krw:,.0f} = {self.N_var.get()} units")

    # ── Save ─────────────────────────────────────────────────────────────────

    def _save_to_disk(self):
        save_positions(self.positions)
        try:
            cfg = {
                'N':                  round(self._get_total_units(), 2),
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
