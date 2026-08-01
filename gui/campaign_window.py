"""Campaign window — the Gearbox V-Commandos cockpit for one watched stock.

Opened by the card's big V-COMMANDOS button. Opening it arms WATCH mode: the
stock is polled every few seconds and this window follows every tick. The bot
follows exactly the lines the CARD draws — same gear, same exit tier.

    WATCH  polling + line watching + fill detection; nothing is placed. A
           crossed line lights the trigger row so it can be fired by hand.
    LIVE   the bot places the LOAD / CHASE / full EXIT by itself the moment a
           line is crossed; allowed only during regular market hours (drops
           back to WATCH at the close).

Layout:

    header   name · campaign state · market phase · LIVE
    banner   gear line (G/​tier · load% / chase% ×frac · vantage · cap)
             position · reserve · campaign age / chases / low
             ▼ next CHASE   ▲ full EXIT
             engine status + poll time
             trigger row (Buy / Sell / Cancel all)
             campaign fill log
    charts   live tick curve with the campaign lines | 5-day candle panel

Closing the window in WATCH mode stops the polling; in LIVE the campaign
keeps running in the background (the card button stays colored).
"""

import time as _time
from datetime import datetime as _dt

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

from core.calc import STOCK_NAMES, fmt_price
from gui.candle_chart import (CandlePanel, bounded_label_layout,
                              required_label_pad)

_F_TITLE = ('Segoe UI', 17, 'bold')
_F_STAT  = ('Segoe UI', 13)
_F_BTN   = ('Segoe UI', 13, 'bold')
_F_INFO  = ('Segoe UI', 12)
_F_AXIS  = ('Segoe UI', 10)
_F_REF   = ('Segoe UI', 11, 'bold')
_F_FILLS = ('Consolas', 11)

# KR color language: red = the EXIT (sell) side, blue = the LOAD/CHASE side.
_CLR = {
    'exit':    '#CC3333',
    'buy':     '#3366CC',
    'vantage': '#E08000',
    'avg':     '#7E3FBF',
    'path':    '#1A1A1A',
    'now':     '#CC0000',
    'zone':    '#F4F1FA',
}
_PHASE_TXT = {'REGULAR': ('OPEN (regular)', '#007700'),
              'PRE':     ('pre-market', '#B8860B'),
              'AFTER':   ('after-market', '#B8860B'),
              'CLOSED':  ('CLOSED', '#888888')}
_LIVE_BG = '#CC0000'
_NO_ARMY_CLR = '#999999'
_STATE_CLR = {
    'FLAT': '#666666', 'ARMED_LOAD': '#0033AA', 'DEPLOYED': '#0033AA',
    'CHASE_PENDING': '#B8860B', 'EXIT_PENDING': '#B8860B',
    'COMPLETED': '#007700', 'RELOAD_ARMED': '#E08000',
    'PAUSED_RECONCILE': '#CC0000', 'ARMING': '#666666',
}
_FILLS_SHOWN = 10


def campaign_line(ui, currency):
    """The one banner line that names the whole campaign setup."""
    c = ui.get('campaign') or {}
    if not c:
        return 'arming…'
    parts = [f"G{c['gear']} {c['gear_name']}",
             f"LOAD -{c['load_pct']}%",
             f"CHASE -{c['chase_pct']}% ×{c['add_frac']}",
             f"EXIT T{c['exit_tier']} +{c['exit_pct']}% (full)"]
    if c.get('vantage'):
        src = {'high5': 'High5', 'close': 'prev close',
               'reload': 'sell fill'}.get(c.get('vantage_src'), '')
        parts.append(f"vantage {fmt_price(c['vantage'], currency)} ({src})")
    parts.append(f"cap {c.get('cap_units', 32):g}u")
    return '  ·  '.join(parts)


def campaign_age_line(ui, currency):
    """Campaign identity + progress: id, start, chases, low, peak deployment."""
    c = ui.get('campaign') or {}
    if not c or not c.get('campaign_id'):
        return ''
    bits = [f"campaign {c['campaign_id']}"]
    if c.get('campaign_start'):
        bits.append(f"since {c['campaign_start']}")
    bits.append(f"chases {c.get('chase_count', 0)}")
    if c.get('campaign_low'):
        bits.append(f"low {fmt_price(c['campaign_low'], currency)}")
    if c.get('max_cost'):
        bits.append(f"peak {fmt_price(c['max_cost'], currency)}")
    if c.get('manually_modified'):
        bits.append('MANUALLY_MODIFIED')
    return '   ·   '.join(bits)


