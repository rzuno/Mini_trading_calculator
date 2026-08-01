"""Embeddable 5-day candle panel — the informational half of the campaign
cockpit.

Shows the five sessions with **V, the strategy's own volatility number** —
100×(High5−Low5)/High5, the very figure the automatic gear is chosen from —
plus whatever reference lines the caller passes (the campaign's lines, the
current price).

No order buttons live here, but a day CAN be clicked: `on_pick_day` receives
that session's bar, which the cockpit uses to pin the vantage to its high.
This is the visible alternative to Automatic Dynamic High5 and is available
only while the stock is flat.
"""

import tkinter as tk
import tkinter.font as tkfont

from core.calc import calc_volatility, fmt_price, select_auto_gear


def bar_range_v(bars):
    """V over the given bars: 100×(high−low)/high across the whole window —
    the same measure `core.calc.calc_volatility` applies to the fetched 5-day
    high/low. Used only as a fallback when the watcher has not supplied its
    own figure yet."""
    highs = [b['high'] for b in (bars or []) if b.get('high')]
    lows = [b['low'] for b in (bars or []) if b.get('low')]
    if not highs or not lows:
        return None
    return calc_volatility(max(highs), min(lows))

_F_STAT = ('Segoe UI', 12, 'bold')
_F_DAY  = ('Segoe UI', 10)
_F_AXIS = ('Segoe UI', 10)
_F_REF  = ('Segoe UI', 10, 'bold')

_CUR_CLR = '#222222'
_UP_CLR = '#CC3333'
_DOWN_CLR = '#3366CC'
_PIN_CLR = '#E08000'
_PIN_BG = '#FFF3D6'


def candle_color(day):
    """Use the same direction color for an OHLC row and its candle body."""
    return _UP_CLR if day['close'] >= day['open'] else _DOWN_CLR


def bar_day_labels(bars):
    """One label per bar for the x-axis, guaranteed distinct.

    `date` is MM/DD, which repeats the moment two bars share a day — and a
    row of identical labels tells the reader nothing about which candle is
    which. Whenever MM/DD is not unique the full ISO date is used instead,
    and anything still colliding is numbered, so every candle is nameable."""
    out = []
    for b in (bars or []):
        ts = str(b.get('ts') or '')
        out.append((b.get('date') or ts[5:10].replace('-', '/') or '?', ts))
    labels = [d for d, _ts in out]
    if len(set(labels)) == len(labels):
        return labels
    labels = [(ts[:10] if len(ts) >= 10 else d) for d, ts in out]
    seen, final = {}, []
    for lab in labels:
        seen[lab] = seen.get(lab, 0) + 1
        final.append(lab if seen[lab] == 1 else f'{lab}#{seen[lab]}')
    return final


def bar_pick_label(bar, display_label=None):
    """Stable human label persisted with a manually selected high."""
    label = display_label or bar.get('date') or str(bar.get('ts') or '')[:10]
    return f'{label or "selected day"} high'


def bar_is_selected(bar, selected_price=None, selected_label='',
                    display_label=None):
    """Whether a candle is the manually pinned Vantage day."""
    try:
        price = float(selected_price)
        high = float(bar.get('high'))
    except (TypeError, ValueError):
        return False
    if abs(high - price) > max(1e-8, abs(price) * 1e-8):
        return False
    label = str(selected_label or '').strip().strip('() ').lower()
    if not label or label == 'selected high':
        return True
    candidates = {
        str(display_label or '').lower(),
        str(bar.get('date') or '').lower(),
        str(bar.get('ts') or '')[:10].lower(),
    }
    return any(token and token in label for token in candidates)


def required_label_pad(labels, measure, minimum, padding=12):
    """Right-side canvas space needed to show every label without clipping.

    ``measure`` is injected so the sizing rule stays easy to test without a
    Tk display (at runtime it is ``tkinter.font.Font.measure``).
    """
    widths = [measure(text) for text in labels if text]
    return max(minimum, (max(widths) if widths else 0) + padding)


