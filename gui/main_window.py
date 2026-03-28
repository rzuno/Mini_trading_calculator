import tkinter as tk
import threading
from datetime import datetime

from core.calc import stock_sort_key
from core.csv_io import load_config, save_config, load_positions, save_positions
from core.data_feed import fetch_all
from gui.deployed_row import DeployedRow
from gui.empty_row import EmptyRow
from gui.candle_chart import CandleChartWindow

# ── Fonts (1.3× scale for QHD) ──────────────────────────────────────────────
_F_SECTION = ('Segoe UI', 16, 'bold')
_F_HDR     = ('Segoe UI', 13)
_F_HDR_B   = ('Segoe UI', 13, 'bold')
_F_BTN     = ('Segoe UI', 13, 'bold')
_F_SM      = ('Segoe UI', 10)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AI Seesaw Mini-Calculator")
        self.root.geometry('1800x1200')
        self.root.minsize(1400, 800)

        self.config    = load_config()
        self.positions = load_positions()

        # Sort positions in fixed order on load
        self.positions.sort(key=lambda p: stock_sort_key(p['ticker']))

        self._fx_rate        = None
        self._current_prices = {}
        self._current_peaks  = {}
        self._ohlc_data      = {}
        self._closes_data    = {}
        self._last_data      = {}

        # Header vars
        self.N_var            = tk.StringVar(value=str(self.config['N']))
        self.unit_krw_var     = tk.StringVar(value=f"{self.config['unit_cash_krw']:,}")
        self.unit_usd_var     = tk.StringVar(value=str(self.config['unit_cash_usd']))
        self.fx_var           = tk.StringVar(value='--')
        self.last_refresh_var = tk.StringVar(value='--')
        self.status_var       = tk.StringVar(value='Initializing...')
        self.deploy_total_var = tk.StringVar(value='--')
        self.deploy_ratio_var = tk.StringVar(value='--')

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
        self._canvas.create_window((0, 0), window=self.content_frame, anchor='nw')
        self.content_frame.bind(
            '<Configure>',
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox('all')))

        self._canvas.bind('<Enter>',
            lambda e: self._canvas.bind_all('<MouseWheel>', self._mwheel))
        self._canvas.bind('<Leave>',
            lambda e: self._canvas.unbind_all('<MouseWheel>'))

        self._build_footer()
        self._rebuild_sections()

        # Auto-derive USD when KRW changes (header only)
        self.unit_krw_var.trace_add('write', lambda *_: self._update_unit_usd())

        # Auto-refresh on launch
        self.root.after(300, self._on_save_refresh)

    def _mwheel(self, event):
        self._canvas.yview_scroll(-1 * (event.delta // 120), 'units')

    # ── Unit cash ────────────────────────────────────────────────────────────

    def _get_unit_cash(self, currency: str) -> float:
        try:
            if currency == 'KRW':
                return float(self.unit_krw_var.get().replace(',', ''))
            return float(self.unit_usd_var.get().replace(',', ''))
        except ValueError:
            return 0.0

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
        _lbl('FX Rate:');        _val(self.fx_var, width=9)
        _lbl('Last Refresh:');   _val(self.last_refresh_var, width=18)
        _lbl('Deployed:');       _val(self.deploy_total_var, width=14)
        _lbl('Ratio:');          _val(self.deploy_ratio_var, width=16)

    # ── Rebuild ──────────────────────────────────────────────────────────────

    def _rebuild_sections(self):
        for w in self.content_frame.winfo_children():
            w.destroy()

        self.deployed_rows = []
        self.empty_rows    = []

        # Sort positions in fixed order
        self.positions.sort(key=lambda p: stock_sort_key(p['ticker']))

        deployed = [p for p in self.positions if p.get('is_deployed')]
        empty    = [p for p in self.positions if not p.get('is_deployed')]

        self._build_deployed(deployed)
        self._build_empty(empty)

    # ── Deployed section ─────────────────────────────────────────────────────

    def _build_deployed(self, deployed):
        sec = tk.Frame(self.content_frame)
        sec.pack(fill='x', pady=(0, 6))

        hdr = tk.Frame(sec)
        hdr.pack(fill='x', pady=(4, 2))
        tk.Label(hdr, text='DEPLOYED STOCKS',
                 font=_F_SECTION).pack(side='left', padx=4)

        box = tk.Frame(sec, bd=1, relief='sunken', padx=4, pady=4)
        box.pack(fill='x', padx=2)

        if not deployed:
            tk.Label(box, text='(no deployed positions \u2014 fill Avg Cost & Shares below, then Save)',
                     fg='gray', font=_F_SM, pady=10).pack()
            return

        for i, pos in enumerate(deployed):
            row = DeployedRow(
                parent=box, row_num=i + 1, pos=pos,
                on_graph=self._on_graph,
                on_compute=self._on_row_compute)
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

        for i, pos in enumerate(empty):
            if i > 0:
                tk.Frame(box, height=1, bg='#D0D0D0').pack(fill='x', pady=1)
            ccy = 'KRW' if pos['ticker'].endswith('.KS') else 'USD'
            row = EmptyRow(
                parent=box, row_num=i + 1, pos=pos,
                get_unit_cash=lambda c=ccy: self._get_unit_cash(c),
                on_graph=self._on_graph)
            self.empty_rows.append(row)
            row.compute()

    # ── Footer ───────────────────────────────────────────────────────────────

    def _build_footer(self):
        f = tk.Frame(self.root, bd=1, relief='ridge', padx=10, pady=6)
        f.pack(fill='x', padx=5, pady=(2, 5), side='bottom')
        tk.Button(f, text='Save & Refresh', command=self._on_save_refresh,
                  width=16, font=_F_BTN).pack(side='left', padx=4)
        tk.Label(f, textvariable=self.status_var, font=_F_SM,
                 anchor='w').pack(side='left', padx=16)

    # ── Graph ────────────────────────────────────────────────────────────────

    def _on_graph(self, ticker):
        """Open chart popup with mode-specific reference lines."""
        ohlc = self._ohlc_data.get(ticker, [])
        ccy  = 'KRW' if ticker.endswith('.KS') else 'USD'
        current_price = self._current_prices.get(ticker)

        pos = self._find_pos(ticker)
        if pos.get('is_deployed'):
            for row in self.deployed_rows:
                if row.ticker == ticker:
                    state = row.get_state()
                    CandleChartWindow(
                        self.root, ticker, ohlc, ccy,
                        mode='deployed',
                        avg_cost=state['avg_cost'],
                        buy_pct=state['buy_pct'],
                        sell_tiers=[(state[f't{i+1}_active'], state[f't{i+1}_pct'])
                                    for i in range(3)],
                        current_price=current_price)
                    return
        else:
            for row in self.empty_rows:
                if row.ticker == ticker:
                    CandleChartWindow(
                        self.root, ticker, ohlc, ccy,
                        mode='empty',
                        load_gear=row._get_gear_key(),
                        current_price=current_price)
                    return

        # Fallback
        CandleChartWindow(self.root, ticker, ohlc, ccy,
                          current_price=current_price)

    def _on_row_compute(self):
        """Called when any deployed row recomputes — update army% across all."""
        if self._fx_rate:
            self._update_army(self._fx_rate)

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

        if changed:
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
                          't1_active', 't2_active', 't3_active', 'load_gear'):
                    if k in s:
                        pos[k] = s[k]

    def _reapply(self):
        if self._last_data:
            self._apply_live(self._last_data, self._fx_rate, quiet=True)

    # ── Live data ────────────────────────────────────────────────────────────

    def _fetch_bg(self):
        tickers = [p['ticker'] for p in self.positions]
        try:
            data, fx = fetch_all(tickers, self.config.get('fx_ticker', 'USDKRW=X'))
        except Exception as e:
            self.root.after(0, lambda: self.status_var.set(f'Error: {e}'))
            return
        self.root.after(0, self._apply_live, data, fx)

    def _apply_live(self, data, fx_rate, quiet=False):
        self._last_data = data
        self._fx_rate   = fx_rate

        for t, d in data.items():
            if d.get('price'):      self._current_prices[t] = d['price']
            if d.get('5d_high'):    self._current_peaks[t]  = d['5d_high']
            if d.get('5d_ohlc'):    self._ohlc_data[t]      = d['5d_ohlc']
            if d.get('5d_closes'):  self._closes_data[t]    = d['5d_closes']

        if fx_rate:
            self.fx_var.set(f"{fx_rate:,.2f}")
            self._update_unit_usd()
        else:
            self.fx_var.set('N/A')

        # Update deployed rows
        for row in self.deployed_rows:
            d = data.get(row.ticker, {})
            if d.get('price'):
                row.update_live(d['price'])
            row.compute()

        # Update empty rows
        for row in self.empty_rows:
            d = data.get(row.ticker, {})
            row.update_live(
                d.get('price'),
                d.get('5d_high'),
                d.get('5d_closes', []))
            row.compute()

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
        for r in self.deployed_rows:
            cb = _cb(r)
            if total <= 0:
                r.set_army_pct(None); continue
            krw = cb * fx_rate if (r.currency == 'USD' and fx_rate) else cb
            r.set_army_pct(krw / total * 100.0)

        # Update header deployed info
        self.deploy_total_var.set(f"\u20a9{total:,.0f}" if total > 0 else '--')
        try:
            n = int(self.N_var.get())
            unit_krw = float(self.unit_krw_var.get().replace(',', ''))
            if unit_krw > 0 and n > 0:
                units = total / unit_krw
                pct = units / n * 100
                self.deploy_ratio_var.set(f"{units:.1f}/{n} ({pct:.1f}%)")
            else:
                self.deploy_ratio_var.set('--')
        except (ValueError, ZeroDivisionError):
            self.deploy_ratio_var.set('--')

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
