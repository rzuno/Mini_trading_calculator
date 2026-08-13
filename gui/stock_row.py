import tkinter as tk

from core.calc import (
    DEFAULT_EXIT_TIER, DEFAULT_GEAR, EXIT_TIERS, GEARS,
    calc_chase_cascade, calc_exit_lines, calc_gap_rate, calc_load_gap_rate,
    calc_load_ladder, calc_reload_price, chase_drop, clamp_gear, display_name,
    effective_entry_gear, exit_pct, fmt_price, gap_color, gear_button_color,
    gear_button_fg, gear_detail, gear_for_chase_pct, gear_label,
    gear_menu_label, gear_params, load_drop, load_gap_color, select_auto_gear,
    sell_pct_color, sell_pct_foreground, tier_pcts,
)

# Readable blue for buy-trigger values (matches the chart's chase lines)
_BUY_FG = '#3366CC'
_SELL_FG = '#CC0000'

# -- Fonts (1.3x scale for QHD) ----------------------------------------------
_F_NAME_DEPLOYED = ('Segoe UI', 13, 'bold')  # deployed stocks are bold
_F_NAME_EMPTY    = ('Segoe UI', 13)          # flat stocks are plain
_F_LBL  = ('Segoe UI', 12)
_F_VAL  = ('Segoe UI', 12)
_F_OUT  = ('Segoe UI', 13, 'bold')
_F_SM   = ('Segoe UI', 10)
_F_SM_B = ('Segoe UI', 10, 'bold')
_F_BTN  = ('Segoe UI', 11, 'bold')
_F_STATUS = ('Segoe UI', 12, 'bold')
_F_GEAR_BADGE = ('Segoe UI', 18, 'bold')

# A ticked tier is written in its own exit colour. sell_pct_color is a
# BACKGROUND ramp that starts near white, which is unreadable as text, so the
# low tiers get a darkened equivalent here.
_TIER_TEXT = {1: '#B8860B', 2: '#C07000', 3: '#CC5500',
              4: '#CC3333', 5: '#CC0000', 6: '#B00000', 7: '#A00000'}


def tier_text_color(pct):
    return _TIER_TEXT.get(int(pct), '#880000')

# Greys for a "muted" (state-inactive but informational) control
_MUTE_TITLE = '#C0C0C0'
_MUTE_BG    = '#F0F0F0'
_MUTE_FG    = '#9A9A9A'
_AP_BUTTON_TOP_GAP = 18       # one heading-line below the gear-box titles


