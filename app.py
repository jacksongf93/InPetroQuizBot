import io
import json
import os
import random
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

# Individual mode: one session per user.
sessions = {}

# poll_id -> metadata
poll_map = {}

# Group mode: one timed session per chat.
group_sessions = {}

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
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold else
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
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

    img = Image.new("RGB", (W, 1800), "white")
    d = ImageDraw.Draw(img)

    logo = Image.open(BASE / "logo_inpetro.png").convert("RGBA")
    max_logo_w, max_logo_h = 360, 190
    scale = min(max_logo_w / logo.width, max_logo_h / logo.height)
    logo = logo.resize(
        (int(logo.width * scale), int(logo.height * scale)),
        Image.LANCZOS
    )
    img.paste(logo, (pad, 42), logo)

    badge_f = font(34, True)
    badge_text = q["id"]
    bb = d.textbbox((0, 0), badge_text, font=badge_f)
    bw = bb[2] - bb[0] + 52
    bh = 64
    bx = W - pad - bw
    by = 74
    d.rounded_rectangle((bx, by, bx + bw, by + bh), radius=24, fill=GREEN)
    d.text((bx + 26, by + 11), badge_text, font=badge_f, fill="white")

    d.rounded_rectangle((pad, 235, W - pad, 245), radius=5, fill=GREEN)
    d.rounded_rectangle((pad, 245, pad + 210, 251), radius=3, fill=GOLD)

    theme_f = font(27, True)
    q_f = font(35, False)
    alt_f = font(31, False)
    alt_letter_f = font(31, True)

    y = 292
    d.text(
        (pad, y),
        f'{q["tema"]} • {q["dificuldade"]}',
        font=theme_f,
        fill=GREEN
    )
    y += 60

    y = draw_wrapped(
        d, q["enunciado"], (pad, y), q_f, W - 2 * pad, TEXT, 13
    )
    y += 38

    # The actual technical visual will be embedded in a later visual pass.
    # Do not show a generic "consider the figure" box when no figure was rendered.

    for i, alt in enumerate(shuffled):
        letter = LETTERS[i]
        lines = wrap_by_pixels(d, alt["texto"], alt_f, W - 2 * pad - 92)
        line_h = alt_f.getbbox("Ag")[3] - alt_f.getbbox("Ag")[1]
        box_h = max(70, len(lines) * (line_h + 10) + 28)

        d.rounded_rectangle(
            (pad, y, W - pad, y + box_h),
            radius=18,
            outline="#D8D8D8",
            width=2
        )
        d.rounded_rectangle(
            (pad + 16, y + 14, pad + 68, y + 66),
            radius=16,
            fill="#F2F7F4"
        )
        d.text(
            (pad + 29, y + 18),
            letter,
            font=alt_letter_f,
            fill=GREEN
        )

        ty = y + 18
        for line in lines:
            d.text((pad + 90, ty), line, font=alt_f, fill=TEXT)
            ty += line_h + 10

        y += box_h + 18

    footer_y = y + 18
    d.line((pad, footer_y, W - pad, footer_y), fill="#E8E8E8", width=2)
    d.text(
        (pad, footer_y + 25),
        "In Petro • Plataforma do Conhecimento",
        font=font(23, False),
        fill=MUTED
    )
    y = footer_y + 80

    crop_h = min(max(y, 1050), 1800)
    return img.crop((0, 0, W, crop_h))


def short_explanation(q):
    s = q.get("resolucao", "").strip()
    if len(s) <= 195:
        return s
    return s[:192].rsplit(" ", 1)[0] + "…"


def question_time(q):
    """
    Automatic per-question timing for group mode.

    Optional override:
      "tempo_segundos": 45

    Otherwise the bot estimates from type + difficulty.
    Telegram open_period must be between 5 seconds and 600 seconds.
    """
    manual = q.get("tempo_segundos")
    if isinstance(manual, (int, float)):
        return max(5, min(int(manual), 600))

    difficulty = str(q.get("dificuldade", "")).strip().lower()
    qtype = str(q.get("tipo", "")).strip().lower()

    # Base by difficulty.
    if "fácil" in difficulty or "facil" in difficulty:
        seconds = 25
    elif "difícil" in difficulty or "dificil" in difficulty:
        seconds = 55
    else:
        seconds = 35

    # Increase only when the task actually needs working time.
    if "cálculo" in qtype or "calculo" in qtype:
        seconds = max(seconds, 50)
    if "tabela" in qtype:
        seconds = max(seconds, 45)
    if "analítica" in qtype or "analitica" in qtype:
        seconds = max(seconds, 60)
    if "situação" in qtype or "situacao" in qtype:
        seconds = max(seconds, 40)

    # Harder calculation/analysis questions deserve more time.
    if ("difícil" in difficulty or "dificil" in difficulty) and (
        "cálculo" in qtype or
        "calculo" in qtype or
        "analítica" in qtype or
        "analitica" in qtype
    ):
        seconds = max(seconds, 75)

    return max(20, min(seconds, 120))


