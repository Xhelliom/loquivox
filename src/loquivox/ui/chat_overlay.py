"""
Chat overlay using WebKit2 with HTML/CSS/JS rendering.

On Wayland: uses gtk-layer-shell for proper overlay anchoring (right edge).
On X11: uses classic GTK window hints with free positioning + drag.
"""
from __future__ import annotations

import html as html_lib
import json
import re
from typing import Callable, Dict, List, Optional

import cairo

from loquivox.config import CFG
from loquivox.platform import SESSION_TYPE, get_clipboard
from loquivox.state import STATE

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gdk, GLib, Gtk, WebKit2

# Optional gtk-layer-shell for Wayland
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    HAS_LAYER_SHELL = True
except (ValueError, ImportError):
    HAS_LAYER_SHELL = False

# Use layer-shell only on Wayland when available
USE_LAYER_SHELL = HAS_LAYER_SHELL and SESSION_TYPE == "wayland"

#: the side conversation: a fixed panel on the right edge
CHAT_WIDTH, CHAT_HEIGHT = 340, 450
#: the talk bubble: fixed width, height driven by content, stacked directly
#: above the recording overlay so the whole exchange is in one place
TALK_WIDTH, TALK_MIN_HEIGHT, TALK_GAP = 560, 96, 16
#: and never taller than this share of the screen — it is an overlay, not a window
TALK_MAX_SCREEN = 0.6
#: resizes are coalesced over this window (ms): each one allocates a new surface
#: and makes the compositor re-blur what is behind it
RESIZE_COALESCE_MS = 120


# ---------------------------------------------------------------------------
# HTML / CSS / JS Templates
# ---------------------------------------------------------------------------
SVG_COPY_ICON = '<svg viewBox="0 0 24 24"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>'

