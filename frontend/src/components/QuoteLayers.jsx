import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, X } from "lucide-react";
import EmailDraft from "./EmailDraft";

// Stackable drawer and modal for the quote screens.
//
// The shared Drawer/Modal (Common.jsx → useModalFocus) trap focus in the *first* dialog of the
// document, so a second panel opened on top of the order drawer would be made inert by it. These
// layers render into their own container at the end of <body>, trap focus by reference, make every
// other body child inert while open and restore it in reverse order, so they stack on any page.

const FOCUSABLE = 'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]';

function useLayer(open, onClose, { initialFocus } = {}) {
  const [host, setHost] = useState(null);
  const dialog = useRef(null);
  const close = useRef(onClose);
  close.current = onClose;

  useLayoutEffect(() => {
    if (!open) return undefined;
    const element = document.createElement("div");
    element.className = "qd-layer";
    document.body.appendChild(element);
    setHost(element);
    return () => { element.remove(); setHost(null); };
  }, [open]);

  useEffect(() => {
    const node = dialog.current;
    if (!host || !node) return undefined;
    const previous = document.activeElement;
    const others = [...document.body.children].filter((element) => element !== host && !element.contains(node));
    const saved = others.map((element) => [element, element.inert]);
    others.forEach((element) => { element.inert = true; });
    const focusables = () => [...node.querySelectorAll(FOCUSABLE)].filter((element) => !element.closest("[inert]"));
    // Drawers focus themselves (the title is read first, Tab moves to the first control); modals their first field.
    const first = initialFocus === "self" ? node : (initialFocus && node.querySelector(initialFocus)) || focusables()[0] || node;
    first.focus();
    const key = (event) => {
      if (event.key === "Escape") {
        // Inline editors marked data-own-escape cancel themselves instead of closing the layer.
        if (event.target.closest?.("[data-own-escape]")) return;
        event.preventDefault(); event.stopPropagation(); close.current?.(); return;
      }
      if (event.key !== "Tab") return;
      const list = focusables();
      const head = list[0] || node;
      const tail = list.at(-1) || node;
      if (event.shiftKey && (document.activeElement === head || document.activeElement === node)) { event.preventDefault(); tail.focus(); }
      else if (!event.shiftKey && (document.activeElement === tail || document.activeElement === node)) { event.preventDefault(); head.focus(); }
    };
    node.addEventListener("keydown", key);
    return () => {
      node.removeEventListener("keydown", key);
      saved.reverse().forEach(([element, value]) => { element.inert = value; });
      if (previous && document.contains(previous)) previous.focus?.();
    };
  }, [host]);

  return [host, dialog];
}

export function LayerDrawer({ open, title, subtitle, onClose, children, footer, labelId = "quote-drawer-title", className = "", initialFocus = "self" }) {
  const [host, dialog] = useLayer(open, onClose, { initialFocus });
  if (!open || !host) return null;
  return createPortal(
    <div className="drawer-backdrop qd-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialog} tabIndex={-1} className={`drawer qd-drawer ${className}`} role="dialog" aria-modal="true" aria-labelledby={labelId}>
        <header className="drawer-head">
          <div>
            <h2 id={labelId}>{title}</h2>
            {subtitle && <div className="qd-subtitle">{subtitle}</div>}
          </div>
          <button type="button" className="icon-btn" aria-label="Close quote" onClick={onClose}><X size={18} /></button>
        </header>
        <div className="drawer-body qd-body">{children}</div>
        {footer && <footer className="drawer-foot qd-foot">{footer}</footer>}
      </section>
    </div>,
    host,
  );
}

export function LayerModal({ open, title, description, onClose, children, footer, tone, labelId = "quote-modal-title", initialFocus = ".modal-body input, .modal-body textarea, .modal-body select" }) {
  const [host, dialog] = useLayer(open, onClose, { initialFocus });
  if (!open || !host) return null;
  return createPortal(
    <div className="modal-backdrop" role="presentation">
      <section ref={dialog} tabIndex={-1} className="modal qd-modal" role="dialog" aria-modal="true" aria-labelledby={labelId}>
        <header className="modal-head">
          {tone === "danger" && <span className="modal-icon"><AlertTriangle size={20} /></span>}
          <div>
            <h2 id={labelId}>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}><X size={18} /></button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot modal-actions">{footer}</footer>}
      </section>
    </div>,
    host,
  );
}

// The shared prepared-email window, opened from a stacked layer: rendered in a container placed first
// in <body> so its own focus handling (first dialog in the document) finds it.
export function EmailDraftLayer({ open, ...props }) {
  const [host, setHost] = useState(null);
  useLayoutEffect(() => {
    if (!open) return undefined;
    const element = document.createElement("div");
    element.className = "qd-layer";
    document.body.prepend(element);
    setHost(element);
    return () => { element.remove(); setHost(null); };
  }, [open]);
  if (!open || !host) return null;
  return createPortal(<EmailDraft open {...props} />, host);
}
