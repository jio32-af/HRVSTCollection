"""HRVST -- tax-loss harvesting scanner (ACTUAL mode only).

Premium terminal UI: Home + Analyze. Interactive charts, instant threshold
re-filtering (fetch once, slice freely), CSV import, optional buy-price
override. Cost basis auto-looked-up when omitted.

Run with:  python MP.py  (Flask, http://127.0.0.1:5000)
"""
import datetime
import os
import random
import sys
import uuid

# Always use the random_tickers.py sitting next to this file, never a stale
# copy that happens to be on sys.path from another folder.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import pandas as pd
import requests

try:
    from flask import (Flask, jsonify, request, Response, redirect, url_for,
                       session as flask_session)
    _FLASK_AVAILABLE = True
except Exception:
    Flask, jsonify, request, Response = None, None, None, None
    redirect, url_for, flask_session = None, None, None
    _FLASK_AVAILABLE = False

import random_tickers as rt


class _SessionState(dict):
    """In-file stand-in for ``st.session_state``.

    The Streamlit dependency used to live here; it now lives in this
    module so every ``st.session_state`` reference below keeps working
    without the streamlit package.
    """

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


class _Element:
    """No-op UI element: usable as context manager and widget host."""

    def __call__(self, *args, **kwargs):
        return None

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def button(self, *args, **kwargs):
        return False

    def checkbox(self, *args, **kwargs):
        return False

    def text_input(self, *args, **kwargs):
        return kwargs.get("value", "")

    def number_input(self, *args, **kwargs):
        return kwargs.get("value", 0.0)

    def date_input(self, *args, **kwargs):
        return kwargs.get("value")

    def selectbox(self, label, options=(), index=0, **kwargs):
        try:
            return list(options)[index]
        except Exception:
            return None

    def multiselect(self, *args, **kwargs):
        return kwargs.get("default", [])

    def slider(self, *args, **kwargs):
        return kwargs.get("value")

    def file_uploader(self, *args, **kwargs):
        return None

    def download_button(self, *args, **kwargs):
        return None

    def columns(self, *args, **kwargs):
        try:
            n = args[0] if args else 1
            n = len(n) if isinstance(n, (list, tuple)) else int(n)
        except Exception:
            n = 1
        return [_Element() for _ in range(max(n, 1))]

    def tabs(self, names, **kwargs):
        return [_Element() for _ in names]

    def expander(self, *args, **kwargs):
        return _Element()

    def spinner(self, *args, **kwargs):
        return _Element()

    def container(self, *args, **kwargs):
        return _Element()

    def empty(self, *args, **kwargs):
        return _Element()

    def form(self, *args, **kwargs):
        return _Element()


class _StShim(_Element):
    """In-file home for everything that depended on streamlit.

    Presentation strings below are unchanged; only the runtime moved
    from Streamlit to Flask (see ``app`` at the bottom of this file).
    """

    def __init__(self):
        self.session_state = _SessionState()
        self.sidebar = _Element()
        self._capture = None

    def markdown(self, body="", unsafe_allow_html=False, **kwargs):
        if self._capture is not None and body:
            self._capture.append(str(body))
        return None

    def cache_data(self, *dargs, **dkwargs):
        def deco(fn):
            return fn

        if dargs and callable(dargs[0]) and len(dargs) == 1 and not dkwargs:
            return dargs[0]
        return deco

    def set_page_config(self, *args, **kwargs):
        return None

    def rerun(self, *args, **kwargs):
        return None

    def stop(self, *args, **kwargs):
        return None


st = _StShim()

# Fill in your credentials below, then leave the rest alone.
GA_MEASUREMENT_ID = "G-DM53GE2RML"  # <-- your G- ID
GA_API_SECRET = "c-r7qqyHQkOf7a3g28hevg"  # <-- your API secret from GA4 Admin


def track_streamlit_view():
    """Log one page_view per session to GA4 via the Measurement Protocol."""
    try:
        if "analytics_user_id" not in st.session_state:
            st.session_state.analytics_user_id = str(uuid.uuid4())
    except Exception:
        return
    url = ("https://www.google-analytics.com/mp/collect"
           f"?measurement_id={GA_MEASUREMENT_ID}&api_secret={GA_API_SECRET}")
    payload = {
        "client_id": st.session_state.analytics_user_id,
        "events": [{"name": "page_view", "params": {"page_title": "HRVST"}}],
    }
    try:
        requests.post(url, data=json.dumps(payload),
                      headers={"Content-Type": "application/json"}, timeout=2)
    except Exception:
        pass  # never crash the app if Google times out


# Fired per Flask request (see app.before_request below), never at import.
@st.cache_data(ttl=600, show_spinner=False)
def _cached_analysis(holdings_json, min_loss, live_json):
    """analyze_actual_portfolio over JSON-serialized inputs.

    Lists of holding dicts are unhashable, so callers serialize them first;
    the JSON text (plus threshold and live prices) is the cache key. Pure
    recompute for identical inputs — no network inside, no mutation of the
    cached result downstream.
    """
    holdings = json.loads(holdings_json)
    for _h in holdings:
        # Cache holdings carry buy_date as a datetime; the JSON round-trip
        # stringifies it, so parse it back (date-only strings stay dates).
        for _k in ("buy_date", "date"):
            _d = _h.get(_k)
            if isinstance(_d, str):
                try:
                    _h[_k] = datetime.datetime.fromisoformat(_d)
                except ValueError:
                    _h[_k] = datetime.date.fromisoformat(_d)
    return rt.analyze_actual_portfolio(holdings, min_loss, json.loads(live_json))

st.set_page_config(
    page_title="HRVST | Tax-Loss Harvesting",
    page_icon="§",
    layout="wide",
    initial_sidebar_state="expanded",
)

GREEN, RED, GOLD = "#1D5C3F", "#A63D2F", "#B07C24"
INK, MUTED = "#1C2420", "#5A645E"

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Logo.png")
# HV mark region of Logo.png (ink-measured on the white artwork:
# mark spans x257-447, y179-411), used as the header icon.
LOGO_ICON_BOX = (235, 157, 469, 433)


def logo_data_uri(size=384, variant="full"):
    """Logo as a data URI: downscaled with its white background keyed out
    to transparency so the mark floats on the light theme. Variant
    "icon" crops to the HV mark for small header use. Returns "" when the
    file is unavailable, in which case callers skip the logo."""
    key = f"logo_uri_{variant}_{size}"
    try:
        cached = st.session_state.get(key)
    except Exception:
        cached = None
    if cached:
        return cached
    uri = ""
    try:
        import base64
        import io
        from PIL import Image
        im = Image.open(LOGO_PATH).convert("RGB")
        if variant == "icon":
            im = im.crop(LOGO_ICON_BOX)
        im.thumbnail((size, size))
        im = im.convert("RGBA")
        try:
            import numpy as np
            arr = np.asarray(im).copy()
            arr[arr[..., :3].min(axis=2) > 240, 3] = 0
            im = Image.fromarray(arr)
        except Exception:
            pass
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        uri = ""
    try:
        st.session_state[key] = uri
    except Exception:
        pass
    return uri

_BASE_CSS = """
<style>
  [data-testid="stSidebar"] { display: none; }
  [data-testid="stHeader"] { background: rgba(10,14,20,0); }
  .appview-container .main .block-container { max-width: 1160px; padding-top: 1.25rem;
    padding-bottom: 2rem; }
  * { font-variant-numeric: tabular-nums; }
  html, body, [class*="css"] { font-family: -apple-system, BlinkMacSystemFont,
    "Segoe UI", Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased; }
  :focus-visible { outline: 2px solid rgba(0,209,143,.7); outline-offset: 2px; }
  .topnav { display: flex; align-items: center; gap: 14px; padding: 12px 2px 16px;
    border-bottom: 1px solid #1D2530; margin-bottom: 22px; }
  .topnav .brand { font-size: 20px; font-weight: 800; letter-spacing: -0.02em;
    color: #F2F5F7; margin-right: auto; display: flex; align-items: center; gap: 10px; }
  .topnav .mark { width: 14px; height: 14px; border-radius: 4px; background: #00D18F;
    box-shadow: 0 0 14px rgba(0,209,143,.55); }
  .topnav .brandlogo { width: 30px; height: 30px; border-radius: 8px;
    border: 1px solid #1D2530; }
  .topnav .tag { font-size: 12px; font-weight: 600; letter-spacing: .04em; color: #9AA7B4;
    border-left: 1px solid #1D2530; padding-left: 14px; }
  .topnav .navlogo { width: 36px; height: 36px; border-radius: 10px;
    border: 1px solid #1D2530; }
  .splash { position: fixed; inset: 0; z-index: 9999; background: #FFFFFF;
    display: flex; align-items: center; justify-content: center;
    pointer-events: none; animation: splashout .6s ease 4.4s forwards; }
  .stage { position: relative; width: min(420px, 82vw); height: 270px;
    display: flex; align-items: center; justify-content: center; }
  .loader { font-family: Georgia, 'Times New Roman', serif; font-size: 68px;
    font-weight: 700; color: #1D5C3F; display: flex; letter-spacing: .02em;
    opacity: 0; animation: leadin .9s cubic-bezier(.16,1,.3,1) .1s forwards,
    loaderOut .5s ease 3.4s forwards; }
  .wl { display: inline-block; }
  .wl.h, .wl.v { mix-blend-mode: multiply; }
  .wl.r { animation: fadeR 1s cubic-bezier(.65,0,.35,1) 1.2s forwards; }
  .wl.s { animation: fadeS 1s cubic-bezier(.65,0,.35,1) 1.2s forwards; }
  .wl.t { animation: fadeT 1s cubic-bezier(.65,0,.35,1) 1.2s forwards; }
  .wl.h { animation: meetH 1.1s cubic-bezier(.16,1,.3,1) 1.7s forwards,
    pushCloserH .8s cubic-bezier(.16,1,.3,1) 2.8s forwards; }
  .wl.v { animation: meetV 1.1s cubic-bezier(.16,1,.3,1) 1.7s forwards,
    pushCloserV .8s cubic-bezier(.16,1,.3,1) 2.8s forwards; }
  @keyframes leadin { from { opacity: 0; transform: translateY(16px); }
    to { opacity: 1; transform: none; } }
  @keyframes fadeR { to { transform: translateX(.34em) scaleX(.2); opacity: 0; } }
  @keyframes fadeS { to { transform: scaleX(.15); opacity: 0; } }
  @keyframes fadeT { to { transform: translateX(-.34em) scaleX(.2); opacity: 0; } }
  @keyframes meetH { to { transform: translateX(.3em); } }
  @keyframes meetV { to { transform: translateX(-.3em); } }
  @keyframes pushCloserH { from { transform: translateX(.3em); }
    to { transform: translateX(1.1em); } }
  @keyframes pushCloserV { from { transform: translateX(-.3em); }
    to { transform: translateX(-1.1em); } }
  @keyframes loaderOut { to { opacity: 0; } }
  .finlogo { position: absolute; top: 50%; left: 50%; width: min(200px, 50vw);
    height: auto; opacity: 0; transform: translate(-50%,-50%);
    animation: finIn .7s cubic-bezier(.16,1,.3,1) 3.4s forwards; }
  @keyframes finIn { to { opacity: 1; } }
  @keyframes splashout { to { opacity: 0; visibility: hidden; } }
  .pill { display: inline-flex; align-items: center; gap: 8px; font-size: 12px;
    font-weight: 700; letter-spacing: .04em; color: #00D18F;
    border: 1px solid rgba(0,209,143,.35); border-radius: 20px;
    padding: 5px 14px; margin-bottom: 16px; background: rgba(0,209,143,.06); }
  .hero { background: #11161F; border: 1px solid #1D2530; border-radius: 12px;
    padding: 48px 44px 40px; margin-bottom: 22px; }
  .hero h1 { font-size: 44px; line-height: 1.08; letter-spacing: -0.03em;
    margin: 0 0 14px; color: #F2F5F7; font-weight: 800; max-width: 680px; }
  .hero h1 .green { color: #00D18F; }
  .hero p { font-size: 16.5px; color: #9AA7B4; margin: 0 0 8px; max-width: 640px;
    line-height: 1.6; }
  .trustrow { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 22px; }
  .trustitem { font-size: 13px; color: #9AA7B4; display: flex; align-items: center; gap: 8px; }
  .trustitem .dot { width: 6px; height: 6px; border-radius: 50%; background: #00D18F; }
  .statband { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
  .stat { flex: 1; min-width: 170px; background: #11161F; border: 1px solid #1D2530;
    border-radius: 8px; padding: 18px 20px; }
  .stat .v { font-size: 22px; font-weight: 700; letter-spacing: -0.02em; color: #F2F5F7; }
  .stat .l { font-size: 13px; color: #9AA7B4; margin-top: 4px; line-height: 1.5; }
  .steps { display: flex; gap: 12px; flex-wrap: wrap; }
  .step { flex: 1; min-width: 210px; background: #11161F; border: 1px solid #1D2530;
    border-radius: 12px; padding: 20px 22px; }
  .step .n { font-size: 13px; font-weight: 800; color: #000000; background: #00D18F;
    width: 26px; height: 26px; border-radius: 8px; display: flex;
    align-items: center; justify-content: center; }
  .step .t { font-weight: 700; margin: 12px 0 6px; color: #F2F5F7; font-size: 15.5px; }
  .step .d { font-size: 13.5px; color: #9AA7B4; line-height: 1.55; }
  .section-title { font-size: 21px; font-weight: 800; letter-spacing: -0.02em;
    color: #F2F5F7; margin: 36px 0 4px; }
  .section-sub { color: #9AA7B4; font-size: 14px; margin-bottom: 14px; }
  .bigmetrics { display: flex; gap: 12px; flex-wrap: wrap; margin: 6px 0 4px; }
  .bigmetric { flex: 1; min-width: 170px; background: #11161F; border: 1px solid #1D2530;
    border-radius: 8px; padding: 18px 22px; }
  .bigmetric .v { font-size: 28px; font-weight: 800; letter-spacing: -0.02em; }
  .bigmetric .l { font-size: 12.5px; color: #9AA7B4; margin-top: 3px; }
  .badge { display: inline-block; font-size: 11px; font-weight: 700;
    border-radius: 6px; padding: 2px 8px; vertical-align: middle; }
  .badge.long { color: #052E22; background: #00D18F; }
  .badge.short { color: #3A2303; background: #E8A13C; }
  .card { background: #11161F; border: 1px solid #1D2530; border-radius: 8px;
    padding: 18px 22px; margin: 14px 0; }
  .note .t { font-weight: 700; color: #F2F5F7; margin-bottom: 4px; }
  .note .d { font-size: 13px; color: #9AA7B4; line-height: 1.55; }
  .faq { background: #11161F; border: 1px solid #1D2530; border-radius: 12px;
    padding: 6px 22px; }
  footer.site { border-top: 1px solid #1D2530; margin-top: 56px; padding: 26px 2px 8px;
    color: #9AA7B4; font-size: 12.5px; }
  footer.site .frow { display: flex; gap: 28px; flex-wrap: wrap; margin-bottom: 14px; }
  footer.site .fcol { min-width: 150px; }
  footer.site .brand { font-weight: 800; color: #F2F5F7; font-size: 15px; }
  footer.site .fhead { font-size: 13px; font-weight: 700;
    color: #F2F5F7; margin-bottom: 8px; }
  footer.site .fnote { line-height: 1.7; }
  .fine { font-size: 12.5px; color: #9AA7B4; margin-top: 26px; }
  div.stButton > button { border-radius: 9px; font-weight: 650; border: 1px solid #2A3648; }
  div.stButton > button[kind="primary"] { background: #00D18F; border-color: #00D18F;
    color: #052E22; }
  div.stButton > button[kind="primary"]:hover { background: #0BE198; border-color: #0BE198; }
  div.stButton > button:disabled { opacity: .85; border-color: #00D18F; color: #00D18F; }
  div[data-testid="stTabs"] button { font-weight: 650; }
  div[data-testid="stExpander"] { border: 1px solid #1D2530; border-radius: 10px; }
  div[data-testid="stMetric"] { background: #11161F; border: 1px solid #1D2530;
    border-radius: 12px; padding: 12px 16px; }
  .tape { overflow: hidden; white-space: nowrap; border-bottom: 1px solid #1D2530;
    margin: 0 0 20px; }
  .tape-inner { display: inline-block; padding: 9px 0; animation: tape 240s linear infinite; }
  .tape:hover .tape-inner { animation-play-state: paused; }
  @keyframes tape { to { transform: translateX(-50%); } }
  .tick { display: inline-block; margin-right: 36px; font-size: 13px; color: #9AA7B4; }
  .tick b { color: #F2F5F7; font-weight: 700; margin-right: 8px; }
  .up { color: #00D18F; font-weight: 700; } .down { color: #FF5C5C; font-weight: 700; }
  .pulse { width: 8px; height: 8px; border-radius: 50%; background: #00D18F;
    display: inline-block; margin-right: 8px; animation: pulse 2s infinite; }
  @keyframes pulse { 50% { opacity: .3; } }
  @media (prefers-reduced-motion: reduce) {
    .tape-inner, .pulse { animation: none; }
    .splash { animation-duration: .01s; animation-delay: .1s; } }
  .hero h1 em { font-style: italic; font-weight: 400; }
  .plrow { display: flex; align-items: center; gap: 10px; margin: 8px 0; }
  .pltick { width: 56px; font-weight: 800; color: #F2F5F7; }
  .pltrack { flex: 1; background: #1D2530; border-radius: 5px; height: 16px; overflow: hidden; }
  .plfill { height: 100%; border-radius: 5px; }
  .plfill.neg { background: #FF5C5C; } .plfill.pos { background: #00D18F; }
  .plval { width: 118px; text-align: right; font-weight: 700; }
  .plval.neg { color: #FF5C5C; } .plval.pos { color: #00D18F; }
  .alloc { display: flex; height: 20px; border-radius: 10px; overflow: hidden;
    background: #1D2530; margin: 6px 0 12px; }
  .alloc div { height: 100%; }
  .legrow { display: flex; align-items: center; gap: 8px; font-size: 13px;
    color: #9AA7B4; margin: 5px 0; }
  .legrow .sw { width: 11px; height: 11px; border-radius: 3px; }
  .legrow b { color: #F2F5F7; }
</style>
"""
st.markdown(_BASE_CSS, unsafe_allow_html=True)