CHAT_CSS = '''
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{
  height: 100%;
  background: transparent !important;
  font-family: 'Inter', 'Ubuntu', system-ui, -apple-system, sans-serif;
  color: {text}; 
  font-size: 14px;
  line-height: 1.6;
  overflow: hidden; /* Hide native window scrollbar */
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}}

/* Rounded Window Container */
.chat-window {{
  display: flex; 
  flex-direction: column;
  height: 100%;
  background-color: {bg_rgba};
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-radius: 20px;
  border: 1px solid {accent_alpha20}; /* Accent border */
  box-shadow: 0 8px 32px {black_alpha40};
  overflow: hidden;
  margin: 0; position: relative;
}}

/* Drag Handle */
.drag-handle {{
  position: absolute; top: 0; left: 0; width: 100%; height: 60px;
  z-index: 5; cursor: move; -webkit-app-region: drag;
}}

/* Scroll Area */
.chat-scroll-area {{
  flex: 1;
  overflow-y: auto;
  scroll-behavior: smooth;
  padding-bottom: 10px;
  z-index: 10; /* Above drag handle */
  position: relative;
  /* Optimization for smoother scrolling and less blurring */
  transform: translateZ(0);
  will-change: transform;
}}
/* Custom Scrollbar for inner area */
.chat-scroll-area::-webkit-scrollbar {{ width: 6px; }}
.chat-scroll-area::-webkit-scrollbar-track {{ background: transparent; }}
.chat-scroll-area::-webkit-scrollbar-thumb {{ background: {white_alpha10}; border-radius: 3px; }}
.chat-scroll-area::-webkit-scrollbar-thumb:hover {{ background: {white_alpha25}; }}

/* HUD / Pin Hint - Static Header */
.pin-hint {{
  flex-shrink: 0; /* Keep it fixed height */
  width: fit-content;
  margin: 12px auto 4px auto;
  background: {accent};
  color: {text_on_accent}; /* Contrast text */
  padding: 5px 14px;
  font-size: 11px; font-weight: 600;
  border-radius: 20px;
  z-index: 20; /* Above drag handle */
  display: flex; gap: 10px; align-items: center; justify-content: center;
  transition: opacity 0.3s;
  cursor: default; position: relative;
}}
.pin-hint a {{ color: inherit; text-decoration: none; opacity: 0.8; transition: opacity 0.2s; cursor: pointer; }}
.pin-hint a:hover {{ opacity: 1; color: {white}; }}

/* Chat Content */
.chat-container {{
  display: flex; flex-direction: column;
  padding: 10px 16px 20px 16px;
}}

/* Messages */
.message-wrapper {{
  display: flex;
  margin-bottom: 14px;
  animation: slideFadeIn 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  opacity: 0;
  transform: translate3d(0, 15px, 0);
}}
.message-wrapper.user {{ justify-content: flex-end; }}
.message-wrapper.assistant {{ justify-content: flex-start; }}

@keyframes slideFadeIn {{
  to {{ opacity: 1; transform: translate3d(0, 0, 0); }}
}}

.message {{
  max-width: 86%;
  padding: 10px 16px;
  border-radius: 14px;
  position: relative;
  word-wrap: break-word;
  /* Force hardware acceleration and stabilization */
  transform: translateZ(0);
  backface-visibility: hidden;
  -webkit-backface-visibility: hidden;
}}

/* User Bubble - Surface Color */
.user .message {{
  background: {surface};
  color: {text};
  border: 1px solid {white_alpha05};
}}

/* Assistant Bubble - Accent Color */
.assistant .message {{
  background: {accent};
  color: {text_on_accent}; /* Contrast text */
  border: 1px solid {white_alpha10};
  font-weight: 500;
}}

/* Copy Button */
.copy-btn {{
  background: none; border: none; cursor: pointer;
  padding: 6px; margin: 0 4px;
  opacity: 0.6; /* Always visible */
  transition: opacity 0.2s;
  align-self: center;
  color: {accent}; /* Accent */
  z-index: 20; /* Ensure Clickable */
}}
.message-wrapper:hover .copy-btn {{ opacity: 1; }}
.copy-btn:hover {{ opacity: 1; color: {text}; transform: scale(1.05); }}
.copy-btn svg {{ width: 15px; height: 15px; fill: currentColor; }}
.copy-btn.copied {{ opacity: 1; color: {accent}; }}
.user .copy-btn {{ order: -1; }}

.text code {{
  background: {accent_alpha10}; padding: 2px 5px; border-radius: 4px;
  font-family: 'SF Mono', monospace; font-size: 0.9em; color: {accent};
}}
.text pre {{
  background: {bg}; border: 1px solid {surface};
  color: {text}; padding: 12px; border-radius: 10px;
  overflow-x: auto; margin: 8px 0; font-family: 'SF Mono', monospace;
  font-size: 0.85em;
}}
.text strong {{ font-weight: 600; color: {accent}; }}

/* Code block copy button styles */
.code-block-wrapper {{
  position: relative;
  margin: 12px 0;
}}
.code-block-wrapper pre {{ margin: 0; }}
.code-copy-btn {{
  position: absolute;
  bottom: 8px;
  right: 8px;
  background: {surface_alpha80};
  border: 1px solid {accent_alpha30};
  border-radius: 6px;
  color: {text};
  padding: 4px;
  cursor: pointer;
  opacity: 0;
  transition: all 0.2s;
  z-index: 30;
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(4px);
}}
.code-block-wrapper:hover .code-copy-btn {{ opacity: 1; }}
.code-copy-btn:hover {{ background: {selection_alpha90}; color: {white}; transform: scale(1.05); }}
.code-copy-btn svg {{ width: 14px; height: 14px; fill: currentColor; }}
.code-copy-btn.copied {{ color: {success}; border-color: {success}; }}

/* The finished text, up for review. Not a spoken turn: full width, squared
   off, and labelled — you are looking at the thing you asked for, not at
   someone answering you. Same message pipeline as everything else, so the
   markdown rendering and the copy button come for free. */
.result .message {{
  max-width: 100%; flex: 1;
  background: {surface};
  color: {text};
  border: 1px solid {accent_alpha40};
  border-left: 3px solid {accent};
  border-radius: 10px;
  font-weight: 400;
}}
/* A result can be many lines tall; its copy button belongs at the top of it,
   not floating halfway down. */
.result .copy-btn {{ align-self: flex-start; margin-top: 10px; }}
.result .text::before {{
  content: 'Texte généré';
  display: block; margin-bottom: 7px;
  font-size: 10px; font-weight: 700; letter-spacing: 0.09em;
  text-transform: uppercase; color: {accent}; opacity: 0.9;
}}

.status {{
  align-self: center; background: {white_alpha05}; color: {dim_text};
  font-size: 11px; padding: 3px 10px; border-radius: 10px;
  margin: 10px 0; border: 1px solid {white_alpha05};
}}

/* Text input bar (type to chat, in addition to voice) */
.chat-input-bar {{
  flex-shrink: 0;
  display: flex; align-items: flex-end; gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid {accent_alpha20};
  background: {bg_rgba};
  z-index: 20;
}}
.chat-input-bar textarea {{
  flex: 1; resize: none;
  background: {white_alpha05};
  color: {text};
  border: 1px solid {accent_alpha20};
  border-radius: 12px;
  padding: 9px 12px;
  font-family: inherit; font-size: 13px; line-height: 1.4;
  max-height: 120px; overflow-y: auto;
  outline: none;
}}
.chat-input-bar textarea::placeholder {{ color: {dim_text}; }}
.chat-input-bar textarea:focus {{ border-color: {accent}; }}
.send-btn {{
  flex-shrink: 0;
  width: 36px; height: 36px; border-radius: 10px;
  border: none; cursor: pointer;
  background: {accent}; color: {text_on_accent};
  font-size: 15px; line-height: 1;
  display: flex; align-items: center; justify-content: center;
  transition: transform 0.15s, opacity 0.2s;
}}
.send-btn:hover {{ transform: scale(1.06); }}
.send-btn:active {{ transform: scale(0.96); }}

/* --- Talk bubble ---------------------------------------------------------
   The same window, re-anchored to the top edge and sized by its content.
   Lighter and more rounded than the side panel, and ringed in the accent
   colour: it sits over what you are working on, and you should know at a
   glance that this one is listening. */
.chat-window.talk {{
  background-color: {bg_rgba_talk};
  border: 1px solid {accent_alpha40};
  border-radius: 22px;
  box-shadow: 0 10px 40px {black_alpha40};
  /* No backdrop blur here. It is the most expensive property in this sheet —
     the compositor has to read back and blur everything under the window on
     every frame — and this window, unlike the side panel, is resized as its
     content grows and sits over whatever the user is working in. The
     background is opaque enough on its own to stay readable. */
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}}
/* Same reason: a promoted layer has to be reallocated on every resize, and the
   bubble barely scrolls — it grows to fit instead. */
.talk .chat-scroll-area {{ will-change: auto; transform: none; }}
/* The keyboard is grabbed for the whole session — there is nothing to type into. */
.talk .chat-input-bar {{ display: none; }}
.talk .chat-container {{ padding-bottom: 12px; }}
.talk .chat-scroll-area {{ padding-bottom: 4px; }}
/* The bubble is sized to its content: a trailing margin under the last turn
   is empty space it would have to reserve. */
.talk .chat-container > .message-wrapper:last-of-type,
#live .message-wrapper {{ margin-bottom: 0; }}

/* The turn being spoken or written right now: no entry animation (it is
   rewritten several times a second) and a caret, so an unfinished sentence
   never reads as a finished one. */
.message-wrapper.live {{ animation: none; opacity: 1; transform: none; }}
.live .text::after {{
  content: '▌'; margin-left: 1px; opacity: 0.55;
  animation: caret 1.05s steps(1) infinite;
}}
@keyframes caret {{ 50% {{ opacity: 0; }} }}
'''