def shuffled_question(q):
    shuffled = list(q["alternativas"])
    random.SystemRandom().shuffle(shuffled)
    correct_index = next(i for i, a in enumerate(shuffled) if a["correta"])
    return shuffled, correct_index


def send_card(chat_id, q, shuffled, index):
    card = render_card(q, shuffled)
    bio = io.BytesIO()
    bio.name = f'{q["id"]}.png'
    card.save(bio, "PNG", optimize=True)
    bio.seek(0)

    tg(
        "sendPhoto",
        {
            "chat_id": chat_id,
            "caption": f'{q["id"]} • Questão {index + 1}/{len(QUESTIONS)}'
        },
        files={"photo": (bio.name, bio, "image/png")},
        timeout=45
    )


# -------------------------
# INDIVIDUAL MODE
# -------------------------

def send_individual_question(chat_id, user_id, index):
    if index >= len(QUESTIONS):
        sessions.pop(user_id, None)
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": (
                f"✅ Simulado concluído. "
                f"Você chegou ao fim das {len(QUESTIONS)} questões do In Petro."
            )
        })
        return

    q = QUESTIONS[index]
    shuffled, correct_index = shuffled_question(q)

    send_card(chat_id, q, shuffled, index)

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
        "mode": "individual",
        "chat_id": chat_id,
        "user_id": user_id,
        "next_index": index + 1
    }
    sessions[user_id] = {"index": index}


def start_individual_quiz(chat_id, user_id):
    sessions[user_id] = {"index": 0}
    tg("sendMessage", {
        "chat_id": chat_id,
        "text": (
            "🟢 In Petro — Simulado de Instrumentação\n\n"
            f"{len(QUESTIONS)} questões no padrão do banco In Petro. "
            "As alternativas são randomizadas a cada envio.\n\n"
            "Modo individual: respondeu, avança."
        )
    })
    send_individual_question(chat_id, user_id, 0)


# -------------------------
# GROUP TIMED MODE
# -------------------------

def send_group_question(chat_id, index, session_id):
    current = group_sessions.get(chat_id)
    if not current or current.get("session_id") != session_id:
        return None

    if index >= len(QUESTIONS):
        group_sessions.pop(chat_id, None)
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": (
                f"🏁 In Petro — Quiz concluído!\n\n"
                f"Fim das {len(QUESTIONS)} questões."
            )
        })
        return None

    q = QUESTIONS[index]
    shuffled, correct_index = shuffled_question(q)
    seconds = question_time(q)

    send_card(chat_id, q, shuffled, index)

    result = tg("sendPoll", {
        "chat_id": chat_id,
        "question": f'{q["id"]} • {seconds}s',
        "options": json.dumps(LETTERS, ensure_ascii=False),
        "type": "quiz",
        "is_anonymous": "false",
        "correct_option_id": str(correct_index),
        "explanation": short_explanation(q),
        "allows_multiple_answers": "false"
    })

    poll_id = result["poll"]["id"]
    message_id = result["message_id"]

    current["index"] = index
    current["current_poll_id"] = poll_id
    current["poll_message_id"] = message_id

    poll_map[poll_id] = {
        "mode": "group",
        "chat_id": chat_id,
        "session_id": session_id,
        "index": index
    }

    return {
        "poll_id": poll_id,
        "message_id": message_id,
        "seconds": seconds
    }


def run_group_quiz(chat_id, session_id):
    """
    One worker controls the whole group quiz.
    It does not wait for voters and does not depend on Telegram poll-close updates.
    When the time ends, it closes the current poll and immediately publishes the next question.
    """
    for index in range(len(QUESTIONS)):
        current = group_sessions.get(chat_id)
        if not current or current.get("session_id") != session_id or current.get("stopped"):
            return

        sent = send_group_question(chat_id, index, session_id)
        if not sent:
            return

        # Wait only for this question's own time.
        deadline = time.time() + sent["seconds"]
        while time.time() < deadline:
            current = group_sessions.get(chat_id)
            if not current or current.get("session_id") != session_id or current.get("stopped"):
                return
            time.sleep(min(0.5, max(0.0, deadline - time.time())))

        current = group_sessions.get(chat_id)
        if not current or current.get("session_id") != session_id or current.get("stopped"):
            return

        # Close exactly at the end of the allotted time.
        try:
            tg("stopPoll", {
                "chat_id": chat_id,
                "message_id": sent["message_id"]
            })
        except Exception:
            pass

        poll_map.pop(sent["poll_id"], None)
        current["current_poll_id"] = None

        # NO extra 4s/10s pause: next question goes out immediately.

    # Finished all questions.
    current = group_sessions.get(chat_id)
    if current and current.get("session_id") == session_id and not current.get("stopped"):
        group_sessions.pop(chat_id, None)
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": (
                f"🏁 In Petro — Quiz concluído!\n\n"
                f"Fim das {len(QUESTIONS)} questões."
            )
        })