_LEDGER_CSS = """
<style>
  /* Ledger theme: broadsheet on white, pine ink, serif display. */
  [data-testid="stAppViewContainer"] { background: #FFFFFF; color: #1C2420; }
  h1, h2, h3 { font-family: Georgia, 'Iowan Old Style', 'Times New Roman', serif;
    color: #1C2420; letter-spacing: -0.01em; }
  .appview-container .main .block-container { max-width: 1080px; }
  .topnav { border-bottom: none; }
  .topnav .brand { font-family: Georgia, 'Times New Roman', serif;
    font-weight: 700; color: #1C2420; }
  .topnav .mark { background: #1D5C3F; box-shadow: none; border-radius: 0; }
  .topnav .brandlogo { border: 1px solid #1C2420; border-radius: 0; }
  .topnav .tag { color: #5A645E; border-left: 1px solid #D9DCD4; font-style: italic; }
  .dateline { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap;
    font-size: 12px; color: #5A645E; padding: 8px 2px 20px; }
  .splash { background: #FFFFFF; }
  .splash img { border: 1px solid #1C2420; border-radius: 4px; }
  .pill { background: none; border: none; border-radius: 0; color: #1D5C3F;
    font-size: 12px; font-weight: 700; letter-spacing: .18em;
    text-transform: uppercase; padding: 0; }
  .pulse { background: #1D5C3F; border-radius: 0; animation: none; }
  .hero { background: none; border: none; border-radius: 0; padding: 24px 2px 18px; }
  .hero h1 { color: #1C2420; font-size: 54px; max-width: 780px; }
  .hero h1 .green { color: #1D5C3F; }
  .hero p { color: #3E4742; font-size: 17px; }
  .trustitem { color: #5A645E; }
  .trustitem .dot { background: #1D5C3F; border-radius: 0; }
  .statband { flex-direction: column; gap: 0; border-top: 2px solid #1C2420;
    border-bottom: 1px solid #1C2420; margin-bottom: 24px; }
  .stat { background: none; border: none; border-radius: 0; display: flex;
    justify-content: space-between; align-items: baseline; gap: 16px;
    padding: 12px 2px; min-width: 0; border-top: 1px solid #D9DCD4; }
  .stat:first-child { border-top: none; }
  .stat .v { font-family: Georgia, serif; font-size: 26px; color: #1C2420; }
  .stat .l { font-size: 13px; color: #5A645E; text-align: right; max-width: 360px; }
  .section-title { font-family: Georgia, serif; font-size: 26px; color: #1C2420;
    border-bottom: 1px solid #D9DCD4; padding-bottom: 8px; margin-bottom: 6px; }
  .section-sub { color: #5A645E; font-style: italic; }
  .step { background: #EFF1EC; border: 1px solid #D9DCD4; border-radius: 3px; }
  .step .n { background: none; color: #1D5C3F; width: auto; height: auto;
    font-family: Georgia, serif; font-size: 34px; font-weight: 700; border-radius: 0; }
  .step .t { color: #1C2420; font-family: Georgia, serif; font-size: 17px; }
  .step .d { color: #3E4742; }
  .bigmetrics { margin: 6px 0 4px; }
  .bigmetric { background: #EFF1EC; border: 1px solid #D9DCD4; border-radius: 3px; }
  .bigmetric .v { font-family: Georgia, serif; color: #1C2420; }
  .bigmetric .l { color: #5A645E; }
  .badge { border-radius: 2px; text-transform: uppercase; letter-spacing: .08em; }
  .badge.long { background: #1D5C3F; color: #FFFFFF; }
  .badge.short { background: #B07C24; color: #FFFFFF; }
  .card { background: #EFF1EC; border: 1px solid #D9DCD4; border-radius: 3px; }
  .note .t { color: #1C2420; font-family: Georgia, serif; }
  .note .d { color: #3E4742; }
  .faq { background: none; border: none; border-radius: 0; padding: 0; }
  div[data-testid="stExpander"] { border: 1px solid #D9DCD4; border-radius: 3px;
    background: #FFFFFF; }
  div[data-testid="stMetric"] { background: #EFF1EC; border: 1px solid #D9DCD4;
    border-radius: 3px; }
  div[data-testid="stMetricValue"] { font-family: Georgia, serif; }
  div[data-testid="stCaptionContainer"] { color: #5A645E; }
  div[data-testid="stDataFrame"] { border: 1px solid #D9DCD4; border-radius: 3px; }
  div[data-testid="stTabs"] button { font-weight: 700; color: #5A645E; }
  div[data-testid="stTabs"] button[aria-selected="true"] { color: #1D5C3F; }
  div.stButton > button { border: none; border-radius: 0; background: none;
    color: #1D5C3F; font-weight: 700; padding: 8px 2px; text-decoration: underline;
    text-underline-offset: 4px; text-decoration-color: #9DBEA9; }
  div.stButton > button:hover { color: #1C2420; text-decoration-color: #1C2420;
    background: none; border: none; }
  div.stButton > button[kind="primary"] { background: #1D5C3F; border: none;
    border-radius: 3px; color: #FFFFFF; text-decoration: none; padding: 12px 26px; }
  div.stButton > button[kind="primary"]:hover { background: #16492F; border: none;
    color: #FFFFFF; }
  div.stButton > button:disabled { background: none; border: none; opacity: 1;
    color: #1C2420; text-decoration: underline; text-decoration-color: #1C2420; }
  div.stButton > button[kind="primary"]:disabled { background: #1D5C3F; color: #FFFFFF;
    text-decoration: none; }
  .tape { border-top: 1px solid #1C2420; border-bottom: 1px solid #1C2420;
    background: #FFFFFF; }
  .tick { color: #5A645E; }
  .tick b { font-family: Georgia, serif; color: #1C2420; }
  .up { color: #1D5C3F; } .down { color: #A63D2F; }
  .pltrack { background: #E4E7DF; }
  .pltick { color: #1C2420; font-family: Georgia, serif; }
  .plfill.neg { background: #A63D2F; } .plfill.pos { background: #1D5C3F; }
  .plval.neg { color: #A63D2F; } .plval.pos { color: #1D5C3F; }
  .alloc { background: #E4E7DF; }
  .legrow { color: #5A645E; }
  .legrow b { color: #1C2420; }
  footer.site { border-top: 3px solid #1C2420; color: #5A645E; }
  footer.site .brand { font-family: Georgia, serif; color: #1C2420; }
  footer.site .fhead { color: #1C2420; font-size: 11px; letter-spacing: .14em;
    text-transform: uppercase; }
  .fine { color: #5A645E; font-style: italic; }
  [data-testid="stSidebar"] { display: flex; background: #EFF1EC;
    border-right: 2px solid #1C2420; }
  .deckhead { font-family: Georgia, serif; font-weight: 700; font-size: 22px;
    color: #1C2420; }
  .decklabel { font-size: 11px; font-weight: 700; letter-spacing: .14em;
    text-transform: uppercase; color: #1D5C3F; margin: 14px 0 8px;
    border-bottom: 1px solid #D9DCD4; padding-bottom: 6px; }
  div.stButton > button { text-align: center; }
  .nameplate { text-align: center; padding: 8px 0 4px; }
  .np-brand { font-family: Georgia, 'Times New Roman', serif; font-weight: 700;
    font-size: 46px; letter-spacing: -0.02em; color: #1C2420;
    display: inline-flex; align-items: center; gap: 12px; }
  .np-brand .brandlogo { width: 44px; height: 44px; border: none; border-radius: 0;
    object-fit: contain; background: none; flex: none; }
  .np-brand .mark { width: 16px; height: 16px; border-radius: 0; background: #1D5C3F; }
  .np-tag { font-style: italic; color: #5A645E; font-size: 14px; margin-top: 2px; }
  .secrule { border-top: 1px solid #1C2420; margin: 10px 0; }
  .secrule.thick { border-top: 3px solid #1C2420; }
  .brief { background: #1C2420; color: #F4F5F1; border-radius: 3px; padding: 24px 26px; }
  .brief .brieftitle { font-family: Georgia, serif; font-size: 20px; margin-bottom: 6px; }
  .brief ol { margin: 0; padding-left: 20px; }
  .brief li { font-size: 13.5px; line-height: 1.6; margin: 8px 0; color: #D9DCD4; }
  .brief li b { color: #FFFFFF; }
  .pullquote { text-align: center; font-family: Georgia, serif; font-size: 34px;
    line-height: 1.3; color: #1C2420; padding: 30px 20px 4px; max-width: 780px;
    margin: 0 auto; }
  .pullquote span { display: block; font-family: -apple-system, BlinkMacSystemFont,
    "Segoe UI", Helvetica, Arial, sans-serif; font-size: 13px; font-style: italic;
    color: #5A645E; margin-top: 10px; }
  .takeaways { border: 1px solid #D9DCD4; border-radius: 3px; padding: 20px 22px;
    background: #FFFFFF; }
  .takeaways .takehead { font-family: Georgia, serif; font-size: 18px; margin-bottom: 8px;
    color: #1C2420; }
  .takeaways ul { margin: 0; padding-left: 18px; }
  .takeaways li { font-size: 13.5px; color: #3E4742; margin: 6px 0; line-height: 1.55; }
  .memo { background: #1C2420; color: #F4F5F1; border-radius: 3px;
    padding: 22px 26px; margin: 4px 0 16px; }
  .memo .memotitle { font-size: 11px; font-weight: 700; letter-spacing: .14em;
    text-transform: uppercase; color: #9DBEA9; margin-bottom: 8px; }
  .memo .memobody { font-family: Georgia, serif; font-size: 19px; line-height: 1.55; }
  .bracktable { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
  .bracktable th { text-align: left; font-size: 11px; text-transform: uppercase;
    letter-spacing: .1em; color: #5A645E; border-bottom: 2px solid #1C2420;
    padding: 6px 8px; }
  .bracktable td { border-bottom: 1px solid #D9DCD4; padding: 6px 8px; color: #1C2420; }
  .faqindex { border-top: 2px solid #1C2420; padding-top: 6px; }
  .faqindex .indextitle { font-size: 11px; font-weight: 700; letter-spacing: .14em;
    text-transform: uppercase; color: #1D5C3F; margin-bottom: 4px; }
  .faqindex a { color: #1D5C3F; font-size: 13px; text-decoration: none; display: block;
    padding: 6px 0; border-bottom: 1px solid #D9DCD4; }
  .faqindex a:hover { color: #1C2420; text-decoration: underline; }
  .countdown { font-family: Georgia, serif; font-size: 15px; color: #5A645E;
    margin-top: 12px; }
  .countdown b { font-size: 28px; color: #A63D2F; margin-right: 6px; }
  .memo { position: relative; }
  .stamp { position: absolute; top: -14px; right: 18px; background: #B07C24;
    color: #FFFFFF; font-size: 10px; font-weight: 800; letter-spacing: .12em;
    text-transform: uppercase; padding: 6px 10px; transform: rotate(4deg);
    border: 2px solid #FFFFFF; box-shadow: 0 1px 0 #1C2420; text-align: center;
    line-height: 1.4; }
  .brackbar { position: relative; display: flex; height: 66px; background: #EFF1EC;
    border: 1px solid #D9DCD4; border-radius: 3px; margin-top: 30px; }
  .seg { flex: 1; position: relative; border-left: 1px solid #D9DCD4; overflow: hidden; }
  .seg:first-child { border-left: none; }
  .segfill { position: absolute; left: 0; top: 0; bottom: 0; background: #1D5C3F;
    opacity: 0.85; }
  .segrate { position: absolute; bottom: 5px; width: 100%; text-align: center;
    font-size: 11px; font-weight: 700; color: #1C2420; }
  .seg.hot .segrate { color: #FFFFFF; background: #1C2420; }
  .marker { position: absolute; top: 0; bottom: 0; width: 2px; background: #1C2420; }
  .marker span { position: absolute; top: -24px; left: -16px; background: #1C2420;
    color: #FFFFFF; font-size: 10px; font-weight: 800; letter-spacing: .1em;
    padding: 2px 7px; border-radius: 2px; }
  .cov { margin: 10px 0; }
  .covhead { display: flex; justify-content: space-between; gap: 12px; font-size: 13px;
    color: #1C2420; margin-bottom: 4px; }
  .covhead span { color: #5A645E; }
  .covtrack { background: #E4E7DF; border-radius: 5px; height: 14px; overflow: hidden; }
  .covfill { height: 100%; border-radius: 5px; background: #1D5C3F; }
  .stepper { display: flex; align-items: center; gap: 10px; margin: 2px 0 18px; }
  .stp { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 700;
    color: #5A645E; white-space: nowrap; }
  .stp span { width: 24px; height: 24px; border-radius: 50%; border: 1.5px solid #D9DCD4;
    display: inline-flex; align-items: center; justify-content: center; font-size: 12px; }
  .stp.done { color: #1D5C3F; }
  .stp.done span { background: #1D5C3F; border-color: #1D5C3F; color: #FFFFFF; }
  .stp.current { color: #1C2420; }
  .stp.current span { border-color: #1C2420; }
  .stpline { flex: 1; border-top: 1.5px solid #D9DCD4; min-width: 20px; }
  .lead { padding: 26px 2px 6px; }
  .lead h1 { font-family: Georgia, serif; font-size: 62px; line-height: 1.02;
    letter-spacing: -0.02em; color: #1C2420; margin: 0 0 12px; max-width: 820px; }
  .lead h1 .green { color: #1D5C3F; }
  .lead .lede { font-family: Georgia, serif; font-size: 19px; line-height: 1.55;
    color: #3E4742; max-width: 640px; margin: 0; }
  .verdict { background: #EFF1EC; border: 1px solid #D9DCD4; border-radius: 3px;
    padding: 16px 20px; font-size: 14px; color: #1C2420; margin-top: 4px; }
  .verdict .vtick { font-family: Georgia, serif; font-weight: 700; font-size: 18px;
    margin-right: 10px; }
  .verdict .vnote { display: block; margin-top: 6px; color: #3E4742; font-style: italic; }
  .briefcols { display: flex; gap: 18px; flex-wrap: wrap; border-top: 2px solid #1C2420;
    border-bottom: 1px solid #1C2420; padding: 4px 0 14px; margin-bottom: 6px; }
  .briefcol { flex: 1; min-width: 200px; padding-top: 10px; }
  .briefcol .bnum { font-family: Georgia, serif; font-size: 13px; color: #B07C24;
    font-weight: 700; }
  .briefcol .bt { font-family: Georgia, serif; font-weight: 700; font-size: 16px;
    color: #1C2420; margin: 2px 0 4px; }
  .briefcol .bd { font-size: 13px; color: #3E4742; line-height: 1.55; }
  .inside { border-top: 1px solid #D9DCD4; }
  .insiderow { display: flex; justify-content: space-between; align-items: baseline;
    gap: 12px; padding: 10px 2px; border-bottom: 1px solid #D9DCD4; font-size: 14px;
    color: #5A645E; }
  .insiderow b { font-family: Georgia, serif; color: #1C2420; font-size: 16px; }
  .deskmast h2 { font-family: Georgia, serif; font-size: 30px; color: #1C2420; margin: 0 0 4px; }
  .deskmast p { color: #5A645E; font-style: italic; font-size: 14px; margin: 0 0 14px; }
  .ledgerdesk { border-top: 3px solid #1C2420; padding-top: 4px; }
  .clerkrail { background: #EFF1EC; border: 1px solid #D9DCD4; border-radius: 3px;
    padding: 18px 20px; position: sticky; top: 12px; }
  .railrow { display: flex; justify-content: space-between; gap: 12px; font-size: 13px;
    color: #5A645E; padding: 7px 0; border-bottom: 1px solid #D9DCD4; }
  .railrow b { color: #1C2420; font-family: Georgia, serif; }
  .railnote { font-size: 12.5px; font-style: italic; color: #5A645E; margin: 10px 0 14px;
    line-height: 1.55; }
  .dossier { border-top: 2px solid #1C2420; margin-top: 26px; padding-top: 6px; }
  .ledger { width: 100%; border-collapse: collapse; font-size: 13.5px; margin-top: 8px;
    border-top: 2px solid #1C2420; }
  .ledger th { text-align: left; font-size: 11px; text-transform: uppercase;
    letter-spacing: .1em; color: #5A645E; border-bottom: 2px solid #1C2420;
    padding: 8px 10px; }
  .ledger td { border-bottom: 1px solid #D9DCD4; padding: 10px; color: #1C2420;
    vertical-align: top; }
  .ledger .lednote { font-size: 12px; font-style: italic; color: #5A645E; }
  .ledrow.urgent td { background: #F7E9E4; border-left: 3px solid #A63D2F; }
  .ledrow.past td { color: #5A645E; background: #EFF1EC; }
  .ledrow.due-next td { border-left: 3px solid #1D5C3F; }
  .ledstamp { display: inline-block; font-size: 10px; font-weight: 800;
    letter-spacing: .12em; padding: 3px 8px; border-radius: 2px; color: #FFFFFF; }
  .ledstamp.urgent { background: #A63D2F; }
  .ledstamp.past { background: #5A645E; }
  .ledstamp.duenext { background: #1D5C3F; }
  .faq-desk h1 { font-family: Georgia, 'Times New Roman', serif; font-size: 46px;
    line-height: 1.1; color: #1C2420; margin: 6px 0 10px; }
  .faq-desk h1 em { font-weight: 400; color: #1D5C3F; }
  .faq-folio { font-size: 11px; font-weight: 700; letter-spacing: .16em;
    text-transform: uppercase; color: #5A645E; border-top: 3px solid #1C2420;
    border-bottom: 1px solid #1C2420; padding: 8px 2px; margin-bottom: 14px; }
  .faq-lede { font-family: Georgia, serif; font-style: italic; font-size: 17px;
    color: #3E4742; max-width: 640px; }
  .faqindex b { font-family: Georgia, serif; color: #1D5C3F; margin-right: 8px; }
  .ch { display: grid; grid-template-columns: 76px 1fr 185px; gap: 22px;
    align-items: start; border-top: 1px solid #D9DCD4; padding: 20px 2px 24px; }
  .ch-alt { grid-template-columns: 185px 1fr 76px; }
  .ch-alt .folio { order: 3; }
  .ch-alt .dropcap { order: 2; }
  .ch-alt .mnote { order: 1; border-left: none; border-right: 2px solid #1D5C3F;
    text-align: right; }
  .folio { font-family: Georgia, serif; font-size: 44px; line-height: 1;
    color: #1D5C3F; border-bottom: 3px solid #1C2420; padding-bottom: 8px; }
  .dropcap { font-size: 15px; line-height: 1.7; color: #1C2420; margin: 0; }
  .dropcap::first-letter { font-family: Georgia, serif; font-weight: 700;
    font-size: 3.1em; float: left; line-height: .85; padding: 4px 8px 0 0;
    color: #1D5C3F; }
  .mnote { font-size: 12px; font-style: italic; line-height: 1.6; color: #5A645E;
    border-left: 2px solid #1D5C3F; padding-left: 12px; }
  .faq-colophon { text-align: center; font-size: 12px; font-style: italic;
    color: #5A645E; border-top: 3px solid #1C2420; margin-top: 8px; padding: 14px 0 4px; }
  @media (max-width: 760px) {
    .ch, .ch-alt { grid-template-columns: 1fr; }
    .ch-alt .folio, .ch-alt .dropcap, .ch-alt .mnote { order: initial; }
    .ch-alt .mnote { border-right: none; border-left: 2px solid #1D5C3F; text-align: left; }
  }
</style>
"""

