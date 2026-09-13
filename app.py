
import io
import json
import os
import random
import textwrap
import threading
import time
from pathlib import Path

import requests
from flask import Flask, request, jsonify
from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
APP_URL = (os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")

with open(BASE / "questions.json", "r", encoding="utf-8") as f:
    QUESTIONS = json.load(f)

app = Flask(__name__)

sessions = {}      # user_id -> {"index": int}
poll_map = {}      # poll_id -> {"chat_id": int, "user_id": int, "next_index": int}

GREEN = "#006837"
GOLD = "#FFB300"
TEXT = "#171717"
MUTED = "#5A5A5A"
LETTERS = ["A", "B", "C", "D", "E"]


def tg(method, payload=None, files=None, timeout=30):
    r = requests.post(f"{API}/{method}", data=payload or {}, files=files, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data["result"]


def font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def wrap_by_pixels(draw, text, fnt, max_width):
    words = str(text).split()
    lines, cur = [], ""
    for word in words:
        trial = word if not cur else cur + " " + word
        if draw.textbbox((0, 0), trial, font=fnt)[2] <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def draw_wrapped(draw, text, xy, fnt, max_width, fill=TEXT, spacing=10):
    x, y = xy
    line_h = fnt.getbbox("Ag")[3] - fnt.getbbox("Ag")[1]
    for line in wrap_by_pixels(draw, text, fnt, max_width):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += line_h + spacing
    return y


def render_card(q, shuffled):
    W = 1080
    pad = 72

    # Estimate canvas height from text lengths, then crop.
    img = Image.new("RGB", (W, 1800), "white")
    d = ImageDraw.Draw(img)

    logo = Image.open(BASE / "logo_inpetro.png").convert("RGBA")
    max_logo_w, max_logo_h = 360, 190
    scale = min(max_logo_w / logo.width, max_logo_h / logo.height)
    logo = logo.resize((int(logo.width * scale), int(logo.height * scale)), Image.LANCZOS)
    img.paste(logo, (pad, 42), logo)

    # ID badge
    badge_f = font(34, True)
    badge_text = q["id"]
    bb = d.textbbox((0, 0), badge_text, font=badge_f)
    bw = bb[2] - bb[0] + 52
    bh = 64
    bx = W - pad - bw
    by = 74
    d.rounded_rectangle((bx, by, bx+bw, by+bh), radius=24, fill=GREEN)
    d.text((bx+26, by+11), badge_text, font=badge_f, fill="white")

    # Accent line
    d.rounded_rectangle((pad, 235, W-pad, 245), radius=5, fill=GREEN)
    d.rounded_rectangle((pad, 245, pad+210, 251), radius=3, fill=GOLD)

    theme_f = font(27, True)
    q_f = font(35, False)
    alt_f = font(31, False)
    alt_letter_f = font(31, True)

    y = 292
    d.text((pad, y), f'{q["tema"]} • {q["dificuldade"]}', font=theme_f, fill=GREEN)
    y += 60

    y = draw_wrapped(d, q["enunciado"], (pad, y), q_f, W-2*pad, TEXT, 13)
    y += 38

    # Only a neutral cue for visual questions; never print the hidden description.
    if q.get("visual"):
        cue = "Considere o esquema/figura técnica indicado no enunciado."
        d.rounded_rectangle((pad, y, W-pad, y+78), radius=18, outline=GOLD, width=3)
        d.text((pad+24, y+20), cue, font=font(25, False), fill=MUTED)
        y += 112

    for i, alt in enumerate(shuffled):
        letter = LETTERS[i]
        lines = wrap_by_pixels(d, alt["texto"], alt_f, W - 2*pad - 92)
        line_h = alt_f.getbbox("Ag")[3] - alt_f.getbbox("Ag")[1]
        box_h = max(70, len(lines) * (line_h + 10) + 28)

        d.rounded_rectangle((pad, y, W-pad, y+box_h), radius=18, outline="#D8D8D8", width=2)
        d.rounded_rectangle((pad+16, y+14, pad+68, y+66), radius=16, fill="#F2F7F4")
        d.text((pad+29, y+18), letter, font=alt_letter_f, fill=GREEN)

        ty = y + 18
        for line in lines:
            d.text((pad+90, ty), line, font=alt_f, fill=TEXT)
            ty += line_h + 10
        y += box_h + 18

    footer_y = y + 18
    d.line((pad, footer_y, W-pad, footer_y), fill="#E8E8E8", width=2)
    d.text((pad, footer_y+25), "In Petro • Plataforma do Conhecimento", font=font(23, False), fill=MUTED)
    y = footer_y + 80

    crop_h = min(max(y, 1050), 1800)
    return img.crop((0, 0, W, crop_h))


def short_explanation(q):
    s = q.get("resolucao", "").strip()
    if len(s) <= 195:
        return s
    # Telegram quiz explanation limit is 200 chars.
    return s[:192].rsplit(" ", 1)[0] + "…"


def send_question(chat_id, user_id, index):
    if index >= len(QUESTIONS):
        sessions.pop(user_id, None)
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": "✅ Simulado concluído. Você chegou ao fim das 40 questões do In Petro."
        })
        return

    q = QUESTIONS[index]

    # True randomization: shuffle whole alternative objects, preserving text/correct flag.
    shuffled = list(q["alternativas"])
    random.SystemRandom().shuffle(shuffled)

    correct_index = next(i for i, a in enumerate(shuffled) if a["correta"])

    card = render_card(q, shuffled)
    bio = io.BytesIO()
    bio.name = f'{q["id"]}.png'
    card.save(bio, "PNG", optimize=True)
    bio.seek(0)

    tg("sendPhoto",
       {"chat_id": chat_id, "caption": f'{q["id"]} • Questão {index+1}/40'},
       files={"photo": (bio.name, bio, "image/png")},
       timeout=45)

    result = tg("sendPoll", {
        "chat_id": chat_id,
        "question": q["id"],
        "options": json.dumps(LETTERS, ensure_ascii=False),
        "type": "quiz",
        "is_anonymous": "false",
        "correct_option_id": str(correct_index),
        "explanation": short_explanation(q),
        "allows_multiple_answers": "false"
    })

    poll_id = result["poll"]["id"]
    poll_map[poll_id] = {
        "chat_id": chat_id,
        "user_id": user_id,
        "next_index": index + 1
    }
    sessions[user_id] = {"index": index}