CHAT_JS = '''
const copyIcon = '<svg viewBox="0 0 24 24"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>';
const checkIcon = '<svg viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>';

function copyText(btn, index) {
  // Use custom protocol to let Python handle clipboard safely
  window.location.href = "copy://" + index;
  
  // Optimistic UI update
  btn.innerHTML = checkIcon;
  btn.classList.add('copied');
  setTimeout(() => { btn.innerHTML = copyIcon; btn.classList.remove('copied'); }, 1500);
}

function post(msg) {
  window.webkit.messageHandlers.signal.postMessage(JSON.stringify(msg));
}

function signalDrag() { post({action: 'Drag'}); }
function signalClose() { post({action: 'Close'}); }

// --- Talk bubble: live text and content-driven height ----------------------
let lastHeight = 0;

function contentHeight() {
  const hint = document.querySelector('.pin-hint');
  const chat = document.getElementById('chat');
  return Math.ceil((hint ? hint.offsetHeight + 16 : 0) +
                   (chat ? chat.scrollHeight : 0)) + 12;
}

// Tell Python how tall the bubble should be. Ignored below a few pixels so a
// sub-pixel reflow can't start a resize/relayout loop.
function reportHeight() {
  if (!window.TALK) return;
  const h = contentHeight();
  if (Math.abs(h - lastHeight) < 4) return;
  lastHeight = h;
  post({action: 'Resize', height: h});
}

// Rewrite the turn in progress in place — the live transcript while the user
// speaks, then the reply as the model writes it. Patching this one node is why
// the text can grow like an autocompletion: reloading the page for every
// delta would restart every animation and lose the scroll position.
function setLive(role, html) {
  const live = document.getElementById('live');
  if (!live) return;
  if (!html) { live.innerHTML = ''; reportHeight(); return; }
  let node = live.firstElementChild;
  if (!node || !node.classList.contains(role)) {
    live.innerHTML = '<div class="message-wrapper live ' + role +
                     '"><div class="message"><div class="text"></div></div></div>';
    node = live.firstElementChild;
  }
  node.querySelector('.text').innerHTML = html;
  // Scrolled here, instantly. The observer below deliberately ignores this
  // node: routing a dozen updates a second through checkScroll() would start
  // a dozen smooth-scroll animations a second, each outliving the next.
  const area = document.getElementById('scroll-area');
  if (area) area.scrollTop = area.scrollHeight;
  reportHeight();
}

function sendMessage() {
  const ta = document.getElementById('chat-input');
  if (!ta) return;
  const text = ta.value.trim();
  if (!text) return;
  window.webkit.messageHandlers.signal.postMessage(JSON.stringify({action: 'Send', content: text}));
  ta.value = '';
  ta.style.height = 'auto';
}

// Wire up the input box: Enter sends (Shift+Enter = newline), focus/blur
// pause the auto-hide timer, and the box grows with its content.
(function initInput() {
  const ta = document.getElementById('chat-input');
  if (!ta) return;
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  ta.addEventListener('focus', () => {
    window.webkit.messageHandlers.signal.postMessage(JSON.stringify({action: 'KeepAlive', state: 'focus'}));
  });
  ta.addEventListener('blur', () => {
    window.webkit.messageHandlers.signal.postMessage(JSON.stringify({action: 'KeepAlive', state: 'blur'}));
  });
  ta.addEventListener('input', () => {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
  });
})();

function copyCode(btn) {
  const code = btn.nextElementSibling.querySelector('code');
  if (!code) return;
  
  const text = code.innerText;
  // Use robust postMessage IPC for large content
  window.webkit.messageHandlers.signal.postMessage(JSON.stringify({
    action: 'CopyContent',
    content: text
  }));
  
  // Feedback
  btn.innerHTML = checkIcon;
  btn.classList.add('copied');
  setTimeout(() => { btn.innerHTML = copyIcon; btn.classList.remove('copied'); }, 1500);
}

// Scroll Logic: Improved to handle reloads and dynamic content
function checkScroll(smooth=true) {
  const scrollArea = document.getElementById('scroll-area');
  if (!scrollArea) return;
  
  const scrollToBottom = () => {
    scrollArea.scrollTo({ 
      top: scrollArea.scrollHeight, 
      behavior: smooth ? 'smooth' : 'auto' 
    });
  };

  // Immediate scroll
  scrollToBottom();
  
  // Backup scrolls to account for rendering delays and images
  requestAnimationFrame(scrollToBottom);
  setTimeout(scrollToBottom, 50);
  setTimeout(scrollToBottom, 250);
}

// Observe new messages
const chat = document.getElementById('chat');
if (chat) {
  // Direct children only: those are real messages. The live node rewrites its
  // own subtree many times a second and scrolls itself — see setLive().
  new MutationObserver(() => checkScroll(true)).observe(chat, { childList: true });
}

window.onload = () => { checkScroll(false); reportHeight(); };

// Height follows the content however it changes — a new message, a reply
// being written, or a long line rewrapping after a resize.
(function initTalk() {
  if (!window.TALK) return;
  const chat = document.getElementById('chat');
  if (chat && window.ResizeObserver) new ResizeObserver(reportHeight).observe(chat);
  reportHeight();
})();
'''