_NORDIC_CSS = """<style>
  /* Nordic Minimalist: off-white SaaS, navy ink, emerald accents. */
  [data-testid="stAppViewContainer"] { background: #F8F9FA; color: #0F172A; }
  html, body, [class*="css"], .stMarkdown, .stText, button, input {
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI",
    system-ui, Helvetica, Arial, sans-serif; }
  h1, h2, h3 { font-family: Inter, -apple-system, "Segoe UI", system-ui, sans-serif;
    font-weight: 800; letter-spacing: -0.02em; color: #0F172A; }
  .appview-container .main .block-container { max-width: 1080px; }
  .nameplate { text-align: left; padding: 6px 0 2px; }
  .np-brand { font-family: Inter, system-ui, sans-serif; font-weight: 800;
    font-size: 30px; letter-spacing: -0.02em; color: #0F172A;
    display: inline-flex; align-items: center; gap: 10px; }
  .np-brand .brandlogo { width: 34px; height: 34px; border-radius: 8px; border: none;
    object-fit: contain; background: #0F172A; flex: none; padding: 3px; }
  .np-brand .mark { width: 12px; height: 12px; border-radius: 3px; background: #10B981; }
  .np-tag { color: #64748B; font-size: 13px; margin-top: 0; font-style: normal; }
  .secrule { border-top: 1px solid #E2E8F0; margin: 10px 0; }
  .secrule.thick { border-top: 2px solid #0F172A; }
  .dateline { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap;
    font-size: 12px; color: #64748B; padding: 8px 2px 18px; }
  [data-testid="stSidebar"] { display: flex; background: #FFFFFF;
    border-right: 1px solid #E2E8F0; }
  .deckhead { font-family: Inter, system-ui, sans-serif; font-weight: 800;
    font-size: 18px; color: #0F172A; }
  .decklabel { font-size: 11px; font-weight: 700; letter-spacing: .08em;
    text-transform: uppercase; color: #10B981; margin: 14px 0 8px;
    border-bottom: 1px solid #E2E8F0; padding-bottom: 6px; }
  .pill { background: #ECFDF5; border: 1px solid #A7F3D0; border-radius: 999px;
    color: #047857; font-size: 12px; font-weight: 600; letter-spacing: .02em;
    padding: 5px 14px; }
  .pulse { background: #10B981; border-radius: 50%; animation: none; }
  .lead { padding: 20px 2px 6px; }
  .lead h1 { font-size: 52px; max-width: 780px; color: #0F172A; margin: 0 0 12px; }
  .lead h1 .green { color: #10B981; }
  .lead .lede { font-size: 17px; line-height: 1.6; color: #475569; max-width: 640px; }
  .countdown { font-size: 14px; color: #64748B; margin-top: 12px; }
  .countdown b { font-size: 26px; font-weight: 800; color: #E11D48; margin-right: 6px; }
  .hero { background: none; border: none; padding: 20px 2px 14px; }
  .hero h1 { color: #0F172A; font-size: 44px; max-width: 780px; }
  .hero h1 .green { color: #10B981; }
  .hero p { color: #475569; font-size: 16px; }
  .brief { background: #0F172A; color: #F1F5F9; border-radius: 12px; padding: 22px 24px;
    box-shadow: 0 4px 14px rgba(15,23,42,.12); }
  .brief .brieftitle { font-weight: 800; font-size: 16px; margin-bottom: 6px; }
  .brief ol { margin: 0; padding-left: 20px; }
  .brief li { font-size: 13.5px; line-height: 1.6; margin: 8px 0; color: #CBD5E1; }
  .brief li b { color: #FFFFFF; }
  .briefcols { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 6px; }
  .briefcol { flex: 1; min-width: 200px; background: #FFFFFF; border: 1px solid #E2E8F0;
    border-radius: 12px; padding: 16px 18px; box-shadow: 0 1px 2px rgba(15,23,42,.05); }
  .briefcol .bnum { font-size: 12px; font-weight: 800; color: #10B981; }
  .briefcol .bt { font-weight: 800; font-size: 15px; color: #0F172A; margin: 2px 0 4px; }
  .briefcol .bd { font-size: 13px; color: #475569; line-height: 1.55; }
  .tape { border-top: 1px solid #E2E8F0; border-bottom: 1px solid #E2E8F0;
    background: #FFFFFF; }
  .tick { color: #64748B; }
  .tick b { color: #0F172A; font-weight: 700; }
  .up { color: #10B981; } .down { color: #E11D48; }
  .section-title { font-size: 22px; font-weight: 800; color: #0F172A;
    padding-bottom: 8px; margin-bottom: 6px; }
  .section-sub { color: #64748B; font-size: 14px; }
  .steps { display: flex; gap: 14px; flex-wrap: wrap; }
  .step { flex: 1; min-width: 200px; background: #FFFFFF; border: 1px solid #E2E8F0;
    border-radius: 12px; padding: 20px; box-shadow: 0 1px 2px rgba(15,23,42,.05); }
  .step .n { background: #ECFDF5; color: #047857; width: 34px; height: 34px;
    border-radius: 10px; font-weight: 800; }
  .step .t { color: #0F172A; font-weight: 700; font-size: 15px; }
  .step .d { color: #475569; font-size: 13.5px; }
  .takeaways { border: 1px solid #E2E8F0; border-radius: 12px; padding: 20px 22px;
    background: #FFFFFF; box-shadow: 0 1px 2px rgba(15,23,42,.05); }
  .takeaways .takehead { font-weight: 800; font-size: 15px; margin-bottom: 8px;
    color: #0F172A; }
  .takeaways li { font-size: 13.5px; color: #475569; margin: 6px 0; line-height: 1.55; }
  .pullquote { text-align: center; font-size: 28px; font-weight: 800;
    letter-spacing: -0.02em; line-height: 1.3; color: #0F172A;
    padding: 28px 20px 4px; max-width: 760px; margin: 0 auto; }
  .pullquote span { display: block; font-size: 13px; font-weight: 400;
    color: #64748B; margin-top: 10px; }
  .inside { border-top: 1px solid #E2E8F0; }
  .insiderow { display: flex; justify-content: space-between; align-items: baseline;
    gap: 12px; padding: 12px 2px; border-bottom: 1px solid #E2E8F0; font-size: 14px;
    color: #64748B; }
  .insiderow b { color: #0F172A; font-size: 15px; }
  .statband { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 22px; }
  .stat { flex: 1; min-width: 200px; background: #FFFFFF; border: 1px solid #E2E8F0;
    border-radius: 12px; padding: 16px 18px; box-shadow: 0 1px 2px rgba(15,23,42,.05); }
  .stat .v { font-size: 24px; font-weight: 800; color: #0F172A; }
  .stat .l { font-size: 13px; color: #64748B; }
  .card, .takeaways { background: #FFFFFF; }
  .card { border: 1px solid #E2E8F0; border-radius: 12px; }
  .note .t { color: #0F172A; font-weight: 800; }
  .note .d { color: #475569; }
  .verdict { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 12px;
    padding: 16px 20px; font-size: 14px; color: #0F172A; margin-top: 4px;
    box-shadow: 0 1px 2px rgba(15,23,42,.05); }
  .verdict .vtick { font-weight: 800; font-size: 17px; margin-right: 10px; }
  .verdict .vnote { display: block; margin-top: 6px; color: #475569; }
  .deskmast h2 { font-size: 28px; color: #0F172A; margin: 0 0 4px; }
  .deskmast p { color: #64748B; font-size: 14px; margin: 0 0 14px; }
  .ledgerdesk { border: none; padding-top: 0; }
  .clerkrail { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 12px;
    padding: 18px 20px; box-shadow: 0 1px 2px rgba(15,23,42,.05); }
  .railrow { display: flex; justify-content: space-between; gap: 12px; font-size: 13px;
    color: #64748B; padding: 7px 0; border-bottom: 1px solid #F1F5F9; }
  .railrow b { color: #0F172A; }
  .railnote { font-size: 12.5px; color: #64748B; margin: 10px 0 14px; line-height: 1.55; }
  .dossier { margin-top: 22px; padding-top: 0; }
  .stepper { display: flex; align-items: center; gap: 10px; margin: 2px 0 18px; }
  .stp { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 700;
    color: #94A3B8; white-space: nowrap; }
  .stp span { width: 24px; height: 24px; border-radius: 50%; background: #F1F5F9;
    color: #64748B; display: inline-flex; align-items: center; justify-content: center;
    font-size: 12px; }
  .stp.done { color: #047857; }
  .stp.done span { background: #10B981; color: #FFFFFF; }
  .stp.current { color: #0F172A; }
  .stp.current span { background: #0F172A; color: #FFFFFF; }
  .stpline { flex: 1; border-top: 2px solid #E2E8F0; min-width: 20px; }
  .bigmetrics { display: flex; gap: 12px; flex-wrap: wrap; margin: 6px 0 12px; }
  .bigmetric { flex: 1; min-width: 170px; background: #FFFFFF;
    border: 1px solid #E2E8F0; border-radius: 12px; padding: 16px 18px;
    box-shadow: 0 1px 2px rgba(15,23,42,.05); }
  .bigmetric .v { font-size: 26px; font-weight: 800; color: #0F172A; }
  .bigmetric .l { font-size: 12.5px; color: #64748B; margin-top: 3px; }
  .badge { border-radius: 999px; font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: .06em; }
  .badge.long { background: #10B981; color: #FFFFFF; }
  .badge.short { background: #F59E0B; color: #FFFFFF; }
  .memo { background: #0F172A; color: #F1F5F9; border-radius: 12px;
    padding: 22px 26px; margin: 4px 0 16px; box-shadow: 0 4px 14px rgba(15,23,42,.12); }
  .memo .memotitle { font-size: 11px; font-weight: 700; letter-spacing: .1em;
    text-transform: uppercase; color: #6EE7B7; margin-bottom: 8px; }
  .memo .memobody { font-size: 17px; line-height: 1.55; }
  .memo .memobody b { color: #FFFFFF; }
  .stamp { background: #F59E0B; }
  .faq { background: none; border: none; padding: 0; }
  div[data-testid="stExpander"] { border: 1px solid #E2E8F0; border-radius: 12px;
    background: #FFFFFF; box-shadow: 0 1px 2px rgba(15,23,42,.05); }
  .faq-desk h1 { font-size: 42px; color: #0F172A; margin: 6px 0 10px; }
  .faq-desk h1 em { font-style: normal; color: #10B981; }
  .faq-folio { font-size: 11px; font-weight: 700; letter-spacing: .12em;
    text-transform: uppercase; color: #64748B; border-top: 2px solid #0F172A;
    border-bottom: 1px solid #E2E8F0; padding: 8px 2px; margin-bottom: 14px; }
  .faq-lede { font-size: 16px; color: #475569; max-width: 640px; }
  .faqindex { border-top: 2px solid #0F172A; padding-top: 6px; }
  .faqindex .indextitle { font-size: 11px; font-weight: 700; letter-spacing: .1em;
    text-transform: uppercase; color: #10B981; margin-bottom: 4px; }
  .faqindex a { color: #047857; font-size: 13px; text-decoration: none; display: block;
    padding: 6px 0; border-bottom: 1px solid #F1F5F9; }
  .faqindex a:hover { color: #0F172A; }
  .faqindex b { color: #047857; margin-right: 8px; }
  .ch { display: grid; grid-template-columns: 64px 1fr 185px; gap: 20px;
    align-items: start; border-top: 1px solid #E2E8F0; padding: 20px 2px 24px; }
  .folio { font-size: 36px; font-weight: 800; letter-spacing: -0.02em; color: #10B981; }
  .dropcap { font-size: 15px; line-height: 1.7; color: #0F172A; margin: 0; }
  .dropcap::first-letter { font-weight: 800; font-size: 2.6em; float: left;
    line-height: .9; padding: 2px 8px 0 0; color: #10B981; }
  .mnote { font-size: 12px; line-height: 1.6; color: #64748B;
    border-left: 3px solid #10B981; padding-left: 12px;
    background: #F8F9FA; border-radius: 0 8px 8px 0; padding-top: 8px; padding-bottom: 8px; }
  .faq-colophon { text-align: center; font-size: 12px; color: #64748B;
    border-top: 2px solid #0F172A; margin-top: 8px; padding: 14px 0 4px; }
  .pltrack { background: #EDF2F7; }
  .pltick { color: #0F172A; font-weight: 700; }
  .plfill.neg { background: #E11D48; } .plfill.pos { background: #10B981; }
  .plval.neg { color: #E11D48; } .plval.pos { color: #047857; }
  .alloc { background: #EDF2F7; }
  .legrow { color: #64748B; } .legrow b { color: #0F172A; }
  .brackbar { background: #EDF2F7; border: 1px solid #E2E8F0; border-radius: 10px; }
  .seg { border-left: 1px solid #E2E8F0; }
  .segfill { background: #10B981; }
  .segrate { color: #0F172A; }
  .seg.hot .segrate { color: #FFFFFF; background: #0F172A; }
  .marker { background: #0F172A; }
  .marker span { background: #0F172A; }
  .bracktable th { color: #64748B; border-bottom: 2px solid #0F172A; }
  .bracktable td { border-bottom: 1px solid #F1F5F9; color: #0F172A; }
  .ledger { border-top: 2px solid #0F172A; }
  .ledger th { color: #64748B; border-bottom: 2px solid #0F172A; }
  .ledger td { border-bottom: 1px solid #F1F5F9; color: #0F172A; }
  .ledger .lednote { color: #64748B; }
  .ledrow.urgent td { background: #FFF1F2; border-left: 3px solid #E11D48; }
  .ledrow.past td { color: #64748B; background: #F8F9FA; }
  .ledrow.due-next td { border-left: 3px solid #10B981; }
  .ledstamp.urgent { background: #E11D48; }
  .ledstamp.past { background: #64748B; }
  .ledstamp.duenext { background: #10B981; }
  .covtrack { background: #EDF2F7; }
  .covfill { background: #10B981; }
  .covhead { color: #0F172A; } .covhead span { color: #64748B; }
  div[data-testid="stMetric"] { background: #FFFFFF; border: 1px solid #E2E8F0;
    border-radius: 12px; box-shadow: 0 1px 2px rgba(15,23,42,.05); }
  div[data-testid="stCaptionContainer"] { color: #64748B; }
  div[data-testid="stDataFrame"] { border: 1px solid #E2E8F0; border-radius: 12px; }
  div[data-testid="stTabs"] button { font-weight: 700; color: #64748B; }
  div[data-testid="stTabs"] button[aria-selected="true"] { color: #047857; }
  div.stButton > button { border: 1px solid #E2E8F0; border-radius: 8px; background: #FFFFFF;
    color: #0F172A; font-weight: 600; padding: 9px 18px; text-decoration: none; }
  div.stButton > button:hover { border-color: #0F172A; color: #0F172A; background: #FFFFFF; }
  div.stButton > button[kind="primary"] { background: #10B981; border: none;
    border-radius: 8px; color: #FFFFFF; padding: 11px 24px; font-weight: 700;
    box-shadow: 0 2px 8px rgba(16,185,129,.35); }
  div.stButton > button[kind="primary"]:hover { background: #059669; color: #FFFFFF; }
  div.stButton > button:disabled { background: #0F172A; border: none; opacity: 1;
    color: #FFFFFF; text-decoration: none; }
  div.stButton > button[kind="primary"]:disabled { background: #10B981; color: #FFFFFF; }
  footer.site { border-top: 2px solid #0F172A; color: #64748B; }
  footer.site .brand { color: #0F172A; font-weight: 800; }
  footer.site .fhead { color: #0F172A; font-size: 11px; letter-spacing: .1em;
    text-transform: uppercase; }
  .fine { color: #94A3B8; }
  @media (max-width: 760px) {
    .ch { grid-template-columns: 1fr; }
  }
</style>
"""

_TERMINAL_CSS = """<style>
  /* Terminal Dark: Nordic neutrals inverted — near-black ground, light ink,
     slate gray text. Accent hues (emerald/gold/red) stay untouched. */
  [data-testid="stAppViewContainer"] { background: #0A0C0E; color: #EDEFF3; }
  html, body, [class*="css"], .stMarkdown, .stText, button, input {
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI",
    system-ui, Helvetica, Arial, sans-serif; }
  h1, h2, h3 { font-family: Inter, -apple-system, "Segoe UI", system-ui, sans-serif;
    font-weight: 800; letter-spacing: -0.02em; color: #EDEFF3; }
  .appview-container .main .block-container { max-width: 1080px; }
  .nameplate { text-align: left; padding: 6px 0 2px; }
  .np-brand { font-family: Inter, system-ui, sans-serif; font-weight: 800;
    font-size: 30px; letter-spacing: -0.02em; color: #EDEFF3;
    display: inline-flex; align-items: center; gap: 10px; }
  .np-brand .brandlogo { width: 34px; height: 34px; border-radius: 8px; border: none;
    object-fit: contain; background: #EDEFF3; flex: none; padding: 3px; }
  .np-brand .mark { width: 12px; height: 12px; border-radius: 3px; background: #10B981; }
  .np-tag { color: #8E99A8; font-size: 13px; margin-top: 0; font-style: normal; }
  .secrule { border-top: 1px solid #26303D; margin: 10px 0; }
  .secrule.thick { border-top: 2px solid #EDEFF3; }
  .dateline { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap;
    font-size: 12px; color: #8E99A8; padding: 8px 2px 18px; }
  [data-testid="stSidebar"] { display: flex; background: #0D1117;
    border-right: 1px solid #26303D; }
  .deckhead { font-family: Inter, system-ui, sans-serif; font-weight: 800;
    font-size: 18px; color: #EDEFF3; }
  .decklabel { font-size: 11px; font-weight: 700; letter-spacing: .08em;
    text-transform: uppercase; color: #34D399; margin: 14px 0 8px;
    border-bottom: 1px solid #26303D; padding-bottom: 6px; }
  .pill { background: #0C2B21; border: 1px solid #14532D; border-radius: 999px;
    color: #6EE7B7; font-size: 12px; font-weight: 600; letter-spacing: .02em;
    padding: 5px 14px; }
  .pulse { background: #34D399; border-radius: 50%; animation: none; }
  .lead { padding: 20px 2px 6px; }
  .lead h1 { font-size: 52px; max-width: 780px; color: #EDEFF3; margin: 0 0 12px; }
  .lead h1 .green { color: #34D399; }
  .lead .lede { font-size: 17px; line-height: 1.6; color: #B4BCC8; max-width: 640px; }
  .countdown { font-size: 14px; color: #8E99A8; margin-top: 12px; }
  .countdown b { font-size: 26px; font-weight: 800; color: #F43F5E; margin-right: 6px; }
  .hero { background: none; border: none; padding: 20px 2px 14px; }
  .hero h1 { color: #EDEFF3; font-size: 44px; max-width: 780px; }
  .hero h1 .green { color: #34D399; }
  .hero p { color: #B4BCC8; font-size: 16px; }
  .brief { background: #121924; border: 1px solid #26303D; color: #EDEFF3;
    border-radius: 12px; padding: 22px 24px; }
  .brief .brieftitle { font-weight: 800; font-size: 16px; margin-bottom: 6px; }
  .brief ol { margin: 0; padding-left: 20px; }
  .brief li { font-size: 13.5px; line-height: 1.6; margin: 8px 0; color: #B4BCC8; }
  .brief li b { color: #FFFFFF; }
  .briefcols { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 6px; }
  .briefcol { flex: 1; min-width: 200px; background: #141920; border: 1px solid #26303D;
    border-radius: 12px; padding: 16px 18px; }
  .briefcol .bnum { font-size: 12px; font-weight: 800; color: #34D399; }
  .briefcol .bt { font-weight: 800; font-size: 15px; color: #EDEFF3; margin: 2px 0 4px; }
  .briefcol .bd { font-size: 13px; color: #B4BCC8; line-height: 1.55; }
  .tape { border-top: 1px solid #26303D; border-bottom: 1px solid #26303D;
    background: #141920; }
  .tick { color: #8E99A8; }
  .tick b { color: #EDEFF3; font-weight: 700; }
  .up { color: #34D399; } .down { color: #F43F5E; }
  .section-title { font-size: 22px; font-weight: 800; color: #EDEFF3;
    padding-bottom: 8px; margin-bottom: 6px; }
  .section-sub { color: #8E99A8; font-size: 14px; }
  .steps { display: flex; gap: 14px; flex-wrap: wrap; }
  .step { flex: 1; min-width: 200px; background: #141920; border: 1px solid #26303D;
    border-radius: 12px; padding: 20px; }
  .step .n { background: #0C2B21; color: #6EE7B7; width: 34px; height: 34px;
    border-radius: 10px; font-weight: 800; }
  .step .t { color: #EDEFF3; font-weight: 700; font-size: 15px; }
  .step .d { color: #B4BCC8; font-size: 13.5px; }
  .takeaways { border: 1px solid #26303D; border-radius: 12px; padding: 20px 22px;
    background: #141920; }
  .takeaways .takehead { font-weight: 800; font-size: 15px; margin-bottom: 8px;
    color: #EDEFF3; }
  .takeaways li { font-size: 13.5px; color: #B4BCC8; margin: 6px 0; line-height: 1.55; }
  .pullquote { text-align: center; font-size: 28px; font-weight: 800;
    letter-spacing: -0.02em; line-height: 1.3; color: #EDEFF3;
    padding: 28px 20px 4px; max-width: 760px; margin: 0 auto; }
  .pullquote span { display: block; font-size: 13px; font-weight: 400;
    color: #8E99A8; margin-top: 10px; }
  .inside { border-top: 1px solid #26303D; }
  .insiderow { display: flex; justify-content: space-between; align-items: baseline;
    gap: 12px; padding: 12px 2px; border-bottom: 1px solid #26303D; font-size: 14px;
    color: #8E99A8; }
  .insiderow b { color: #EDEFF3; font-size: 15px; }
  .statband { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 22px; }
  .stat { flex: 1; min-width: 200px; background: #141920; border: 1px solid #26303D;
    border-radius: 12px; padding: 16px 18px; }
  .stat .v { font-size: 24px; font-weight: 800; color: #EDEFF3; }
  .stat .l { font-size: 13px; color: #8E99A8; }
  .card { background: #141920; border: 1px solid #26303D; border-radius: 12px; }
  .note .t { color: #EDEFF3; font-weight: 800; }
  .note .d { color: #B4BCC8; }
  .verdict { background: #141920; border: 1px solid #26303D; border-radius: 12px;
    padding: 16px 20px; font-size: 14px; color: #EDEFF3; margin-top: 4px; }
  .verdict .vtick { font-weight: 800; font-size: 17px; margin-right: 10px; }
  .verdict .vnote { display: block; margin-top: 6px; color: #B4BCC8; }
  .deskmast h2 { font-size: 28px; color: #EDEFF3; margin: 0 0 4px; }
  .deskmast p { color: #8E99A8; font-size: 14px; margin: 0 0 14px; }
  .ledgerdesk { border: none; padding-top: 0; }
  .clerkrail { background: #141920; border: 1px solid #26303D; border-radius: 12px;
    padding: 18px 20px; }
  .railrow { display: flex; justify-content: space-between; gap: 12px; font-size: 13px;
    color: #8E99A8; padding: 7px 0; border-bottom: 1px solid #1B222C; }
  .railrow b { color: #EDEFF3; }
  .railnote { font-size: 12.5px; color: #8E99A8; margin: 10px 0 14px; line-height: 1.55; }
  .dossier { margin-top: 22px; padding-top: 0; }
  .stepper { display: flex; align-items: center; gap: 10px; margin: 2px 0 18px; }
  .stp { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 700;
    color: #6E7787; white-space: nowrap; }
  .stp span { width: 24px; height: 24px; border-radius: 50%; background: #1B222C;
    color: #8E99A8; display: inline-flex; align-items: center; justify-content: center;
    font-size: 12px; }
  .stp.done { color: #34D399; }
  .stp.done span { background: #10B981; color: #06281D; }
  .stp.current { color: #EDEFF3; }
  .stp.current span { background: #EDEFF3; color: #0A0C0E; }
  .stpline { flex: 1; border-top: 2px solid #26303D; min-width: 20px; }
  .bigmetrics { display: flex; gap: 12px; flex-wrap: wrap; margin: 6px 0 12px; }
  .bigmetric { flex: 1; min-width: 170px; background: #141920;
    border: 1px solid #26303D; border-radius: 12px; padding: 16px 18px; }
  .bigmetric .v { font-size: 26px; font-weight: 800; color: #EDEFF3; }
  .bigmetric .l { font-size: 12.5px; color: #8E99A8; margin-top: 3px; }
  .badge { border-radius: 999px; font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: .06em; }
  .badge.long { background: #10B981; color: #06281D; }
  .badge.short { background: #F59E0B; color: #06281D; }
  .memo { background: #131B26; border: 1px solid #26303D; color: #EDEFF3;
    border-radius: 12px; padding: 22px 26px; margin: 4px 0 16px; }
  .memo .memotitle { font-size: 11px; font-weight: 700; letter-spacing: .1em;
    text-transform: uppercase; color: #6EE7B7; margin-bottom: 8px; }
  .memo .memobody { font-size: 17px; line-height: 1.55; }
  .memo .memobody b { color: #FFFFFF; }
  .stamp { background: #F59E0B; }
  .faq { background: none; border: none; padding: 0; }
  div[data-testid="stExpander"] { border: 1px solid #26303D; border-radius: 12px;
    background: #141920; }
  .faq-desk h1 { font-size: 42px; color: #EDEFF3; margin: 6px 0 10px; }
  .faq-desk h1 em { font-style: normal; color: #34D399; }
  .faq-folio { font-size: 11px; font-weight: 700; letter-spacing: .12em;
    text-transform: uppercase; color: #8E99A8; border-top: 2px solid #EDEFF3;
    border-bottom: 1px solid #26303D; padding: 8px 2px; margin-bottom: 14px; }
  .faq-lede { font-size: 16px; color: #B4BCC8; max-width: 640px; }
  .faqindex { border-top: 2px solid #EDEFF3; padding-top: 6px; }
  .faqindex .indextitle { font-size: 11px; font-weight: 700; letter-spacing: .1em;
    text-transform: uppercase; color: #34D399; margin-bottom: 4px; }
  .faqindex a { color: #34D399; font-size: 13px; text-decoration: none; display: block;
    padding: 6px 0; border-bottom: 1px solid #1B222C; }
  .faqindex a:hover { color: #EDEFF3; }
  .faqindex b { color: #34D399; margin-right: 8px; }
  .ch { display: grid; grid-template-columns: 64px 1fr 185px; gap: 20px;
    align-items: start; border-top: 1px solid #26303D; padding: 20px 2px 24px; }
  .folio { font-size: 36px; font-weight: 800; letter-spacing: -0.02em; color: #34D399; }
  .dropcap { font-size: 15px; line-height: 1.7; color: #EDEFF3; margin: 0; }
  .dropcap::first-letter { font-weight: 800; font-size: 2.6em; float: left;
    line-height: .9; padding: 2px 8px 0 0; color: #34D399; }
  .mnote { font-size: 12px; line-height: 1.6; color: #8E99A8;
    border-left: 3px solid #10B981; padding-left: 12px;
    background: #10151C; border-radius: 0 8px 8px 0; padding-top: 8px; padding-bottom: 8px; }
  .faq-colophon { text-align: center; font-size: 12px; color: #8E99A8;
    border-top: 2px solid #EDEFF3; margin-top: 8px; padding: 14px 0 4px; }
  .pltrack { background: #1E242E; }
  .pltick { color: #EDEFF3; font-weight: 700; }
  .plfill.neg { background: #F43F5E; } .plfill.pos { background: #10B981; }
  .plval.neg { color: #F43F5E; } .plval.pos { color: #34D399; }
  .alloc { background: #1E242E; }
  .legrow { color: #8E99A8; } .legrow b { color: #EDEFF3; }
  .brackbar { background: #1E242E; border: 1px solid #26303D; border-radius: 10px; }
  .seg { border-left: 1px solid #26303D; }
  .segfill { background: #10B981; }
  .segrate { color: #EDEFF3; }
  .seg.hot .segrate { color: #06281D; background: #10B981; }
  .marker { background: #EDEFF3; }
  .marker span { background: #EDEFF3; color: #0A0C0E; }
  .bracktable th { color: #8E99A8; border-bottom: 2px solid #EDEFF3; }
  .bracktable td { border-bottom: 1px solid #1B222C; color: #EDEFF3; }
  .ledger { border-top: 2px solid #EDEFF3; }
  .ledger th { color: #8E99A8; border-bottom: 2px solid #EDEFF3; }
  .ledger td { border-bottom: 1px solid #1B222C; color: #EDEFF3; }
  .ledger .lednote { color: #8E99A8; }
  .ledrow.urgent td { background: #2A1218; border-left: 3px solid #F43F5E; }
  .ledrow.past td { color: #8E99A8; background: #10151C; }
  .ledrow.due-next td { border-left: 3px solid #10B981; }
  .ledstamp.urgent { background: #F43F5E; }
  .ledstamp.past { background: #6E7787; }
  .ledstamp.duenext { background: #10B981; }
  .covtrack { background: #1E242E; }
  .covfill { background: #10B981; }
  .covhead { color: #EDEFF3; } .covhead span { color: #8E99A8; }
  div[data-testid="stMetric"] { background: #141920; border: 1px solid #26303D;
    border-radius: 12px; }
  div[data-testid="stCaptionContainer"] { color: #8E99A8; }
  div[data-testid="stDataFrame"] { border: 1px solid #26303D; border-radius: 12px; }
  div[data-testid="stTabs"] button { font-weight: 700; color: #8E99A8; }
  div[data-testid="stTabs"] button[aria-selected="true"] { color: #34D399; }
  div.stButton > button { border: 1px solid #26303D; border-radius: 8px; background: transparent;
    color: #EDEFF3; font-weight: 600; padding: 9px 18px; text-decoration: none; }
  div.stButton > button:hover { border-color: #34D399; color: #34D399; background: none; }
  div.stButton > button[kind="primary"] { background: #10B981; border: none;
    border-radius: 8px; color: #06281D; padding: 11px 24px; font-weight: 700; }
  div.stButton > button[kind="primary"]:hover { background: #34D399; color: #06281D; }
  div.stButton > button:disabled { background: #1B222C; border: none; opacity: 1;
    color: #8E99A8; text-decoration: none; }
  div.stButton > button[kind="primary"]:disabled { background: #10B981; color: #06281D; }
  footer.site { border-top: 2px solid #26303D; color: #8E99A8; }
  footer.site .brand { color: #EDEFF3; font-weight: 800; }
  footer.site .fhead { color: #EDEFF3; font-size: 11px; letter-spacing: .1em;
    text-transform: uppercase; }
  .fine { color: #6E7787; }
  @media (max-width: 760px) {
    .ch { grid-template-columns: 1fr; }
  }
</style>
"""

