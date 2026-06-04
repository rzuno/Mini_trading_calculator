import tkinter as tk


class Stepper(tk.Frame):
    """Horizontal numeric stepper:  [down]  value  [up].

    A bigger, easier-to-click replacement for the tiny tk.Spinbox arrows.
    The left button steps the bound variable down, the right button steps it
    up; both clamp to [lo, hi].  The centre entry stays editable for typing.
    """

    _DOWN = '▼'   # down-pointing triangle
    _UP   = '▲'   # up-pointing triangle

    def __init__(self, parent, variable, lo, hi, *, step=1,
                 entry_width=4, value_font=None, btn_font=None,
                 btn_padx=2, btn_pady=0):
        super().__init__(parent)
        self._var  = variable
        self._lo   = lo
        self._hi   = hi
        self._step = step

        self.down_btn = tk.Button(
            self, text=self._DOWN, font=btn_font, width=1,
            padx=btn_padx, pady=btn_pady, takefocus=0,
            command=lambda: self._nudge(-step))
        self.down_btn.pack(side='left', fill='y')

        self.entry = tk.Entry(self, textvariable=variable, width=entry_width,
                              justify='center', font=value_font)
        self.entry.pack(side='left', fill='y')

        self.up_btn = tk.Button(
            self, text=self._UP, font=btn_font, width=1,
            padx=btn_padx, pady=btn_pady, takefocus=0,
            command=lambda: self._nudge(step))
        self.up_btn.pack(side='left', fill='y')

    def _nudge(self, delta):
        try:
            v = int(self._var.get())
        except (tk.TclError, ValueError):
            v = self._lo
        self._var.set(max(self._lo, min(self._hi, v + delta)))

    def set_value_color(self, bg, fg):
        """Tint the centre value box; the buttons stay neutral. The tint is
        applied across normal/readonly/disabled states so a locked (auto-mode)
        stepper still shows its gear color."""
        self.entry.config(bg=bg, fg=fg, insertbackground=fg,
                          readonlybackground=bg,
                          disabledbackground=bg, disabledforeground=fg)

    def set_enabled(self, enabled: bool):
        """Enable for manual editing, or lock for auto mode. When locked the
        arrows are disabled and the value box is read-only (value still shown,
        not editable)."""
        btn_state = 'normal' if enabled else 'disabled'
        self.down_btn.config(state=btn_state)
        self.up_btn.config(state=btn_state)
        self.entry.config(state='normal' if enabled else 'readonly')
