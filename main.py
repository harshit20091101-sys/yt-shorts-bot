import os, json, re, time, random, asyncio, subprocess, datetime, math
from urllib.parse import quote
import requests
from PIL import Image, ImageDraw, ImageFont, features

W, H, FPS = 1080, 1920, 30
WORK, OUT = "work", "out"
os.makedirs(WORK, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

DRY_RUN = os.environ.get("DRY_RUN", "") == "1"
VOICE = os.environ.get("VOICE", "hi-IN-MadhurNeural")  # female: hi-IN-SwaraNeural
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
POLL_TOKEN = os.environ.get("POLLINATIONS_TOKEN", "")
# Pehla model chalega, fail hua to agla. Model naam badle to GEMINI_MODEL secret/env me daalo.
MODELS = [m for m in [os.environ.get("GEMINI_MODEL"), "gemini-3.8-flash",
                      "gemini-3.6-flash", "gemini-3.1-flash-lite"] if m]

GENRES = [
    "horror story set in an Indian village",
    "mystery with a shocking twist ending",
    "emotional story with a heart-touching ending",
    "revenge thriller with a clever twist",
    "folk tale / lok katha with a surprising lesson",
    "sci-fi story set in near-future India",
    "friendship and betrayal story",
    "haunted old haveli or hotel story",
]
STYLE = ("cinematic, dramatic lighting, highly detailed, vertical 9:16 composition, "
         "no text, no letters, no watermark")

FONT = None


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2000:])
        raise RuntimeError("command failed: " + " ".join(cmd[:2]))
    return r.stdout


# ---------------------------------------------------------------- history
def load_history():
    try:
        return json.load(open("history.json", encoding="utf-8"))
    except Exception:
        return []


def save_history(h):
    with open("history.json", "w", encoding="utf-8") as f:
        json.dump(h[-200:], f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- story
def ask_gemini(prompt):
    last = None
    for model in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json"}}
        try:
            r = requests.post(url, headers={"x-goog-api-key": GEMINI_KEY,
                                            "Content-Type": "application/json"},
                              json=body, timeout=180)
            if r.status_code != 200:
                print(model, "HTTP", r.status_code, r.text[:300])
                last = f"{model}: {r.status_code}"
                continue
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
            print("story model:", model)
            return json.loads(text)
        except Exception as e:
            print(model, "error:", e)
            last = f"{model}: {e}"
    raise RuntimeError(f"Gemini fail: {last}")


PROMPT = """You are the best Hindi storyteller for YouTube Shorts. Write ONE original short story.

Genre today: __GENRE__

NARRATION RULES
- Hindi in Devanagari script ONLY (simple, spoken, conversational Hindi). No English letters. Write numbers in words.
- Structure: (1) a shocking hook in the very first sentence, (2) fast build-up with concrete details like names, places, objects, (3) a twist, (4) a memorable last line.
- No moral lecture, no "dosto aaj hum..." intro. Start directly inside the story.
- Total narration: 150 to 180 Hindi words, split into 9 to 11 scenes. Each scene = 1 or 2 short sentences (max 22 words).

IMAGE RULES
- For every scene give image_prompt in English, 25 to 40 words, one concrete cinematic visual.
- Describe the main character the same way in every prompt (age, clothes, look).
- No text, letters or signs inside images.

Do NOT reuse ideas from these earlier titles: __TITLES__

Return ONLY JSON in exactly this shape:
{"title": "catchy Hindi title, max 60 chars", "description": "2 short Hindi lines",
 "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
 "scenes": [{"narration": "...", "image_prompt": "..."}]}"""


def make_story(history):
    genre = os.environ.get("GENRE") or GENRES[datetime.date.today().toordinal() % len(GENRES)]
    prompt = PROMPT.replace("__GENRE__", genre).replace("__TITLES__", "; ".join(history[-40:]) or "none")
    print("genre:", genre)
    for attempt in range(3):
        data = ask_gemini(prompt)
        scenes = [s for s in data.get("scenes", []) if s.get("narration") and s.get("image_prompt")]
        words = sum(len(s["narration"].split()) for s in scenes)
        print(f"attempt {attempt + 1}: {len(scenes)} scenes, {words} words")
        if len(scenes) >= 7 and words >= 110:
            data["scenes"] = scenes[:14]
            data.setdefault("title", "Ek ajeeb kahani")
            return data
    raise RuntimeError("Story too short/poor after 3 tries")


# ---------------------------------------------------------------- voice
async def _edge(text, out):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate="-5%").save(out)


