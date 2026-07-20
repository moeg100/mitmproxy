import logging
from mitmproxy import http
from openai import OpenAI

# Connect to your remote Ollama machine on your local network
client = OpenAI(
        base_url="http://10.0.0.63:11434/v1",  
        api_key="ollama"
        )

# Set up clean logging to standard log file
logging.basicConfig(filename="zappa_proxy.log", level=logging.INFO)

# --- CONFIGURATION THRESHOLDS ---
MAX_BYTES = 250 * 1024  # 60 KB size safety limit
REQUEST_TIMEOUT = 90.0  # 30 second limit to let the RTX 3050 load the model


SYSTEM_PROMPT =  """
You are Zappa, a lightning-fast local web asset transformer. 
Analyze the provided HTML code. 
Completely remove all ads, trackers, popups, and visual noise.

CRITICAL VISUAL OVERRIDE: 
You must transform this website cleanly into an intense PINK MODE layout.
Directly inject a <style> block into the <head> of the document that forces:
body, html, div, main, section { background-color: #ff69b4 !important; color: #ffffff !important; }
Ensure all headings and paragraph text are pure white or deep magenta so they remain legible.

Return ONLY the cleaned, fully functional version of the website code. 
Do not include markdown wrappers like ```html or any conversational text. Just output the raw transformed code.
"""


#SYSTEM_PROMPT =
"""
You are Zappa, a lightning-fast local web clutter stripper. 
Analyze the provided web asset (HTML). 
Completely remove all ads, popups, tracking scripts, bright distracting colors, moving elements, and tracking code.
Transform the layout structure cleanly into an aesthetic, comfortable PINK MODE. Use pink backgrounds and light gray text.
Return ONLY the cleaned, fully functional version of the website code. 
Make it cyberpunky site.
Do not include markdown wrappers like ```html or any conversational text. Just output the clean code.
"""


def response(flow: http.HTTPFlow) -> None:
    # 1. CORE BROWSER BYPASS: Instantly ignore internal browser noise
    INTERNAL_NOISE = ["mozilla", "firefox", "safebrowsing", "telemetry", "detectportal", "ocsp"]
    request_url = flow.request.url.lower()
    if any(noise in request_url for noise in INTERNAL_NOISE):
        return

    # 2. STATUS CHECK: Proceed only if the page request was fully successful
    if not flow.response or flow.response.status_code != 200:
        return

    # 3. THE GLOBAL FILTER: Check the actual file type format
    content_type = flow.response.headers.get("Content-Type", "").lower()
    
    # UNIVERSAL TRICK: If it's not a real HTML webpage, bypass the AI completely!
    # This automatically drops all background data streams/trackers on ANY site instantly.
    if "text/html" not in content_type:
        return

    # 4. STRIP CACHING: Force servers to always send full content, never an empty '304' status
    flow.response.headers.pop("ETag", None)
    flow.response.headers.pop("Last-Modified", None)
    flow.response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    flow.response.headers["Pragma"] = "no-cache"
    flow.response.headers["Expires"] = "0"

    # 5. SIZE CHECK: Calculate raw byte payload size
    content_size = len(flow.response.raw_content)
    
    # Skip pages that are too massive to avoid VRAM overload or browsing lag
    if content_size > MAX_BYTES:
        logging.info(f"Skipping {flow.request.url} - File size too large ({content_size / 1024:.1f} KB)")
        return

    # 6. AI TRANSFORMATION ENGINE
    try:
        original_content = flow.response.text
        logging.info(f"Interception Active: Transforming {flow.request.url} ({content_size / 1024:.1f} KB)...")

        # Run content extraction through the remote Qwen 3.5 instance
        completion = client.chat.completions.create(
            model="qwen35-64k:latest", 
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": original_content}
            ],
            temperature=0.1,
            timeout=REQUEST_TIMEOUT
        )

        # Robust Parsing Layer: Safely handles local Ollama's choice formatting styles
        if hasattr(completion, 'choices') and len(completion.choices) > 0:
            choice = completion.choices[0]
            if hasattr(choice, 'message'):
                cleaned_content = choice.message.content
            elif isinstance(choice, dict) and 'message' in choice:
                cleaned_content = choice['message']['content']
            else:
                raise ValueError("Could not find message text inside choice structure.")
        else:
            raise ValueError("Unexpected response structure layout from Ollama endpoint.")

        # Inject the AI's clean dark-mode markup directly back into the browser flow
        flow.response.text = cleaned_content
        logging.info(f"Successfully cleaned asset: {flow.request.url}")

    except Exception as e:
        logging.error(f"Transformation Error on {flow.request.url}: {str(e)}")
        # Fallback: If it fails or times out, pass original content so browsing doesn't break
        pass 