def next_line(ui, currency):
    """'▼ next CHASE …   ▲ full EXIT …' straight off the engine's two lines."""
    lines = ui.get('lines') or {}
    parts = []
    chase = lines.get('chase')
    load = lines.get('load')
    if load:
        tail = (' [NO ARMY]' if ui.get('buy_state') == 'EXHAUSTED' else '')
        parts.append(f"▼ LOAD {fmt_price(load[0], currency)} × {load[1]}{tail}")
    elif chase:
        state = ui.get('buy_state')
        tail = (' [CAP]' if state == 'CAPPED'
                else ' [NO ARMY]' if state == 'EXHAUSTED' else '')
        parts.append(f"▼ CHASE {fmt_price(chase[0], currency)} × "
                     f"{chase[1]}{tail}")
    else:
        parts.append('▼ no chase line')
    ex = lines.get('exit') or lines.get('pexit')
    if ex:
        tag = 'EXIT' if lines.get('exit') else 'exit (projected)'
        parts.append(f"▲ {tag} {fmt_price(ex[0], currency)} × {ex[1]}")
    else:
        parts.append('▲ no exit line')
    return '      '.join(parts)


def fills_text(ui, currency, limit=_FILLS_SHOWN):
    """This campaign's fills — '' before the first one."""
    events = ui.get('events') or []
    if not events:
        return ''
    shown = events[-limit:]
    head = f'campaign fills ({len(events)})'
    if len(events) > len(shown):
        head += f' — last {len(shown)}'
    lines = [head + ':']
    for e in shown:
        price = e.get('price')
        p = fmt_price(price, currency) if price else '--'
        src = (e.get('source') or 'BOT')[:3]
        avg = e.get('avg')
        lines.append(f"  {e['ts']}  {e['kind']:<8} {src:<4}{e['qty']:+d} @ {p}"
                     f"  → {e['shares']} sh"
                     + (f" @ {fmt_price(avg, currency)}" if avg else ''))
    return '\n'.join(lines)


