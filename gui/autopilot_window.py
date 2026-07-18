"""Autopilot window — the ONE cockpit for a watched stock.

Opened by the card's big Autopilot button. Opening it arms WATCH mode: the
stock is polled every 10 s and this window follows every tick. There is one
strategy — the watcher follows the exact lines the card computes (same gear
tables, same tiers). No strategy toggle, no dry-run: just WATCH or LIVE.

    WATCH  the crossed line lights the trigger row; the Buy / Sell buttons
           let the commander fire that same order manually.
    LIVE   the watcher fires the order by itself the moment a line is
           crossed; allowed only during regular market hours (drops back
           to WATCH at the close). While LIVE, the manual buttons rest.

Left: the live tick curve inside the gear zone with every threshold line.
Right: the live campaign-status snapshot, recorded fill log, and 5-day
candle panel with the V value and the same lines — the old Graph popup
lives here now, always shown.
When the army cannot fund the buy line, no popup fires: the buy line is
drawn muted (✕ … no army) and the trigger row explains why the manual
Buy button is off.

Closing the window in WATCH mode stops the polling; in LIVE the autopilot
keeps running in the background (the card button stays colored).
"""

import time as _time
from datetime import datetime as _dt

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

from core.calc import STOCK_NAMES, BUY_GEAR_INFO, fmt_price
from gui.candle_chart import (CandlePanel, bounded_label_layout,
                              required_label_pad)

_F_TITLE = ('Segoe UI', 17, 'bold')
_F_STAT  = ('Segoe UI', 13)
_F_BTN   = ('Segoe UI', 13, 'bold')
_F_INFO  = ('Segoe UI', 12)
_F_AXIS  = ('Segoe UI', 10)
_F_REF   = ('Segoe UI', 11, 'bold')
_F_LOG   = ('Consolas', 11)
_F_LOG_B = ('Consolas', 11, 'bold')

_CLR = {
    'tier1': '#007700',
    'tier2': '#33A033',
    'tier3': '#66C266',
    'psell': '#88BB88',
    'chase': '#7E3FBF',
    'load':  '#E08000',
    'avg':   '#FF8C00',
    'anchor': '#888888',
    'path':  '#1A1A1A',
    'now':   '#CC0000',
    'zone':  '#F0F7F0',
    'state_dep': '#0033AA',
}
_KIND_CLR = {'LOAD': '#E08000', 'CHASE': '#7E3FBF', 'SELL': '#007700'}

_PHASE_TXT = {'REGULAR': ('OPEN (regular)', '#007700'),
              'PRE':     ('pre-market', '#B8860B'),
              'AFTER':   ('after-market', '#B8860B'),
              'CLOSED':  ('CLOSED', '#888888')}
_LIVE_BG = '#CC0000'
_NO_ARMY_CLR = '#999999'      # muted buy line when the army can't fund it
_GEAR_TEXT_CLR = {
    1: '#C62828',     # red
    2: '#A64B00',     # orange
    3: '#806800',     # readable dark yellow
    4: '#18733C',     # green
    5: '#1565C0',     # blue
}
_GEAR_FALLBACK_CLR = '#795548'
_CAMPAIGN_STATE_CLR = {
    'DEPLOYED': '#1565C0',
    'EMPTY': '#A64B00',
    'ARMING': '#666666',
}


def gear_text_color(gear):
    """Readable red→orange→yellow→green→blue identity for G1→G5."""
    try:
        gear = int(gear)
    except (TypeError, ValueError):
        return _GEAR_FALLBACK_CLR
    return _GEAR_TEXT_CLR.get(gear, _GEAR_FALLBACK_CLR)


