"""Autopilot live window — the adaptive-gear watcher's cockpit.

Opened by the card's big Autopilot button. Opening it arms bare WATCH mode:
the stock is polled every 10 s and this window follows every tick — the
5-day chart and the main panel stay refresh-button-driven.

Top-right controls select the mode:

    (no button active)  WATCH — bare live info: zone chart + lines, no orders
    [DRY RUN]           paper simulation (virtual fills, not connected)
    [LIVE]              real watcher: fires a real order when a line is hit;
                        allowed only during regular market hours

Closing the window in WATCH mode stops the polling; in DRY/LIVE the
autopilot keeps running in the background (the card button stays colored).

The gears are NOT chosen here — they are picked automatically from the
stock's deployment ratio (PART II of the manual) and displayed live:
B1/B2/B3 set the chase line (−4/−5/−6%, ×1/2, ×2/3, ×3/4) and S1/S2/S3 the
full-exit line (+2/+2.5/+3%). The chart shows the intraday tick path inside
that shaded gear zone, avg/anchor, the campaign fill log (cleared when the
position is fully sold), deployed size and the market phase.
"""

import time as _time
from datetime import datetime as _dt

import tkinter as tk
from tkinter import messagebox

from core.calc import STOCK_NAMES, fmt_price
from core.autopilot import BUY_GEARS, SELL_GEARS
from core.tazza import LADDER_MODES

_F_TITLE = ('Segoe UI', 17, 'bold')
_F_STAT  = ('Segoe UI', 13)
_F_BTN   = ('Segoe UI', 13, 'bold')
_F_INFO  = ('Segoe UI', 12)
_F_AXIS  = ('Segoe UI', 10)
_F_REF   = ('Segoe UI', 11, 'bold')
_F_LOG   = ('Consolas', 11)

_CLR = {
    'sell':  '#007700',
    'psell': '#66AA66',
    'tier1': '#007700',
    'tier2': '#44AA44',
    'chase': '#7E3FBF',
    'lower': '#7E3FBF',
    'rebuy': '#B8860B',
    'load':  '#E08000',
    'proj':  '#9C7BC8',
    'avg':   '#FF8C00',
    'anchor': '#888888',
    'path':  '#1A1A1A',
    'now':   '#CC0000',
    'zone':  '#F0F7F0',
    'state_dep': '#0033AA',
    'state_stop': '#880000',
}
_KIND_CLR = {'LOAD': '#E08000', 'CHASE': '#7E3FBF', 'SELL': '#007700',
             'HOLD': '#555555', 'DOUBLE': '#7E3FBF', 'SKIM': '#B8860B',
             'REBUY': '#B8860B', 'TIER1': '#007700', 'TIER2': '#44AA44',
             'FINAL': '#880000', 'RECON': '#880000', 'BUY': '#555555'}

# Reference lines by engine key: adaptive uses sell/psell/chase/load,
# 타짜 uses tier1/tier2/lower/rebuy/load/psell.
_LINE_ORDER = (('sell', 'SELL'), ('psell', 'SELL (pseudo)'),
               ('tier1', '매도 T1'), ('tier2', '매도 T2'),
               ('chase', 'CHASE'), ('lower', '더블'),
               ('rebuy', '밑장 재매수'), ('load', 'LOAD'))
_TAZZA_BG = '#4B0082'
_PHASE_TXT = {'REGULAR': ('OPEN (regular)', '#007700'),
              'PRE':     ('pre-market', '#B8860B'),
              'AFTER':   ('after-market', '#B8860B'),
              'CLOSED':  ('CLOSED', '#888888')}
_DRY_BG = '#E08000'
_LIVE_BG = '#CC0000'