for key, default in (("page", "Home"), ("rows", None), ("portfolio", None),
                      ("csv_live", {}), ("alt_cache", {})):
    if key not in st.session_state:
        st.session_state[key] = default

# ------------------------------ themes ------------------------------
if "theme" not in st.session_state:
    st.session_state.theme = "ledger"
theme = st.session_state.theme
if theme == "ledger":
    st.markdown(_LEDGER_CSS, unsafe_allow_html=True)
elif theme == "nordic":
    st.markdown(_NORDIC_CSS, unsafe_allow_html=True)
elif theme == "terminal":
    st.markdown(_TERMINAL_CSS, unsafe_allow_html=True)

if theme == "terminal":
    GREEN, RED, GOLD = "#34D399", "#F43F5E", "#F59E0B"
    INK, MUTED = "#EDEFF3", "#8E99A8"
    ALLOC_COLORS = ["#34D399", "#EDEFF3", "#38BDF8", "#F59E0B",
                    "#F43F5E", "#64748B", "#6EE7B7", "#334155"]
elif theme == "nordic":
    GREEN, RED, GOLD = "#10B981", "#E11D48", "#F59E0B"
    INK, MUTED = "#0F172A", "#64748B"
    ALLOC_COLORS = ["#10B981", "#0F172A", "#38BDF8", "#F59E0B",
                    "#E11D48", "#94A3B8", "#34D399", "#CBD5E1"]
if st.session_state.rows is None:
    st.session_state.rows = [{"ticker": "AAPL", "shares": 10.0, "buy": 0.0,
                              "date": datetime.date(2023, 6, 1)}]


def go(page):
    st.session_state.page = page


def money(n):
    return f"${n:,.2f}"


def _triage_verdict(ticker):
    """One-ticker harvest verdict: live price vs ~1yr ago. Dict or None."""
    import datetime as dt
    try:
        now = dt.datetime.now()
        live = rt.fetch_live_prices([ticker]).get(ticker)
        then = rt.get_price_at(ticker, now - dt.timedelta(days=365))
        if not live or not then:
            return None
        chg = (live - then) / then * 100.0
        if chg <= -15:
            note = "deep red — prime harvest material if you hold it at a loss"
        elif chg <= -5:
            note = "bruised — worth a look if you hold it"
        elif chg < 5:
            note = "flat on the year — probably nothing to harvest"
        else:
            note = "running green — no loss to harvest here"
        return {"ticker": ticker, "price": live, "chg": chg, "note": note}
    except Exception:
        return None


def market_status(now=None):
    """NYSE session snapshot for the dateline. Holidays ignored; times in ET.
    Accepts an explicit datetime for tests, otherwise uses America/New_York."""
    import datetime as dt
    try:
        from zoneinfo import ZoneInfo
        now = now or dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = now or dt.datetime.now()

    def fmt(delta):
        s = max(int(delta.total_seconds()), 0)
        d, s = divmod(s, 86400)
        h, s = divmod(s, 3600)
        return (f"{d}d " if d else "") + f"{h}h {s // 60:02d}m"

    open_t, close_t = dt.time(9, 30), dt.time(16, 0)
    if now.weekday() < 5 and open_t <= now.time() < close_t:
        close_at = now.replace(hour=16, minute=0, second=0, microsecond=0)
        return f"NYSE Open · closes in {fmt(close_at - now)}"
    day_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now.weekday() < 5 and now.time() < open_t and day_open > now:
        return f"NYSE Closed · opens in {fmt(day_open - now)}"
    add = 1 if now.weekday() < 4 else (3 if now.weekday() == 4 else 7 - now.weekday())
    nxt = (now + dt.timedelta(days=add)).replace(hour=9, minute=30, second=0,
                                                 microsecond=0)
    return f"NYSE Closed · opens in {fmt(nxt - now)}"


DEADLINES = [
    ("Q1 estimates", datetime.date(2026, 4, 15), "2026 Q1 estimated tax due"),
    ("Q2 estimates", datetime.date(2026, 6, 15), "2026 Q2 estimated tax due"),
    ("Q3 estimates", datetime.date(2026, 9, 15), "2026 Q3 estimated tax due"),
    ("Harvest cutoff", datetime.date(2026, 12, 31), "Last day to realize 2026 losses"),
    ("Q4 estimates", datetime.date(2027, 1, 15), "2026 Q4 estimated tax due"),
    ("Filing", datetime.date(2027, 4, 15), "2026 return due"),
]


def deadline_rows(today=None):
    """Countdown rows for the 2026 tax year. Each dict has label, date, iso,
    days_left, state, note. State: 'past', 'urgent' (<31d), or 'due-next'."""
    today = today or datetime.date.today()
    rows = []
    for label, day, note in DEADLINES:
        days_left = (day - today).days
        if days_left < 0:
            state = "past"
        elif days_left < 31:
            state = "urgent"
        else:
            state = "due-next"
        rows.append({"label": label, "date": day, "iso": day.isoformat(),
                     "days_left": days_left, "state": state, "note": note})
    return rows


def footer():
    year = datetime.date.today().year
    st.markdown(f"""
    <footer class="site">
      <div class="frow">
        <div class="fcol">
          <div class="brand">HRVST</div>
          <div class="fnote">Tax-loss harvesting on the positions<br>you actually hold.</div>
        </div>
        <div class="fcol">
          <div class="fhead">Product</div>
          <div class="fnote">Portfolio analysis<br>Tax estimates<br>Position review</div>
        </div>
        <div class="fcol">
          <div class="fhead">Data</div>
          <div class="fnote">Buy-date and live quotes via Yahoo Finance.<br>
          Session-only: holdings never leave your browser.</div>
        </div>
        <div class="fcol">
          <div class="fhead">Compliance</div>
          <div class="fnote">For education only; not tax advice.<br>
          Consult a qualified tax advisor. Wash-sale rules apply.</div>
        </div>
      </div>
      <div class="fnote">&copy; {year} HRVST. All rights reserved.</div>
    </footer>
    """, unsafe_allow_html=True)
    _f1, _f2, _f3 = st.columns(3)
    if _f1.button("Home", use_container_width=True, key="foot_go_home"):
        go("Home"); st.rerun()
    if _f2.button("Analyze", use_container_width=True, key="foot_go_analyze"):
        go("Analyze"); st.rerun()
    if _f3.button("FAQ", use_container_width=True, key="foot_go_faq"):
        go("FAQ"); st.rerun()


ALLOC_COLORS = ["#1D5C3F", "#1C2420", "#B07C24", "#A63D2F",
                  "#5B8C5A", "#7A6C4F", "#3E7CB1", "#8A8A8A"]


def gain_loss_chart(positions):
    """Crisp CSS bars (no chart-library version to break). Hover for details."""
    import html as html_lib
    ordered = sorted(positions, key=lambda p: p["unrealized_gain_loss"])
    peak = max([abs(p["unrealized_gain_loss"]) for p in ordered] + [1.0])
    rows = []
    for p in ordered:
        pl = p["unrealized_gain_loss"]
        cls = "neg" if pl < 0 else "pos"
        width = abs(pl) / peak * 100
        tip = (f"{p['ticker']}: {p['shares']:g} shares, paid ${p['buy_price']:,.2f}, "
               f"now ${p['current_price']:,.2f}, P/L ${pl:,.2f}")
        rows.append(
            f'<div class="plrow"><span class="pltick">{html_lib.escape(p["ticker"])}</span>'
            f'<div class="pltrack"><div class="plfill {cls}" style="width:{width:.1f}%" '
            f'title="{html_lib.escape(tip)}"></div></div>'
            f'<span class="plval {cls}">${pl:,.2f}</span></div>')
    return "".join(rows)


def allocation_chart(positions):
    """Allocation strip + legend. Pure HTML, renders everywhere."""
    import html as html_lib
    items = [p for p in positions if p["current_value"] > 0]
    total = sum(p["current_value"] for p in items) or 1.0
    segs, legend = [], []
    for i, p in enumerate(sorted(items, key=lambda p: p["current_value"], reverse=True)):
        color = ALLOC_COLORS[i % len(ALLOC_COLORS)]
        pct = p["current_value"] / total * 100
        segs.append(f'<div style="width:{pct:.1f}%;background:{color}" '
                    f'title="{html_lib.escape(p["ticker"])}: ${p["current_value"]:,.2f}"></div>')
        legend.append(
            f'<div class="legrow"><span class="sw" style="background:{color}"></span>'
            f'<b>{html_lib.escape(p["ticker"])}</b>'
            f'<span>${p["current_value"]:,.2f} &middot; {pct:.1f}%</span></div>')
    return '<div class="alloc">' + "".join(segs) + "</div>" + "".join(legend)


HERO_CHART = """
<canvas id="hrvst-hero" width="960" height="280" style="width:100%;height:auto;display:block;"></canvas>
<script>
(function() {
  var data = [0,120,310,280,540,900,860,1240,1610,1550,1980,2360,2310,2780,3250,3190,3710,4210];
  var cv = document.getElementById("hrvst-hero");
  if (!cv || !cv.getContext) return;
  var dpr = window.devicePixelRatio || 1;
  var W = 960, H = 280, padL = 8, padR = 92, padT = 20, padB = 30;
  cv.width = W * dpr; cv.height = H * dpr;
  var ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
  var max = Math.max.apply(null, data) * 1.08;
  var X = function(i){ return padL + i * (W - padL - padR) / (data.length - 1); };
  var Y = function(v){ return H - padB - (v / max) * (H - padT - padB); };
  ctx.strokeStyle = "#E4E7DF"; ctx.lineWidth = 1;
  for (var g = 0; g <= 4; g++) {
    var gy = padT + g * (H - padT - padB) / 4;
    ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(W - padR, gy); ctx.stroke();
  }
  var pts = data.map(function(v, i){ return [X(i), Y(v)]; });
  ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
  for (var i = 1; i < pts.length - 1; i++) {
    var xc = (pts[i][0] + pts[i+1][0]) / 2, yc = (pts[i][1] + pts[i+1][1]) / 2;
    ctx.quadraticCurveTo(pts[i][0], pts[i][1], xc, yc);
  }
  ctx.lineTo(pts[pts.length-1][0], pts[pts.length-1][1]);
  ctx.strokeStyle = "#1D5C3F"; ctx.lineWidth = 2.5; ctx.stroke();
  ctx.lineTo(pts[pts.length-1][0], H - padB); ctx.lineTo(pts[0][0], H - padB);
  ctx.closePath();
  ctx.fillStyle = "rgba(29,92,63,0.12)"; ctx.fill();
  var dx = X(6);
  ctx.setLineDash([5,4]); ctx.strokeStyle = "#A63D2F"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(dx, padT); ctx.lineTo(dx, H - padB); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#A63D2F"; ctx.font = "700 12px Georgia, serif";
  ctx.fillText("dip \\u2192 harvest", dx + 8, padT + 14);
  var last = pts[pts.length-1];
  ctx.fillStyle = "#1C2420"; ctx.font = "700 20px Georgia, serif";
  ctx.fillText("$4,210", last[0] + 10, last[1] + 7);
  ctx.fillStyle = "#5A645E"; ctx.font = "12px -apple-system, 'Segoe UI', sans-serif";
  ctx.fillText("banked", last[0] + 10, last[1] + 24);
  ctx.fillText("Jan", X(0), H - 10);
  ctx.fillText("Dec", X(data.length-1) - 14, H - 10);
})();
</script>
"""


def hero_chart(height=300):
    """Render the illustrative hero canvas. Falls back to nothing outside a
    Streamlit run so bare-mode imports (tests) stay safe."""
    try:
        import streamlit.components.v1 as components
        return components.html(HERO_CHART, height=height, scrolling=False)
    except Exception:
        return None


def bracket_bar(taxable_income, status="single"):
    """Illustrative 2026 bracket strip with a YOU marker. Segments share equal
    width; the marker sits fractionally inside the taxpayer's segment."""
    table = rt.FEDERAL_ORDINARY_2026.get(status, rt.FEDERAL_ORDINARY_2026["single"])
    caps = [c for c, _ in table]
    rates = [r for _, r in table]
    floors = [0.0] + caps[:-1]
    n = len(rates)
    segs, pos = [], 0.0
    for i, rate in enumerate(rates):
        floor, cap = floors[i], caps[i]
        if taxable_income > cap:
            fill, hot = 100.0, False
        elif taxable_income >= floor:
            span = (cap - floor) if cap != float("inf") else max(floor, 1.0)
            fill = min(max((taxable_income - floor) / span, 0.0), 1.0) * 100.0
            hot = True
            pos = (i + fill / 100.0) / n * 100.0
        else:
            fill, hot = 0.0, False
        cls = "seg hot" if hot else "seg"
        segs.append(
            f'<div class="{cls}"><div class="segfill" style="width:{fill:.1f}%"></div>'
            f'<span class="segrate">{rate:.0%}</span></div>')
    bar = "".join(segs)
    return (f'<div class="brackbar">{bar}'
            f'<div class="marker" style="left:{pos:.1f}%"><span>YOU</span></div></div>')


# ------------------------------ launch splash ------------------------------
# Wordmark squish on first paint: HRVST sets in Georgia green, R/S/T
# collapse and fade, H and V meet then push into an interlocking HV,
# and the monogram crossfades once into the real logo.
# Shown once per session.
if not st.session_state.get("splash_done"):
    _splash_uri = logo_data_uri()
    if _splash_uri:
        st.markdown(
            '<div class="splash"><div class="stage" aria-hidden="true">'
            '<div class="loader">'
            '<span class="wl h">H</span><span class="wl r">R</span>'
            '<span class="wl s">S</span><span class="wl t">T</span>'
            '<span class="wl v">V</span></div>'
            f'<img class="finlogo" src="{_splash_uri}" alt="HRVST logo">'
            "</div></div>",
            unsafe_allow_html=True,
        )
    st.session_state.splash_done = True


# ------------------------------ top nav ------------------------------
# Website-style header: brand on the left, page links on the right.
active = st.session_state.page
_brand_uri = logo_data_uri(96, "icon")
_brand_mark = (f'<img class="brandlogo" src="{_brand_uri}" alt="HRVST logo">'
               if _brand_uri else '<span class="mark"></span>')
st.markdown(f"""
<div class="nameplate"><span class="np-brand">{_brand_mark}HRVST</span>
<div class="np-tag">Tax-loss harvesting for real portfolios</div></div>
""", unsafe_allow_html=True)
st.markdown('<div class="secrule"></div>', unsafe_allow_html=True)
sec1, sec2, sec3 = st.columns(3)
with sec1:
    if st.button("Home", use_container_width=True, disabled=active == "Home",
                 key="nav_go_home"):
        go("Home"); st.rerun()
with sec2:
    if st.button("Analyze", use_container_width=True, disabled=active == "Analyze",
                 key="nav_go_analyze"):
        go("Analyze"); st.rerun()
with sec3:
    if st.button("FAQ", use_container_width=True, disabled=active == "FAQ",
                 key="nav_go_faq"):
        go("FAQ"); st.rerun()
st.markdown('<div class="secrule thick"></div>', unsafe_allow_html=True)
st.markdown(f'<div class="dateline"><span>{datetime.date.today().strftime("%A, %B %d, %Y")}</span>'
            f'<span>{market_status()} &middot; Vol. 2026</span></div>',
            unsafe_allow_html=True)


# ------------------------------ Home ------------------------------
def _tape_quotes_fast(tickers, timeout=8):
    """fetch_tape_quotes with a hard timeout; {} on slow/offline networks.

    Output contract unchanged: callers already fall back to bare symbols
    when quotes are missing.
    """
    import concurrent.futures as _cf
    try:
        with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
            return _ex.submit(rt.fetch_tape_quotes, tickers).result(
                timeout=timeout)
    except Exception:
        return {}


def tape_html():
    """Seamless infinite tape of the Nasdaq-100 list (content duplicated once,
    animation loops it forever). Live price + day change when fetchable,
    bare symbols when offline."""
    import html as html_lib
    if st.session_state.get("tape") is None:
        try:
            tickers = rt.load_tickers("NASDAQ")[:100]
        except Exception:
            tickers = []
        quotes = _tape_quotes_fast(tickers) if tickers else {}
        st.session_state.tape = (tickers, quotes)
    tickers, quotes = st.session_state.tape
    items = []
    for t in tickers:
        safe = html_lib.escape(t)
        if t in quotes:
            price, change = quotes[t]
            cls = "up" if change >= 0 else "down"
            sign = "+" if change >= 0 else ""
            items.append(f'<span class="tick"><b>{safe}</b> {price:,.2f} '
                         f'<span class="{cls}">{sign}{change:.1f}%</span></span>')
        else:
            items.append(f'<span class="tick"><b>{safe}</b></span>')
    half = "".join(items)
    return f'<div class="tape"><div class="tape-inner">{half}{half}</div></div>'





