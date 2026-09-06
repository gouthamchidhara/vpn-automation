"""Double-click GUI to update the stored VPN password (username stays remembered).

No console window — plain Tk dialog. Password never touches disk; it goes
straight into Windows Credential Manager via keyring (DPAPI-encrypted).
"""
from __future__ import annotations
import tkinter as tk
from tkinter import messagebox

from src.config import load_config, save_config
from src.creds import store_password


def _save(username_var: tk.StringVar, password_var: tk.StringVar, root: tk.Tk) -> None:
    username = username_var.get().strip()
    password = password_var.get()
    if not username:
        messagebox.showerror("Missing username", "Please enter your VPN username.")
        return
    if not password:
        messagebox.showerror("Missing password", "Please enter your new VPN password.")
        return

    store_password(username, password)
    cfg = load_config()
    cfg.last_username = username
    save_config(cfg)

    messagebox.showinfo("Saved", f"Password updated for '{username}'.\nStored securely in Windows Credential Manager.")
    root.destroy()


def main() -> None:
    cfg = load_config()

    root = tk.Tk()
    root.title("Update VPN Password")
    root.resizable(False, False)

    tk.Label(root, text="VPN Username:").grid(row=0, column=0, sticky="e", padx=8, pady=8)
    username_var = tk.StringVar(value=cfg.last_username)
    tk.Entry(root, textvariable=username_var, width=30).grid(row=0, column=1, padx=8, pady=8)

    tk.Label(root, text="New Password:").grid(row=1, column=0, sticky="e", padx=8, pady=8)
    password_var = tk.StringVar()
    password_entry = tk.Entry(root, textvariable=password_var, show="*", width=30)
    password_entry.grid(row=1, column=1, padx=8, pady=8)
    password_entry.focus_set()

    btn = tk.Button(root, text="Save", width=12,
                    command=lambda: _save(username_var, password_var, root))
    btn.grid(row=2, column=0, columnspan=2, pady=10)
    root.bind("<Return>", lambda _event: _save(username_var, password_var, root))

    root.mainloop()


if __name__ == "__main__":
    main()