class Daily443ChartWindow:
    """Live cockpit for one watched stock. Subscribes to the controller and
    redraws on every 10-second tick."""

    def __init__(self, parent, ticker, currency, ap_ctx):
        self.ap = ap_ctx
        self.ticker = ticker
        self.ccy = currency
        self.ui = None

        self.win = tk.Toplevel(parent)
        name = STOCK_NAMES.get(ticker, ticker)
        self.win.title(f'{name} — Autopilot live')
        self.win.geometry('1180x680')
        self.win.minsize(780, 480)

        head = tk.Frame(self.win, padx=12, pady=8)
        head.pack(fill='x')
        tk.Label(head, text=f'{name}  — Autopilot', font=_F_TITLE
                 ).pack(side='left')
        self._state_lbl = tk.Label(head, text='', font=_F_TITLE)
        self._state_lbl.pack(side='left', padx=(16, 0))

        # Strategy toggle: 타짜 (묻고 더블로 가, default) ↔ adaptive gears.
        self._tz_btn = tk.Button(head, text='타짜', font=_F_BTN, width=6,
                                 command=lambda: self._on_strategy('TAZZA'))
        self._tz_btn.pack(side='left', padx=(18, 0))
        self._ad_btn = tk.Button(head, text='기어', font=_F_BTN, width=6,
                                 command=lambda: self._on_strategy('ADAPTIVE'))
        self._ad_btn.pack(side='left', padx=(4, 0))

        # Mode controls, right-aligned. Gears are automatic (no selector) —
        # the current B/S gear pair is displayed next to the market phase.
        self._live_btn = tk.Button(head, text='LIVE', font=_F_BTN, width=8,
                                   command=lambda: self._on_mode('LIVE'))
        self._live_btn.pack(side='right', padx=(6, 0))
        self._dry_btn = tk.Button(head, text='DRY RUN', font=_F_BTN, width=9,
                                  command=lambda: self._on_mode('DRY'))
        self._dry_btn.pack(side='right', padx=(6, 0))
        self._default_bg = self._dry_btn.cget('bg')

        self._gear_lbl = tk.Label(head, text='', font=_F_BTN, fg='#4B0082')
        self._gear_lbl.pack(side='right', padx=(12, 12))

        self._phase_lbl = tk.Label(head, text='', font=_F_STAT)
        self._phase_lbl.pack(side='right', padx=(0, 4))

        self._info_lbl = tk.Label(self.win, text='', font=_F_INFO, fg='#333',
                                  anchor='w')
        self._info_lbl.pack(fill='x', padx=14)
        self._status_lbl = tk.Label(self.win, text='', font=_F_INFO,
                                    fg='#4B0082', anchor='w')
        self._status_lbl.pack(fill='x', padx=14)

        body = tk.Frame(self.win)
        body.pack(fill='both', expand=True, padx=12, pady=(6, 12))

        self.canvas = tk.Canvas(body, bg='white', highlightthickness=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self._draw())

        side = tk.Frame(body)
        side.pack(side='left', fill='y', padx=(10, 0))
        # 타짜 ladder table — the exact numbers to type into Toss by hand.
        self._proj_lbl = tk.Label(side, text='', font=_F_STAT)
        self._proj_lbl.pack(anchor='w')
        self._proj_txt = tk.Text(side, width=38, height=7, font=_F_LOG,
                                 state='disabled', bg='#F5F0FA',
                                 relief='groove', bd=1)
        self._proj_txt.tag_configure('buy', foreground=_CLR['chase'])
        self._proj_txt.tag_configure('sell', foreground=_CLR['sell'])
        self._proj_txt.pack(fill='x', pady=(2, 8))
        tk.Label(side, text='Campaign fills', font=_F_STAT).pack(anchor='w')
        self._log_txt = tk.Text(side, width=38, font=_F_LOG, state='disabled',
                                bg='#FAFAFA', relief='groove', bd=1)
        self._log_txt.pack(fill='y', expand=True, pady=(4, 0))
        for kind, clr in _KIND_CLR.items():
            self._log_txt.tag_configure(kind, foreground=clr)

        self._cb = self._on_update
        self.ap['subscribe'](self._cb)
        self.win.bind('<Destroy>', self._on_destroy)
        self._on_update(self.ap['ui_state']())

    def _on_destroy(self, event):
        if event.widget is not self.win:
            return
        if self._cb:
            self.ap['unsubscribe'](self._cb)
            self._cb = None
        # Bare watching stops with its window; DRY/LIVE keep running in the
        # background (the card's 443 button stays colored).
        if self.ap['mode_of']() == 'WATCH':
            self.ap['disable']()

    # ── Mode / pedal controls ─────────────────────────────────────────────────

    def _on_strategy(self, strategy):
        if self.ap['strategy_of']() == strategy:
            return
        ui = self.ap['ui_state']() or {}
        shares = ui.get('shares') or 0
        name = '타짜 (묻고 더블로 가)' if strategy == 'TAZZA' else 'adaptive gears'
        if shares > 0:
            if not messagebox.askyesno(
                    'Strategy switch',
                    f'{self.ticker} holds {shares} shares.\n\n'
                    f'Switch to {name}? The engine re-arms on the live '
                    f'position (타짜 seeds its campaign ledger from the '
                    f'current cost basis). Resting orders are untouched.',
                    parent=self.win):
                return
        ok, msg = self.ap['set_strategy'](strategy)
        if not ok:
            messagebox.showwarning('Autopilot', msg, parent=self.win)

    def _on_mode(self, mode):
        current = self.ap['mode_of']()
        if current == mode:                    # click active button → WATCH
            self.ap['set_mode']('WATCH')
            return
        if mode == 'LIVE':
            ui = self.ap['ui_state']() or {}
            lines = ui.get('lines') or {}
            detail = [f'Go LIVE on {self.ticker}?', '',
                      'The watcher fires REAL orders when a line is hit:']
            for key, tag in (('sell', 'SELL all'), ('tier1', 'SELL tier 1'),
                             ('tier2', 'SELL tier 2'), ('chase', 'CHASE BUY'),
                             ('lower', '더블 BUY'), ('rebuy', '밑장 재매수'),
                             ('load', 'LOAD BUY')):
                if lines.get(key):
                    p, q = lines[key]
                    detail.append(f'  {tag}:  {q} @ {fmt_price(p, self.ccy)}')
            detail.append('')
            if ui.get('strategy') == 'TAZZA':
                detail.append('타짜: 묻고 더블로 가 — full doubles only; '
                              '모자라면 밑장 빼기. Nothing rests before a '
                              'trigger; LIVE drops back to WATCH when the '
                              'market closes.')
            else:
                detail.append('Gears follow the deployed army automatically. '
                              'Nothing rests before a trigger; LIVE drops '
                              'back to WATCH when the market closes.')
            if not messagebox.askyesno('Autopilot LIVE', '\n'.join(detail),
                                       parent=self.win):
                return
        ok, msg = self.ap['set_mode'](mode)
        if not ok:
            messagebox.showwarning('Autopilot', msg, parent=self.win)

    # ── Controller callback (tk thread) ───────────────────────────────────────

    def _on_update(self, ui):
        try:
            if not self.canvas.winfo_exists():
                return
        except tk.TclError:
            return
        if ui is None or not self.ap['is_enabled']():
            self._state_lbl.config(text='off', fg='#888')
            self._status_lbl.config(text='')
            self.ui = None
            self._draw()
            return
        self.ui = ui

        state = ui.get('state', '?')
        clr = (_CLR['state_stop'] if state == 'STOPPED'
               else _CLR['state_dep'] if state == 'DEPLOYED' else '#666')
        self._state_lbl.config(text=state, fg=clr)

        mode = ui.get('mode', 'WATCH')
        self._dry_btn.config(
            bg=(_DRY_BG if mode == 'DRY' else self._default_bg),
            fg=('white' if mode == 'DRY' else 'black'))
        self._live_btn.config(
            bg=(_LIVE_BG if mode == 'LIVE' else self._default_bg),
            fg=('white' if mode == 'LIVE' else 'black'))

        strat = ui.get('strategy', 'ADAPTIVE')
        self._tz_btn.config(
            bg=(_TAZZA_BG if strat == 'TAZZA' else self._default_bg),
            fg=('white' if strat == 'TAZZA' else 'black'))
        self._ad_btn.config(
            bg=(_CLR['state_dep'] if strat == 'ADAPTIVE' else self._default_bg),
            fg=('white' if strat == 'ADAPTIVE' else 'black'))

        phase = ui.get('phase', 'CLOSED')
        txt, pclr = _PHASE_TXT.get(phase, (phase, '#888'))
        mkt = 'KR' if self.ticker.endswith('.KS') else 'US'
        self._phase_lbl.config(text=f'{mkt} market: {txt}', fg=pclr)

        if strat == 'TAZZA':
            lp, lm = ui.get('ladder_pct'), ui.get('ladder_mode')
            if lp and lm:
                parts = [f'ladder -{lp * 100:.0f}%',
                         f"매도 {LADDER_MODES[lm]['label']}",
                         f"{ui.get('deployed_units', 0)}u deployed"]
                stage = ui.get('emergency_stage', 0)
                if stage:
                    parts.append(f'stage {stage} '
                                 f"(idle {ui.get('stage_idle_days', 0)}/5d)")
                if ui.get('skim_pending'):
                    parts.append('밑장 대기 @ '
                                 + fmt_price(ui.get('skim_rebuy_line'),
                                             self.ccy))
                self._gear_lbl.config(
                    text='타짜  ' + '  ·  '.join(parts),
                    fg=('#880000' if stage else _TAZZA_BG))
            else:
                self._gear_lbl.config(text='타짜: empty — load -3%',
                                      fg='#888')
        else:
            # Auto-chosen gear pair (deployment-driven). Red when exhausted.
            bg_, sg_ = ui.get('buy_gear'), ui.get('sell_gear')
            if bg_ and sg_:
                gear_txt = (f"{BUY_GEARS[bg_]['label']}   "
                            f"{SELL_GEARS[sg_]['label']}   "
                            f"deploy {ui.get('deploy_ratio', 0) * 100:.0f}%")
                if ui.get('buy_state') == 'EXHAUSTED':
                    gear_txt += '   EXHAUSTED'
                elif ui.get('buy_state') == 'FALLBACK':
                    gear_txt += '   B1 fallback'
                self._gear_lbl.config(
                    text=gear_txt,
                    fg=('#880000' if ui.get('buy_state') == 'EXHAUSTED'
                        else '#4B0082'))
            else:
                self._gear_lbl.config(text='gears: auto (empty — load -4%)',
                                      fg='#888')

        self._info_lbl.config(text=self._info_text(ui))
        self._status_lbl.config(
            text=f"{ui.get('status', '')}    poll {ui.get('ts', '--')}")
        self._fill_proj(ui)
        self._fill_log(ui.get('events') or [])
        self._draw()

    def _info_text(self, ui):
        parts = []
        shares = ui.get('shares') or 0
        price = ui.get('price')
        avg = ui.get('avg_cost') or 0
        unit = ui.get('unit_cash') or 0
        if shares > 0:
            parts.append(f'held {shares} @ {fmt_price(avg, self.ccy)}')
            val = shares * (price or avg)
            if unit > 0:
                parts.append(f'deployed ≈ {val / unit:,.2f} u')
            else:
                parts.append(f'deployed {fmt_price(val, self.ccy)}')
        else:
            src = ('sell point' if ui.get('anchor_source') == 'sell'
                   else 'prev close')
            if ui.get('anchor'):
                parts.append(f"anchor {fmt_price(ui['anchor'], self.ccy)} "
                             f'({src})')
        bp = ui.get('buying_power')
        if bp is not None:
            parts.append(f'reserve {fmt_price(bp, self.ccy)}')
        if ui.get('chase_count'):
            parts.append(f"chase #{ui['chase_count']}")
        if ui.get('strategy') == 'TAZZA' and (ui.get('campaign_B') or 0) > 0:
            parts.append(f"campaign B {fmt_price(ui['campaign_B'], self.ccy)}"
                         f" / S {fmt_price(ui.get('campaign_S'), self.ccy)}"
                         f" / K {fmt_price(ui.get('campaign_K'), self.ccy)}")
            if ui.get('skim_pending') and ui.get('skim_lock_amount'):
                lock = fmt_price(ui['skim_lock_amount'], self.ccy)
                parts.append(f'lock {lock}')
        return '      '.join(parts)

    def _fill_proj(self, ui):
        """The ladder table: numbers ready to type into the Toss app/web.
        타짜: next 3 double lines (×1, ×2, ×4 of the deployment) + the two
        sell tiers. Adaptive: the current active lines."""
        proj = ui.get('projection') if ui.get('strategy') == 'TAZZA' else None
        txt = self._proj_txt
        txt.config(state='normal')
        txt.delete('1.0', 'end')
        if ui.get('strategy') == 'TAZZA':
            self._proj_lbl.config(text='타짜 ladder (manual entry)')
            if proj:
                for i, (p, q) in enumerate(proj.get('buys') or [], 1):
                    txt.insert('end',
                               f' BUY {i}   {fmt_price(p, self.ccy):>12}'
                               f'  ×{q}\n', 'buy')
                for i, (p, q) in enumerate(proj.get('sells') or [], 1):
                    txt.insert('end',
                               f' SELL T{i} {fmt_price(p, self.ccy):>12}'
                               f'  ×{q}\n', 'sell')
            else:
                txt.insert('end', ' (waiting for lines…)\n')
        else:
            self._proj_lbl.config(text='Active lines')
            for key, tag in _LINE_ORDER:
                ln = (ui.get('lines') or {}).get(key)
                if ln:
                    side = ('sell' if key in ('sell', 'psell', 'tier1',
                                              'tier2') else 'buy')
                    txt.insert('end',
                               f' {tag:<12}{fmt_price(ln[0], self.ccy):>12}'
                               f'  ×{ln[1]}\n', side)
        txt.config(state='disabled')

    def _fill_log(self, events):
        txt = self._log_txt
        txt.config(state='normal')
        txt.delete('1.0', 'end')
        if not events:
            txt.insert('end', '(campaign log is empty —\n cleared after '
                              'each full sell)\n')
        for e in events:
            price = e.get('price')
            p = fmt_price(price, self.ccy) if price else '--'
            line = (f"{e['ts']}  {e['kind']:<5} {e['qty']:+d} @ {p}"
                    f"  → {e['shares']} sh")
            if e.get('avg'):
                line += f" @ {fmt_price(e['avg'], self.ccy)}"
            txt.insert('end', line + '\n', e['kind'])
        txt.config(state='disabled')
        txt.see('end')

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self):
        c = self.canvas
        c.delete('all')
        w, h = c.winfo_width(), c.winfo_height()
        if w < 160 or h < 120:
            return
        ui = self.ui
        if not ui:
            c.create_text(w / 2, h / 2, text='Autopilot is off',
                          font=_F_STAT, fill='#888')
            return

        lines = {k: v for k, v in (ui.get('lines') or {}).items()
                 if v and v[0]}
        ticks = ui.get('ticks') or []
        deployed = (ui.get('shares') or 0) > 0
        avg = ui.get('avg_cost') or 0
        anchor = ui.get('anchor')

        proj_buys = []
        if ui.get('strategy') == 'TAZZA' and ui.get('projection'):
            proj_buys = list((ui['projection'].get('buys') or [])[1:])

        prices = [p for _, p in ticks]
        prices += [v[0] for v in lines.values()]
        prices += [p for p, _q in proj_buys]
        if deployed and avg > 0:
            prices.append(avg)
        if not deployed and anchor:
            prices.append(anchor)
        if ui.get('price'):
            prices.append(ui['price'])
        if not prices:
            c.create_text(w / 2, h / 2, text='waiting for the first tick…',
                          font=_F_STAT, fill='#888')
            return

        left, right, top, bottom = 90, 185, 18, 32
        cw, ch = w - left - right, h - top - bottom
        if cw < 60 or ch < 60:
            return
        p_min, p_max = min(prices), max(prices)
        p_rng = (p_max - p_min) or (p_max * 0.01) or 1
        p_min -= p_rng * 0.10
        p_max += p_rng * 0.10
        p_rng = p_max - p_min

        def y_of(p):
            return top + ch * (1 - (p - p_min) / p_rng)

        # The pedal zone: buy line up to the (pseudo) sell line, shaded.
        lo = (lines.get('chase') or lines.get('lower') or lines.get('rebuy')
              or lines.get('load') or (None,))[0]
        hi = (lines.get('sell') or lines.get('tier1') or lines.get('psell')
              or (None,))[0]
        if lo and hi and hi > lo:
            c.create_rectangle(left, y_of(hi), left + cw, y_of(lo),
                               fill=_CLR['zone'], outline='')

        for i in range(5):
            p = p_min + p_rng * i / 4
            y = y_of(p)
            c.create_line(left, y, left + cw, y, fill='#E8E8E8')
            c.create_text(left - 5, y, text=fmt_price(p, self.ccy),
                          anchor='e', font=_F_AXIS, fill='#888')

        # Time axis from the first tick (min span 30 min so early ticks
        # don't smear across the full width).
        now = _time.time()
        t0 = ticks[0][0] if ticks else now
        span = max(now - t0, 1800.0)

        def x_of(t):
            return left + cw * (t - t0) / span

        for i in range(5):
            t = t0 + span * i / 4
            x = left + cw * i / 4
            c.create_line(x, top, x, top + ch, fill='#F2F2F2')
            c.create_text(x, h - 6, text=_dt.fromtimestamp(t).strftime('%H:%M'),
                          font=_F_AXIS, fill='#777', anchor='s')

        label_x = left + cw + 6

        def ref(price, color, text, dash=None, width=2.4):
            y = y_of(price)
            c.create_line(left, y, left + cw, y, fill=color,
                          width=width, dash=dash)
            c.create_text(label_x, y, text=text, anchor='w',
                          font=_F_REF, fill=color)

        if deployed and avg > 0:
            ref(avg, _CLR['avg'], f'AVG {fmt_price(avg, self.ccy)}',
                dash=(5, 4), width=1.6)
        elif anchor:
            ref(anchor, _CLR['anchor'],
                f'ANCHOR {fmt_price(anchor, self.ccy)}', dash=(5, 4),
                width=1.4)
        for key, name in _LINE_ORDER:
            ln = lines.get(key)
            if ln:
                ref(ln[0], _CLR[key],
                    f'{name} {fmt_price(ln[0], self.ccy)} ×{ln[1]}')

        # 타짜: the doubles BEYOND the live lower line, dashed — what the
        # chase would look like if the dip keeps going (manual-entry aid).
        for i, (p, q) in enumerate(proj_buys, 2):
            ref(p, _CLR['proj'],
                f'더블 D{i} {fmt_price(p, self.ccy)} ×{q}',
                dash=(3, 5), width=1.4)

        if len(ticks) >= 2:
            pts = []
            for t, p in ticks:
                pts += [x_of(t), y_of(p)]
            c.create_line(*pts, fill=_CLR['path'], width=2)
        if ticks:
            t, p = ticks[-1]
            x, y = x_of(t), y_of(p)
            c.create_oval(x - 4, y - 4, x + 4, y + 4, fill=_CLR['now'],
                          outline='white')
            c.create_text(x, y - 12, text=fmt_price(p, self.ccy),
                          font=_F_REF, fill=_CLR['now'])