# ------------------------------ control deck ------------------------------
# Assumptions live in the sidebar so the main column stays a clean dossier.
income, status, min_loss = 150000.0, "single", 5.0
with st.sidebar:
    st.markdown('<div class="deckhead">HRVST</div>', unsafe_allow_html=True)
    st.markdown('<div class="decklabel">Theme</div>', unsafe_allow_html=True)
    _theme_labels = ["Ledger", "Nordic Minimalist", "Terminal"]
    _theme_keys = ["ledger", "nordic", "terminal"]
    _picked = st.selectbox("Theme", _theme_labels,
                           index=_theme_keys.index(st.session_state.theme),
                           key="theme_picker", label_visibility="collapsed")
    _new_theme = _theme_keys[_theme_labels.index(_picked)]
    if _new_theme != st.session_state.theme:
        st.session_state.theme = _new_theme
        st.rerun()
    if active == "Analyze":
        st.markdown('<div class="decklabel">Assumptions</div>', unsafe_allow_html=True)
        income = st.number_input("Taxable income ($/yr)", value=150000.0,
                                 min_value=0.0, step=10000.0)
        status = st.selectbox("Filing status", ["single", "joint"])
        min_loss = st.slider("Minimum loss % to flag", 0.5, 25.0, 5.0, 0.5,
                             help="Adjusts instantly, no refetch")
        st.caption("Estimates use simplified 2026 federal brackets: short-term "
                   "losses offset short-term gains first, then cross over.")
    else:
        st.markdown('<div class="decklabel">Start here</div>', unsafe_allow_html=True)
        st.write("Price what you own, find the losses worth realizing, "
                 "and keep the deduction.")
        if st.button("Analyze my portfolio", type="primary", use_container_width=True,
                       key="deck_go_analyze"):
            go("Analyze"); st.rerun()


HEADLINES = [
    ("Every red position is", "a coupon."),
    ("Sell the dip.", "Keep the deduction."),
    ("Losing well is", "a strategy."),
    ("Your losers are", "working overtime."),
    ("Red ink in.", "Black ink out."),
    ("The tax code pays", "for honesty."),
    ("Bank the loss.", "Keep the portfolio."),
    ("Down markets", "pay twice."),
    ("Harvest season", "never ends."),
    ("A loss realized", "is a gain secured."),
    ("Prune the red.", "Keep the green."),
    ("The IRS buys", "your worst trade."),
    ("Turn drawdowns", "into deductions."),
    ("Losses are", "taxes prepaid."),
    ("Admit defeat.", "Deduct it."),
]