def campaign_status_text(ui, currency):
    """Current read-only watcher snapshot shown above the real fill list.

    This is presentation only: it never becomes a campaign event and never
    touches persisted watcher state.
    """
    ui = ui or {}
    state = ui.get('state') or 'ARMING'
    mode = ui.get('mode') or 'WATCH'
    lines = ui.get('lines') or {}
    shares = int(ui.get('shares') or 0)
    avg = float(ui.get('avg_cost') or 0)
    price = ui.get('price')
    parts = [f'CURRENT — {state} · {mode}']

    if state == 'DEPLOYED' or shares > 0:
        parts.append(f'Holding: {shares:,} sh @ '
                     f'{fmt_price(avg, currency)} avg')
        if price:
            parts.append(f'Now: {fmt_price(price, currency)}')
        if lines.get('chase'):
            p, q = lines['chase']
            tail = '  [NO ARMY]' if ui.get('buy_state') == 'EXHAUSTED' else ''
            parts.append(f'Next BUY: {q:,} @ {fmt_price(p, currency)}{tail}')
        sells = []
        for key in ('tier1', 'tier2', 'tier3'):
            if lines.get(key):
                p, q = lines[key]
                sells.append(f'T{key[-1]} {q:,}@{fmt_price(p, currency)}')
        if sells:
            parts.append('Next SELL: ' + '  '.join(sells))
    elif state == 'EMPTY':
        parts.append('Holding: no position (0 sh)')
        if price:
            parts.append(f'Now: {fmt_price(price, currency)}')
        if ui.get('anchor'):
            source = ('same-day re-bait' if ui.get('anchor_source') == 'sell'
                      else 'previous close')
            parts.append(f"Vantage: {fmt_price(ui['anchor'], currency)} "
                         f'({source})')
        if lines.get('load'):
            p, q = lines['load']
            tail = '  [NO ARMY]' if ui.get('buy_state') == 'EXHAUSTED' else ''
            parts.append(f'Next LOAD: {q:,} @ {fmt_price(p, currency)}{tail}')
        if lines.get('psell'):
            p, q = lines['psell']
            parts.append(f'After LOAD, first SELL: {q:,} @ '
                         f'{fmt_price(p, currency)}')
    else:
        parts.append('Waiting for the first broker snapshot…')

    orders = ui.get('orders') or []
    if orders:
        sides = sorted({o.get('side') for o in orders if o.get('side')})
        if sides:
            parts.append('Resting on Toss: ' + '/'.join(sides))
    return '\n'.join(parts)