class StockRow:
    """One Gearbox V-Commandos campaign card (both FLAT and DEPLOYED).

    ONE gearbox drives the card AND the campaign bot:

        LOAD    vantage        × (1 - gear.load%)      ≈ 1 unit of cash
        CHASE   actual avg     × (1 - gear.chase%)     shares × gear.ratio
        EXIT    actual avg     × (1 + tier%)           split across the tiers
                                                       that are armed

    * DEPLOYED — the buy ladder is the real chase cascade off the broker's
      average cost (Chase 1/2/3); the exit row is the real ladder, the holding
      split across the armed tiers.
    * FLAT — both are projections off the LOAD: Load / Chase 1 / Chase 2, and
      the exits computed as if the load had filled.

    **Exit tiers are a multi-select.** Arm one and the whole position leaves
    there. Arm two or three and it leaves in portions — a tier that fills is
    spent, the rest stay armed, and the campaign ends only when the holding is
    actually zero. A chase re-arms every tier on the larger holding. Tier
    selections apply immediately; the last armed tier cannot be switched off.

    The gear can be shifted at any time, deployed or not: only the average
    cost is history, and every line is recomputed from it on the spot. AUTO
    tracks the 5-day range V; MANUAL holds whatever the commander picked.

    The parent grids ``self.frame``; the row does not place itself.
    """

    def __init__(self, parent, row_num: int, pos: dict, deployed: bool,
                 get_unit_cash, on_compute=None, editable=True,
                 on_autopilot=None):
        self.deployed       = deployed
        self.editable       = editable
        self.ticker         = pos['ticker']
        self.tier           = pos.get('tier', 'Major')
        self.currency       = 'KRW' if self.ticker.endswith('.KS') else 'USD'
        self.get_unit_cash  = get_unit_cash
        self.on_autopilot   = on_autopilot
        self._on_compute_cb = on_compute
        self.current_price  = None
        self.vantage        = None    # High5 / prev close / same-day sell fill
        self.vantage_src    = 'high5' # high5 | close | reload | manual
        self.volatility     = None
        self._army_pct      = None
        self._gap           = None    # actionable gap %, for global ordering
        self._computing     = False
        self._syncing_gear  = False
        self._order_side    = None
        self._base_gear = None        # pure volatility gear
        self._eff_gear  = None        # after the heavy-unit rule

        # -- Card frame --------------------------------------------------------
        self.frame = tk.Frame(parent, bd=1, relief='groove', padx=8, pady=3)

        # -- Input variables ---------------------------------------------------
        self.shares_var   = tk.StringVar(
            value=str(pos.get('shares', 0)) if pos.get('shares', 0) > 0 else '')
        self.avg_cost_var = tk.StringVar(value=self._fmt_init(pos.get('avg_cost', 0)))

        self.gear_var = tk.IntVar(value=self._initial_gear(pos))
        # One BooleanVar per exit tier: arm one for a clean full-position exit,
        # or two/three to leave in portions (the split lives in calc.py).
        self.tier_vars = [tk.BooleanVar(value=v)
                          for v in self._initial_tiers(pos)]
        self.auto_var = tk.BooleanVar(value=bool(pos.get('auto_mode', True)))

        # -- Output variables --------------------------------------------------
        self.current_var  = tk.StringVar(value='--')
        self.gap_var      = tk.StringVar(value='--')
        self.cost_var     = tk.StringVar(value='--')
        self.army_pct_var = tk.StringVar(value='')
        self.vol_var      = tk.StringVar(value='V --')
        self.gear_txt_var = tk.StringVar(value='')
        self.buy_info_var = [tk.StringVar(value='--') for _ in range(3)]
        self.t_info_var   = [tk.StringVar(value='--') for _ in range(3)]
        # Buy-ladder row labels differ by state (Load/Chase 1/2 vs Chase 1/2/3)
        self._buy_lbl_var = [tk.StringVar(value='') for _ in range(3)]

        self._build_title_row(row_num)
        self._build_body()

        # -- Reactive traces (after all widgets exist) -------------------------
        self.shares_var.trace_add('write', lambda *_: self._on_input_change())
        self.avg_cost_var.trace_add('write', lambda *_: self._on_input_change())
        self.gear_var.trace_add('write', lambda *_: self._on_input_change())
        for v in self.tier_vars:
            v.trace_add('write', lambda *_: self._on_input_change())
        self.auto_var.trace_add('write', lambda *_: self._on_input_change())

        # Initial styling pass (rows are computed on the first price fetch).
        self._refresh_gear_styles()
        self._update_auto_btn()
        self._update_vol_label()

    # ── Initial gear / tier (with legacy CSV migration) ──────────────────────

    @staticmethod
    def _initial_gear(pos):
        if pos.get('gear'):
            return clamp_gear(pos['gear'])
        # Legacy positions carried a bait DROP PERCENT, not a gear number.
        legacy = pos.get('buy_pct') or pos.get('load_gear')
        return gear_for_chase_pct(legacy) if legacy else DEFAULT_GEAR

    @staticmethod
    def _initial_tiers(pos):
        """Three on/off flags. `t1_active`..`t3_active` have carried them
        since the first CSV; `exit_tier` (the single-tier era) is read as a
        one-tier selection when the flags are absent."""
        if any(f't{i}_active' in pos for i in EXIT_TIERS):
            flags = [bool(pos.get(f't{i}_active')) for i in EXIT_TIERS]
            if any(flags):
                return flags
        t = pos.get('exit_tier') or DEFAULT_EXIT_TIER
        return [i == int(t) for i in EXIT_TIERS]

    # ── Build: title row ──────────────────────────────────────────────────────

    def _build_title_row(self, row_num):
        r0 = tk.Frame(self.frame)
        r0.pack(fill='x', pady=(0, 2))

        name = display_name(self.ticker)
        if self.ticker.endswith('.KS'):
            name += ' (KR)'
        self._disp_name = name
        # Bold marks DEPLOYED (not KR — KR already shows the (KR) tag).
        name_font = _F_NAME_DEPLOYED if self.deployed else _F_NAME_EMPTY
        self._name_lbl = tk.Label(r0, text=f"{row_num}. {name}",
                                  font=name_font, anchor='w')
        self._name_lbl.pack(side='left')

        # Right side: "[DEPLOYED] [ordered status]".
        self._status_frame = tk.Frame(r0)
        self._status_frame.pack(side='right', padx=(6, 2))
        self._status_lbl = tk.Label(self._status_frame, text='',
                                    font=_F_STATUS)
        self._status_lbl.pack(side='left', padx=(2, 0))
        if self.deployed:
            tk.Label(r0, text='DEPLOYED', font=_F_SM_B, fg='#0033AA'
                     ).pack(side='right', padx=(4, 0))

        # Army % (deployed only)
        tk.Label(r0, textvariable=self.army_pct_var,
                 font=_F_SM, fg='#888').pack(side='left', padx=(3, 6))

        self.auto_btn = tk.Checkbutton(
            r0, variable=self.auto_var, indicatoron=False, takefocus=0,
            width=7, font=_F_BTN, bd=1)
        self.auto_btn.pack(side='left', padx=(0, 6))
        tk.Label(r0, textvariable=self.vol_var, font=_F_SM, fg='#666'
                 ).pack(side='left', padx=(0, 12))

        ent_state = 'normal' if self.editable else 'readonly'
        tk.Label(r0, text='Avg Cost:', font=_F_LBL).pack(side='left')
        self.avg_entry = tk.Entry(r0, textvariable=self.avg_cost_var,
                                  width=10, justify='right', font=_F_VAL,
                                  state=ent_state)
        self.avg_entry.pack(side='left', padx=(2, 8))
        self.avg_entry.bind('<FocusOut>', lambda e: self._format_avg())

        tk.Label(r0, text='Shares:', font=_F_LBL).pack(side='left')
        tk.Entry(r0, textvariable=self.shares_var,
                 width=6, justify='right', font=_F_VAL, state=ent_state
                 ).pack(side='left', padx=(2, 8))

        if not self.deployed and self.editable:
            tk.Label(r0, text='(fill & Save to deploy)', font=_F_SM,
                     fg='#AAA').pack(side='left', padx=(2, 0))

    # ── Build: body (left stats+ladder | right gear boxes) ────────────────────

    def _build_body(self):
        body = tk.Frame(self.frame)
        body.pack(fill='x')

        left = tk.Frame(body)
        left.pack(side='left')

        # Stats: anchor value | current | gap
        stats = tk.Frame(left)
        stats.pack(fill='x')

        def _out(label, var, **kw):
            tk.Label(stats, text=label, fg='#888', font=_F_SM).pack(side='left')
            lbl = tk.Label(stats, textvariable=var, font=_F_OUT, **kw)
            lbl.pack(side='left', padx=(2, 12))
            return lbl

        cost_label = 'Total Cost:' if self.deployed else 'Vantage:'
        _out(cost_label, self.cost_var)
        _out('Current:', self.current_var)
        self.current_lbl = stats.winfo_children()[-1]  # the Current value label
        self.gap_lbl = _out('Gap:', self.gap_var, width=9)

        # Ladder: 3 columns. Buys on top (Load/Chase), the gear's three EXIT
        # tiers beneath. One armed tier takes all shares; multiple armed tiers
        # split the position.
        ladder = tk.Frame(left)
        ladder.pack(fill='x', pady=(2, 0))

        self.buy_info_lbl = []
        self.t_info_lbl   = []
        self.t_head_lbl   = []
        for col in range(3):
            cell = tk.Frame(ladder)
            cell.grid(row=0, column=col, sticky='w', padx=(0, 14))

            bf = tk.Frame(cell)
            bf.pack(anchor='w')
            tk.Label(bf, textvariable=self._buy_lbl_var[col], fg='#888',
                     font=_F_SM, width=8, anchor='w').pack(side='left')
            blbl = tk.Label(bf, textvariable=self.buy_info_var[col],
                            font=_F_OUT, fg=_BUY_FG)
            blbl.pack(side='left')
            self.buy_info_lbl.append(blbl)

            sf = tk.Frame(cell)
            sf.pack(anchor='w')
            head = tk.Label(sf, text=f'T{col+1}:', fg='#888', font=_F_SM,
                            width=8, anchor='w')
            head.pack(side='left')
            self.t_head_lbl.append(head)
            slbl = tk.Label(sf, textvariable=self.t_info_var[col], font=_F_OUT)
            slbl.pack(side='left')
            self.t_info_lbl.append(slbl)

        # Gear + exit-tier boxes on the right
        self._build_gear_boxes(body)

    def _build_gear_boxes(self, body):
        wrap = tk.Frame(body)
        wrap.pack(side='left', anchor='n', padx=(16, 0))
        self._gear_title = {}

        # -- Gear picker (one gear = load %, chase %, add size, tier table) ----
        self._card_bg = self.frame.cget('bg')
        gear_box = tk.Frame(wrap)
        gear_box.pack(side='left', anchor='n', padx=(0, 12))
        self._gear_title['gear'] = tk.Label(gear_box, text='Gear',
                                            font=_F_SM, fg='#888')
        self._gear_title['gear'].grid(row=0, column=0, sticky='w')
        menu_row = tk.Frame(gear_box)
        menu_row.grid(row=1, column=0, sticky='w')
        self.gear_menu = tk.Menubutton(
            menu_row, textvariable=self.gear_txt_var, font=_F_SM,
            width=16, relief='raised', takefocus=0)
        self._gear_menu_default_bg = self.gear_menu.cget('bg')
        self._gear_menu_default_fg = self.gear_menu.cget('fg')
        menu = tk.Menu(self.gear_menu, tearoff=0)
        for gear in sorted(GEARS):
            menu.add_command(label=gear_menu_label(gear),
                             command=lambda g=gear: self._on_gear_select(g))
        self.gear_menu.config(menu=menu)
        self.gear_menu.pack(side='left')

        # The big gear number. Across a grid of sixteen cards this is the one
        # mark readable at a glance, which is the whole point of it.
        badge_row = tk.Frame(gear_box)
        badge_row.grid(row=2, column=0, sticky='w', pady=(3, 0))
        tk.Label(badge_row, text='gear:', font=_F_SM, fg='#888'
                 ).pack(side='left', padx=(0, 3))
        self.gear_badge = tk.Canvas(badge_row, width=46, height=46,
                                    highlightthickness=0, bd=0)
        self.gear_badge.pack(side='left')

        # -- Exit tiers: plain checkboxes ---------------------------------------
        # Ticked boxes, not lit buttons. The card is a worksheet, and a tick is
        # what a worksheet uses; it also reads differently from the cockpit's
        # coloured tier buttons, which is the point — those trade, these do not.
        exit_box = tk.Frame(wrap)
        exit_box.pack(side='left', anchor='n')
        self._gear_title['exit'] = tk.Label(exit_box, text='Exit',
                                            font=_F_SM, fg='#888')
        self._gear_title['exit'].grid(row=0, column=0, columnspan=2, sticky='w')
        self._tier_btns = {}
        for disp, t in enumerate(reversed(EXIT_TIERS)):    # T3 on top
            btn = tk.Checkbutton(
                exit_box, variable=self.tier_vars[t - 1], anchor='w',
                width=9, font=_F_SM, bd=0, takefocus=0, padx=0, pady=0,
                command=lambda t=t: self._on_tier_click(t))
            btn.grid(row=disp + 1, column=0, sticky='w')
            self._tier_btns[t] = btn

        # -- Autopilot button: opens the campaign cockpit and arms WATCH -------
        if self.on_autopilot:
            ap = tk.Frame(wrap)
            ap.pack(side='left', anchor='n', padx=(12, 0),
                    pady=(_AP_BUTTON_TOP_GAP, 0))
            self.ap_btn = tk.Button(
                ap, text='AUTOPILOT', font=_F_SM_B, width=14,
                height=3, bd=2, takefocus=0,
                command=lambda: self.on_autopilot(self.ticker))
            self._ap_btn_default_bg = self.ap_btn.cget('bg')
            self.ap_btn.pack(side='top')
            # No sync button: the bot runs the Daily v^ grid with its own
            # scale picker, and this card is the V-Commandos worksheet whose
            # numbers the commander types into the broker app by hand. The
            # two share nothing but the stock.

    # ── Formatting ────────────────────────────────────────────────────────────

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

    # ── Gear / tier getters ───────────────────────────────────────────────────

    def _get_gear(self) -> int:
        try:
            return clamp_gear(self.gear_var.get())
        except (tk.TclError, ValueError):
            return 3

    def _get_tiers(self) -> list:
        """The armed exit tiers as three booleans; never all-off."""
        try:
            flags = [bool(v.get()) for v in self.tier_vars]
        except (tk.TclError, ValueError):
            flags = []
        if not any(flags):
            flags = [i == DEFAULT_EXIT_TIER for i in EXIT_TIERS]
        return flags

    def _on_tier_click(self, tier):
        """Apply the click immediately; silently keep the final tier armed."""
        flags = [bool(v.get()) for v in self.tier_vars]
        if not any(flags):
            self.tier_vars[tier - 1].set(True)

    def _tier_text(self) -> str:
        on = [f'T{i}' for i in EXIT_TIERS if self._get_tiers()[i - 1]]
        return '+'.join(on) if on else '—'

    def _set_gear(self, gear: int):
        gear = clamp_gear(gear)
        if self._get_gear() == gear:
            return
        self._syncing_gear = True
        try:
            self.gear_var.set(gear)
        finally:
            self._syncing_gear = False

    def _on_gear_select(self, gear):
        self._set_gear(gear)
        self._on_input_change()

    # ── Auto / manual ─────────────────────────────────────────────────────────

    def _update_auto_btn(self):
        if self.auto_var.get():
            self.auto_btn.config(text='AUTO', fg='white', bg='#2E8B57',
                                 selectcolor='#2E8B57')
        else:
            self.auto_btn.config(text='MANUAL', fg='black', bg='#E6B800',
                                 selectcolor='#E6B800')

    def _update_vol_label(self):
        # Two decimals so a 20.04% read never looks like the 20.0% cut point
        # while selecting the gear above it.
        vol = self.volatility
        if vol is None:
            self.vol_var.set('V --')
        elif not self.auto_var.get():
            self.vol_var.set(f'V {vol:.2f}%')
        else:
            base = select_auto_gear(vol)
            eff = self._eff_gear or base
            heavy = ' ▲heavy' if eff > base else ''
            self.vol_var.set(f'V {vol:.2f}% → G{eff}{heavy}')

    def _apply_auto(self):
        """AUTO tracks the 5-day range, deployed or not — the gear is not
        pinned by a live campaign, because only the average cost is history
        and the full ladder is recomputed from it. The heavy-unit floor applies
        to a FLAT card only: it is an ENTRY rule about opening a position with
        useful resolution, not about one that already exists. Resting orders
        do not freeze the strategy: the bot self-heals its own order when a
        line moves, while a foreign order pauses execution safely."""
        if not self.auto_var.get():
            return
        if self.volatility is None:
            return
        self._base_gear = select_auto_gear(self.volatility)
        if self.deployed:
            gear = self._base_gear
        else:
            ref_price = self.current_price or self.vantage
            gear = effective_entry_gear(self.volatility, ref_price,
                                        self.get_unit_cash())
        self._eff_gear = gear
        self._set_gear(gear)

    def _refresh_gear_title_text(self):
        gear = self._get_gear()
        g = gear_params(gear)
        self._gear_title['gear'].config(
            text=f"Gear ({gear_label(gear)} {g['name']})")
        self._gear_title['exit'].config(text=f"Exit ({self._tier_text()})")
        self.gear_txt_var.set(gear_detail(gear))

    # ── Gear styling (enabled + state muting) ─────────────────────────────────

    def set_order_state(self, side):
        """Reflect live Toss orders in the title without locking strategy."""
        self._order_side = side
        self._refresh_status()

    def _refresh_status(self):
        """Title status shows live resting orders only."""
        if self._order_side == 'BUY':
            self._status_lbl.config(text='buy ordered', fg=_BUY_FG)
        elif self._order_side == 'SELL':
            self._status_lbl.config(text='sell ordered', fg=_SELL_FG)
        else:
            self._status_lbl.config(text='')

    def _refresh_gear_styles(self):
        self._refresh_gear_title_text()
        self._draw_gear_badge(self._get_gear())
        self._style_tier_buttons()

        self.auto_btn.config(state='normal')
        for b in self._tier_btns.values():
            b.config(state='normal')

        self.gear_menu.config(
            state='disabled' if self.auto_var.get() else 'normal')
        self.gear_menu.config(
            fg=self._gear_menu_default_fg, bg=self._gear_menu_default_bg,
            activeforeground=self._gear_menu_default_fg,
            activebackground=self._gear_menu_default_bg,
            disabledforeground='#888888', font=_F_SM)

    def _draw_gear_badge(self, gear):
        """The big number, in that gear's own colour."""
        if not hasattr(self, 'gear_badge'):
            return
        gear = clamp_gear(gear)
        self.gear_badge.delete('all')
        self.gear_badge.create_oval(3, 3, 43, 43,
                                    fill=gear_button_color(gear),
                                    outline='#555555', width=1)
        self.gear_badge.create_text(23, 23, text=str(gear),
                                    fill=gear_button_fg(gear),
                                    font=_F_GEAR_BADGE)

    def _style_tier_buttons(self):
        """A ticked tier is written in its own exit colour; an unticked one is
        grey. The box itself carries the state, so the text stays plain and
        full-size either way — a faded number is still a number to read."""
        gear = self._get_gear()
        armed = self._get_tiers()
        for t, btn in self._tier_btns.items():
            pct = exit_pct(gear, t)
            on = armed[t - 1]
            btn.config(text=f'T{t} +{pct}%',
                       fg=(tier_text_color(pct) if on else _MUTE_FG),
                       font=(_F_SM_B if on else _F_SM),
                       bg=self._card_bg, activebackground=self._card_bg,
                       selectcolor='white',
                       activeforeground=(tier_text_color(pct) if on
                                         else _MUTE_FG))

    # ── Compute ───────────────────────────────────────────────────────────────

    def _on_input_change(self):
        if not self._computing and not self._syncing_gear:
            self.compute()
            if self._on_compute_cb:
                self._on_compute_cb()

    def compute(self):
        if self._computing:
            return
        self._computing = True
        try:
            self._compute_impl()
        finally:
            self._computing = False

    def _compute_impl(self):
        self._apply_auto()
        self._refresh_gear_styles()
        self._update_auto_btn()
        self._update_vol_label()

        ccy  = self.currency
        gear = self._get_gear()
        armed = self._get_tiers()

        try:
            shares = int(self.shares_var.get().replace(',', '') or 0)
        except ValueError:
            shares = 0
        try:
            avg_cost = float(self.avg_cost_var.get().replace(',', '') or 0)
        except ValueError:
            avg_cost = 0.0

        if self.deployed:
            buy_labels = ['Chase 1', 'Chase 2', 'Chase 3']
            anchor_price = avg_cost if avg_cost > 0 else None
            if shares > 0 and avg_cost > 0:
                self.cost_var.set(fmt_price(shares * avg_cost, ccy))
                buy_lines  = calc_chase_cascade(shares, avg_cost, gear, 3)
                exit_lines = calc_exit_lines(shares, avg_cost, gear, armed)
            else:
                self.cost_var.set('--')
                buy_lines  = [{'price': None, 'qty': None} for _ in range(3)]
                exit_lines = [{'price': None, 'qty': None} for _ in range(3)]
        else:
            buy_labels = ['Load', 'Chase 1', 'Chase 2']
            reload_mode = (self.vantage_src == 'reload')
            if self.vantage and self.vantage > 0:
                if reload_mode:
                    # After a same-day full exit the entry is a flat -3% off
                    # the actual sell fill, not the gear's load drop.
                    load_price = calc_reload_price(self.vantage)
                    unit = self.get_unit_cash()
                    load_shares = (max(1, int(unit / load_price + 0.5))
                                   if unit > 0 and load_price > 0 else 0)
                    buy_lines = [{'price': load_price, 'qty': load_shares}]
                    buy_lines += calc_chase_cascade(load_shares, load_price,
                                                    gear, 2)
                else:
                    buy_lines, load_price, load_shares = calc_load_ladder(
                        self.vantage, gear, self.get_unit_cash(), chases=2)
                anchor_price = load_price if load_price > 0 else None
                exit_lines = calc_exit_lines(load_shares, load_price, gear,
                                             armed)
                tag = (' (reload)' if reload_mode else
                       (' (pinned)' if self.vantage_src == 'manual' else ''))
                self.cost_var.set(fmt_price(self.vantage, ccy) + tag)
            else:
                anchor_price = None
                buy_lines  = [{'price': None, 'qty': None} for _ in range(3)]
                exit_lines = [{'price': None, 'qty': None} for _ in range(3)]
                self.cost_var.set('--')

        # Current price + gap
        if self.current_price:
            self.current_var.set(fmt_price(self.current_price, ccy))
        else:
            self.current_var.set('--')

        # Deployed: current vs average cost. Empty: current vs LOAD, so a price
        # below the bait is negative. Deployed keeps red/blue P&L colors; EMPTY
        # uses orange above LOAD and purple below it.
        if self.deployed and self.current_price and anchor_price:
            gap = calc_gap_rate(self.current_price, anchor_price)
            self._gap = gap
            self.gap_var.set(f"{gap:+.2f}%")
            self.gap_lbl.config(fg=gap_color(gap))
        elif (not self.deployed and self.current_price and anchor_price):
            gap = calc_load_gap_rate(self.current_price, anchor_price)
            self._gap = gap
            self.gap_var.set(f"{gap:+.2f}%")
            self.gap_lbl.config(fg=load_gap_color(gap))
        else:
            self._gap = None
            self.gap_var.set('--')
            self.gap_lbl.config(fg='black')

        # Flat stocks turn the Current value green once the price is at/below
        # the LOAD trigger (the entry condition is met).
        if (not self.deployed and anchor_price and self.current_price
                and self.current_price <= anchor_price):
            self.current_lbl.config(fg='#006400')
        else:
            self.current_lbl.config(fg='black')

        # Fill the ladder. A line is "triggered" once the current price crosses
        # it (buy: current <= line; exit: current >= line). A flat card's exits
        # are projections, so they stay informational instead of alarming.
        cur = self.current_price
        for i in range(3):
            self._buy_lbl_var[i].set(f'{buy_labels[i]}:')
            e = buy_lines[i]
            if e['price'] is not None:
                self.buy_info_var[i].set(f"{fmt_price(e['price'], ccy)} × {e['qty']}")
                hit = cur is not None and cur <= e['price']
                self.buy_info_lbl[i].config(fg=(_BUY_FG if hit else 'black'))
            else:
                self.buy_info_var[i].set('--')
                self.buy_info_lbl[i].config(fg='#CCC')

            # An armed tier shows its price AND the shares it would take.
            # A disarmed one still shows its price, faded but the same size —
            # it is a real number the commander compares against.
            s = exit_lines[i]
            on = armed[i]
            self.t_head_lbl[i].config(
                text=f'▶ T{i+1}:' if on else f'T{i+1}:',
                fg='#000' if on else _MUTE_FG,
                font=_F_SM_B if on else _F_SM)
            if s['price'] is not None:
                self.t_info_var[i].set(
                    f"{fmt_price(s['price'], ccy)} × {s['qty']}")
                hit = (self.deployed and cur is not None
                       and cur >= s['price'])
                self.t_info_lbl[i].config(fg=(_SELL_FG if hit else 'black'),
                                          font=_F_OUT)
            else:
                ref = calc_exit_lines(1, anchor_price or 0, gear)[i]
                self.t_info_var[i].set(
                    fmt_price(ref['price'], ccy) if ref['price'] else '--')
                self.t_info_lbl[i].config(fg=_MUTE_FG, font=_F_VAL)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_row_num(self, n: int):
        self._name_lbl.config(text=f"{n}. {self._disp_name}")

    # Autopilot button styling per status (None = off).
    _AP_STYLES = {
        None:    ('AUTOPILOT',        None,      'black'),
        'WATCH': ('AUTOPILOT\nWATCH', '#3366CC', 'white'),
        'LIVE':  ('AUTOPILOT\nLIVE',  '#CC0000', 'white'),
    }
    def set_autopilot(self, key):
        """Color the autopilot button to its status (None/'WATCH'/'LIVE')."""
        btn = getattr(self, 'ap_btn', None)
        if btn is None:
            return
        text, bg, fg = self._AP_STYLES.get(key, self._AP_STYLES[None])
        bg = bg or self._ap_btn_default_bg
        btn.config(text=text, bg=bg, fg=fg,
                   activebackground=bg, activeforeground=fg)

    def current_shares(self) -> int:
        try:
            return int(self.shares_var.get().replace(',', '') or 0)
        except ValueError:
            return 0

    def set_army_pct(self, pct):
        self._army_pct = pct
        self.army_pct_var.set(
            f"({pct:.1f}%)" if (pct is not None and self.deployed) else '')

    def update_live(self, price=None, vantage=None, volatility=None,
                    vantage_src=None):
        if price is not None:
            self.current_price = price
        if vantage is not None:
            self.vantage = vantage
        if vantage_src is not None:
            self.vantage_src = vantage_src
        if volatility is not None:
            self.volatility = volatility

    def get_state(self) -> dict:
        try:
            shares = int(self.shares_var.get().replace(',', '') or 0)
        except ValueError:
            shares = 0
        try:
            avg_cost = float(self.avg_cost_var.get().replace(',', '') or 0)
        except ValueError:
            avg_cost = 0.0
        gear = self._get_gear()
        armed = self._get_tiers()
        pcts = tier_pcts(gear)
        return {
            'ticker':     self.ticker,
            'tier':       self.tier,
            'is_deployed': self.deployed,
            'shares':     shares,
            'avg_cost':   avg_cost,
            'cost_basis': shares * avg_cost,
            'gear':       gear,
            # The lowest armed tier is what the single-tier `exit_tier` column
            # can express; the three flags carry the real selection.
            'exit_tier':  next((i for i in EXIT_TIERS if armed[i - 1]),
                               DEFAULT_EXIT_TIER),
            'load_gear':  load_drop(gear),
            'buy_pct':    chase_drop(gear),
            't1_pct':     pcts[0],
            't2_pct':     pcts[1],
            't3_pct':     pcts[2],
            't1_active':  armed[0],
            't2_active':  armed[1],
            't3_active':  armed[2],
            'auto_mode':  self.auto_var.get(),
        }
