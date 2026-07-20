import logging
import time
import re
import threading
from mitmproxy import http
from openrouter import OpenRouter

API_KEY = ""

logging.basicConfig(filename="zappa_proxy.log", level=logging.INFO)

client = OpenRouter(api_key=API_KEY)

SYSTEM_PROMPT = """
You are Zappa, a Web 1.0 CSS designer. Based on the sample HTML below, generate a CSS style block that transforms the page into a clean old-school web look.
- Fonts: Times New Roman or Courier
- Background: #00aff0, text: #1a1a1a
- Blue links, simple flat design
- No gradients, no animations, no rounded corners, no shadows
- Use !important to override everything
- Also hide common ad elements (selectors containing "ad", "sponsored", "promo")
Return ONLY raw CSS inside a <style> tag. No explanation, no markdown.
"""

WEB1_FALLBACK = """
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Times New Roman', Times, serif; background: #f5f0eb; color: #1a1a1a; padding: 20px; line-height: 1.6; }
a { color: #0000cc; }
img { max-width: 100%; }
h1, h2, h3, h4 { margin: 1em 0 0.5em; }
p, ul, ol { margin: 0.5em 0; }
</style>
"""



def response(flow: http.HTTPFlow) -> None:
    INTERNAL_NOISE = ["mozilla", "firefox", "safebrowsing", "telemetry", "detectportal", "ocsp"]
    request_url = flow.request.url.lower()
    if any(noise in request_url for noise in INTERNAL_NOISE):
        return

    if not flow.response or flow.response.status_code != 200:
        return

    content_type = flow.response.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type:
        return

    accept = flow.request.headers.get("Accept", "")
    if "text/html" not in accept:
        return

    try:
        full_html = flow.response.text
        if not full_html or len(full_html) < 50:
            return

        #sample = strip_bloat(full_html)[:5000]
        logging.info(f"Transforming {flow.request.url} ({len(full_html) / 1024:.1f} KB)...")

        t0 = time.time()
        done = threading.Event()
        def tick():
            while not done.wait(5):
                logging.info(f"  waiting... {time.time() - t0:.0f}s")
        thr = threading.Thread(target=tick, daemon=True)
        thr.start()

        style = None
        try:
            resp = client.chat.send(
                model="poolside/laguna-m.1:free",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": full_html}
                ],
                temperature=0.1,
            )
            done.set()
            style = resp.choices[0].message.content
            # If model returned nothing useful, fall back to the built-in Web 1.0 CSS
            if not style or len(style) < 50:
                logging.info("  model returned empty/short CSS, using fallback")
                style = WEB1_FALLBACK
        except Exception:
            done.set()
            # On any API error, use the fallback so the page is still styled
            logging.error("  API error, using fallback")
            style = WEB1_FALLBACK

        # Only inject if we actually have a style block
        if style:
            # Insert our marker so we can find/avoid duplicate injection later
            marker_open = "<!-- ZAPPA_STYLE_START -->"
            marker_close = "<!-- ZAPPA_STYLE_END -->"
            injected = f"{marker_open}{style}{marker_close}"
            if "</head>" in full_html:
                # Normal case: put the style inside <head>
                full_html = full_html.replace("</head>", injected + "</head>", 1)
            elif "<head>" in full_html:
                full_html = full_html.replace("<head>", "<head>" + injected, 1)
            else:
                # No head tag at all: inject right after <html> or at the very top
                if "<html" in full_html:
                    full_html = full_html.replace("<html", injected + "<html", 1)
                else:
                    full_html = injected + full_html

        # Write the (possibly modified) HTML back into the response
        flow.response.text = full_html
        logging.info(f"  done ({time.time() - t0:.1f}s)")

    except Exception as e:
        done.set()
        logging.error(f"  error: {e}")
