import { useEffect } from "react";

// Traps focus in a modal dialog. Pass the dialog's ref when dialogs can stack (a panel opened on
// top of another); without one, the most recently rendered modal dialog is used.
export default function useModalFocus(active, dismiss, dialogRef) {
  useEffect(() => {
    if (!active) return;
    const dialog = dialogRef?.current || [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')].at(-1);
    if (!dialog) return;
    const previous = document.activeElement;
    const inert = [];
    let node = dialog;
    while (node.parentElement && node.parentElement !== document.body) {
      for (const sibling of node.parentElement.children) if (sibling !== node) {
        inert.push([sibling, sibling.inert]); sibling.inert = true;
      }
      node = node.parentElement;
    }
    const focusables = () => [...dialog.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]')];
    dialog.tabIndex = -1; (focusables()[0] || dialog).focus();
    const key = event => {
      // Escape closes only this dialog, not the one underneath it.
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); dismiss(); return; }
      if (event.key !== "Tab") return;
      event.stopPropagation();
      const list = focusables(); const first = list[0] || dialog; const last = list.at(-1) || dialog;
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog)) { event.preventDefault(); first.focus(); }
    };
    dialog.addEventListener("keydown", key);
    return () => { dialog.removeEventListener("keydown", key); inert.forEach(([el, value]) => { el.inert = value; }); previous?.focus?.(); };
  }, [active]);
}