if active == "Home":
    try:
        if "headline" not in st.session_state:
            st.session_state.headline = random.choice(HEADLINES)
        _hl, _hl_acc = st.session_state.headline
    except Exception:
        _hl, _hl_acc = random.choice(HEADLINES)
    st.markdown(f"""
    <div class="lead">
      <div class="pill"><span class="pulse"></span>The morning call &middot; Tax-loss harvesting</div>
      <h1>{_hl} <span class="green"><em>{_hl_acc}</em></span></h1>
      <p class="lede">HRVST prices what you actually own, circles the losses worth
      realizing, and tells you what each one is worth in federal tax.
      Manual entry or one-step broker import.</p>
    </div>
    """, unsafe_allow_html=True)
    lead_a, lead_b = st.columns(2)
    if lead_a.button("Price my holdings", type="primary", use_container_width=True,
                     key="home2_go_analyze"):
        go("Analyze"); st.rerun()
    if lead_b.button("Load the sample portfolio", use_container_width=True,
                     key="home2_sample"):
        st.session_state.rows = [
            {"ticker": "AAPL", "shares": 10.0, "buy": 0.0, "date": datetime.date(2020, 6, 1)},
            {"ticker": "MSFT", "shares": 5.0, "buy": 0.0, "date": datetime.date(2021, 1, 4)},
            {"ticker": "NVDA", "shares": 10.0, "buy": 0.0, "date": datetime.date(2023, 1, 3)},
            {"ticker": "TSLA", "shares": 8.0, "buy": 0.0, "date": datetime.date(2021, 11, 4)},
            {"ticker": "PYPL", "shares": 12.0, "buy": 0.0, "date": datetime.date(2021, 11, 1)},
        ]
        st.session_state.portfolio = None
        go("Analyze"); st.rerun()
    _days_left = max(0, (datetime.date(2026, 12, 31) - datetime.date.today()).days)
    st.markdown(f'<div class="countdown"><b>{_days_left}</b>'
                f'days left to harvest in 2026</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="section-title">Triage a ticker</div>
    <div class="section-sub">One symbol, one verdict. No holdings needed.</div>
    """, unsafe_allow_html=True)
    tri_t, tri_b = st.columns([2.4, 1], vertical_alignment="bottom")
    _sym = tri_t.text_input("Symbol", placeholder="TSLA", key="home2_triage_t").strip().upper()
    if tri_b.button("Get verdict", use_container_width=True, key="home2_triage_go"):
        st.session_state.triage = (_triage_verdict(_sym) if _sym
                                   else {"error": "Type a symbol first."})
        st.rerun()
    _tri = st.session_state.get("triage")
    if _tri:
        if "error" in _tri:
            st.markdown(f'<div class="verdict">{_tri["error"]}</div>', unsafe_allow_html=True)
        else:
            _cls = "down" if _tri["chg"] < 0 else "up"
            _sign = "" if _tri["chg"] < 0 else "+"
            st.markdown(
                f'<div class="verdict"><span class="vtick">{_tri["ticker"]}</span>'
                f'${_tri["price"]:,.2f} &middot; '
                f'<span class="{_cls}">{_sign}{_tri["chg"]:.1f}% on the year</span>'
                f'<span class="vnote">{_tri["note"]}</span></div>',
                unsafe_allow_html=True)

    st.markdown("""
    <div class="section-title">The brief</div>
    <div class="briefcols">
      <div class="briefcol"><div class="bnum">01</div><div class="bt">Losses pay for gains</div>
        <div class="bd">A realized loss wipes out a realized gain, dollar for dollar. That is the whole game.</div></div>
      <div class="briefcol"><div class="bnum">02</div><div class="bt">Leftovers count</div>
        <div class="bd">Up to $3,000 of extra loss deducts ordinary income yearly; the rest carries forward indefinitely.</div></div>
      <div class="briefcol"><div class="bnum">03</div><div class="bt">Mind the 30 days</div>
        <div class="bd">Repurchasing within 30 days can void the loss under the wash-sale rule. Mark the calendar.</div></div>
    </div>
    <div class="section-title">The tape</div>
    <div class="section-sub">Nasdaq-100, live. Hover to pause the scroll.</div>
    """, unsafe_allow_html=True)
    st.markdown(tape_html(), unsafe_allow_html=True)

    st.markdown("""
    <div class="section-title">Inside this edition</div>
    <div class="inside">
      <div class="insiderow"><b>The desk</b><span>Price your holdings, build the harvest</span></div>
      <div class="insiderow"><b>The reference</b><span>Nine chapters, zero jargon</span></div>
      <div class="insiderow"><b>The sample</b><span>Five positions, instant dossier</span></div>
    </div>
    """, unsafe_allow_html=True)
    in_a, in_b, in_c = st.columns(3)
    if in_a.button("Open the desk →", use_container_width=True, key="home2_in_analyze"):
        go("Analyze"); st.rerun()
    if in_b.button("Read the reference →", use_container_width=True, key="home2_in_faq"):
        go("FAQ"); st.rerun()
    if in_c.button("Try the sample →", use_container_width=True, key="home2_in_sample"):
        st.session_state.rows = [
            {"ticker": "AAPL", "shares": 10.0, "buy": 0.0, "date": datetime.date(2020, 6, 1)},
            {"ticker": "MSFT", "shares": 5.0, "buy": 0.0, "date": datetime.date(2021, 1, 4)},
            {"ticker": "NVDA", "shares": 10.0, "buy": 0.0, "date": datetime.date(2023, 1, 3)},
            {"ticker": "TSLA", "shares": 8.0, "buy": 0.0, "date": datetime.date(2021, 11, 4)},
            {"ticker": "PYPL", "shares": 12.0, "buy": 0.0, "date": datetime.date(2021, 11, 1)},
        ]
        st.session_state.portfolio = None
        go("Analyze"); st.rerun()

    st.markdown("""
    <div class="pullquote">&ldquo;Amateurs chase winners. Professionals monetize losers.&rdquo;
    <span>— the house view</span></div>
    <div class="card note">
      <div class="t">Please note</div>
      <div class="d">Estimates use simplified 2026 federal brackets and the 30-day wash-sale
      rule still applies. HRVST is an educational tool, not tax advice.
      The fine print lives on the FAQ page.</div>
    </div>
    """, unsafe_allow_html=True)
    footer()
    st.stop()


# ------------------------------ FAQ ------------------------------
if active == "FAQ":
    st.markdown("""
    <div class="faq-desk">
      <div class="pill"><span class="pulse"></span>The explainer &middot; Education only</div>
      <div class="faq-folio">Vol. 2026 &middot; No. 9 &middot; Nine chapters</div>
      <h1>The Harvester's<br><em>Desk Reference</em></h1>
      <p class="faq-lede">Nine short chapters on how HRVST prices your holdings,
      where its numbers come from, and what harvesting can and cannot do.
      Read top to bottom, or jump from the contents.</p>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("""
    <div class="faqindex"><div class="indextitle">Contents</div>
      <a href="#faq-c1"><b>I.</b> The harvest</a>
      <a href="#faq-c2"><b>II.</b> Price sources</a>
      <a href="#faq-c3"><b>III.</b> Privacy</a>
      <a href="#faq-c4"><b>IV.</b> Wash sales</a>
      <a href="#faq-c5"><b>V.</b> Holding periods</a>
      <a href="#faq-c6"><b>VI.</b> Accuracy</a>
      <a href="#faq-c7"><b>VII.</b> Leftovers</a>
      <a href="#faq-c8"><b>VIII.</b> State &amp; costs</a>
      <a href="#faq-c9"><b>IX.</b> Disclaimer</a>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div id="faq-c1"></div>', unsafe_allow_html=True)
    st.header("Chapter I — The harvest")
    st.markdown("""
    <div class="ch"><div class="folio">I</div>
      <p class="dropcap">Selling a position at a loss so the realized loss offsets realized
      gains, dollar for dollar, or up to $3,000 of ordinary income per year, lowering
      this year's tax bill. HRVST scans your holdings for losses worth realizing and
      ranks them by estimated federal savings.</p>
      <aside class="mnote">$3,000 a year against ordinary income; the rest keeps working.</aside>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div id="faq-c2"></div>', unsafe_allow_html=True)
    st.header("Chapter II — Price sources")
    st.markdown("""
    <div class="ch ch-alt"><div class="folio">II</div>
      <p class="dropcap">Buy-date closing prices and current quotes are provided by Yahoo
      Finance. Weekend or holiday purchase dates roll back to the previous close. A CSV
      with a current-price column uses your broker's figures instead.</p>
      <aside class="mnote">Source: Yahoo Finance closes; broker column wins when present.</aside>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div id="faq-c3"></div>', unsafe_allow_html=True)
    st.header("Chapter III — Privacy")
    st.markdown("""
    <div class="ch"><div class="folio">III</div>
      <p class="dropcap">Analysis runs entirely in your browser session. Holdings are never
      uploaded, saved, or shared, and they disappear when you close the tab.</p>
      <aside class="mnote">Session-only: nothing leaves your browser.</aside>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div id="faq-c4"></div>', unsafe_allow_html=True)
    st.header("Chapter IV — Wash sales")
    st.markdown("""
    <div class="ch ch-alt"><div class="folio">IV</div>
      <p class="dropcap">Selling a security at a loss and repurchasing a substantially
      identical security within 30 days before or after the sale can lead the IRS to
      disallow the loss. HRVST ranks candidates by estimated benefit; execution timing
      is yours to confirm with a qualified advisor.</p>
      <aside class="mnote">30 days each way — the sale is ringed on both sides.</aside>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div id="faq-c5"></div>', unsafe_allow_html=True)
    st.header("Chapter V — Holding periods")
    st.markdown("""
    <div class="ch"><div class="folio">V</div>
      <p class="dropcap">Positions held one year or less are short-term: gains and losses
      are taxed like ordinary income (10%–37% in 2026). Positions held more than a
      year are long-term, taxed at preferential 0%/15%/20% rates. Per IRS netting rules,
      short-term losses offset short-term gains first, so each dollar of short-term loss
      usually shelters more tax than a long-term dollar.</p>
      <aside class="mnote">One year is the hinge: 10–37% vs. 0/15/20%.</aside>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div id="faq-c6"></div>', unsafe_allow_html=True)
    st.header("Chapter VI — Accuracy")
    st.markdown("""
    <div class="ch ch-alt"><div class="folio">VI</div>
      <p class="dropcap">Estimates use simplified 2026 federal brackets: short-term losses
      offset short-term gains, long-term offset long-term, leftovers cross over, and up to
      $3,000 of remaining loss deducts ordinary income per year. State taxes, the 3.8% net
      investment income tax, AMT, and fees are excluded, so treat every figure as an
      educational estimate.</p>
      <aside class="mnote">Federal-only model; states, NIIT, AMT and fees sit outside it.</aside>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div id="faq-c7"></div>', unsafe_allow_html=True)
    st.header("Chapter VII — Leftovers")
    st.markdown("""
    <div class="ch"><div class="folio">VII</div>
      <p class="dropcap">After offsetting this year's gains and deducting up to $3,000
      against ordinary income, any remaining net loss carries forward to future tax years
      indefinitely. HRVST reports this carryforward on the Tax estimate tab so you can see
      the full multi-year value of a harvest.</p>
      <aside class="mnote">Unused loss never expires — it carries forward.</aside>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div id="faq-c8"></div>', unsafe_allow_html=True)
    st.header("Chapter VIII — State & costs")
    st.markdown("""
    <div class="ch ch-alt"><div class="folio">VIII</div>
      <p class="dropcap">Estimates are federal-only and ignore state income tax,
      commissions, and bid-ask spreads. In high-tax states the true benefit of a harvest is
      larger, while trading costs trim it slightly. Confirm the net benefit with your
      advisor before placing orders.</p>
      <aside class="mnote">States add; spreads and commissions subtract.</aside>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div id="faq-c9"></div>', unsafe_allow_html=True)
    st.header("Chapter IX — Disclaimer")
    st.markdown("""
    <div class="ch"><div class="folio">IX</div>
      <p class="dropcap">HRVST is an educational tool, not tax advice. Tax law is complex
      and personal: always confirm harvest timing, replacement securities, and filing with
      a qualified tax advisor before acting.</p>
      <aside class="mnote">Education only — your advisor has the last word.</aside>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="faq-colophon">Set in Georgia &middot; Printed on white &middot; Fin.</div>',
               unsafe_allow_html=True)
    d1, d2 = st.columns(2)
    if d1.button("Analyze my portfolio", type="primary", use_container_width=True,
                 key="faq_go_analyze2"):
        go("Analyze"); st.rerun()
    if d2.button("Back to Home", use_container_width=True, key="faq_go_home2"):
        go("Home"); st.rerun()
    footer()
    st.stop()


# ------------------------------ Analyze ------------------------------
_has_results = st.session_state.get("portfolio") is not None
_s1, _s2 = ("done", "current") if _has_results else ("current", "todo")
st.markdown(f"""
<div class="stepper"><div class="stp {_s1}"><span>1</span>Ledger</div>
<div class="stpline"></div><div class="stp {_s2}"><span>2</span>Dossier</div></div>
""", unsafe_allow_html=True)
st.markdown("""
<div class="deskmast">
  <div class="decklabel">The harvest desk &middot; Vol. 2026</div>
  <h2>Enter the ledger. Leave with a plan.</h2>
  <p>Left: every position on the books. Right: the clerk's rail —
  what you hold, what you assume, when you run.</p>
</div>
""", unsafe_allow_html=True)

_filled_preview = [r for r in st.session_state.rows if r.get("ticker")]
_oldest_preview = min((r["date"] for r in _filled_preview), default=None)
ledger, rail = st.columns([2.1, 1], gap="large")

with ledger:
    st.markdown('<div class="ledgerdesk">', unsafe_allow_html=True)
    st.markdown('<div class="decklabel">I. Holdings ledger</div>', unsafe_allow_html=True)
    with st.expander("Import positions from a broker CSV"):
        st.caption("Headers optional. Recognizes Symbol, Quantity, Purchase date, Price, "
                   "and Total cost columns, semicolon delimiters, and US or ISO dates. "
                   "Loading a file replaces your current list.")
        uploaded = st.file_uploader("Choose a CSV file", type=["csv"], key="desk_csv_uploader")
        fallback_date = st.date_input("Date to use for rows missing one",
                                      value=datetime.date(2023, 1, 2), key="desk_csv_fallback")
        if uploaded is not None:
            parsed = rt.parse_csv_holdings(uploaded.getvalue().decode("utf-8", "ignore"),
                                           fallback_date.isoformat())
            if parsed["mapping"]:
                st.caption("Columns detected: " + ", ".join(
                    f"{v} -> {k}" for k, v in parsed["mapping"].items()))
            for warn in parsed["warnings"]:
                st.warning(warn)
            if parsed["entries"]:
                st.dataframe(pd.DataFrame([{
                    "Ticker": t, "Date": d, "Shares": s,
                    "Buy $": b if b else "auto"} for t, d, s, b in parsed["entries"]]),
                    hide_index=True)
                if st.button(f"Load {len(parsed['entries'])} holdings", key="desk_csv_load"):
                    st.session_state.rows = [{
                        "ticker": t, "shares": float(s), "buy": float(b) if b else 0.0,
                        "date": datetime.date.fromisoformat(d)} for t, d, s, b in parsed["entries"]]
                    st.session_state.csv_live = dict(parsed.get("live") or {})
                    st.session_state.portfolio = None
                    st.session_state.alt_cache = {}
                    st.rerun()
            else:
                st.info("No usable rows found in this file.")
    for i, row in enumerate(st.session_state.rows):
        c1, c2, c3, c4, c5 = st.columns([2, 1.6, 1.8, 2, 1])
        row["ticker"] = c1.text_input("Ticker", value=row["ticker"], key=f"t_{i}",
                                      placeholder="AAPL").strip().upper()
        row["shares"] = c2.number_input("Shares", value=float(row.get("shares", 1.0)),
                                        min_value=0.0, step=1.0, key=f"s_{i}")
        row["buy"] = c3.number_input("Bought at $", value=float(row.get("buy", 0.0)),
                                     min_value=0.0, step=1.0, key=f"b_{i}",
                                     help="0 = look up the buy-date price automatically")
        row["date"] = c4.date_input("Bought on", value=row["date"], key=f"d_{i}",
                                    min_value=datetime.date(2000, 1, 1),
                                    max_value=datetime.date.today())
        if c5.button("Remove", key=f"x_{i}"):
            st.session_state.rows.pop(i)
            st.session_state.portfolio = None
            st.rerun()
    st.caption(f"{len(_filled_preview)} holdings listed"
               + (f" · oldest purchase {_oldest_preview.strftime('%b %d, %Y')}" if _oldest_preview else "")
               + " · prices fetch when you run the analysis.")
    add_c, clear_c = st.columns(2)
    add_c.button("+ Add holding", use_container_width=True, key="desk_add_holding",
                 on_click=lambda: st.session_state.rows.append(
                     {"ticker": "", "shares": 1.0, "buy": 0.0,
                      "date": datetime.date.today()}))
    if clear_c.button("Remove all", use_container_width=True, key="desk_remove_all"):
        st.session_state.rows = []
        st.session_state.portfolio = None
        st.session_state.alt_cache = {}
        st.session_state.csv_live = {}
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

with rail:
    st.markdown('<div class="clerkrail">', unsafe_allow_html=True)
    st.markdown('<div class="decklabel">Clerk&rsquo;s rail</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="railrow"><span>Positions</span><b>{len(_filled_preview)}</b></div>
    <div class="railrow"><span>Oldest buy</span><b>{_oldest_preview.strftime('%b %d, %Y') if _oldest_preview else '—'}</b></div>
    <div class="railrow"><span>Loss threshold</span><b>{min_loss:g}%</b></div>
    <div class="railrow"><span>Income / filing</span><b>{money(income)} · {status}</b></div>
    <div class="railnote">Assumptions set in the sidebar. Threshold refilters instantly — no refetch.</div>
    """, unsafe_allow_html=True)
    fetch = st.button("Run harvest analysis", type="primary", use_container_width=True, key="desk_run_analysis")
    if st.button("Refresh live prices", use_container_width=True, key="desk_refresh_prices"):
        st.session_state.portfolio = None
        st.session_state.alt_cache = {}
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

if fetch:
    entries = [(r["ticker"], r["date"], r["shares"], r["buy"] or None)
               for r in st.session_state.rows if r["ticker"]]
    if not entries:
        st.warning("Add at least one ticker to analyze.")
        st.stop()
    tickers_now = {t for t, _, _, _ in entries}
    broker = {t: p for t, p in st.session_state.csv_live.items() if t in tickers_now}
    try:
        with st.spinner("Pulling buy-date costs and live quotes..."):
            holdings = rt.holdings_from_ticker_dates(entries)
            live = dict(broker)
            missing = [h["ticker"] for h in holdings if h["ticker"] not in live]
            if missing:
                live.update(rt.fetch_live_prices(missing))
            st.session_state.portfolio = {
                "holdings": holdings, "live": live,
                "at": datetime.datetime.now().strftime("%H:%M:%S")}
            st.session_state.alt_cache = {}
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

cache = st.session_state.portfolio
if cache is None:
    footer()
    st.stop()
    cache = {"holdings": [], "live": {}, "at": ""}

result = _cached_analysis(json.dumps(cache["holdings"], default=str),
                         min_loss, json.dumps(dict(cache["live"])))
if result["skipped"]:
    st.warning(f"No live price for: {', '.join(result['skipped'])}.")

positions = result["positions"]
if not positions:
    st.info("None of your holdings returned price data. Check the tickers and retry.")
    st.stop()

totals = result["totals"]
gain_color = GREEN if totals["unrealized_gain_loss"] >= 0 else RED
gains = rt.position_gains(positions)
est = rt.estimate_tax_savings(result["opportunities"], gains, income, status)
rebuy_date = (datetime.date.today() + datetime.timedelta(days=31)).strftime("%b %d, %Y")
st.markdown('<div class="dossier">', unsafe_allow_html=True)
st.markdown(f"""
<div class="bigmetrics">
  <div class="bigmetric"><div class="v">{money(totals['current_value'])}</div>
    <div class="l">Portfolio value &middot; as of {cache['at']}</div></div>
  <div class="bigmetric"><div class="v" style="color:{gain_color}">{money(totals['unrealized_gain_loss'])}</div>
    <div class="l">Unrealized gain / loss</div></div>
  <div class="bigmetric"><div class="v" style="color:{RED}">{money(totals['harvestable_loss'])}</div>
    <div class="l">Harvestable loss &middot; {totals['opportunity_count']} candidates</div></div>
  <div class="bigmetric"><div class="v">{money(est['total_savings'])}</div>
    <div class="l">Est. federal savings &middot; {money(income)} {status}</div></div>
</div>
""", unsafe_allow_html=True)
_opps = result["opportunities"]
_n_lt = sum(1 for o in _opps if o["is_long_term"])
_n_st = len(_opps) - _n_lt
if _opps:
    memo_body = (f"Your <b>{len(positions)} positions</b> are worth "
                 f"<b>{money(totals['current_value'])}</b>. <b>{len(_opps)}</b> "
                 f"{'is' if len(_opps) == 1 else 'are'} harvestable for "
                 f"<b>{money(totals['harvestable_loss'])}</b> "
                 f"({_n_lt} long-term, {_n_st} short-term) — worth about "
                 f"<b>{money(est['total_savings'])}</b> in federal tax at "
                 f"{money(income)} ({status}).")
else:
    memo_body = (f"Your <b>{len(positions)} positions</b> are worth "
                 f"<b>{money(totals['current_value'])}</b>. Nothing clears the "
                 f"{min_loss:g}% loss threshold — lower it in the sidebar or "
                 f"check back after market movement.")
st.markdown(f"""
<div class="memo"><div class="stamp">2026<br>Harvest</div>
<div class="memotitle">Harvest memo &middot; {cache['at']}</div>
<div class="memobody">{memo_body}</div></div>
""", unsafe_allow_html=True)
st.markdown("</div>", unsafe_allow_html=True)

tab_opp, tab_tax, tab_act, tab_vis, tab_all, tab_cal = st.tabs(
    ["Opportunities", "Tax estimate", "Action plan", "Charts", "All positions", "Deadlines"])

with tab_opp:
    st.subheader("Build your harvest")
    st.caption("Tick only the losers you plan to sell — the savings reprice instantly.")
    _tickers = [o["ticker"] for o in result["opportunities"]]
    chosen = st.multiselect("Include in this harvest", _tickers, default=_tickers,
                              key="opp_harvest_pick")
    picked = [o for o in result["opportunities"] if o["ticker"] in chosen]
    pest = rt.estimate_tax_savings(picked, gains, income, status)
    p1, p2 = st.columns(2)
    p1.metric("Selected losses", money(sum(o["unrealized_loss"] for o in picked)))
    p2.metric("Est. savings on selection", money(pest["total_savings"]))
    opp_rows = [{
        "Ticker": o["ticker"],
        "Shares": o["shares"],
        "Bought": next(h.get("price_date", "") for h in cache["holdings"] if h["ticker"] == o["ticker"]),
        "Paid $": round(o["cost_basis"] / o["shares"], 2) if o["shares"] else 0.0,
        "Now $": round(o["current_price"], 2),
        "Loss $": round(o["unrealized_loss"], 2),
        "Loss %": round(o["loss_percentage"], 1),
        "Held": f"{o['holding_days']}d",
        "Term": "Long" if o["is_long_term"] else "Short",
        "Rebuy ≥": rebuy_date,
    } for o in result["opportunities"]]
    if opp_rows:
        st.dataframe(pd.DataFrame(opp_rows), hide_index=True)
        st.caption(f"Rebuy ≥ assumes you sell today — wait 31 days to dodge the wash-sale rule (clear after {rebuy_date}).")
    else:
        st.info("No positions meet the current threshold. Lower the threshold or check back after market movement.")
        if st.button("Why nothing qualifies →", key="opp_empty_faq"):
            go("FAQ"); st.rerun()
    st.download_button("Download harvest plan (CSV)",
                       data=pd.DataFrame([r for r in opp_rows if r["Ticker"] in chosen]).to_csv(index=False),
                       file_name="hrvst_harvest_plan.csv", mime="text/csv",
                       key="opp_download_plan")

with tab_tax:
    st.subheader("Estimated tax benefit")
    st.caption(f"At {money(income)} ({status}), set in the sidebar. Simplified 2026 "
               "federal estimate: short-term losses offset short-term "
               "gains, long-term offset long-term, leftovers cross over. Extra loss "
               "deducts up to $3,000 of ordinary income per year.")
    e1, e2, e3 = st.columns(3)
    e1.metric("Short-term rate", f"{est['short_term_rate']:.0%}")
    e2.metric("Long-term rate", f"{est['long_term_rate']:.0%}")
    e3.metric("Est. federal savings", money(est["total_savings"]))
    st.dataframe(pd.DataFrame([{
        "Bucket": label.replace("-", " ").title(),
        "Loss $": round(d["loss"], 2),
        "Gain $": round(d["gain"], 2),
        "Sheltered $": round(d["sheltered"] + d["cross_sheltered"], 2),
        "Savings $": round(d["savings"] + d["cross_savings"], 2),
    } for label, d in est["detail"].items()]), hide_index=True)
    if est["deducted_vs_income"]:
        st.caption(f"{money(est['deducted_vs_income'])} of leftover loss deducts "
                   "ordinary income this year (cap $3,000/yr).")
    if est["carryforward"]:
        _yrs = est["carryforward"] / 3000.0
        st.caption(f"{money(est['carryforward'])} carries forward to next year — "
                   f"about {_yrs:.1f} more years of $3,000 deductions.")
    st.markdown('<div class="decklabel">Gain coverage</div>', unsafe_allow_html=True)
    _cov = []
    for _label in ("short-term", "long-term"):
        _d = est["detail"][_label]
        _sheltered = _d["sheltered"] + _d["cross_sheltered"]
        if _d["gain"] > 0:
            _pct = min(_sheltered / _d["gain"] * 100.0, 100.0)
            _note = f"{money(_sheltered)} of {money(_d['gain'])} sheltered"
        else:
            _pct, _note = 0.0, "No gains to shelter — losses deduct income instead"
        _cov.append(
            f'<div class="cov"><div class="covhead"><b>{_label.replace("-", " ").title()} gains</b>'
            f'<span>{_note}</span></div><div class="covtrack">'
            f'<div class="covfill" style="width:{_pct:.0f}%"></div></div></div>')
    st.markdown("".join(_cov), unsafe_allow_html=True)

    st.subheader("2026 federal brackets")
    st.caption("Your marginal rate prices each sheltered dollar. The bar is illustrative, not to scale.")
    st.markdown(bracket_bar(income, status), unsafe_allow_html=True)
    st.caption("Single filer, Rev. Proc. 2025-32. Your marginal rate prices each sheltered dollar.")
    st.markdown("""
    <table class="bracktable">
      <tr><th>Rate</th><th>Taxable income</th></tr>
      <tr><td>10%</td><td>to $12,400</td></tr>
      <tr><td>12%</td><td>$12,401 – $50,400</td></tr>
      <tr><td>22%</td><td>$50,401 – $105,700</td></tr>
      <tr><td>24%</td><td>$105,701 – $201,775</td></tr>
      <tr><td>32%</td><td>$201,776 – $256,225</td></tr>
      <tr><td>35%</td><td>$256,226 – $640,600</td></tr>
      <tr><td>37%</td><td>over $640,600</td></tr>
    </table>
    """, unsafe_allow_html=True)

    st.subheader("Tax Triples")
    st.caption("Each group: sell a loser for the loss, sell a winner against it, "
               "then buy a wash-sale-safe alternative with the proceeds. "
               "Ranked by dollars saved.")
    alt_cache = rt.update_alt_cache(
        st.session_state.get("alt_cache", {}),
        [opp["ticker"] for opp in result["opportunities"]])
    st.session_state.alt_cache = alt_cache
    triples = rt.suggest_tax_triples(
        result["opportunities"], gains,
        est["short_term_rate"], est["long_term_rate"],
        alt_prices={a: p for a, _, p in alt_cache.values() if p},
        alt_overrides={t: (a, r) for t, (a, r, _) in alt_cache.items()})
    if not triples:
        st.info("No winners to build triples right now -- harvest the losses on their own.")
    for num, triple in enumerate(triples, 1):
        badge = ('<span class="badge long">TAX-FREE REBALANCE</span>' if triple["fully_covered"]
                 else '<span class="badge short">PARTIAL OFFSET</span>')
        if triple["buy_shares"]:
            buy_line = (f"Buy {triple['buy_ticker']} -- {triple['buy_reason']} "
                        f"(&asymp;{triple['buy_shares']:.1f} shares with "
                        f"{money(triple['buy_proceeds'])}).")
        else:
            buy_line = (f"Buy {triple['buy_ticker']} -- {triple['buy_reason']} "
                        f"(price unavailable, size it to {money(triple['buy_proceeds'])}).")
        st.markdown(f"""
        <div class="step" style="margin-bottom:10px">
          <div class="t">Triple {num} {badge}</div>
          <div class="d">1 &middot; Sell {triple['loss_ticker']} -- realize a
          {money(triple['loss_amount'])} loss
          ({'long' if triple['loss_long_term'] else 'short'}-term).<br>
          2 &middot; Sell {triple['gain_ticker']} -- realize a
          {money(triple['gain_amount'])} gain
          ({'long' if triple['gain_long_term'] else 'short'}-term) &rarr;
          {money(triple['net_taxable'])} left taxable.<br>
          3 &middot; {buy_line}<br>
          <strong>Save ~{money(triple['savings_vs_gain_alone'])}</strong> vs selling
          {triple['gain_ticker']} alone.</div>
        </div>
        """, unsafe_allow_html=True)

with tab_act:
    st.subheader("Your action plan")
    if not result["opportunities"]:
        st.info("Nothing to do yet — no harvestable losses at this threshold.")
        if st.button("How harvesting works →", key="plan_empty_faq"):
            go("FAQ"); st.rerun()
    else:
        st.caption("Work top to bottom. Ticks persist until you refresh prices.")
        _done, _total = 0, 0
        for o in result["opportunities"]:
            _total += 1
            if st.checkbox(
                    f"Sell {o['ticker']} — lock in a {money(o['unrealized_loss'])} loss "
                    f"({'long' if o['is_long_term'] else 'short'}-term)",
                    key=f"plan_sell_{o['ticker']}"):
                _done += 1
        _total += 1
        if st.checkbox(f"Wait 31 days before rebuying — clear after {rebuy_date}",
                       key="plan_wait"):
            _done += 1
        _total += 1
        if st.checkbox("Confirm timing and replacements with your tax advisor",
                       key="plan_advisor"):
            _done += 1
        st.progress(_done / _total, text=f"{_done} of {_total} steps complete")

with tab_vis:
    st.markdown('<div class="decklabel">Losses at a glance</div>', unsafe_allow_html=True)
    st.markdown(gain_loss_chart(positions), unsafe_allow_html=True)
    st.caption("Hover any bar for paid vs current. Green is gain, red is harvestable.")
    st.subheader("Allocation by current value")
    st.markdown(allocation_chart(positions), unsafe_allow_html=True)

with tab_all:
    st.markdown('<div class="decklabel">The full register</div>', unsafe_allow_html=True)
    st.caption(f"{len(positions)} positions · Rebuy clock: 31 days (clear after {rebuy_date}).")
    st.dataframe(pd.DataFrame([{
        "Ticker": p["ticker"],
        "Shares": p["shares"],
        "Paid $": round(p["buy_price"], 2),
        "Now $": round(p["current_price"], 2),
        "P/L $": round(p["unrealized_gain_loss"], 2),
        "P/L %": round(p["gain_loss_percentage"], 1),
        "Term": "Long" if p["is_long_term"] else "Short",
    } for p in positions]), hide_index=True)

with tab_cal:
    import html as _html
    st.subheader("2026 tax-year countdown desk")
    st.caption("Real 2026 dates. Days remaining count from today; harvests settle by Dec 31, 2026.")
    _rows = deadline_rows(datetime.date.today())
    _cells = []
    for _r in _rows:
        if _r["state"] == "past":
            _when = f"{abs(_r['days_left'])}d ago"
            _stamp = '<span class="ledstamp past">PAST</span>'
        elif _r["state"] == "urgent":
            _when = "today" if _r["days_left"] == 0 else f"{_r['days_left']}d left"
            _stamp = '<span class="ledstamp urgent">URGENT</span>'
        else:
            _when = f"{_r['days_left']}d left"
            _stamp = '<span class="ledstamp duenext">DUE NEXT</span>'
        _cells.append(
            f'<tr class="ledrow {_r["state"]}"><td><b>{_html.escape(_r["label"])}</b>'
            f'<br><span class="lednote">{_html.escape(_r["note"])}</span></td>'
            f"<td>{_r['iso']}</td><td>{_when}</td><td>{_stamp}</td></tr>")
    st.markdown(
        '<table class="ledger"><tr><th>Deadline</th><th>Date</th>'
        "<th>Countdown</th><th>Status</th></tr>" + "".join(_cells) + "</table>",
        unsafe_allow_html=True)
    st.caption(f"Leftover losses still deduct up to {money(3000)} of ordinary income per year; the rest carries forward.")

st.markdown('<p class="fine">Figures reflect the share counts and cost basis entered. '
            'Educational estimates only, not tax advice. The 30-day wash-sale rule may '
            'disallow losses on repurchased securities.</p>', unsafe_allow_html=True)
footer()


# ------------------------------ Flask ------------------------------
# Streamlit runtime replaced by Flask. Presentation above (CSS constants,
# HTML strings, *_chart, bracket_bar, tape_html, logo_data_uri, footer) is
# reused verbatim; only request/session plumbing lives here.
_FLASK_SAMPLE_ROWS = [
    {"ticker": "AAPL", "shares": 10.0, "buy": 0.0, "date": "2020-06-01"},
    {"ticker": "MSFT", "shares": 5.0, "buy": 0.0, "date": "2021-01-04"},
    {"ticker": "NVDA", "shares": 10.0, "buy": 0.0, "date": "2023-01-03"},
    {"ticker": "TSLA", "shares": 8.0, "buy": 0.0, "date": "2021-11-04"},
    {"ticker": "PYPL", "shares": 12.0, "buy": 0.0, "date": "2021-11-01"},
]

if _FLASK_AVAILABLE:
    app = Flask(__name__)
    app.secret_key = "hrvst-local-dev-key"  # local dev only; sessions live in a cookie

    @app.before_request
    def _flask_analytics():
        try:
            track_streamlit_view()
        except Exception:
            pass

    def _f_state():
        """Per-visitor state; mirrors the old st.session_state keys."""
        s = flask_session
        s.setdefault("theme", "ledger")
        s.setdefault("rows", [{"ticker": "AAPL", "shares": 10.0, "buy": 0.0,
                               "date": "2023-06-01"}])
        s.setdefault("portfolio", None)
        s.setdefault("csv_live", {})
        s.setdefault("alt_cache", {})
        s.setdefault("income", 150000.0)
        s.setdefault("status", "single")
        s.setdefault("min_loss", 5.0)
        s.setdefault("splash_seen_v2", False)
        s.setdefault("harvest_pick", None)
        s.setdefault("plan_checks", [])
        return s

    def _f_net(fn, timeout=20, default=None):
        """Run a network helper with a hard timeout; default on slow/offline."""
        import concurrent.futures as _cf
        try:
            with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                return _ex.submit(fn).result(timeout=timeout)
        except Exception:
            return default

    # Minimal Flask-shell rules only: plain-word buttons with an underline
    # (no grey boxes) and the centered Streamlit container the theme CSS
    # expects via .appview-container .main .block-container.
    _F_SHELL_CSS = ("<style>"
                    ".fnav form{display:inline;margin:0 18px 0 0}"
                    ".fnav button{background:none;border:0;padding:0;font:inherit;"
                    "color:inherit;cursor:pointer;text-decoration:underline;"
                    "text-underline-offset:4px}"
                    ".fnav button:disabled{text-decoration:none;opacity:.45;cursor:default}"
                    ".fnav select{font:inherit}"
                    "main.block-container{margin-left:auto;margin-right:auto}"
                    ".fnav select{background:none;border:0;font:inherit;color:inherit;cursor:pointer}"
                    "main.block-container button{background:none;border:0;padding:0 2px;"
                    "font:inherit;color:inherit;cursor:pointer;text-decoration:underline;"
                    "text-underline-offset:4px}"
                    "main.block-container button:disabled{text-decoration:none;"
                    "opacity:.45;cursor:default}"
                    "body{margin:0;padding:0}"
                    ""
                    ".appview-container .main main.block-container{max-width:none;padding-left:48px;padding-right:48px;"
                    "line-height:1.7}"
                    "main.block-container p{margin:0 0 1em}"
                    ".clerkrail{background:none}"
                    ".fnav{display:flex;justify-content:flex-start;gap:48px;align-items:center}"
                    "/* splash at 1.75x: every duration and delay below is the original / 1.75 */"
                    ".splash{animation:splashout .34s ease 2.51s forwards}"
                    ".loader{animation:leadin .51s cubic-bezier(.16,1,.3,1) .06s forwards,"
                    "loaderOut .29s ease 1.94s forwards}"
                    ".wl.r{animation:fadeR .57s cubic-bezier(.65,0,.35,1) .69s forwards}"
                    ".wl.s{animation:fadeS .57s cubic-bezier(.65,0,.35,1) .69s forwards}"
                    ".wl.t{animation:fadeT .57s cubic-bezier(.65,0,.35,1) .69s forwards}"
                    ".wl.h{animation:meetH .63s cubic-bezier(.16,1,.3,1) .97s forwards,"
                    "pushCloserH .46s cubic-bezier(.16,1,.3,1) 1.6s forwards}"
                    ".wl.v{animation:meetV .63s cubic-bezier(.16,1,.3,1) .97s forwards,"
                    "pushCloserV .46s cubic-bezier(.16,1,.3,1) 1.6s forwards}"
                    ".finlogo{animation:finIn .4s cubic-bezier(.16,1,.3,1) 1.94s forwards}"
                    ".fnav form{margin:0}"
                    "</style>")

    def _f_css(theme):
        base = _BASE_CSS
        if theme == "nordic":
            return base + _NORDIC_CSS
        if theme == "terminal":
            return base + _TERMINAL_CSS
        return base + _LEDGER_CSS

    def _f_palette(theme):
        if theme == "terminal":
            return ("#34D399", "#F43F5E", "#F59E0B", "#EDEFF3", "#8E99A8",
                    ["#34D399", "#EDEFF3", "#38BDF8", "#F59E0B",
                     "#F43F5E", "#64748B", "#6EE7B7", "#334155"])
        if theme == "nordic":
            return ("#10B981", "#E11D48", "#F59E0B", "#0F172A", "#64748B",
                    ["#10B981", "#0F172A", "#38BDF8", "#F59E0B",
                     "#E11D48", "#94A3B8", "#34D399", "#CBD5E1"])
        return (GREEN, RED, GOLD, INK, MUTED, ALLOC_COLORS)

    _F_NAV_PATHS = {"Home": "/", "Analyze": "/analyze", "FAQ": "/faq"}

    def _f_nav(active):
        s = _f_state()
        def btn(label, href, on):
            if on:
                return f'<button disabled>{label}</button>'
            return (f'<form action="{href}" method="get">'
                    f'<button>{label}</button></form>')
        theme_opts = "".join(
            f'<option value="{k}"{" selected" if s["theme"] == k else ""}>{v}</option>'
            for k, v in (("ledger", "Ledger"), ("nordic", "Nordic Minimalist"),
                         ("terminal", "Terminal")))
        return (f'<div class="fnav">'
                + btn("Home", "/", active == "Home")
                + btn("Analyze", "/analyze", active == "Analyze")
                + btn("FAQ", "/faq", active == "FAQ")
                + f'<form action="/theme" method="get">'
                  f'<input type="hidden" name="next" value="{_F_NAV_PATHS.get(active, "/")}">'
                  f'<select name="theme" onchange="this.form.submit()">{theme_opts}</select>'
                  "</form></div>")

    def _f_header(active):
        import html as _h
        uri = logo_data_uri(96, "icon")
        mark = (f'<img class="brandlogo" src="{uri}" alt="HRVST logo">' if uri
                else '<span class="mark"></span>')
        return (f'<div class="nameplate"><span class="np-brand">{mark}HRVST</span>'
                '<div class="np-tag">Tax-loss harvesting for real portfolios</div></div>'
                '<div class="secrule"></div>'
                f'<div>{_f_nav(active)}</div>'
                '<div class="secrule thick"></div>'
                f'<div class="dateline"><span>{datetime.date.today().strftime("%A, %B %d, %Y")}</span>'
                f'<span>{_h.escape(market_status())} &middot; Vol. 2026</span></div>')

    def _f_footer_html():
        st._capture = []
        try:
            footer()
            html = "".join(st._capture)
        finally:
            st._capture = None
        return html + f'<div>{_f_nav("")}</div>'

    def _f_splash_html(s):
        # Replay on fresh visits and full-page reloads, but not on in-app
        # button clicks: clicks arrive with one of our own *other* pages as
        # the referrer, while reloads re-send the same path (or none).
        show = True
        try:
            from urllib.parse import urlparse
            ref = getattr(request, "referrer", None) or ""
            host = getattr(request, "host", "") or ""
            here = getattr(request, "path", "") or "/"
            if ref and host and urlparse(ref).netloc == host:
                if (urlparse(ref).path or "/") != here:
                    show = False
        except Exception:
            show = True
        if not show:
            return ""
        uri = logo_data_uri()
        if not uri:
            return ""
        return ('<div class="splash"><div class="stage" aria-hidden="true">'
                '<div class="loader">'
                '<span class="wl h">H</span><span class="wl r">R</span>'
                '<span class="wl s">S</span><span class="wl t">T</span>'
                '<span class="wl v">V</span></div>'
                f'<img class="finlogo" src="{uri}" alt="HRVST logo">'
                "</div></div>")

    def _f_page(title, active, body):
        s = _f_state()
        return ("<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                f"<title>{title}</title>" + _f_css(s["theme"]) + _F_SHELL_CSS +
                "</head><body><div class='appview-container' data-testid='stAppViewContainer'>"
                "<div class='main'><main class='block-container'>"
                + _f_splash_html(s) + _f_header(active) + body
                + _f_footer_html() + "</main></div></div></body></html>")

    def _f_home_html():
        import html as _h
        s = _f_state()
        hl, hl_acc = random.choice(HEADLINES)
        days_left = max(0, (datetime.date(2026, 12, 31)
                             - datetime.date.today()).days)
        tri = s.pop("triage", None)
        if tri:
            if "error" in tri:
                tri_html = f'<div class="verdict">{_h.escape(tri["error"])}</div>'
            else:
                cls = "down" if tri["chg"] < 0 else "up"
                sign = "" if tri["chg"] < 0 else "+"
                tri_html = (f'<div class="verdict"><span class="vtick">{_h.escape(tri["ticker"])}</span>'
                            f'${tri["price"]:,.2f} &middot; '
                            f'<span class="{cls}">{sign}{tri["chg"]:.1f}% on the year</span>'
                            f'<span class="vnote">{_h.escape(tri["note"])}</span></div>')
        else:
            tri_html = ""
        return (f"""
    <div class="lead">
      <div class="pill"><span class="pulse"></span>The morning call &middot; Tax-loss harvesting</div>
      <h1>{_h.escape(hl)} <span class="green"><em>{_h.escape(hl_acc)}</em></span></h1>
      <p class="lede">HRVST prices what you actually own, circles the losses worth
      realizing, and tells you what each one is worth in federal tax.
      Manual entry or one-step broker import.</p>
    </div>
    <div>
      <form action="/analyze" method="get" style="display:inline"><button>Price my holdings</button></form>
      <form action="/sample" method="post" style="display:inline"><button>Load the sample portfolio</button></form>
    </div>
    <div class="countdown"><b>{days_left}</b>days left to harvest in 2026</div>
    <div class="section-title">Triage a ticker</div>
    <div class="section-sub">One symbol, one verdict. No holdings needed.</div>
    <form action="/triage" method="post">
      <input name="symbol" placeholder="TSLA" maxlength="12">
      <button>Get verdict</button>
    </form>
    {tri_html}
    <div class="section-title">The brief</div>
    <div class="briefcols">
      <div class="briefcol"><div class="bnum">01</div><div class="bt">Losses pay for gains</div>
        <div class="bd">A realized loss wipes out a realized gain, dollar for dollar. That is the whole game.</div></div>
      <div class="briefcol"><div class="bnum">02</div><div class="bt">Leftovers count</div>
        <div class="bd">Up to $3,000 of extra loss deducts ordinary income yearly; the rest carries forward indefinitely.</div></div>
      <div class="briefcol"><div class="bnum">03</div><div class="bt">Mind the 30 days</div>
        <div class="bd">Repurchasing within 30 days can void the loss under the wash-sale rule. Mark the calendar.</div></div>
    </div>
    <div class="section-title">Inside this edition</div>
    <div class="inside">
      <div class="insiderow"><b>The desk</b><span>Price your holdings, build the harvest</span></div>
      <div class="insiderow"><b>The reference</b><span>Nine chapters, zero jargon</span></div>
      <div class="insiderow"><b>The sample</b><span>Five positions, instant dossier</span></div>
    </div>
    <div>
      <form action="/analyze" method="get" style="display:inline"><button>Open the desk &rarr;</button></form>
      <form action="/faq" method="get" style="display:inline"><button>Read the reference &rarr;</button></form>
      <form action="/sample" method="post" style="display:inline"><button>Try the sample &rarr;</button></form>
    </div>
    <div class="pullquote">&ldquo;Amateurs chase winners. Professionals monetize losers.&rdquo;
    <span>&mdash; the house view</span></div>
    <div class="card note">
      <div class="t">Please note</div>
      <div class="d">Estimates use simplified 2026 federal brackets and the 30-day wash-sale
      rule still applies. HRVST is an educational tool, not tax advice.
      The fine print lives on the FAQ page.</div>
    </div>
    """)

    _F_FAQ_CHAPTERS = [
        ("Chapter I — The harvest", "ch", "I",
         "Selling a position at a loss so the realized loss offsets realized\n"
         "      gains, dollar for dollar, or up to $3,000 of ordinary income per year, lowering\n"
         "      this year's tax bill. HRVST scans your holdings for losses worth realizing and\n"
         "      ranks them by estimated federal savings.",
         "$3,000 a year against ordinary income; the rest keeps working."),
        ("Chapter II — Price sources", "ch ch-alt", "II",
         "Buy-date closing prices and current quotes are provided by Yahoo\n"
         "      Finance. Weekend or holiday purchase dates roll back to the previous close. A CSV\n"
         "      with a current-price column uses your broker's figures instead.",
         "Source: Yahoo Finance closes; broker column wins when present."),
        ("Chapter III — Privacy", "ch", "III",
         "Analysis runs entirely in your browser session. Holdings are never\n"
         "      uploaded, saved, or shared, and they disappear when you close the tab.",
         "Session-only: nothing leaves your browser."),
        ("Chapter IV — Wash sales", "ch ch-alt", "IV",
         "Selling a security at a loss and repurchasing a substantially\n"
         "      identical security within 30 days before or after the sale can lead the IRS to\n"
         "      disallow the loss. HRVST ranks candidates by estimated benefit; execution timing\n"
         "      is yours to confirm with a qualified advisor.",
         "30 days each way — the sale is ringed on both sides."),
        ("Chapter V — Holding periods", "ch", "V",
         "Positions held one year or less are short-term: gains and losses\n"
         "      are taxed like ordinary income (10%–37% in 2026). Positions held more than a\n"
         "      year are long-term, taxed at preferential 0%/15%/20% rates. Per IRS netting rules,\n"
         "      short-term losses offset short-term gains first, so each dollar of short-term loss\n"
         "      usually shelters more tax than a long-term dollar.",
         "One year is the hinge: 10–37% vs. 0/15/20%."),
        ("Chapter VI — Accuracy", "ch ch-alt", "VI",
         "Estimates use simplified 2026 federal brackets: short-term losses\n"
         "      offset short-term gains, long-term offset long-term, leftovers cross over, and up to\n"
         "      $3,000 of remaining loss deducts ordinary income per year. State taxes, the 3.8% net\n"
         "      investment income tax, AMT, and fees are excluded, so treat every figure as an\n"
         "      educational estimate.",
         "Federal-only model; states, NIIT, AMT and fees sit outside it."),
        ("Chapter VII — Leftovers", "ch", "VII",
         "After offsetting this year's gains and deducting up to $3,000\n"
         "      against ordinary income, any remaining net loss carries forward to future tax years\n"
         "      indefinitely. HRVST reports this carryforward on the Tax estimate tab so you can see\n"
         "      the full multi-year value of a harvest.",
         "Unused loss never expires — it carries forward."),
        ("Chapter VIII — State & costs", "ch ch-alt", "VIII",
         "Estimates are federal-only and ignore state income tax,\n"
         "      commissions, and bid-ask spreads. In high-tax states the true benefit of a harvest is\n"
         "      larger, while trading costs trim it slightly. Confirm the net benefit with your\n"
         "      advisor before placing orders.",
         "States add; spreads and commissions subtract."),
        ("Chapter IX — Disclaimer", "ch", "IX",
         "HRVST is an educational tool, not tax advice. Tax law is complex\n"
         "      and personal: always confirm harvest timing, replacement securities, and filing with\n"
         "      a qualified tax advisor before acting.",
         "Education only — your advisor has the last word."),
    ]

    def _f_faq_html():
        parts = ["""
    <div class="faq-desk">
      <div class="pill"><span class="pulse"></span>The explainer &middot; Education only</div>
      <div class="faq-folio">Vol. 2026 &middot; No. 9 &middot; Nine chapters</div>
      <h1>The Harvester's<br><em>Desk Reference</em></h1>
      <p class="faq-lede">Nine short chapters on how HRVST prices your holdings,
      where its numbers come from, and what harvesting can and cannot do.
      Read top to bottom, or jump from the contents.</p>
    </div>
    <div class="faqindex"><div class="indextitle">Contents</div>
      <a href="#faq-c1"><b>I.</b> The harvest</a>
      <a href="#faq-c2"><b>II.</b> Price sources</a>
      <a href="#faq-c3"><b>III.</b> Privacy</a>
      <a href="#faq-c4"><b>IV.</b> Wash sales</a>
      <a href="#faq-c5"><b>V.</b> Holding periods</a>
      <a href="#faq-c6"><b>VI.</b> Accuracy</a>
      <a href="#faq-c7"><b>VII.</b> Leftovers</a>
      <a href="#faq-c8"><b>VIII.</b> State &amp; costs</a>
      <a href="#faq-c9"><b>IX.</b> Disclaimer</a>
    </div>
    """]
        for i, (title, cls, folio, body, note) in enumerate(_F_FAQ_CHAPTERS, 1):
            parts.append(
                f'<div id="faq-c{i}"></div><h2>{title}</h2>'
                f'<div class="{cls}"><div class="folio">{folio}</div>'
                f'<p class="dropcap">{body}</p>'
                f'<aside class="mnote">{note}</aside></div>')
        parts.append('<div class="faq-colophon">Set in Georgia &middot; Printed on white &middot; Fin.</div>'
                     '<div><form action="/analyze" method="get" style="display:inline">'
                     '<button>Analyze my portfolio</button></form>'
                     '<form action="/" method="get" style="display:inline">'
                     '<button>Back to Home</button></form></div>')
        return "".join(parts)

    def _f_ledger_table(rows):
        import html as _h
        out = ['<table class="ledger"><tr><th>Ticker</th><th>Shares</th>'
               '<th>Bought at $</th><th>Bought on</th><th></th></tr>']
        for i, r in enumerate(rows):
            out.append(
                f'<tr><td><input name="ticker_{i}" value="{_h.escape(str(r.get("ticker", "")))}" '
                'maxlength="12" placeholder="AAPL"></td>'
                f'<td><input name="shares_{i}" type="number" step="any" min="0" value="{r.get("shares", 1.0)}"></td>'
                f'<td><input name="buy_{i}" type="number" step="any" min="0" value="{r.get("buy", 0.0)}" '
                'title="0 = look up the buy-date price automatically"></td>'
                f'<td><input name="date_{i}" type="date" value="{_h.escape(str(r.get("date", "")))}" '
                'min="2000-01-01"></td>'
                f'<td><button name="remove" value="{i}">Remove</button></td></tr>')
        out.append("</table>")
        return "".join(out)

    def _f_table_html(columns, rows):
        import html as _h
        cells = "".join(f"<th>{_h.escape(c)}</th>" for c in columns)
        body = "".join("<tr>" + "".join(f"<td>{_h.escape(str(v))}</td>" for v in r)
                       + "</tr>" for r in rows)
        return f'<table class="ledger"><tr>{cells}</tr>{body}</table>'

    def _f_dossier_html(s, GREEN_, RED_):
        import html as _h
        cache = s["portfolio"]
        income, status, min_loss = s["income"], s["status"], s["min_loss"]
        result = _cached_analysis(json.dumps(cache["holdings"], default=str),
                                  min_loss, json.dumps(dict(cache["live"])))
        if result["skipped"]:
            skip = ('<div class="error">No live price for: '
                    + _h.escape(", ".join(result["skipped"])) + ".</div>")
        else:
            skip = ""
        positions = result["positions"]
        if not positions:
            return (skip + '<div class="verdict">None of your holdings returned '
                    'price data. Check the tickers and retry.</div>')
        totals = result["totals"]
        gain_color = GREEN_ if totals["unrealized_gain_loss"] >= 0 else RED_
        gains = rt.position_gains(positions)
        est = rt.estimate_tax_savings(result["opportunities"], gains, income, status)
        rebuy_date = (datetime.date.today()
                      + datetime.timedelta(days=31)).strftime("%b %d, %Y")
        opps = result["opportunities"]
        n_lt = sum(1 for o in opps if o["is_long_term"])
        n_st = len(opps) - n_lt
        if opps:
            memo_body = (f"Your <b>{len(positions)} positions</b> are worth "
                         f"<b>{money(totals['current_value'])}</b>. <b>{len(opps)}</b> "
                         f"{'is' if len(opps) == 1 else 'are'} harvestable for "
                         f"<b>{money(totals['harvestable_loss'])}</b> "
                         f"({_n_lt} long-term, {n_st} short-term) — worth about "
                         f"<b>{money(est['total_savings'])}</b> in federal tax at "
                         f"{money(income)} ({status}).")
        else:
            memo_body = (f"Your <b>{len(positions)} positions</b> are worth "
                         f"<b>{money(totals['current_value'])}</b>. Nothing clears the "
                         f"{min_loss:g}% loss threshold — lower it in the sidebar or "
                         "check back after market movement.")
        parts = [skip, '<div class="dossier">', f"""
<div class="bigmetrics">
  <div class="bigmetric"><div class="v">{money(totals['current_value'])}</div>
    <div class="l">Portfolio value &middot; as of {_h.escape(cache['at'])}</div></div>
  <div class="bigmetric"><div class="v" style="color:{gain_color}">{money(totals['unrealized_gain_loss'])}</div>
    <div class="l">Unrealized gain / loss</div></div>
  <div class="bigmetric"><div class="v" style="color:{RED_}">{money(totals['harvestable_loss'])}</div>
    <div class="l">Harvestable loss &middot; {totals['opportunity_count']} candidates</div></div>
  <div class="bigmetric"><div class="v">{money(est['total_savings'])}</div>
    <div class="l">Est. federal savings &middot; {money(income)} {status}</div></div>
</div>
<div class="memo"><div class="stamp">2026<br>Harvest</div>
<div class="memotitle">Harvest memo &middot; {_h.escape(cache['at'])}</div>
<div class="memobody">{memo_body}</div></div>
</div>"""]
        # --- Opportunities ---
        tickers = [o["ticker"] for o in opps]
        pick = s.get("harvest_pick")
        if pick is None:
            pick = list(tickers)
        picked = [o for o in opps if o["ticker"] in pick]
        pest = rt.estimate_tax_savings(picked, gains, income, status)
        boxes = "".join(
            f'<label><input type="checkbox" name="pick" value="{_h.escape(o["ticker"])}"'
            f'{" checked" if o["ticker"] in pick else ""} onchange="this.form.submit()"> '
            f'{_h.escape(o["ticker"])}</label> ' for o in opps)
        opp_rows = [[o["ticker"], o["shares"],
                     next((h.get("price_date", "") for h in cache["holdings"]
                           if h["ticker"] == o["ticker"]), ""),
                     round(o["cost_basis"] / o["shares"], 2) if o["shares"] else 0.0,
                     round(o["current_price"], 2), round(o["unrealized_loss"], 2),
                     round(o["loss_percentage"], 1), f"{o['holding_days']}d",
                     "Long" if o["is_long_term"] else "Short", rebuy_date]
                    for o in opps]
        parts.append('<div class="decklabel">Opportunities — build your harvest</div>'
                     '<p class="fine">Tick only the losers you plan to sell — the savings reprice instantly.</p>'
                     '<form action="/analyze" method="post">'
                     f'{boxes}<br>'
                     f'<div class="bigmetrics"><div class="bigmetric"><div class="v">'
                     f'{money(sum(o["unrealized_loss"] for o in picked))}</div>'
                     '<div class="l">Selected losses</div></div>'
                     f'<div class="bigmetric"><div class="v">{money(pest["total_savings"])}</div>'
                     '<div class="l">Est. savings on selection</div></div></div>'
                     "</form>")
        if opp_rows:
            parts.append(_f_table_html(
                ["Ticker", "Shares", "Bought", "Paid $", "Now $", "Loss $",
                 "Loss %", "Held", "Term", "Rebuy ≥"], opp_rows))
            parts.append(f'<p class="fine">Rebuy ≥ assumes you sell today — wait 31 days '
                         f'to dodge the wash-sale rule (clear after {rebuy_date}).</p>'
                         '<form action="/download" method="get"><button>'
                         "Download harvest plan (CSV)</button></form>")
        else:
            parts.append('<div class="verdict">No positions meet the current threshold. '
                         'Lower the threshold or check back after market movement.</div>'
                         '<form action="/faq" method="get"><button>Why nothing qualifies &rarr;</button></form>')
        # --- Tax estimate ---
        parts.append('<div class="decklabel">Tax estimate — estimated tax benefit</div>'
                     f'<p class="fine">At {money(income)} ({status}). Simplified 2026 '
                     "federal estimate: short-term losses offset short-term "
                     "gains, long-term offset long-term, leftovers cross over. Extra loss "
                     "deducts up to $3,000 of ordinary income per year.</p>"
                     '<div class="bigmetrics">'
                     f'<div class="bigmetric"><div class="v">{est["short_term_rate"]:.0%}</div>'
                     '<div class="l">Short-term rate</div></div>'
                     f'<div class="bigmetric"><div class="v">{est["long_term_rate"]:.0%}</div>'
                     '<div class="l">Long-term rate</div></div>'
                     f'<div class="bigmetric"><div class="v">{money(est["total_savings"])}</div>'
                     '<div class="l">Est. federal savings</div></div></div>')
        parts.append(_f_table_html(
            ["Bucket", "Loss $", "Gain $", "Sheltered $", "Savings $"],
            [[label.replace("-", " ").title(), round(d["loss"], 2),
              round(d["gain"], 2), round(d["sheltered"] + d["cross_sheltered"], 2),
              round(d["savings"] + d["cross_savings"], 2)]
             for label, d in est["detail"].items()]))
        if est["deducted_vs_income"]:
            parts.append(f'<p class="fine">{money(est["deducted_vs_income"])} of leftover loss deducts '
                         "ordinary income this year (cap $3,000/yr).</p>")
        if est["carryforward"]:
            yrs = est["carryforward"] / 3000.0
            parts.append(f'<p class="fine">{money(est["carryforward"])} carries forward to next year — '
                         f"about {yrs:.1f} more years of $3,000 deductions.</p>")
        cov = []
        for label in ("short-term", "long-term"):
            d = est["detail"][label]
            sheltered = d["sheltered"] + d["cross_sheltered"]
            if d["gain"] > 0:
                pct = min(sheltered / d["gain"] * 100.0, 100.0)
                note = f"{money(sheltered)} of {money(d['gain'])} sheltered"
            else:
                pct, note = 0.0, "No gains to shelter — losses deduct income instead"
            cov.append(
                f'<div class="cov"><div class="covhead"><b>{label.replace("-", " ").title()} gains</b>'
                f'<span>{note}</span></div><div class="covtrack">'
                f'<div class="covfill" style="width:{pct:.0f}%"></div></div></div>')
        parts.append('<div class="decklabel">Gain coverage</div>' + "".join(cov))
        parts.append("<h2>2026 federal brackets</h2>"
                     '<p class="fine">Your marginal rate prices each sheltered dollar. '
                     "The bar is illustrative, not to scale.</p>"
                     + bracket_bar(income, status) +
                     '<p class="fine">Single filer, Rev. Proc. 2025-32. Your marginal rate '
                     "prices each sheltered dollar.</p>"
                     """
    <table class="bracktable">
      <tr><th>Rate</th><th>Taxable income</th></tr>
      <tr><td>10%</td><td>to $12,400</td></tr>
      <tr><td>12%</td><td>$12,401 – $50,400</td></tr>
      <tr><td>22%</td><td>$50,401 – $105,700</td></tr>
      <tr><td>24%</td><td>$105,701 – $201,775</td></tr>
      <tr><td>32%</td><td>$201,776 – $256,225</td></tr>
      <tr><td>35%</td><td>$256,226 – $640,600</td></tr>
      <tr><td>37%</td><td>over $640,600</td></tr>
    </table>
    """)
        # --- Triples ---
        parts.append("<h2>Tax Triples</h2>"
                     '<p class="fine">Each group: sell a loser for the loss, sell a winner against it, '
                     "then buy a wash-sale-safe alternative with the proceeds. "
                     "Ranked by dollars saved.</p>")
        try:
            alt_cache = {t: [a, r, p] for t, (a, r, p) in
                         ((k, tuple(v)) for k, v in s.get("alt_cache", {}).items())}
            alt_cache = rt.update_alt_cache(
                alt_cache, [o["ticker"] for o in opps])
            s["alt_cache"] = {t: list(v) for t, v in alt_cache.items()}
            triples = rt.suggest_tax_triples(
                opps, gains, est["short_term_rate"], est["long_term_rate"],
                alt_prices={a: p for a, _, p in alt_cache.values() if p},
                alt_overrides={t: (a, r) for t, (a, r, _) in alt_cache.items()})
        except Exception:
            triples = None
        if not triples:
            parts.append('<div class="verdict">No winners to build triples right now -- '
                         "harvest the losses on their own.</div>")
        else:
            for num, triple in enumerate(triples, 1):
                badge = ('<span class="badge long">TAX-FREE REBALANCE</span>'
                         if triple["fully_covered"]
                         else '<span class="badge short">PARTIAL OFFSET</span>')
                if triple["buy_shares"]:
                    buy_line = (f"Buy {triple['buy_ticker']} -- {triple['buy_reason']} "
                                f"(&asymp;{triple['buy_shares']:.1f} shares with "
                                f"{money(triple['buy_proceeds'])}).")
                else:
                    buy_line = (f"Buy {triple['buy_ticker']} -- {triple['buy_reason']} "
                                f"(price unavailable, size it to {money(triple['buy_proceeds'])}).")
                parts.append(f"""
        <div class="step" style="margin-bottom:10px">
          <div class="t">Triple {num} {badge}</div>
          <div class="d">1 &middot; Sell {triple['loss_ticker']} -- realize a
          {money(triple['loss_amount'])} loss
          ({'long' if triple['loss_long_term'] else 'short'}-term).<br>
          2 &middot; Sell {triple['gain_ticker']} -- realize a
          {money(triple['gain_amount'])} gain
          ({'long' if triple['gain_long_term'] else 'short'}-term) &rarr;
          {money(triple['net_taxable'])} left taxable.<br>
          3 &middot; {buy_line}<br>
          <strong>Save ~{money(triple['savings_vs_gain_alone'])}</strong> vs selling
          {triple['gain_ticker']} alone.</div>
        </div>
        """)
        # --- Action plan ---
        parts.append("<h2>Your action plan</h2>")
        if not opps:
            parts.append('<div class="verdict">Nothing to do yet — no harvestable losses '
                         "at this threshold.</div>"
                         '<form action="/faq" method="get"><button>How harvesting works &rarr;</button></form>')
        else:
            checks = s.get("plan_checks", [])
            total = len(opps) + 2
            done = sum(1 for o in opps if f"sell_{o['ticker']}" in checks)
            done += ("wait" in checks) + ("advisor" in checks)
            items = "".join(
                f'<label><input type="checkbox" name="plan" value="sell_{_h.escape(o["ticker"])}"'
                f'{" checked" if f"sell_{o["ticker"]}" in checks else ""} onchange="this.form.submit()"> '
                f'Sell {_h.escape(o["ticker"])} — lock in a {money(o["unrealized_loss"])} loss '
                f'({"long" if o["is_long_term"] else "short"}-term)</label><br>'
                for o in opps)
            items += (f'<label><input type="checkbox" name="plan" value="wait"'
                      f'{" checked" if "wait" in checks else ""} onchange="this.form.submit()"> '
                      f"Wait 31 days before rebuying — clear after {rebuy_date}</label><br>"
                      f'<label><input type="checkbox" name="plan" value="advisor"'
                      f'{" checked" if "advisor" in checks else ""} onchange="this.form.submit()"> '
                      "Confirm timing and replacements with your tax advisor</label>")
            pct = done / total * 100.0 if total else 0.0
            parts.append('<p class="fine">Work top to bottom. Ticks persist until you refresh prices.</p>'
                         '<form action="/analyze" method="post">' + items +
                         f'<div class="covtrack"><div class="covfill" style="width:{pct:.0f}%"></div></div>'
                         f'<p class="fine">{done} of {total} steps complete</p></form>')
        # --- Charts ---
        parts.append('<div class="decklabel">Losses at a glance</div>'
                     + gain_loss_chart(positions) +
                     '<p class="fine">Hover any bar for paid vs current. Green is gain, red is harvestable.</p>'
                     "<h2>Allocation by current value</h2>"
                     + allocation_chart(positions))
        # --- Register ---
        parts.append('<div class="decklabel">The full register</div>'
                     f'<p class="fine">{len(positions)} positions · Rebuy clock: 31 days '
                     f"(clear after {rebuy_date}).</p>"
                     + _f_table_html(
                         ["Ticker", "Shares", "Paid $", "Now $", "P/L $", "P/L %", "Term"],
                         [[p["ticker"], p["shares"], round(p["buy_price"], 2),
                           round(p["current_price"], 2),
                           round(p["unrealized_gain_loss"], 2),
                           round(p["gain_loss_percentage"], 1),
                           "Long" if p["is_long_term"] else "Short"]
                          for p in positions]))
        # --- Countdown ---
        rows = deadline_rows(datetime.date.today())
        cells = []
        for r in rows:
            if r["state"] == "past":
                when = f"{abs(r['days_left'])}d ago"
                stamp = '<span class="ledstamp past">PAST</span>'
            elif r["state"] == "urgent":
                when = "today" if r["days_left"] == 0 else f"{r['days_left']}d left"
                stamp = '<span class="ledstamp urgent">URGENT</span>'
            else:
                when = f"{r['days_left']}d left"
                stamp = '<span class="ledstamp duenext">DUE NEXT</span>'
            cells.append(
                f'<tr class="ledrow {r["state"]}"><td><b>{_h.escape(r["label"])}</b>'
                f'<br><span class="lednote">{_h.escape(r["note"])}</span></td>'
                f"<td>{r['iso']}</td><td>{when}</td><td>{stamp}</td></tr>")
        parts.append("<h2>2026 tax-year countdown desk</h2>"
                     "<p class='fine'>Real 2026 dates. Days remaining count from today; "
                     "harvests settle by Dec 31, 2026.</p>"
                     '<table class="ledger"><tr><th>Deadline</th><th>Date</th>'
                     "<th>Countdown</th><th>Status</th></tr>" + "".join(cells) + "</table>"
                     f"<p class='fine'>Leftover losses still deduct up to {money(3000)} "
                     "of ordinary income per year; the rest carries forward.</p>")
        parts.append('<p class="fine">Figures reflect the share counts and cost basis entered. '
                     "Educational estimates only, not tax advice. The 30-day wash-sale rule may "
                     "disallow losses on repurchased securities.</p>")
        return "".join(parts)

    def _f_analyze_html(error=""):
        import html as _h
        s = _f_state()
        GREEN_, RED_, GOLD_, INK_, MUTED_, _ = _f_palette(s["theme"])
        rows = s["rows"]
        filled = [r for r in rows if r.get("ticker")]
        try:
            oldest = min(r["date"] for r in filled)
        except Exception:
            oldest = None
        has_results = s["portfolio"] is not None
        s1, s2 = ("done", "current") if has_results else ("current", "todo")
        csv_preview = s.pop("csv_preview", None)
        if csv_preview:
            prev_rows = [[t, d, sh, (b if b else "auto")]
                         for t, d, sh, b in csv_preview["entries"]]
            csv_html = (_f_table_html(["Ticker", "Date", "Shares", "Buy $"], prev_rows)
                        + '<form action="/analyze" method="post">'
                          '<button name="action" value="csv_load">Load '
                          f'{len(prev_rows)} holdings</button></form>')
        else:
            csv_html = ""
        err_html = f'<div class="error">{_h.escape(error)}</div>' if error else ""
        body = f"""
<div class="stepper"><div class="stp {s1}"><span>1</span>Ledger</div>
<div class="stpline"></div><div class="stp {s2}"><span>2</span>Dossier</div></div>
<div class="deskmast">
  <div class="decklabel">The harvest desk &middot; Vol. 2026</div>
  <h2>Enter the ledger. Leave with a plan.</h2>
  <p>Left: every position on the books. Right: the clerk's rail —
  what you hold, what you assume, when you run.</p>
</div>
{err_html}
<div class="ledgerdesk">
<div class="decklabel">I. Holdings ledger</div>
<details><summary>Import positions from a broker CSV</summary>
<p class="fine">Headers optional. Loading a file replaces your current list.</p>
<form action="/analyze" method="post" enctype="multipart/form-data">
<input type="file" name="csvfile" accept=".csv">
<input name="fallback_date" type="date" value="2023-01-02">
<button name="action" value="csv_preview">Preview CSV</button>
</form>{csv_html}</details>
<form action="/analyze" method="post">
{_f_ledger_table(rows)}
<p class="fine">{len(filled)} holdings listed"""
        if oldest:
            import datetime as _dt
            body += f" &middot; oldest purchase {_dt.date.fromisoformat(oldest).strftime('%b %d, %Y')}"
        body += """ &middot; prices fetch when you run the analysis.</p>
<button name="action" value="save_rows">Save ledger</button>
<button name="action" value="add_row">+ Add holding</button>
<button name="action" value="clear_rows">Remove all</button>
</form></div>
<div class="clerkrail">
<div class="decklabel">Clerk&rsquo;s rail</div>
"""
        body += f"""
    <div class="railrow"><span>Positions</span><b>{len(filled)}</b></div>
    <div class="railrow"><span>Oldest buy</span><b>{oldest or "—"}</b></div>
    <div class="railrow"><span>Loss threshold</span><b>{s["min_loss"]:g}%</b></div>
    <div class="railrow"><span>Income / filing</span><b>{money(s["income"])} &middot; {s["status"]}</b></div>
    <div class="railnote">Assumptions set below. Threshold refilters instantly — no refetch.</div>
<form action="/analyze" method="post">
<label>Taxable income ($/yr) <input name="income" type="number" step="any" min="0" value="{s['income']}"></label><br>
<label>Filing status <select name="status">
<option value="single"{' selected' if s['status'] == 'single' else ''}>single</option>
<option value="joint"{' selected' if s['status'] == 'joint' else ''}>joint</option>
</select></label><br>
<label>Minimum loss % to flag <input name="min_loss" type="number" step="0.5" min="0.5" max="25" value="{s['min_loss']}"></label><br>
<button name="action" value="save_assumptions">Save assumptions</button>
</form>
<form action="/analyze" method="post">
<button name="action" value="run">Run harvest analysis</button>
<button name="action" value="refresh">Refresh live prices</button>
</form>
</div>
"""
        if s["portfolio"] is not None:
            body += _f_dossier_html(s, GREEN_, RED_)
        return body

    def _f_entries_from_rows(rows):
        entries = []
        for r in rows:
            t = (r.get("ticker") or "").strip().upper()
            if not t:
                continue
            d = r.get("date")
            if isinstance(d, str):
                d = datetime.date.fromisoformat(d)
            entries.append((t, d, float(r.get("shares", 0)),
                            float(r.get("buy") or 0) or None))
        return entries

    def _f_run_analysis(s):
        entries = _f_entries_from_rows(s["rows"])
        if not entries:
            return "Add at least one ticker to analyze."
        tickers_now = {t for t, _, _, _ in entries}
        broker = {t: p for t, p in s.get("csv_live", {}).items()
                  if t in tickers_now}
        try:
            holdings, live = rt.price_holdings(entries, broker)
            s["portfolio"] = {"holdings": json.loads(json.dumps(holdings, default=str)),
                              "live": live,
                              "at": datetime.datetime.now().strftime("%H:%M:%S")}
            s["alt_cache"] = {}
            s["harvest_pick"] = None
            s["plan_checks"] = []
        except ValueError as exc:
            return str(exc)
        except Exception as exc:
            return f"Price fetch failed: {exc}"
        return ""

    @app.get("/theme")
    def theme():
        s = _f_state()
        t = request.args.get("theme", "")
        if t in ("ledger", "nordic", "terminal"):
            s["theme"] = t
        nxt = request.args.get("next", "/")
        if nxt not in ("/", "/analyze", "/faq"):
            nxt = "/"
        return redirect(nxt)

    @app.get("/")
    def index():
        return Response(_f_page("HRVST | Tax-Loss Harvesting", "Home", _f_home_html()),
                        mimetype="text/html")

    @app.post("/triage")
    def triage():
        s = _f_state()
        sym = (request.form.get("symbol") or "").strip().upper()
        if not sym:
            s["triage"] = {"error": "Type a symbol first."}
        else:
            verdict = _f_net(lambda: _triage_verdict(sym), timeout=25)
            s["triage"] = verdict if verdict else {"error": "Price unavailable — try again."}
        return redirect(url_for("index"))

    @app.post("/sample")
    def sample():
        s = _f_state()
        s["rows"] = [dict(r) for r in _FLASK_SAMPLE_ROWS]
        s["portfolio"] = None
        s["csv_live"] = {}
        err = _f_run_analysis(s)
        if err:
            return Response(_f_page("HRVST | Analyze", "Analyze",
                                    _f_analyze_html(err)), mimetype="text/html")
        return redirect("/analyze")

    @app.get("/faq")
    def faq():
        return Response(_f_page("HRVST | Desk Reference", "FAQ", _f_faq_html()),
                        mimetype="text/html")

    @app.get("/analyze")
    def analyze_get():
        return Response(_f_page("HRVST | Analyze", "Analyze", _f_analyze_html()),
                        mimetype="text/html")

    @app.post("/analyze")
    def analyze_post():
        s = _f_state()
        form = request.form
        if "pick" in form or "plan" in form:
            if "pick" in form:
                s["harvest_pick"] = form.getlist("pick")
            if "plan" in form:
                s["plan_checks"] = form.getlist("plan")
            return redirect("/analyze")
        # sync ledger inputs first so every action sees current edits
        rows = []
        i = 0
        while f"ticker_{i}" in form:
            rows.append({"ticker": (form.get(f"ticker_{i}") or "").strip().upper(),
                         "shares": float(form.get(f"shares_{i}") or 0),
                         "buy": float(form.get(f"buy_{i}") or 0),
                         "date": form.get(f"date_{i}") or "2023-01-02"})
            i += 1
        if rows or "action" in form:
            s["rows"] = rows
        action = form.get("action", "")
        if "remove" in form:
            try:
                rows.pop(int(form["remove"]))
            except Exception:
                pass
            s["rows"] = rows
            s["portfolio"] = None
        elif action == "add_row":
            rows.append({"ticker": "", "shares": 1.0, "buy": 0.0,
                         "date": datetime.date.today().isoformat()})
            s["rows"] = rows
            s["portfolio"] = None
        elif action == "clear_rows":
            s["rows"] = []
            s["portfolio"] = None
            s["alt_cache"] = {}
            s["csv_live"] = {}
        elif action == "save_rows":
            s["portfolio"] = None
        elif action == "save_assumptions":
            try:
                if form.get("theme") in ("ledger", "nordic", "terminal"):
                    s["theme"] = form.get("theme")
                s["income"] = float(form.get("income", s["income"]))
                if form.get("status") in ("single", "joint"):
                    s["status"] = form.get("status")
                s["min_loss"] = float(form.get("min_loss", s["min_loss"]))
            except Exception:
                pass
        elif action == "csv_preview":
            up = request.files.get("csvfile")
            if up is not None:
                try:
                    parsed = rt.parse_csv_holdings(
                        up.read().decode("utf-8", "ignore"),
                        form.get("fallback_date") or "2023-01-02")
                    s["csv_preview"] = {"entries": [list(e) for e in parsed["entries"]],
                                        "live": dict(parsed.get("live") or {})}
                except Exception as exc:
                    return Response(_f_page("HRVST | Analyze", "Analyze",
                                            _f_analyze_html(f"CSV error: {exc}")),
                                    mimetype="text/html")
        elif action == "csv_load":
            prev = s.get("csv_preview") or {}
            if prev.get("entries"):
                s["rows"] = [{"ticker": t, "shares": float(sh),
                              "buy": float(b) if b else 0.0, "date": d}
                             for t, d, sh, b in prev["entries"]]
                s["csv_live"] = dict(prev.get("live") or {})
                s["portfolio"] = None
                s["alt_cache"] = {}
        elif action == "run":
            err = _f_run_analysis(s)
            if err:
                return Response(_f_page("HRVST | Analyze", "Analyze",
                                        _f_analyze_html(err)), mimetype="text/html")
        elif action == "refresh":
            s["portfolio"] = None
            s["alt_cache"] = {}
            err = _f_run_analysis(s)
            if err:
                return Response(_f_page("HRVST | Analyze", "Analyze",
                                        _f_analyze_html(err)), mimetype="text/html")
        return redirect("/analyze")

    @app.get("/download")
    def download():
        import io as _io
        s = _f_state()
        cache = s.get("portfolio") or {"holdings": [], "live": {}}
        result = _cached_analysis(json.dumps(cache["holdings"], default=str),
                                  s["min_loss"], json.dumps(dict(cache["live"])))
        pick = s.get("harvest_pick")
        opps = result["opportunities"]
        if pick is not None:
            opps = [o for o in opps if o["ticker"] in pick]
        rebuy = (datetime.date.today() + datetime.timedelta(days=31)).isoformat()
        df = pd.DataFrame([{
            "Ticker": o["ticker"], "Shares": o["shares"],
            "Now $": round(o["current_price"], 2),
            "Loss $": round(o["unrealized_loss"], 2),
            "Loss %": round(o["loss_percentage"], 1),
            "Term": "Long" if o["is_long_term"] else "Short",
            "Rebuy >=": rebuy} for o in opps])
        buf = _io.StringIO()
        df.to_csv(buf, index=False)
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition":
                                 "attachment; filename=hrvst_harvest_plan.csv"})

    @app.post("/api/analyze")
    def api_analyze():
        body = request.get_json(force=True, silent=True) or {}
        try:
            entries = []
            for e in body.get("entries", []):
                d = e.get("date")
                if isinstance(d, str):
                    d = datetime.date.fromisoformat(d)
                entries.append((e["ticker"], d, float(e.get("shares", 0)),
                                e.get("buy")))
            min_loss = float(body.get("min_loss", 5.0))
            income_v = float(body.get("income", 150000.0))
            status_v = body.get("status", "single")
            live = dict(body.get("live") or {})
            holdings, live = rt.price_holdings(entries, live)
            result = _cached_analysis(json.dumps(holdings, default=str),
                                      min_loss, json.dumps(live))
            gains = rt.position_gains(result["positions"])
            est = rt.estimate_tax_savings(result["opportunities"], gains,
                                          income_v, status_v)
            return jsonify(json.loads(json.dumps(
                {"opportunities": result["opportunities"],
                 "positions": result["positions"],
                 "totals": result["totals"],
                 "estimate": est}, default=str)))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    def _f_serve(host="127.0.0.1", port=8000):
        """Serve `app` with a threaded stdlib WSGI server (no new deps).

        `app` stays a plain WSGI callable, so any external WSGI container
        can serve it too (e.g. `gunicorn MP:app`, `waitress-serve MP:app`).
        HOST/PORT env vars override the defaults for deployment.
        """
        import os as _os
        from wsgiref.simple_server import WSGIServer, WSGIRequestHandler
        from socketserver import ThreadingMixIn
        host = _os.environ.get("HOST", host)
        port = int(_os.environ.get("PORT", port))

        class _ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
            daemon_threads = True

        class _Quiet(WSGIRequestHandler):
            def log_message(self, *args):
                pass

        with _ThreadedWSGIServer((host, port), _Quiet) as httpd:
            httpd.set_app(app)
            print(f"HRVST on http://{host}:{port} (WSGI, threaded stdlib)")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass

    if __name__ == "__main__":
        _f_serve()
else:
    app = None