def start_quiz(chat_id, user_id):
    sessions[user_id] = {"index": 0}
    tg("sendMessage", {
        "chat_id": chat_id,
        "text": "🟢 In Petro — Simulado de Instrumentação\n\n40 questões no padrão do banco In Petro. As alternativas são randomizadas a cada envio.\n\nComeçando agora."
    })
    send_question(chat_id, user_id, 0)


def handle_update(update):
    msg = update.get("message")
    if msg and msg.get("text"):
        text = msg["text"].strip()
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]

        if text.startswith("/start") or text.startswith("/quiz"):
            start_quiz(chat_id, user_id)
        elif text.startswith("/reiniciar"):
            start_quiz(chat_id, user_id)
        elif text.startswith("/ajuda"):
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": "Comandos:\n/quiz — iniciar o simulado\n/reiniciar — voltar à questão 1"
            })

    pa = update.get("poll_answer")
    if pa:
        poll_id = pa["poll_id"]
        info = poll_map.pop(poll_id, None)
        if info:
            # Small delay so the user sees Telegram's native correction/explanation first.
            time.sleep(1.2)
            send_question(info["chat_id"], info["user_id"], info["next_index"])


@app.get("/")
def health():
    return "InPetroQuizBot online", 200


@app.post("/webhook")
def webhook():
    update = request.get_json(silent=True) or {}
    # Reply immediately; process in background to avoid Telegram timeout.
    threading.Thread(target=handle_update, args=(update,), daemon=True).start()
    return jsonify(ok=True)


def setup_webhook():
    if not APP_URL:
        print("WEBHOOK_URL/RENDER_EXTERNAL_URL not available yet.")
        return
    url = APP_URL + "/webhook"
    for attempt in range(12):
        try:
            result = tg("setWebhook", {"url": url, "drop_pending_updates": "true"})
            print("Webhook configured:", result, url)
            return
        except Exception as e:
            print("Webhook setup attempt failed:", e)
            time.sleep(5)


# Configure the Telegram webhook when the module is loaded by Gunicorn/Render.
threading.Thread(target=setup_webhook, daemon=True).start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