class AutopilotWindow:
    """Live cockpit for one watched stock. Subscribes to the controller and
    redraws on every 10-second tick."""

    def __init__(self, parent, ticker, currency, ap_ctx):
        self.ap = ap_ctx
        self.ticker = ticker
        self.ccy = currency
        self.ui = None

        self.win = tk.Toplevel(parent)
        name = STOCK_NAMES.get(ticker, ticker)
        self.win.title(f'{name} — Autopilot')
        self.win.geometry('1620x720')
        # All three panels are always present.  Keep enough room for a useful
        # live plot beside the fixed status and five-day panels.
        self.win.minsize(1280, 560)
        self._ref_font = tkfont.Font(root=self.win, font=_F_REF)

        # ── Header: title/state left, market phase + LIVE right ──────────────
        head = tk.Frame(self.win, padx=12, pady=8)
        head.pack(fill='x')
        tk.Label(head, text=f'{name}  — Autopilot', font=_F_TITLE
                 ).pack(side='left')
        self._state_lbl = tk.Label(head, text='', font=_F_TITLE)
        self._state_lbl.pack(side='left', padx=(16, 0))

        self._live_btn = tk.Button(head, text='LIVE', font=_F_BTN, width=8,
                                   command=self._on_live)
        self._live_btn.pack(side='right', padx=(6, 0))
        self._default_bg = self._live_btn.cget('bg')
        self._phase_lbl = tk.Label(head, text='', font=_F_STAT)
        self._phase_lbl.pack(side='right', padx=(0, 8))

        # ── Gear line ────────────────────────────────────────────────────────
        head2 = tk.Frame(self.win, padx=12)
        head2.pack(fill='x')
        self._gear_lbl = tk.Label(head2, text='', font=_F_BTN, fg='#4B0082',
                                  anchor='w')
        self._gear_lbl.pack(side='left')

        self._info_lbl = tk.Label(self.win, text='', font=_F_INFO, fg='#333',
                                  anchor='w')
        self._info_lbl.pack(fill='x', padx=14)
        self._status_lbl = tk.Label(self.win, text='', font=_F_INFO,
                                    fg='#4B0082', anchor='w')
        self._status_lbl.pack(fill='x', padx=14)

        # ── Trigger row: crossed-line indicator + manual Buy/Sell/Cancel ─────
        trig = tk.Frame(self.win, padx=12, pady=4)
        trig.pack(fill='x')
        self._trig_lbl = tk.Label(trig, text='no line crossed', font=_F_STAT,
                                  fg='#888', width=60, anchor='w')
        self._trig_lbl.pack(side='left')
        self._buy_btn = tk.Button(trig, text='Buy', font=_F_STAT, width=8,
                                  state='disabled',
                                  command=lambda: self._do_fire('BUY'))
        self._buy_btn.pack(side='left', padx=(6, 6))
        self._sell_btn = tk.Button(trig, text='Sell', font=_F_STAT, width=8,
                                   state='disabled',
                                   command=lambda: self._do_fire('SELL'))
        self._sell_btn.pack(side='left', padx=(0, 6))
        self._cancel_btn = tk.Button(trig, text='Cancel', font=_F_STAT,
                                     width=8, state='disabled',
                                     command=self._do_cancel)
        self._cancel_btn.pack(side='left', padx=(0, 12))
        self._fire_msg = tk.Label(trig, text='', font=_F_INFO, fg='#333')
        self._fire_msg.pack(side='left')

        # ── Body: live chart | fills log | always-visible 5-day panel ────────
        body = tk.Frame(self.win)
        body.pack(fill='both', expand=True, padx=12, pady=(6, 12))

        self.canvas = tk.Canvas(body, bg='white', highlightthickness=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self._draw())

        side = tk.Frame(body)
        side.pack(side='left', fill='y', padx=(10, 0))
        tk.Label(side, text='Campaign status & fills', font=_F_STAT
                 ).pack(anchor='w')
        self._campaign_status_lbl = tk.Label(
            side, text='', width=36, font=_F_LOG, anchor='nw', justify='left',
            wraplength=340, bg='#F5F5F5', relief='groove', bd=1,
            padx=6, pady=5)
        self._campaign_status_lbl.pack(fill='x', pady=(4, 4))
        self._log_txt = tk.Text(side, width=36, font=_F_LOG, state='disabled',
                                wrap='word', bg='#FAFAFA', relief='groove', bd=1)
        self._log_txt.pack(fill='y', expand=True)
        for kind, clr in _KIND_CLR.items():
            self._log_txt.tag_configure(kind, foreground=clr)
        self._log_txt.tag_configure('TIER', foreground='#007700')
        self._log_txt.tag_configure('OTHER', foreground='#555555')
        self._log_txt.tag_configure('HEADER', foreground='#333333',
                                    font=_F_LOG_B)

        self.candle_panel = CandlePanel(body, currency, width=470)
        self.candle_panel.pack(side='left', fill='both', padx=(10, 0))

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
        # Bare watching stops with its window; LIVE keeps running in the
        # background (the card's Autopilot button stays colored).
        if self.ap['mode_of']() == 'WATCH':
            self.ap['disable']()

    # ── Controls ──────────────────────────────────────────────────────────────

    def _on_live(self):
        if self.ap['mode_of']() == 'LIVE':       # click again → back to WATCH
            self.ap['set_mode']('WATCH')
            return
        ui = self.ap['ui_state']() or {}
        lines = ui.get('lines') or {}
        detail = [f'Go LIVE on {self.ticker}?', '',
                  'The watcher fires REAL orders when a line is crossed:']
        for key, tag in (('tier1', 'SELL T1'), ('tier2', 'SELL T2'),
                         ('tier3', 'SELL T3'), ('chase', 'BUY (chase)'),
                         ('load', 'LOAD BUY')):
            if lines.get(key):
                p, q = lines[key]
                detail.append(f'  {tag}:  {q} @ {fmt_price(p, self.ccy)}')
        detail += ['', 'One logic: the lines above are exactly the card\'s '
                       'lines. Nothing rests before a trigger; after a fill '
                       'the lines reset and the watch continues. LIVE drops '
                       'back to WATCH when the market closes.']
        if not messagebox.askyesno('Autopilot LIVE', '\n'.join(detail),
                                   parent=self.win):
            return
        ok, msg = self.ap['set_mode']('LIVE')
        if not ok:
            messagebox.showwarning('Autopilot', msg, parent=self.win)

    def _do_fire(self, side):
        trig = ((self.ui or {}).get('trigger') or {}).get(side)
        if not trig:
            return
        q, p = trig['qty'], trig['price']
        text = (f"{trig.get('label', side)}\n\n"
                f"{side} {q} @ {fmt_price(p, self.ccy)}\n\n"
                f"Send this order to Toss now?")
        if not messagebox.askyesno(f'Manual {side}', text, parent=self.win):
            return
        ok, msg = self.ap['manual_fire'](side)
        self._fire_msg.config(text=msg, fg=('green' if ok else 'red'))

    def _do_cancel(self):
        if not messagebox.askyesno(
                'Cancel orders',
                f'Cancel ALL resting Toss orders for {self.ticker}?',
                parent=self.win):
            return
        ok, msg = self.ap['cancel_all']()
        self._fire_msg.config(text=msg, fg=('green' if ok else 'red'))

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
        clr = _CLR['state_dep'] if state == 'DEPLOYED' else '#666'
        self._state_lbl.config(text=state, fg=clr)

        mode = ui.get('mode', 'WATCH')
        self._live_btn.config(
            bg=(_LIVE_BG if mode == 'LIVE' else self._default_bg),
            fg=('white' if mode == 'LIVE' else 'black'))

        phase = ui.get('phase', 'CLOSED')
        txt, pclr = _PHASE_TXT.get(phase, (phase, '#888'))
        mkt = 'KR' if self.ticker.endswith('.KS') else 'US'
        self._phase_lbl.config(text=f'{mkt} market: {txt}', fg=pclr)

        self._gear_lbl.config(text=self._gear_text(ui),
                              fg=gear_text_color(ui.get('gear')))
        self._info_lbl.config(text=self._info_text(ui))
        self._status_lbl.config(
            text=f"{ui.get('status', '')}    poll {ui.get('ts', '--')}")
        self._update_trigger_row(ui)
        self._update_campaign_status(ui)
        self._fill_log(ui)
        self._draw()
        self._refresh_candles()

    def _gear_text(self, ui):
        g, pct = ui.get('gear'), ui.get('pct')
        if not g or not pct:
            return 'gear: waiting for the card'
        frac = BUY_GEAR_INFO.get(pct, {}).get('frac', '')
        tiers = ui.get('tier_pcts') or []
        acts = ui.get('tier_actives') or []
        tstr = '/'.join(f'+{p}%' for p, a in zip(tiers, acts) if a) or '--'
        parts = [f'G{g} (-{pct}% ×{frac})', f'매도 {tstr}']
        if ui.get('gear1_pinned'):
            parts.append('재입질 캠페인 — G1 고정')
        elif (ui.get('state') == 'EMPTY'
                and ui.get('anchor_source') == 'sell'):
            parts.append('재입질 대기 (오늘만, G1)')
        if ui.get('buy_state') == 'EXHAUSTED':
            parts.append('EXHAUSTED — buy off')
        return '   ·   '.join(parts)

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
        elif ui.get('anchor'):
            src = ('재입질 (sell fill)' if ui.get('anchor_source') == 'sell'
                   else 'prev close')
            parts.append(f"vantage {fmt_price(ui['anchor'], self.ccy)} ({src})")
        bp = ui.get('buying_power')
        if bp is not None:
            parts.append(f'reserve {fmt_price(bp, self.ccy)}')
        if ui.get('chase_count'):
            parts.append(f"buy #{ui['chase_count']} today")
        return '      '.join(parts)

    def _update_trigger_row(self, ui):
        trig = ui.get('trigger') or {}
        buy_t, sell_t = trig.get('BUY'), trig.get('SELL')
        mode = ui.get('mode', 'WATCH')
        manual_ok = mode != 'LIVE'
        self._buy_btn.config(state=('normal' if buy_t and manual_ok
                                    else 'disabled'))
        self._sell_btn.config(state=('normal' if sell_t and manual_ok
                                     else 'disabled'))
        self._cancel_btn.config(state=('normal' if ui.get('orders')
                                       else 'disabled'))
        if sell_t:
            self._trig_lbl.config(
                text=f"▲ SELL line crossed:  {sell_t['qty']} @ "
                     f"{fmt_price(sell_t['price'], self.ccy)}"
                     + ('' if manual_ok else '   (LIVE fires it)'),
                fg='#CC0000')
        elif buy_t:
            self._trig_lbl.config(
                text=f"▼ BUY line crossed:  {buy_t['qty']} @ "
                     f"{fmt_price(buy_t['price'], self.ccy)}"
                     + ('' if manual_ok else '   (LIVE fires it)'),
                fg='#3366CC')
        elif ui.get('trigger_note'):
            # A line IS crossed but cannot be fired (e.g. no reserve army) —
            # say exactly why the manual button is off.
            self._trig_lbl.config(text=ui['trigger_note'], fg='#B8860B')
        elif ui.get('orders'):
            sides = {o.get('side') for o in ui['orders']}
            self._trig_lbl.config(
                text='● ' + '/'.join(s.lower() for s in sides if s)
                     + ' order resting on Toss', fg='#0033AA')
        else:
            self._trig_lbl.config(text='no line crossed', fg='#888')

    # ── Line labels shared by both charts ────────────────────────────────────

    def _line_labels(self, ui):
        """[(key, label, color)] for every present line, top (sells) first.
        An unfundable buy line stays visible but muted: ✕ … (no army)."""
        pct = ui.get('pct')
        no_army = ui.get('buy_state') == 'EXHAUSTED'
        out = []
        for key in ('tier3', 'tier2', 'tier1'):
            if (ui.get('lines') or {}).get(key):
                out.append((key, f'매도 T{key[-1]}', _CLR[key]))
        if (ui.get('lines') or {}).get('psell'):
            out.append(('psell', 'SELL (pseudo)', _CLR['psell']))
        if (ui.get('lines') or {}).get('chase'):
            name = f'-{pct}%' + (' x1.0' if pct == 8 else '') + ' BUY'
            if no_army:
                out.append(('chase', f'✕ {name} (no army)', _NO_ARMY_CLR))
            else:
                out.append(('chase', name, _CLR['chase']))
        if (ui.get('lines') or {}).get('load'):
            tag = ' 재입질' if ui.get('anchor_source') == 'sell' else ''
            name = f'-{pct}% LOAD{tag}'
            if no_army:
                out.append(('load', f'✕ {name} (no army)', _NO_ARMY_CLR))
            else:
                out.append(('load', name, _CLR['load']))
        return out

    def _refresh_candles(self):
        ui = self.ui
        ohlc = self.ap['ohlc']() or []
        refs = []
        if ui:
            lines = ui.get('lines') or {}
            deployed = (ui.get('shares') or 0) > 0
            avg = ui.get('avg_cost') or 0
            if deployed and avg > 0:
                refs.append({'label': f'AVG {fmt_price(avg, self.ccy)}',
                             'price': avg, 'color': _CLR['avg'],
                             'dash': (5, 4), 'width': 2})
            elif ui.get('anchor'):
                refs.append({'label': f'Vantage {fmt_price(ui["anchor"], self.ccy)}',
                             'price': ui['anchor'], 'color': _CLR['anchor'],
                             'dash': (5, 4), 'width': 1.6})
            for key, name, color in self._line_labels(ui):
                p, q = lines[key]
                refs.append({'label': f'{name} ×{q}', 'price': p,
                             'color': color, 'dash': (2, 4), 'width': 1.6})
        self.candle_panel.update(ohlc=ohlc, ref_lines=refs,
                                 current=(ui or {}).get('price'))

    def _update_campaign_status(self, ui):
        state = ui.get('state') or 'ARMING'
        self._campaign_status_lbl.config(
            text=campaign_status_text(ui, self.ccy),
            fg=_CAMPAIGN_STATE_CLR.get(state, '#666666'))

    def _fill_log(self, ui):
        events = ui.get('events') or []
        state = ui.get('state') or 'ARMING'
        txt = self._log_txt
        txt.config(state='normal')
        txt.delete('1.0', 'end')
        txt.insert('end', 'RECORDED FILLS\n', 'HEADER')
        if not events:
            if state == 'DEPLOYED':
                txt.insert('end', '(no buy/sell changes recorded\n'
                                  'since this watcher started)\n')
            elif state == 'EMPTY':
                txt.insert('end', '(no active campaign fills —\n'
                                  'waiting for the next LOAD)\n')
            else:
                txt.insert('end', '(watcher is arming)\n')
        for e in events:
            price = e.get('price')
            p = fmt_price(price, self.ccy) if price else '--'
            line = (f"{e['ts']}  {e['kind']:<5} {e['qty']:+d} @ {p}"
                    f"  → {e['shares']} sh")
            if e.get('avg'):
                line += f" @ {fmt_price(e['avg'], self.ccy)}"
            kind = e['kind'] if e['kind'] in _KIND_CLR else (
                'TIER' if e['kind'].startswith('T') else 'OTHER')
            txt.insert('end', line + '\n', kind)
        txt.config(state='disabled')
        txt.see('end')

    # ── Live chart ────────────────────────────────────────────────────────────

    def _live_reference_rows(self, ui, lines, deployed, avg, anchor):
        """[(price, color, text, dash, width)] drawn on the live chart."""
        rows = []
        if deployed and avg > 0:
            rows.append((avg, _CLR['avg'],
                         f'AVG {fmt_price(avg, self.ccy)}', (5, 4), 1.6))
        elif anchor:
            rows.append((anchor, _CLR['anchor'],
                         f'Vantage {fmt_price(anchor, self.ccy)}',
                         (5, 4), 1.4))
        for key, name, color in self._line_labels(ui):
            p, q = lines[key]
            rows.append((p, color,
                         f'{name} {fmt_price(p, self.ccy)} ×{q}', None, 2.4))
        return rows

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

        prices = [p for _, p in ticks]
        prices += [v[0] for v in lines.values()]
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

        ref_rows = self._live_reference_rows(
            ui, lines, deployed, avg, anchor)
        left, top, bottom = 90, 18, 32
        wanted_right = required_label_pad(
            [row[2] for row in ref_rows], self._ref_font.measure,
            minimum=185, padding=16)
        max_right = max(100, w - left - 80)
        right = min(wanted_right, max_right)
        narrow_labels = wanted_right > max_right
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

        # The gear zone: buy line up to the first sell line, shaded.
        lo = (lines.get('chase') or lines.get('load') or (None,))[0]
        sell_prices = [lines[k][0] for k in ('tier1', 'tier2', 'tier3', 'psell')
                       if k in lines]
        hi = min(sell_prices) if sell_prices else None
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
            text_x, anchor, wrap_width = label_x, 'w', None
            if narrow_labels:
                text_x, anchor, wrap_width = bounded_label_layout(
                    w, self._ref_font.measure(text))
            options = {'text': text, 'anchor': anchor,
                       'font': _F_REF, 'fill': color}
            if wrap_width is not None:
                options['width'] = wrap_width
            c.create_text(text_x, y, **options)

        for price, color, text, dash, width in ref_rows:
            ref(price, color, text, dash=dash, width=width)

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