def start_group_quiz(chat_id):
    old = group_sessions.get(chat_id)
    if old:
        old["stopped"] = True

    session_id = time.time_ns()
    group_sessions[chat_id] = {
        "session_id": session_id,
        "index": 0,
        "current_poll_id": None,
        "poll_message_id": None,
        "stopped": False
    }

    tg("sendMessage", {
        "chat_id": chat_id,
        "text": (
            "🟢 In Petro — Quiz cronometrado\n\n"
            f"{len(QUESTIONS)} questões.\n"
            "⏱ O tempo varia automaticamente conforme o tipo e a dificuldade "
            "de cada questão.\n\n"
            "Quando o tempo acaba, a enquete fecha e a próxima questão entra imediatamente."
        )
    })

    worker = threading.Thread(
        target=run_group_quiz,
        args=(chat_id, session_id),
        daemon=True
    )
    group_sessions[chat_id]["worker"] = worker
    worker.start()


def stop_group_quiz(chat_id):
    current = group_sessions.get(chat_id)
    if current:
        current["stopped"] = True

        try:
            message_id = current.get("poll_message_id")
            if message_id:
                tg("stopPoll", {
                    "chat_id": chat_id,
                    "message_id": message_id
                })
        except Exception:
            pass

        group_sessions.pop(chat_id, None)
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": "⏹ Quiz cronometrado encerrado."
        })

def handle_update(update):
    msg = update.get("message")

    if msg and msg.get("text"):
        text = msg["text"].strip()
        chat_id = msg["chat"]["id"]
        chat_type = msg["chat"].get("type", "private")
        user_id = msg["from"]["id"]

        if text.startswith("/start") or text.startswith("/quiz"):
            if chat_type in ("group", "supergroup"):
                start_group_quiz(chat_id)
            else:
                start_individual_quiz(chat_id, user_id)

        elif text.startswith("/grupo"):
            start_group_quiz(chat_id)

        elif text.startswith("/parar"):
            stop_group_quiz(chat_id)

        elif text.startswith("/reiniciar"):
            if chat_type in ("group", "supergroup"):
                start_group_quiz(chat_id)
            else:
                start_individual_quiz(chat_id, user_id)

        elif text.startswith("/ajuda"):
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": (
                    "Comandos:\n"
                    "/quiz — inicia o quiz\n"
                    "/grupo — inicia o modo cronometrado\n"
                    "/parar — encerra o quiz do grupo\n"
                    "/reiniciar — volta à questão 1"
                )
            })

    pa = update.get("poll_answer")
    if pa:
        poll_id = pa["poll_id"]
        info = poll_map.get(poll_id)

        if not info:
            return

        # Individual mode advances immediately after the answer.
        if info.get("mode") == "individual":
            poll_map.pop(poll_id, None)
            time.sleep(1.2)
            send_individual_question(
                info["chat_id"],
                info["user_id"],
                info["next_index"]
            )

        # Group mode NEVER advances on the first person's answer.
        # The timer controls the entire group.
        elif info.get("mode") == "group":
            return


@app.get("/")
def health():
    return "InPetroQuizBot online", 200


@app.post("/webhook")
def webhook():
    update = request.get_json(silent=True) or {}
    threading.Thread(
        target=handle_update,
        args=(update,),
        daemon=True
    ).start()
    return jsonify(ok=True)


def setup_webhook():
    if not APP_URL:
        print("WEBHOOK_URL/RENDER_EXTERNAL_URL not available yet.")
        return

    url = APP_URL + "/webhook"

    for attempt in range(12):
        try:
            result = tg(
                "setWebhook",
                {
                    "url": url,
                    "drop_pending_updates": "true",
                    "allowed_updates": json.dumps(
                        ["message", "poll_answer"]
                    )
                }
            )
            print("Webhook configured:", result, url)
            return
        except Exception as e:
            print("Webhook setup attempt failed:", e)
            time.sleep(5)


# Configure webhook when Gunicorn/Render imports the module.
threading.Thread(target=setup_webhook, daemon=True).start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