CHAT_HTML_TEMPLATE = '''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><style>{CHAT_CSS}</style></head>
<body>
<div class="chat-window{talk_class}">
  <div class="drag-handle" onmousedown="signalDrag()"></div>
  {pin_hint}
  <div class="chat-scroll-area" id="scroll-area">
    <div id="chat" class="chat-container">{messages}<div id="live"></div></div>
  </div>
  <div class="chat-input-bar">
    <textarea id="chat-input" rows="1" placeholder="Type a message…"></textarea>
    <button class="send-btn" onclick="sendMessage()" title="Send">&#10148;</button>
  </div>
</div>
<script>window.TALK = {TALK};
{CHAT_JS}</script>
</body>
</html>'''


class ChatOverlay(Gtk.Window):
    """Chat overlay using WebKit2."""

    def __init__(self, talk: bool = False):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        #: talk bubble (top edge, content-sized) vs side conversation panel
        self.talk = talk
        self._height = TALK_MIN_HEIGHT
        self._wanted_height = TALK_MIN_HEIGHT
        self._resize_timer: Optional[int] = None
        self._ready = False        # the page is loaded and can run JS
        self._pending_js: Optional[str] = None
        self._setup_window()
        self._setup_webview()
        self._init_animation()
        self.connect("draw", self._on_draw_window)
        self.show_all()

    def _setup_window(self) -> None:
        """Configure window properties."""
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_app_paintable(True)

        # Transparency
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)

        if USE_LAYER_SHELL:
            # --- Wayland: gtk-layer-shell ---
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
            GtkLayerShell.set_namespace(self, "loquivox-chat")
        else:
            # --- X11: classic approach ---
            self.set_keep_above(True)
            self.set_type_hint(Gdk.WindowTypeHint.UTILITY)

        self._apply_geometry()

    @staticmethod
    def _monitor_geometry():
        """Geometry of the monitor the overlay lives on."""
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        return monitor.get_geometry()

    def _max_height(self) -> int:
        """The tallest the bubble may grow — past this it stops being an overlay."""
        return int(self._monitor_geometry().height * TALK_MAX_SCREEN)

    @staticmethod
    def _talk_bottom() -> int:
        """
        How far the bubble's bottom edge sits above the screen's.

        Directly on top of the recording overlay, which the user is already
        watching: two floating windows at opposite ends of the screen would
        mean reading the same exchange in two places.
        """
        from loquivox.ui.recording_overlay import BOTTOM_MARGIN  # lazy: cycle
        return BOTTOM_MARGIN + CFG.OVERLAY_HEIGHT + TALK_GAP

    def _apply_geometry(self) -> None:
        """
        Place and size the window for the mode it is in.

        The side conversation keeps its fixed panel against the right edge. The
        talk bubble sits centred just above the recording overlay instead, and
        takes its height from its content (``_apply_height``) — anchored by its
        bottom edge, so it grows upwards and the newest line never moves.

        On layer-shell an unanchored axis centres the surface, which is exactly
        what both placements want; on X11 the same thing is done by hand.
        """
        geometry = self._monitor_geometry()
        if self.talk:
            width = min(TALK_WIDTH, geometry.width - 2 * TALK_GAP)
            height = self._height = max(
                TALK_MIN_HEIGHT, min(self._height, self._max_height()))
        else:
            width, height = CHAT_WIDTH, CHAT_HEIGHT

        # Keyboard focus only where there is something to type into. The talk
        # bubble hides its input bar — the session has the keyboard grabbed —
        # and a focused overlay is a paste that lands in the wrong window: both
        # dictation and talk mode's own delivery aim Ctrl+V at whatever the
        # compositor says is focused.
        if USE_LAYER_SHELL:
            GtkLayerShell.set_keyboard_mode(
                self, GtkLayerShell.KeyboardMode.NONE if self.talk
                else GtkLayerShell.KeyboardMode.ON_DEMAND)
        else:
            self.set_accept_focus(not self.talk)

        if USE_LAYER_SHELL:
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, not self.talk)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, False)
            GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, self.talk)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT,
                                     0 if self.talk else 20)
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM,
                                     self._talk_bottom() if self.talk else 0)
        else:
            x = (geometry.x + (geometry.width - width) // 2 if self.talk
                 else geometry.x + geometry.width - width - 20)
            y = (geometry.y + geometry.height - height - self._talk_bottom()
                 if self.talk else geometry.y + (geometry.height - height) // 2)
            self.move(x, y)

        self.set_size_request(width, height)
        self.resize(width, height)

    def _apply_height(self, height: int) -> None:
        """
        Grow or shrink the bubble to the height its content just reported.

        Coalesced: a reply arriving line by line asks for a new height every
        few hundred milliseconds, and every resize costs a fresh surface, a
        full relayout and a repaint of what is behind it. The last height asked
        for within the window is the one that lands.
        """
        if not self.talk or height <= 0:
            return
        self._wanted_height = max(TALK_MIN_HEIGHT, min(height, self._max_height()))
        if self._resize_timer is None:
            self._resize_timer = GLib.timeout_add(RESIZE_COALESCE_MS,
                                                  self._flush_height)

    def _flush_height(self) -> bool:
        """Apply the height last asked for, if it is not the current one."""
        self._resize_timer = None
        if self._wanted_height != self._height:
            self._height = self._wanted_height
            self._apply_geometry()
        return False

    def set_talk_mode(self, talk: bool) -> None:
        """
        Switch between the talk bubble and the side conversation.

        Re-anchoring beats tearing the window down and building another: the
        WebView, its page and the conversation on it all survive, so starting
        a session moves the panel instead of flashing a new one into place.
        Caller re-renders afterwards — the ``talk`` class is baked into the HTML.
        """
        if self.talk == talk:
            return
        self.talk = talk
        self._height = TALK_MIN_HEIGHT
        self._apply_geometry()

    def set_live(self, role: str, text: str) -> None:
        """
        Show the turn in progress: the transcript as it is heard, then the
        reply as it is written. Empty ``text`` clears it.

        Patched straight into the DOM — a full reload per delta would restart
        every animation and lose the scroll position, which is the difference
        between text that grows and text that flickers.
        """
        rendered = self._render_markdown(text) if text else ""
        self._run_js(f"setLive({json.dumps(role)}, {json.dumps(rendered)});")

    def _run_js(self, script: str) -> None:
        """
        Run a script in the page, deferring it if the page is still loading.

        Only the last deferred script is kept: every caller here rewrites the
        same node with the newest text, so replaying the backlog would just
        redraw intermediate states nobody would see.
        """
        if not self._ready:
            self._pending_js = script
            return
        try:
            self.webview.run_javascript(script, None, None, None)
        except Exception:
            pass

    def _on_load_changed(self, webview, event) -> None:
        """Release deferred scripts once the page can run them."""
        if event != WebKit2.LoadEvent.FINISHED:
            return
        self._ready = True
        script, self._pending_js = self._pending_js, None
        if script:
            self._run_js(script)

    def _on_draw_window(self, widget: Gtk.Window, cr: cairo.Context) -> bool:
        """Clear window background to fixed transparency for rounded corners."""
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        return False

    def _setup_webview(self) -> None:
        """Setup WebKit2 webview."""
        self.webview = WebKit2.WebView()
        self.webview.set_background_color(Gdk.RGBA(0, 0, 0, 0))
        settings = self.webview.get_settings()
        settings.set_enable_javascript(True)

        # Robust IPC via UserContentManager
        content_manager = self.webview.get_user_content_manager()
        content_manager.register_script_message_handler("signal")
        content_manager.connect("script-message-received::signal", self._on_script_message)

        self.webview.connect("decide-policy", self._on_policy_decision)
        self.webview.connect("load-changed", self._on_load_changed)
        self.add(self.webview)

    def _on_script_message(self, manager, message) -> None:
        """Handle robust signals from JavaScript."""
        try:
            val = message.get_js_value()
            if not val:
                return

            # Message is sent as a JSON string from JS
            data = val.to_string()
            msg = json.loads(data)

            action = msg.get('action')
            if action == 'Drag':
                if USE_LAYER_SHELL:
                    # Layer-shell windows cannot be moved via drag
                    return
                display = self.get_display()
                seat = display.get_default_seat()
                pointer = seat.get_pointer()
                screen, x, y = pointer.get_position()
                self.begin_move_drag(1, x, y, Gtk.get_current_event_time())
            elif action == 'CopyContent':
                content = msg.get('content', '')
                clipboard = get_clipboard()
                clipboard.copy(content)
            elif action == 'Send':
                content = msg.get('content', '').strip()
                if content:
                    # Late import to avoid a circular import at module load.
                    from loquivox.handlers.mode import ModeHandler
                    ModeHandler.submit_text_chat(content)
            elif action == 'Resize':
                self._apply_height(int(msg.get('height', 0)))
            elif action == 'Close':
                from loquivox.managers.chat import ChatManager
                ChatManager.hide_manual()
            elif action == 'KeepAlive':
                # Pause the auto-hide while the input box has focus, resume on blur.
                from loquivox.managers.chat import ChatManager
                ChatManager.set_keepalive(msg.get('state') == 'focus')
        except Exception as e:
            print(f"❌ ScriptMessage Error: {e}")

    def _init_animation(self) -> None:
        """Initialize fade animation state."""
        self.opacity_value = 0.0
        self.fade_in_active = False
        self.fade_out_active = False
        self.fade_timer = None
        self.fade_callback = None
        self.start_fade_in()

    def start_fade_in(self) -> None:
        """Start fade-in animation."""
        self.fade_out_active = False
        self.fade_in_active = True
        self.opacity_value = 0.0
        self._cancel_fade_timer()
        self.fade_timer = GLib.timeout_add(16, self._fade_in_step)

    def _fade_in_step(self) -> bool:
        """Fade-in animation step."""
        self.opacity_value = min(1.0, self.opacity_value + 0.1)
        try:
            self.set_opacity(self.opacity_value)
        except Exception:
            pass
        if self.opacity_value >= 1.0:
            self.fade_in_active = False
            self.fade_timer = None
            return False
        return True

    def start_fade_out(self, callback: Optional[Callable] = None) -> None:
        """Start fade-out animation."""
        self.fade_in_active = False
        self.fade_out_active = True
        self.fade_callback = callback
        self._cancel_fade_timer()
        self.fade_timer = GLib.timeout_add(16, self._fade_out_step)

    def _fade_out_step(self) -> bool:
        """Fade-out animation step."""
        self.opacity_value = max(0.0, self.opacity_value - 0.1)
        try:
            self.set_opacity(self.opacity_value)
        except Exception:
            pass
        if self.opacity_value <= 0.0:
            self.fade_out_active = False
            self.fade_timer = None
            if self.fade_callback:
                self.fade_callback()
            return False
        return True

    def _cancel_fade_timer(self) -> None:
        """Cancel active fade timer."""
        if self.fade_timer:
            GLib.source_remove(self.fade_timer)
            self.fade_timer = None

    def update_content(self, messages: List[Dict[str, str]], status_text: Optional[str] = None,
                       is_pinned: bool = False, is_tts: bool = False) -> None:
        """Update chat content with markdown rendering."""
        html_messages = []

        for idx, msg in enumerate(messages):
            role = msg["role"]
            rendered = self._render_markdown(msg["text"])
            # Pass index, not ID, for robust handling
            copy_btn = f'<button class="copy-btn" onclick="copyText(this, {idx})">{SVG_COPY_ICON}</button>'
            msg_html = f'<div class="message"><div class="text">{rendered}</div></div>'

            html_messages.append(
                f'<div class="message-wrapper {role}">'
                f'{msg_html}'
                f'{copy_btn}'
                f'</div>'
            )

        if status_text:
            html_messages.append(f'<div class="message status">{status_text}</div>')

        # Build the hint bar. During a talk session the global hotkeys are
        # grabbed, so showing F9/F10 there would name keys that do nothing —
        # the session's own keys are shown instead.
        if self.talk:
            from loquivox.handlers.keyboard import KeyboardHandler  # lazy: cycle
            labels = ["🗣️ Talk"] + [f"{'+'.join(keys)}: {what}"
                                    for keys, what in KeyboardHandler.TALK_HINTS]
        else:
            pin_label = CFG.HOTKEY_DEFS["pin"][0]
            tts_label = CFG.HOTKEY_DEFS["tts"][0]
            labels = [
                f"{pin_label}: Unpin" if is_pinned else f"{pin_label}: Pin",
                f"{tts_label}: Mute" if is_tts else f"{tts_label}: Voice",
            ]
        separator = '<span style="opacity:0.2; margin:0 4px">|</span>'
        pin_hint = (
            '<div class="pin-hint">'
            + separator.join(f'<span>{label}</span>' for label in labels)
            + separator
            + '<a href="settings://open" class="settings-link" title="Settings">⚙️</a>'
            + '<a href="#" onclick="signalClose(); return false;" title="Hide">✕</a>'
            + '</div>'
        )

        # Prepare dynamic CSS with centralized colors
        def hex_to_rgba(hex_str, alpha):
            h = hex_str.lstrip('#')
            rgb = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
            return f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {alpha})"

        def get_contrast_text(bg_hex):
            # Simple luminance-based contrast
            h = bg_hex.lstrip('#')
            rgb = [int(h[i:i+2], 16) for i in (0, 2, 4)]
            # Standard relative luminance formula
            lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255
            return "#000000" if lum > 0.5 else "#FFFFFF"

        scheme = CFG.COLOR_SCHEMES.get(STATE.color_scheme, CFG.COLOR_SCHEMES[CFG.DEFAULT_SCHEME])

        formatted_css = CHAT_CSS.format(
            bg=scheme["bg"],
            bg_rgba=hex_to_rgba(scheme["bg"], 0.95),
            # The bubble floats over what the user is working on: it must dim
            # the content underneath, never hide it.
            bg_rgba_talk=hex_to_rgba(scheme["bg"], 0.86),
            surface=scheme["surface"],
            surface_alpha80=hex_to_rgba(scheme["surface"], 0.8),
            accent=scheme["accent"],
            accent_alpha10=hex_to_rgba(scheme["accent"], 0.1),
            accent_alpha20=hex_to_rgba(scheme["accent"], 0.2),
            accent_alpha30=hex_to_rgba(scheme["accent"], 0.3),
            accent_alpha40=hex_to_rgba(scheme["accent"], 0.4),
            text=scheme["text"],
            text_on_accent=scheme["text"] if STATE.color_scheme == "Pink Orchid" else get_contrast_text(scheme["accent"]),
            success=scheme["accent"],
            dim_text=hex_to_rgba(scheme["text"], 0.6),
            selection_alpha90=hex_to_rgba(scheme["accent"], 0.3),
            white=scheme["text"],
            white_alpha05=hex_to_rgba(scheme["text"], 0.05),
            white_alpha10=hex_to_rgba(scheme["text"], 0.1),
            white_alpha25=hex_to_rgba(scheme["text"], 0.25),
            black_alpha40=hex_to_rgba(scheme["bg"], 0.4)
        )

        html = CHAT_HTML_TEMPLATE.replace("{messages}", "\n".join(html_messages))
        html = html.replace("{pin_hint}", pin_hint)
        html = html.replace("{talk_class}", " talk" if self.talk else "")
        html = html.replace("{TALK}", "true" if self.talk else "false")
        html = html.replace("{CHAT_CSS}", formatted_css)
        html = html.replace("{CHAT_JS}", CHAT_JS)

        # The page is about to be replaced: anything queued for the old one is
        # stale, and JS can't run again until the new one has finished loading.
        self._ready = False
        self._pending_js = None
        self.webview.load_html(html, None)

    def _on_policy_decision(self, webview, decision, decision_type) -> bool:
        """Handle URI navigations (copy://, settings://)."""
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            nav = decision.get_navigation_action()
            uri = nav.get_request().get_uri()
            if not uri:
                return False

            if uri.startswith("settings://"):
                from loquivox.ui.settings_dialog import SettingsDialog
                GLib.idle_add(SettingsDialog.show)
                decision.ignore()
                return True

            if uri.startswith("copy://"):
                try:
                    idx = int(uri.split("copy://")[1])
                    if 0 <= idx < len(STATE.chat_messages):
                        text = STATE.chat_messages[idx]["text"]
                        clipboard = get_clipboard()
                        clipboard.copy(text)
                except Exception:
                    pass
                decision.ignore()
                return True

        return False

    @staticmethod
    def _render_markdown(text: str) -> str:
        """Convert simple markdown to HTML."""
        text = html_lib.escape(text)

        # Code blocks with copy button
        def repl_code_block(match):
            code_content = match.group(1).strip()
            return (
                f'<div class="code-block-wrapper">'
                f'<button class="code-copy-btn" onclick="copyCode(this)" title="Copy Code">{SVG_COPY_ICON}</button>'
                f'<pre><code>{code_content}</code></pre>'
                f'</div>'
            )
        text = re.sub(r'```(?:\w+)?(?:\s*\n)(.*?)\n?```', repl_code_block, text, flags=re.DOTALL)
        # Inline code
        text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
        # Bold
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
        # Italic
        text = re.sub(r'(?<!\w)\*([^*]+)\*(?!\w)', r'<em>\1</em>', text)
        text = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'<em>\1</em>', text)
        # Line breaks
        text = text.replace('\n', '<br>')

        return text

    def close(self) -> None:
        """Clean up and destroy."""
        self._cancel_fade_timer()
        if self._resize_timer is not None:
            GLib.source_remove(self._resize_timer)
            self._resize_timer = None
        self.destroy()