def tts(text, out):
    try:
        asyncio.run(_edge(text, out))
        if os.path.getsize(out) > 1000:
            return
    except Exception as e:
        print("edge-tts failed, gTTS fallback:", e)
    from gtts import gTTS
    gTTS(text=text, lang="hi").save(out)


def duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", path])
    return float(out.strip())


# ---------------------------------------------------------------- images
def fetch_image(prompt, seed, out, first):
    if not first:
        time.sleep(16)  # anonymous limit: ~1 image / 15 sec
    url = "https://image.pollinations.ai/prompt/" + quote(prompt[:600])
    params = {"width": 720, "height": 1280, "seed": seed, "nologo": "true"}
    headers = {}
    if POLL_TOKEN:
        params["token"] = POLL_TOKEN
        headers["Authorization"] = "Bearer " + POLL_TOKEN
    for attempt in range(5):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=150)
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and ctype.startswith("image") and len(r.content) > 5000:
                with open(out, "wb") as f:
                    f.write(r.content)
                return True
            print(f"image try {attempt + 1}: HTTP {r.status_code} {ctype} {r.text[:120] if 'text' in ctype else ''}")
        except Exception as e:
            print(f"image try {attempt + 1} error:", e)
        time.sleep(20 + attempt * 10)
    return False


def make_bg(src, out):
    if src and os.path.exists(src):
        try:
            im = Image.open(src).convert("RGB")
            s = max(W / im.width, H / im.height)
            im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
            l, t = (im.width - W) // 2, (im.height - H) // 2
            im.crop((l, t, l + W, t + H)).save(out, quality=92)
            return
        except Exception as e:
            print("bad image:", e)
    c1 = tuple(random.randint(10, 60) for _ in range(3))
    c2 = tuple(random.randint(50, 140) for _ in range(3))
    col = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / (H - 1)
        col.putpixel((0, y), tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
    col.resize((W, H)).save(out, quality=92)


# ---------------------------------------------------------------- captions
def find_font():
    cands = ["fonts/Mukta-Bold.ttf"]
    try:
        p = subprocess.run(["fc-match", "-f", "%{file}", "Noto Sans Devanagari:bold"],
                           capture_output=True, text=True).stdout.strip()
        if p:
            cands.append(p)
    except Exception:
        pass
    for c in cands:
        if os.path.exists(c) and os.path.getsize(c) > 10000:
            return c
    raise RuntimeError("Hindi font nahi mila")


def load_font(size):
    kw = {}
    if features.check("raqm"):
        kw["layout_engine"] = ImageFont.Layout.RAQM
    return ImageFont.truetype(FONT, size, **kw)


def wrap(text, font, draw, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def caption_png(text, out):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    probe = ImageDraw.Draw(img)
    size = 76
    font = load_font(size)
    lines = wrap(text, font, probe, W - 150)
    while len(lines) > 5 and size > 52:
        size -= 6
        font = load_font(size)
        lines = wrap(text, font, probe, W - 150)
    lh = int(size * 1.55)
    total = lh * len(lines)
    y = int(H * 0.60 - total / 2)
    pad = 36
    img.alpha_composite(Image.new("RGBA", (W, total + 2 * pad), (0, 0, 0, 120)), (0, y - pad))
    d = ImageDraw.Draw(img)
    for ln in lines:
        x = (W - d.textlength(ln, font=font)) / 2
        d.text((x, y), ln, font=font, fill=(255, 255, 255, 255),
               stroke_width=5, stroke_fill=(0, 0, 0, 255))
        y += lh
    img.save(out)


# ---------------------------------------------------------------- video
def make_clip(idx, bg, cap, audio, dur, out):
    frames = max(int(math.ceil(dur * FPS)), 1)
    inc = 0.12 / frames
    z = f"1+{inc:.8f}*on" if idx % 2 == 0 else f"1.12-{inc:.8f}*on"
    fc = (f"[0:v]scale={W * 2}:{H * 2},"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}[bg];"
          f"[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", bg, "-i", cap, "-i", audio,
         "-filter_complex", fc, "-map", "[v]", "-map", "2:a", "-af", "apad",
         "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-r", str(FPS), "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", out])