def bounded_label_layout(canvas_width, text_width, inset=6):
    """Keep a right-edge label horizontally inside a narrow canvas.

    A label that fits is right-anchored.  If the complete text is wider than
    the canvas, return a bounded wrap width instead of letting either edge be
    clipped.  Normal-width charts do not need this fallback.
    """
    canvas_width = max(1, int(canvas_width))
    inset = min(max(0, int(inset)), canvas_width // 2)
    usable = max(1, canvas_width - inset * 2)
    if text_width <= usable:
        return canvas_width - inset, 'e', None
    return inset, 'w', usable


class CandlePanel(tk.Frame):
    """5-day candles + reference lines. Call update() with fresh data; the
    panel redraws itself (also on resize)."""

    def __init__(self, parent, currency, width=560, on_pick_day=None):
        super().__init__(parent)
        self.ccy = currency
        self.ohlc = []
        self.ref_lines = []     # [{'label','price','color','dash','width'}]
        self.current = None
        self.vol5 = None        # 5-day range V from the watcher
        self.on_pick_day = on_pick_day
        self._day_bands = []    # [(x0, x1, bar, display_label)]
        self.selection_enabled = False
        self.selected_price = None
        self.selected_label = ''
        self._ref_font = tkfont.Font(root=self, font=_F_REF)
        self._row_wrap = max(100, width - 10)

        self._stat = tk.Label(self, text='5-day chart — waiting for data',
                              font=_F_STAT, anchor='w')
        self._stat.pack(fill='x')
        self._pick_hint = tk.Label(self, text='', font=_F_DAY, fg='#777',
                                   anchor='w')
        self._pick_hint.pack(fill='x')
        self._days = tk.Frame(self)
        self._days.pack(fill='x')
        self.canvas = tk.Canvas(self, bg='white', highlightthickness=0,
                                width=width)
        self.canvas.pack(fill='both', expand=True, pady=(4, 0))
        self.canvas.bind('<Configure>', lambda e: self._draw())
        self.canvas.bind('<Button-1>', self._on_click)
        self.bind('<Configure>', self._resize_day_rows)

    def _on_click(self, event):
        """Clicking a candle hands its bar to the cockpit."""
        if not self.on_pick_day or not self.selection_enabled:
            return
        for x0, x1, bar, label in self._day_bands:
            if x0 <= event.x <= x1:
                self._pick_bar(bar, label)
                return

    def _pick_bar(self, bar, display_label):
        if not self.on_pick_day or not self.selection_enabled:
            return
        picked = dict(bar)
        picked['_vantage_label'] = bar_pick_label(bar, display_label)
        self.on_pick_day(picked)

    def _resize_day_rows(self, event):
        """Keep every colored OHLC row inside the panel as it is resized."""
        self._row_wrap = max(100, event.width - 10)
        for child in self._days.winfo_children():
            child.config(wraplength=self._row_wrap)

    # ── Data in ───────────────────────────────────────────────────────────────

    def update(self, ohlc=None, ref_lines=None, current=None, vol5=None,
               selection_enabled=False, selected_price=None,
               selected_label=''):
        if ohlc is not None:
            self.ohlc = list(ohlc)
        if ref_lines is not None:
            self.ref_lines = [r for r in ref_lines if r.get('price')]
        self.current = current
        self.vol5 = vol5
        self.selection_enabled = bool(selection_enabled)
        self.selected_price = selected_price
        self.selected_label = selected_label or ''
        self._update_stats()
        self._draw()

    def _update_stats(self):
        if not self.ohlc:
            self._stat.config(text='5-day chart — no data')
            self._pick_hint.config(text='')
            for child in self._days.winfo_children():
                child.destroy()
            return
        hi = max(d['high'] for d in self.ohlc)
        lo = min(d['low'] for d in self.ohlc)
        # V is the strategy's own volatility: the span of the whole 5-day
        # window as a percent of its high — the number the automatic gear is
        # read off, not a per-day average.
        v = self.vol5
        if v is None:
            v = bar_range_v(self.ohlc)
        v_txt = (f'V {v:.1f}% → G{select_auto_gear(v)}' if v is not None
                 else 'V --')
        self._stat.config(
            text=f'5D  High {fmt_price(hi, self.ccy)}   '
                 f'Low {fmt_price(lo, self.ccy)}   {v_txt}')
        selected = self.selected_price is not None
        if not self.selection_enabled:
            hint = 'Vantage selection is locked while deployed or an order is pending.'
        elif selected:
            hint = ('Pinned day is highlighted. Pick another day to move it, '
                    'or return to Dynamic High5 above.')
        else:
            hint = 'Pick Vantage: click a candle or an OHLC row to pin its high.'
        self._pick_hint.config(text=hint)
        for child in self._days.winfo_children():
            child.destroy()
        for d, lab in zip(self.ohlc, bar_day_labels(self.ohlc)):
            rng = (d['high'] - d['low']) / d['low'] * 100 if d['low'] else 0
            picked = bar_is_selected(d, self.selected_price,
                                     self.selected_label, lab)
            marker = ('✓ PINNED  ' if picked else
                      ('▲ PIN HIGH  ' if self.selection_enabled else ''))
            row = (f"{marker}{lab}  O {fmt_price(d['open'], self.ccy)}"
                   f"  H {fmt_price(d['high'], self.ccy)}"
                   f"  L {fmt_price(d['low'], self.ccy)}"
                   f"  C {fmt_price(d['close'], self.ccy)}"
                   f"  ({rng:.1f}%)")
            button = tk.Button(
                self._days, text=row, font=_F_DAY,
                fg=candle_color(d), disabledforeground=candle_color(d),
                bg=(_PIN_BG if picked else 'white'),
                activebackground=_PIN_BG, anchor='w', justify='left',
                wraplength=self._row_wrap, bd=(2 if picked else 1),
                relief=('sunken' if picked else 'raised'), takefocus=0,
                command=lambda bar=d, label=lab: self._pick_bar(bar, label))
            button.config(state=('normal' if self.selection_enabled
                                 else 'disabled'),
                          cursor=('hand2' if self.selection_enabled else ''))
            button.pack(fill='x', anchor='w', pady=1)

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self):
        c = self.canvas
        c.delete('all')
        w, h = c.winfo_width(), c.winfo_height()
        if w < 120 or h < 80:
            return
        ohlc = self.ohlc
        if not ohlc:
            c.create_text(w / 2, h / 2, text='no candle data',
                          font=_F_DAY, fill='#888')
            return
        n = len(ohlc)

        left_pad, top_pad, bottom_pad = 62, 14, 30
        label_texts = [r['label'] for r in self.ref_lines]
        if self.current and self.current > 0:
            label_texts.append(f'Now {fmt_price(self.current, self.ccy)}')
        wanted_right = required_label_pad(
            label_texts, self._ref_font.measure, minimum=140, padding=14)
        max_right = max(90, w - left_pad - 80)
        right_pad = min(wanted_right, max_right)
        narrow_labels = wanted_right > max_right

        prices = []
        for d in ohlc:
            prices.extend([d['high'], d['low']])
        for r in self.ref_lines:
            prices.append(r['price'])
        if self.current and self.current > 0:
            prices.append(self.current)

        p_min, p_max = min(prices), max(prices)
        p_range = (p_max - p_min) or 1
        p_min -= p_range * 0.08
        p_max += p_range * 0.08
        p_range = p_max - p_min

        chart_w = w - left_pad - right_pad
        chart_h = h - top_pad - bottom_pad
        if chart_w < 50 or chart_h < 50:
            return
        candle_w = chart_w / n
        body_w = candle_w * 0.55
        label_x = left_pad + chart_w + 5

        def y_of(price):
            return top_pad + chart_h * (1 - (price - p_min) / p_range)

        # Grid + left axis
        for i in range(5):
            price = p_min + p_range * i / 4
            y = y_of(price)
            c.create_line(left_pad, y, left_pad + chart_w, y, fill='#E8E8E8')
            c.create_text(left_pad - 4, y, text=fmt_price(price, self.ccy),
                          anchor='e', font=_F_AXIS, fill='#888')

        # Reference lines (labels on the right)
        for r in self.ref_lines:
            y = y_of(r['price'])
            c.create_line(left_pad, y, left_pad + chart_w, y,
                          fill=r['color'], dash=r.get('dash', (2, 4)),
                          width=r.get('width', 1.5))
            text_x, anchor, wrap_width = label_x, 'w', None
            if narrow_labels:
                text_x, anchor, wrap_width = bounded_label_layout(
                    w, self._ref_font.measure(r['label']))
            options = {'text': r['label'], 'anchor': anchor,
                       'font': _F_REF, 'fill': r['color']}
            if wrap_width is not None:
                options['width'] = wrap_width
            c.create_text(text_x, y, **options)

        # Current price — solid
        if self.current and self.current > 0:
            y = y_of(self.current)
            c.create_line(left_pad, y, left_pad + chart_w, y,
                          fill=_CUR_CLR, width=1.5)
            now_text = f'Now {fmt_price(self.current, self.ccy)}'
            text_x, anchor, wrap_width = label_x, 'w', None
            if narrow_labels:
                text_x, anchor, wrap_width = bounded_label_layout(
                    w, self._ref_font.measure(now_text))
            options = {'text': now_text, 'anchor': anchor,
                       'font': _F_REF, 'fill': _CUR_CLR}
            if wrap_width is not None:
                options['width'] = wrap_width
            c.create_text(text_x, y, **options)

        # Candles. Every label is distinct (bar_day_labels); when they still
        # cannot all fit side by side, every other one is dropped rather than
        # letting them overlap into an unreadable smear.
        labels = bar_day_labels(ohlc)
        widest = max((self._ref_font.measure(t) for t in labels), default=0)
        step = 1 if widest + 6 <= candle_w else 2
        self._day_bands = []
        for i, d in enumerate(ohlc):
            x = left_pad + candle_w * (i + 0.5)
            selected = bar_is_selected(d, self.selected_price,
                                       self.selected_label, labels[i])
            self._day_bands.append(
                (x - candle_w / 2, x + candle_w / 2, d, labels[i]))
            if selected:
                c.create_rectangle(
                    x - candle_w / 2 + 2, top_pad,
                    x + candle_w / 2 - 2, top_pad + chart_h,
                    outline=_PIN_CLR, width=2)
            c.create_line(x, y_of(d['high']), x, y_of(d['low']),
                          fill='#555', width=1)
            color = candle_color(d)
            y_top = min(y_of(d['open']), y_of(d['close']))
            y_bot = max(y_of(d['open']), y_of(d['close']))
            if y_bot - y_top < 2:
                y_bot = y_top + 2
            c.create_rectangle(x - body_w / 2, y_top, x + body_w / 2, y_bot,
                               fill=color,
                               outline=(_PIN_CLR if selected else '#444'),
                               width=(3 if selected else 1))
            if i % step == 0 or i == len(ohlc) - 1:
                c.create_text(x, h - 6, text=labels[i],
                              font=_F_AXIS, fill='#555', anchor='s')
        c.config(cursor=('hand2' if self.on_pick_day
                         and self.selection_enabled else ''))