class CampaignWindow:
    """Live cockpit for one V-Commandos campaign. Subscribes to the
    controller and redraws on every tick."""

    def __init__(self, parent, ticker, currency, ap_ctx):
        self.ap = ap_ctx
        self.ticker = ticker
        self.ccy = currency
        self.ui = None

        self.win = tk.Toplevel(parent)
        name = STOCK_NAMES.get(ticker, ticker)
        self.win.title(f'{name} — V-Commandos campaign')
        self.win.geometry('1260x880')
        self.win.minsize(980, 640)
        self._ref_font = tkfont.Font(root=self.win, font=_F_REF)

        # ── Header ────────────────────────────────────────────────────────────
        head = tk.Frame(self.win, padx=12, pady=8)
        head.pack(fill='x')
        tk.Label(head, text=f'{name}  — V-Commandos Gearbox', font=_F_TITLE
                 ).pack(side='left')
        self._state_lbl = tk.Label(head, text='', font=_F_TITLE)
        self._state_lbl.pack(side='left', padx=(16, 0))

        self._live_btn = tk.Button(head, text='LIVE', font=_F_BTN, width=8,
                                   command=self._on_live)
        self._live_btn.pack(side='right', padx=(6, 0))
        self._default_bg = self._live_btn.cget('bg')
        self._phase_lbl = tk.Label(head, text='', font=_F_STAT)
        self._phase_lbl.pack(side='right', padx=(0, 8))

        # ── Banner ────────────────────────────────────────────────────────────
        self._gear_lbl = tk.Label(self.win, text='', font=_F_BTN,
                                  fg='#4B0082', anchor='w')
        self._gear_lbl.pack(fill='x', padx=12)
        self._age_lbl = tk.Label(self.win, text='', font=_F_INFO, fg='#666',
                                 anchor='w')
        self._age_lbl.pack(fill='x', padx=14)
        self._info_lbl = tk.Label(self.win, text='', font=_F_INFO, fg='#333',
                                  anchor='w')
        self._info_lbl.pack(fill='x', padx=14)
        self._next_lbl = tk.Label(self.win, text='', font=_F_INFO, fg='#333',
                                  anchor='w')
        self._next_lbl.pack(fill='x', padx=14)
        self._status_lbl = tk.Label(self.win, text='', font=_F_INFO,
                                    fg='#4B0082', anchor='w')
        self._status_lbl.pack(fill='x', padx=14)

        # ── Trigger row: fire a crossed line by hand (WATCH mode) ─────────────
        trig = tk.Frame(self.win, padx=14, pady=2)
        trig.pack(fill='x')
        self._trig_lbl = tk.Label(trig, text='', font=_F_INFO, anchor='w')
        self._trig_lbl.pack(side='left')
        self._cancel_btn = tk.Button(trig, text='Cancel all', font=_F_INFO,
                                     command=self._do_cancel)
        self._cancel_btn.pack(side='right', padx=(6, 0))
        self._sell_btn = tk.Button(trig, text='SELL', font=_F_BTN, width=8,
                                   command=lambda: self._do_fire('SELL'))
        self._sell_btn.pack(side='right', padx=(6, 0))
        self._buy_btn = tk.Button(trig, text='BUY', font=_F_BTN, width=8,
                                  command=lambda: self._do_fire('BUY'))
        self._buy_btn.pack(side='right', padx=(6, 0))

        self._fills_lbl = tk.Label(self.win, text='', font=_F_FILLS,
                                   fg='#333', anchor='w', justify='left')
        # packed/unpacked on demand in _update_fills

        # ── Body: live chart | 5-day candle panel ─────────────────────────────
        self._body = tk.Frame(self.win)
        self._body.pack(fill='both', expand=True, padx=12, pady=(6, 12))

        self.canvas = tk.Canvas(self._body, bg='white', highlightthickness=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self._draw())

        self.candle_panel = CandlePanel(self._body, currency, width=470)
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
        if self.ap['mode_of']() == 'WATCH':
            self.ap['disable']()

    # ── Controls ──────────────────────────────────────────────────────────────

    def _on_live(self):
        if self.ap['mode_of']() == 'LIVE':       # click again → back to WATCH
            self.ap['set_mode']('WATCH')
            return
        ui = self.ap['ui_state']() or {}
        c = ui.get('campaign') or {}
        lines = ui.get('lines') or {}
        detail = [f'Go LIVE on {self.ticker}?', '',
                  'The V-Commandos campaign bot places these by ITSELF the '
                  'moment a line is crossed:']
        if lines.get('load'):
            detail.append(f"  LOAD   {lines['load'][1]} @ "
                          f"{fmt_price(lines['load'][0], self.ccy)}")
        if lines.get('chase'):
            detail.append(f"  CHASE  {lines['chase'][1]} @ "
                          f"{fmt_price(lines['chase'][0], self.ccy)}")
        if lines.get('exit'):
            detail.append(f"  EXIT   {lines['exit'][1]} (ALL) @ "
                          f"{fmt_price(lines['exit'][0], self.ccy)}")
        detail += [
            '',
            f"Gear {c.get('gear', '?')} is fixed for the campaign: LOAD "
            f"-{c.get('load_pct', '?')}%, CHASE -{c.get('chase_pct', '?')}% "
            f"×{c.get('add_frac', '?')}, one full EXIT at T"
            f"{c.get('exit_tier', '?')} +{c.get('exit_pct', '?')}%. "
            f"One order at a time; every fill recomputes both lines from the "
            f"broker's real average; the campaign ends only when the holding "
            f"is zero. LIVE drops back to WATCH when the market closes.",
        ]
        if not messagebox.askyesno('Campaign LIVE', '\n'.join(detail),
                                   parent=self.win):
            return
        ok, msg = self.ap['set_mode']('LIVE')
        if not ok:
            messagebox.showwarning('Autopilot', msg, parent=self.win)

    def _do_fire(self, side):
        trig = ((self.ui or {}).get('trigger') or {}).get(side)
        if not trig:
            return
        text = (f"{trig.get('label', side)}\n\n{side} {trig['qty']} @ "
                f"{fmt_price(trig['price'], self.ccy)}\n\nSend this order now?")
        if not messagebox.askyesno('Fire the crossed line', text,
                                   parent=self.win):
            return
        ok, msg = self.ap['manual_fire'](side)
        if not ok:
            messagebox.showwarning('Order', msg, parent=self.win)

    def _do_cancel(self):
        if not messagebox.askyesno(
                'Cancel', f'Cancel EVERY live order on {self.ticker}?',
                parent=self.win):
            return
        ok, msg = self.ap['cancel_all']()
        if not ok:
            messagebox.showwarning('Cancel', msg, parent=self.win)

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

        state = ui.get('campaign_state') or ui.get('state', '?')
        self._state_lbl.config(text=state, fg=_STATE_CLR.get(state, '#666'))

        mode = ui.get('mode', 'WATCH')
        self._live_btn.config(
            bg=(_LIVE_BG if mode == 'LIVE' else self._default_bg),
            fg=('white' if mode == 'LIVE' else 'black'))

        phase = ui.get('phase', 'CLOSED')
        txt, pclr = _PHASE_TXT.get(phase, (phase, '#888'))
        mkt = 'KR' if self.ticker.endswith('.KS') else 'US'
        self._phase_lbl.config(text=f'{mkt} market: {txt}', fg=pclr)

        self._gear_lbl.config(text=campaign_line(ui, self.ccy))
        self._age_lbl.config(text=campaign_age_line(ui, self.ccy))
        self._info_lbl.config(text=self._info_text(ui))
        self._next_lbl.config(text=next_line(ui, self.ccy))
        self._status_lbl.config(
            text=f"{ui.get('status', '')}    poll {ui.get('ts', '--')}")
        self._update_trigger_row(ui)
        self._update_fills(ui)
        self._draw()
        self._refresh_candles()

    def _info_text(self, ui):
        parts = []
        shares = ui.get('shares') or 0
        price = ui.get('price')
        avg = ui.get('avg_cost') or 0
        unit = ui.get('unit_cash') or 0
        parts.append(f'{shares:,} sh'
                     + (f' @ {fmt_price(avg, self.ccy)} avg' if avg else ''))
        if shares and avg and unit > 0:
            parts.append(f'{shares * avg / unit:,.2f} u deployed')
        if price:
            parts.append(f'now {fmt_price(price, self.ccy)}')
        if shares and avg and price:
            parts.append(f'P&L {(price - avg) / avg * 100:+.2f}%')
        c = ui.get('campaign') or {}
        if c.get('gross_target'):
            parts.append(f"target {fmt_price(c['gross_target'], self.ccy)}")
        bp = ui.get('buying_power')
        if bp is not None:
            parts.append(f'reserve {fmt_price(bp, self.ccy)}')
        orders = ui.get('orders') or []
        if orders:
            sides = sorted({o.get('side') for o in orders if o.get('side')})
            parts.append('● resting: ' + '/'.join(sides))
        return '      '.join(parts)

    def _update_trigger_row(self, ui):
        trig = ui.get('trigger') or {}
        live = ui.get('mode') == 'LIVE'
        note = ui.get('trigger_note')
        msgs = []
        for side, btn in (('BUY', self._buy_btn), ('SELL', self._sell_btn)):
            t = trig.get(side)
            if t and not live:
                btn.config(state='normal',
                           text=f"{side} {t['qty']}",
                           bg=(_CLR['buy'] if side == 'BUY' else _CLR['exit']),
                           fg='white')
                msgs.append(f"{t.get('label', side)} @ "
                            f"{fmt_price(t['price'], self.ccy)}")
            else:
                btn.config(state='disabled', text=side,
                           bg=self._default_bg, fg='black')
        if live:
            text, color = 'LIVE — the bot fires by itself', '#CC0000'
        elif note:
            text, color = note, _NO_ARMY_CLR
        elif msgs:
            text, color = 'line crossed:  ' + '   ·   '.join(msgs), '#0033AA'
        else:
            text, color = 'no line crossed — watching', '#888'
        self._trig_lbl.config(text=text, fg=color)

    def _update_fills(self, ui):
        text = fills_text(ui, self.ccy)
        if text:
            self._fills_lbl.config(text=text)
            if not self._fills_lbl.winfo_ismapped():
                self._fills_lbl.pack(fill='x', padx=14, pady=(2, 0),
                                     before=self._body)
        elif self._fills_lbl.winfo_ismapped():
            self._fills_lbl.pack_forget()

    # ── Campaign reference lines, shared by both charts ──────────────────────

    def _line_rows(self, ui):
        """[(price, color, text, bold)] — the campaign's real lines only."""
        rows = []
        lines = ui.get('lines') or {}
        c = ui.get('campaign') or {}
        avg = ui.get('avg_cost') or 0
        state = ui.get('buy_state')

        if lines.get('load'):
            p, q = lines['load']
            tag = 'RELOAD' if c.get('vantage_src') == 'reload' else 'LOAD'
            text = f'{tag} {fmt_price(p, self.ccy)} × {q}'
            color = _CLR['buy']
            if state == 'EXHAUSTED':
                color, text = _NO_ARMY_CLR, f'✕ {text} (no army)'
            rows.append((p, color, text, True))
        if lines.get('chase'):
            p, q = lines['chase']
            text = (f"CHASE -{c.get('chase_pct', '?')}% "
                    f"{fmt_price(p, self.ccy)} × {q}")
            color = _CLR['buy']
            if state in ('EXHAUSTED', 'CAPPED'):
                why = 'cap' if state == 'CAPPED' else 'no army'
                color, text = _NO_ARMY_CLR, f'✕ {text} ({why})'
            rows.append((p, color, text, True))
        if avg > 0:
            rows.append((avg, _CLR['avg'],
                         f'avg {fmt_price(avg, self.ccy)}', True))
        if lines.get('exit'):
            p, q = lines['exit']
            rows.append((p, _CLR['exit'],
                         f"EXIT T{c.get('exit_tier', '?')} "
                         f"+{c.get('exit_pct', '?')}% "
                         f"{fmt_price(p, self.ccy)} × {q} (all)", True))
        elif lines.get('pexit'):
            p, q = lines['pexit']
            rows.append((p, _CLR['exit'],
                         f'exit if loaded {fmt_price(p, self.ccy)} × {q}',
                         False))
        if c.get('vantage'):
            v = c['vantage']
            src = {'high5': 'High5', 'close': 'prev close',
                   'reload': 'sell fill'}.get(c.get('vantage_src'), '')
            rows.append((v, _CLR['vantage'],
                         f'vantage {fmt_price(v, self.ccy)} ({src})', False))
        return rows

    def _refresh_candles(self):
        ui = self.ui
        ohlc = self.ap['ohlc']() or []
        refs = []
        if ui:
            for price, color, text, bold in self._line_rows(ui):
                refs.append({'label': text, 'price': price, 'color': color,
                             'dash': ((2, 4) if bold else (3, 6)),
                             'width': (1.8 if bold else 1.1)})
        self.candle_panel.update(ohlc=ohlc, ref_lines=refs,
                                 current=(ui or {}).get('price'),
                                 day_v_avg=(ui or {}).get('day_v_avg'))

    # ── Live chart ────────────────────────────────────────────────────────────

    def _draw(self):
        c = self.canvas
        c.delete('all')
        w, h = c.winfo_width(), c.winfo_height()
        if w < 160 or h < 120:
            return
        ui = self.ui
        if not ui:
            c.create_text(w / 2, h / 2, text='Campaign bot is off',
                          font=_F_STAT, fill='#888')
            return

        ticks = ui.get('ticks') or []
        rows = self._line_rows(ui)

        prices = [p for _, p in ticks]
        if ui.get('price'):
            prices.append(ui['price'])
        # Scale to the two watched lines plus the average; the vantage only
        # widens the chart when it is already close.
        for price, _clr, _txt, bold in rows:
            if bold:
                prices.append(price)
        if not prices:
            c.create_text(w / 2, h / 2, text='waiting for the first tick…',
                          font=_F_STAT, fill='#888')
            return

        left, top, bottom = 90, 18, 32
        wanted_right = required_label_pad(
            [r[2] for r in rows], self._ref_font.measure,
            minimum=200, padding=16)
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

        # Shade the corridor the campaign lives in: next buy line → EXIT.
        lines = ui.get('lines') or {}
        lo = (lines.get('chase') or lines.get('load') or (None,))[0]
        hi = (lines.get('exit') or lines.get('pexit') or (None,))[0]
        if lo and hi and hi > lo:
            c.create_rectangle(left, y_of(min(hi, p_max)),
                               left + cw, y_of(max(lo, p_min)),
                               fill=_CLR['zone'], outline='')

        for i in range(5):
            p = p_min + p_rng * i / 4
            y = y_of(p)
            c.create_line(left, y, left + cw, y, fill='#E8E8E8')
            c.create_text(left - 5, y, text=fmt_price(p, self.ccy),
                          anchor='e', font=_F_AXIS, fill='#888')

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

        for price, color, text, bold in rows:
            if price < p_min or price > p_max:
                continue
            ref(price, color, text,
                dash=(None if bold else (2, 5)),
                width=(2.4 if bold else 1.2))

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
