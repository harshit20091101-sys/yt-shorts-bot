import os, sys, json, re, time, random, asyncio, subprocess, datetime, math, shutil
from urllib.parse import quote
import requests
from PIL import Image, ImageDraw, ImageFont, features

W, H, FPS = 1080, 1920, 30
WORK, OUT = "work", "out"
for d in (WORK, OUT):
    os.makedirs(d, exist_ok=True)

DRY_RUN = os.environ.get("DRY_RUN", "") == "1"
VOICE = os.environ.get("VOICE", "hi-IN-MadhurNeural")  # female: hi-IN-SwaraNeural
SPEED = float(os.environ.get("SPEED", "1.2"))           # voice speed 1.2x
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
POLL_TOKEN = os.environ.get("POLLINATIONS_TOKEN", "")
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
    for rnd in range(4):  # server busy (503/429) ho to wait karke dobara
        if rnd:
            wait = 30 * rnd
            print(f"Gemini busy/fail, {wait}s wait, round {rnd + 1}...")
            time.sleep(wait)
        for model in MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            body = {"contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json"}}
            try:
                r = requests.post(url, headers={"x-goog-api-key": GEMINI_KEY,
                                                "Content-Type": "application/json"},
                                  json=body, timeout=180)
                if r.status_code != 200:
                    print(model, "HTTP", r.status_code, r.text[:200])
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
- Total narration: 170 to 200 Hindi words, split into 9 to 11 scenes. Each scene = 1 or 2 short sentences (max 26 words).

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
        if len(scenes) >= 7 and words >= 120:
            data["scenes"] = scenes[:14]
            data.setdefault("title", "Ek ajeeb kahani")
            return data
    raise RuntimeError("Story too short/poor after 3 tries")


# ---------------------------------------------------------------- voice
async def _edge(text, out):
    import edge_tts
    pct = int(round((SPEED - 1) * 100))
    await edge_tts.Communicate(text, VOICE, rate=f"{pct:+d}%").save(out)


def speed_up(src, dst):
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
         "-filter:a", f"atempo={SPEED}", dst])


def tts(text, out):
    try:
        asyncio.run(_edge(text, out))
        if os.path.getsize(out) > 1000:
            return
    except Exception as e:
        print("edge-tts failed, gTTS fallback:", e)
    from gtts import gTTS
    tmp = out + ".slow.mp3"
    gTTS(text=text, lang="hi").save(tmp)
    speed_up(tmp, out)


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
            print(f"image try {attempt + 1}: HTTP {r.status_code} {ctype}")
        except Exception as e:
            print(f"image try {attempt + 1} error:", e)
        time.sleep(20 + attempt * 10)
    return False


def fill_image_gaps(paths, ok_flags):
    """Jis scene ki image nahi aayi, usme sabse paas wali kaam-wali image copy karo."""
    good = [i for i, ok in enumerate(ok_flags) if ok]
    if not good:
        return False
    for i, ok in enumerate(ok_flags):
        if not ok:
            j = min(good, key=lambda g: abs(g - i))
            shutil.copyfile(paths[j], paths[i])
            ok_flags[i] = True
            print(f"scene {i + 1}: image missing, scene {j + 1} ki image reuse ki")
    return True


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


def write_info(story):
    tags = ", ".join(str(t) for t in story.get("tags", []))
    txt = (
        "TITLE:\n" + story["title"].strip()[:88] + " #Shorts\n\n"
        "DESCRIPTION:\n" + story.get("description", "") + "\n\n#Shorts #HindiStory #Kahani\n\n"
        "TAGS:\n" + tags + "\n\n"
        "STUDIO SETTINGS:\n- Made for kids: No\n- Altered content (AI-generated): Yes\n"
    )
    with open(f"{OUT}/info.txt", "w", encoding="utf-8") as f:
        f.write(txt)
    meta = {"title": story["title"].strip()[:88] + " #Shorts",
            "description": story.get("description", "") + "\n\n#Shorts #HindiStory #Kahani",
            "tags": tags}
    with open(f"{OUT}/meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


def make_small_video(src, dst, target_bytes=4_200_000, limit_bytes=4_600_000):
    """Make.com free plan ki 5 MB file limit ke liye chhoti 720p copy."""
    dur = duration(src)
    audio_k = 64
    vid_k = max(int(target_bytes * 8 / dur / 1000) - audio_k, 250)
    for _ in range(3):
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf", "scale=720:1280",
             "-c:v", "libx264", "-preset", "medium", "-b:v", f"{vid_k}k",
             "-maxrate", f"{int(vid_k * 1.3)}k", "-bufsize", f"{vid_k * 2}k",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{audio_k}k", "-ac", "1",
             "-movflags", "+faststart", dst])
        size = os.path.getsize(dst)
        print(f"small video: {size / 1e6:.2f} MB at {vid_k}k")
        if size <= limit_bytes:
            return
        vid_k = int(vid_k * 0.8)
    raise RuntimeError("small video 5 MB se bada reh gaya")


# ---------------------------------------------------------------- upload (optional)
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
            st["containsSyntheticMedia"] = True
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
                print("::warning title=Video private lock::YouTube API audit pending hai, video private rahegi.")
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
    scenes = story["scenes"]
    n = len(scenes)

    audios, imgs, ok_flags, durs = [], [], [], []
    for i, sc in enumerate(scenes):
        print(f"--- scene {i + 1}/{n}")
        a = f"{WORK}/a{i}.mp3"
        tts(sc["narration"].strip(), a)
        audios.append(a)
        durs.append(duration(a) + 0.25)
        p = f"{WORK}/i{i}.jpg"
        ok = fetch_image(sc["image_prompt"].strip() + ", " + STYLE, random.randint(1, 999999), p, i == 0)
        imgs.append(p)
        ok_flags.append(ok)

    real_ok = sum(ok_flags)
    print(f"IMAGES OK: {real_ok}/{n}")
    have_images = fill_image_gaps(imgs, ok_flags)

    clips = []
    for i, sc in enumerate(scenes):
        bg, cap, clip = f"{WORK}/bg{i}.jpg", f"{WORK}/c{i}.png", f"{WORK}/clip{i}.mp4"
        make_bg(imgs[i] if have_images else None, bg)
        caption_png(sc["narration"].strip(), cap)
        make_clip(i, bg, cap, audios[i], durs[i], clip)
        clips.append(clip)

    final = f"{OUT}/short.mp4"
    concat(clips, final)
    write_info(story)
    try:
        make_small_video(final, f"{OUT}/short_small.mp4")
    except Exception as e:
        print("small video fail:", e)
    total = duration(final)
    print(f"VIDEO LENGTH: {total:.1f}s")

    if not have_images:
        print("::warning title=Images nahi aayi::Aaj koi image nahi bani, upload/history skip. Kal dobara try hoga.")
        return
    if total < 25:
        print("::warning title=Video chhoti::Video 25 sec se chhoti hai, upload skip.")
        return
    if DRY_RUN:
        print("DRY RUN: upload aur history skip")
        return
    with open(f"{OUT}/READY", "w") as f:
        f.write("1")

    if os.environ.get("YT_REFRESH_TOKEN"):
        try:
            upload(final, story)
        except Exception as e:
            print(f"::warning title=YouTube upload fail::{e}. Video artifact me hai, manual upload kar sakte ho.")
    else:
        print("MANUAL MODE: video ready. Artifacts se download karke YouTube Studio me upload karo.")
    history.append(story["title"])
    save_history(history)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"::warning title=Aaj ka Short nahi bana::{e}")
        sys.exit(0)  # workflow red na ho; kal phir chalega