def concat(clips, out):
    lst = os.path.join(WORK, "list.txt")
    with open(lst, "w") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", "-movflags", "+faststart", out])


# ---------------------------------------------------------------- upload
def upload(path, story):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.environ["YT_CLIENT_ID"],
                        client_secret=os.environ["YT_CLIENT_SECRET"],
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

    title = story["title"].strip()[:88] + " #Shorts"
    desc = story.get("description", "") + "\n\n#Shorts #HindiStory #Kahani"
    tags = [str(t)[:30] for t in story.get("tags", [])][:12] + ["shorts"]

    def body(synthetic):
        st = {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
        if synthetic:
            st["containsSyntheticMedia"] = True  # AI-generated content disclosure
        return {"snippet": {"title": title, "description": desc, "tags": tags,
                            "categoryId": "24", "defaultLanguage": "hi",
                            "defaultAudioLanguage": "hi"},
                "status": st}

    for synthetic in (True, False):
        try:
            media = MediaFileUpload(path, mimetype="video/mp4", resumable=True,
                                    chunksize=8 * 1024 * 1024)
            req = yt.videos().insert(part="snippet,status", body=body(synthetic), media_body=media)
            resp = None
            while resp is None:
                _, resp = req.next_chunk()
            print("UPLOADED: https://youtube.com/shorts/" + resp["id"])
            if resp.get("status", {}).get("privacyStatus") != "public":
                print("WARNING: video public nahi hai (private lock). YouTube API audit pending hoga.")
            return
        except HttpError as e:
            print("upload error:", e)
            if synthetic and e.resp.status == 400:
                continue
            raise


# ---------------------------------------------------------------- main
def main():
    global FONT
    FONT = find_font()
    print("font:", FONT, "| raqm (Hindi shaping):", features.check("raqm"))
    history = load_history()
    story = make_story(history)
    print("TITLE:", story["title"])

    clips, ok_images = [], 0
    n = len(story["scenes"])
    for i, sc in enumerate(story["scenes"]):
        print(f"--- scene {i + 1}/{n}")
        audio = f"{WORK}/a{i}.mp3"
        tts(sc["narration"].strip(), audio)
        dur = duration(audio) + 0.35
        img = f"{WORK}/i{i}.jpg"
        ok = fetch_image(sc["image_prompt"].strip() + ", " + STYLE, random.randint(1, 999999), img, i == 0)
        ok_images += 1 if ok else 0
        bg, cap, clip = f"{WORK}/bg{i}.jpg", f"{WORK}/c{i}.png", f"{WORK}/clip{i}.mp4"
        make_bg(img if ok else None, bg)
        caption_png(sc["narration"].strip(), cap)
        make_clip(i, bg, cap, audio, dur, clip)
        clips.append(clip)

    final = f"{OUT}/short.mp4"
    concat(clips, final)
    total = duration(final)
    print(f"IMAGES OK: {ok_images}/{n} | VIDEO LENGTH: {total:.1f}s")
    if total < 25:
        raise RuntimeError("Video bahut chhoti hai, upload skip")
    if ok_images < n * 0.5:
        raise RuntimeError("Aadhi se zyada images nahi aayi, upload skip (video artifact me dekh sakte ho)")
    if DRY_RUN:
        print("DRY RUN: upload skip kiya")
        return
    upload(final, story)
    history.append(story["title"])
    save_history(history)


if __name__ == "__main__":
    main()
