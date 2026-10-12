import type { FieldResult } from "./types";

export interface IndicatorActions {
  retry: () => void;
  copy: () => void;
  cancel: () => void;
}

const escapeHtml = (value: unknown) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#39;");

function stateTone(state: string): string {
  const normalized = state.toLowerCase();
  if (/error|stopped|expired/.test(normalized)) return "error";
  if (/review|action|required/.test(normalized)) return "warning";
  if (/filled|verified|finished/.test(normalized)) return "success";
  return "progress";
}

export class BridgeIndicator {
  readonly host: HTMLElement;
  readonly shadow: ShadowRoot;
  private expanded = false;
  private destroyed = false;
  private state = "Connected";
  private message = "Loading the quote packet.";
  private progress: string | null = null;
  private readonly observer: MutationObserver;

  constructor(private readonly actions: IndicatorActions) {
    this.host = document.createElement("aside");
    this.host.id = "build2quote-status";
    this.host.setAttribute("aria-label", "Build2Quote Bridge status");
    this.shadow = this.host.attachShadow({ mode: "open" });
    document.documentElement.append(this.host);
    this.observer = new MutationObserver(() => {
      if (!this.destroyed && !this.host.isConnected) document.documentElement.append(this.host);
    });
    this.observer.observe(document.documentElement, { childList: true });
    this.draw();
  }

  render(state: string, message: string, _fields: FieldResult[] = []): void {
    this.state = state;
    this.message = message;
    this.draw();
  }

  /** Run progress from the background, e.g. "Filling 2 of 5…"; null hides the label. */
  setProgress(progress: string | null): void {
    if (progress === this.progress) return;
    this.progress = progress;
    this.draw();
  }

  setExpanded(expanded: boolean): void {
    this.expanded = expanded;
    this.draw();
  }

  destroy(): void {
    this.destroyed = true;
    this.observer.disconnect();
    this.host.remove();
  }

  private draw(): void {
    const tone = stateTone(this.state);
    const tooltip = `${this.progress ? `${this.progress} · ` : ""}${this.state}: ${this.message}`;
    this.shadow.innerHTML = `
      <style>
        :host { all: initial; position: fixed; right: 12px; bottom: 12px; z-index: 2147483647; font: 12px/1.35 "Segoe UI", Arial, sans-serif; color: #17384d; }
        *, *::before, *::after { box-sizing: border-box; }
        .anchor { position: relative; display: flex; align-items: center; justify-content: flex-end; gap: 6px; }
        .progress-label { max-width: 220px; padding: 4px 9px; border-radius: 12px; background: #17384d; color: #fff; font: 600 11px/1.3 "Segoe UI", Arial, sans-serif; box-shadow: 0 2px 9px #0004; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .dot { width: 27px; height: 27px; padding: 0; border: 2px solid #fff; border-radius: 50%; cursor: pointer; background: #2d9cdb; box-shadow: 0 2px 9px #0005; }
        .dot.progress { background: #2d9cdb; animation: pulse 1.35s ease-in-out infinite; }
        .dot.success { background: #2ead67; }
        .dot.warning { background: #e4a11b; }
        .dot.error { background: #d64545; }
        .dot:focus-visible, .actions button:focus-visible { outline: 3px solid #68c7e8; outline-offset: 2px; }
        .popover { position: absolute; right: 0; bottom: 35px; width: 278px; padding: 10px; border: 1px solid #c7d5dc; border-radius: 8px; background: #fff; color: #17384d; box-shadow: 0 7px 22px #0004; }
        .message { margin: 0 0 9px; overflow-wrap: anywhere; }
        .actions { display: flex; gap: 6px; }
        .actions button { min-height: 27px; padding: 0 9px; border: 1px solid #9eb4bf; border-radius: 5px; background: #f7fafb; color: #17384d; cursor: pointer; font: 600 11px/1 "Segoe UI", Arial, sans-serif; }
        .actions button:hover { background: #e8f2f5; }
        @keyframes pulse { 0%,100% { opacity: .58; } 50% { opacity: 1; } }
        @media (prefers-reduced-motion: reduce) { .dot.progress { animation: none; } }
      </style>
      <div class="anchor">
        ${this.progress ? `<span class="progress-label" role="status">${escapeHtml(this.progress)}</span>` : ""}
        ${this.expanded ? `<div class="popover" role="dialog" aria-label="Build2Quote status details"><p class="message">${this.progress ? `<i>${escapeHtml(this.progress)}</i><br>` : ""}<b>${escapeHtml(this.state)}.</b> ${escapeHtml(this.message)}</p><div class="actions"><button id="b2q-retry" type="button">Retry</button><button id="b2q-copy" type="button">Copy error</button><button id="b2q-cancel" type="button">Cancel</button></div></div>` : ""}
        <button class="dot ${tone}" id="b2q-toggle" type="button" title="${escapeHtml(tooltip)}" aria-label="${escapeHtml(tooltip)}" aria-expanded="${this.expanded}"></button>
      </div>`;
    this.shadow.querySelector("#b2q-toggle")?.addEventListener("click", () => this.setExpanded(!this.expanded));
    this.shadow.querySelector("#b2q-retry")?.addEventListener("click", this.actions.retry);
    this.shadow.querySelector("#b2q-copy")?.addEventListener("click", this.actions.copy);
    this.shadow.querySelector("#b2q-cancel")?.addEventListener("click", this.actions.cancel);
  }
}
