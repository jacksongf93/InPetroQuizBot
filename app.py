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


def draw_visual(img, d, q, y, W, pad):
    qid=q.get("id","")
    visual_ids={"IP-I-002","IP-I-007","IP-I-008","IP-I-014","IP-I-015","IP-I-016","IP-I-017","IP-I-023","IP-I-025","IP-I-026","IP-I-028","IP-I-031","IP-I-032","IP-I-033","IP-I-034","IP-I-036","IP-I-039"}
    if qid not in visual_ids: return y
    x0,x1=pad+70,W-pad-70; h=300
    d.rounded_rectangle((x0,y,x1,y+h),radius=20,fill="#FAFCFB",outline="#DDE7E1",width=2)
    cx=(x0+x1)//2; cy=y+h//2; f=font(25,True); fs=font(22,False)
    if qid=="IP-I-002":
        rows=[("Grandeza","u"),("x","0,09"),("y","0,12"),("z","0,12")]; tw=440; th=48; sx=cx-tw//2; sy=y+48
        for r,row in enumerate(rows):
            for c,val in enumerate(row):
                a=sx+c*tw//2; b=sy+r*th; d.rectangle((a,b,a+tw//2,b+th),outline="#AEBDB5",width=2); d.text((a+25,b+10),val,font=fs,fill=TEXT)
    elif qid=="IP-I-007":
        d.line((x0+80,cy,x1-80,cy),fill=TEXT,width=7); d.ellipse((cx-65,cy-65,cx+65,cy+65),outline=TEXT,width=6); d.line((cx-45,cy-45,cx+45,cy+45),fill=TEXT,width=8); d.line((cx+45,cy-45,cx-45,cy+45),fill=TEXT,width=8)
    elif qid=="IP-I-008":
        d.ellipse((cx-85,cy-85,cx+85,cy+85),fill="#CBD5E1",outline=TEXT,width=7); d.ellipse((cx-62,cy-62,cx+62,cy+62),fill="#64748B"); d.rounded_rectangle((cx-65,cy-18,cx+65,cy+18),radius=18,fill="white"); d.line((cx,cy-85,cx,y+45),fill=TEXT,width=8); d.line((cx,y+45,cx+105,y+45),fill=TEXT,width=10)
    elif qid=="IP-I-014":
        for i,xx in enumerate((cx-210,cx,cx+210),1): d.ellipse((xx-43,y+45,xx+43,y+131),fill="#EAF3EF",outline=GREEN,width=4); d.text((xx-28,y+73),f"PT{i}",font=fs,fill=TEXT); d.line((xx,y+131,cx,y+190),fill="#66736C",width=3)
        d.rounded_rectangle((cx-85,y+190,cx+85,y+250),radius=12,fill="#FFF1C7",outline=GOLD,width=4); d.text((cx-38,y+205),"2oo3",font=f,fill=TEXT)
    elif qid=="IP-I-015":
        d.rounded_rectangle((cx-75,cy-35,cx+75,cy+35),radius=12,fill="#FFF1C7",outline=GOLD,width=4); d.text((cx-47,cy-14),"SWITCH",font=fs,fill=TEXT)
        pts=[(x0+110,y+55),(x1-110,y+55),(x0+110,y+235),(x1-110,y+235)]
        for lab,(xx,yy) in zip(("CLP","IHM","E/S","SCADA"),pts): d.line((cx,cy,xx,yy),fill="#66736C",width=4); d.rounded_rectangle((xx-55,yy-28,xx+55,yy+28),radius=10,outline=GREEN,width=3); d.text((xx-35,yy-12),lab,font=fs,fill=TEXT)
    elif qid=="IP-I-016":
        d.line((x0+110,y+230,x1-90,y+230),fill=TEXT,width=3); d.line((x0+110,y+230,x0+110,y+55),fill=TEXT,width=3); pts=[(x0+110,y+220),(x0+210,y+220),(x0+210,y+195),(x0+310,y+195),(x0+310,y+165),(x0+410,y+165),(x0+410,y+135),(x0+510,y+135),(x0+510,y+105),(x0+610,y+105)]; d.line(pts,fill=GREEN,width=6); d.text((x1-170,y+245),"Vin",font=fs,fill=TEXT); d.text((x0+125,y+60),"código",font=fs,fill=TEXT)
    elif qid=="IP-I-017":
        d.rectangle((cx-260,y+65,cx+260,y+235),outline=TEXT,width=5); d.ellipse((cx-205,y+95,cx-115,y+185),outline=GREEN,width=5); d.text((cx-194,y+120),"LT",font=f,fill=TEXT); d.ellipse((cx-45,y+95,cx+45,y+185),outline=GREEN,width=5); d.text((cx-37,y+120),"LIC",font=fs,fill=TEXT); d.line((cx-115,y+140,cx-45,y+140),fill=TEXT,width=4); d.line((cx+45,y+140,cx+170,y+140),fill=TEXT,width=4); d.polygon([(cx+170,y+110),(cx+220,y+140),(cx+170,y+170)],outline=TEXT); d.text((cx+160,y+185),"LV",font=fs,fill=TEXT)
    elif qid=="IP-I-023":
        d.line((x0+70,cy,x1-70,cy),fill=TEXT,width=8); d.line((cx-40,cy,cx-40,y+70),fill=GREEN,width=7); d.line((cx+40,cy,cx+40,y+115),fill=GREEN,width=7); d.line((cx-40,y+70,cx+40,y+70),fill=GREEN,width=5); d.text((cx-170,y+210),"Tubo de Pitot / Δp",font=f,fill=TEXT)
    elif qid=="IP-I-025":
        d.line((x0+100,y+230,x1-100,y+70),fill=GREEN,width=7); d.text((x0+70,y+240),"4 mA / 0 °C",font=fs,fill=TEXT); d.text((x1-260,y+45),"20 mA / 100 °C",font=fs,fill=TEXT); d.ellipse((cx-7,cy-7,cx+7,cy+7),fill=GOLD); d.text((cx+18,cy-18),"12 mA",font=f,fill=TEXT)
    elif qid=="IP-I-026":
        lx=x0+100; rx=x1-100; d.line((lx,y+45,lx,y+255),fill=TEXT,width=5); d.line((rx,y+45,rx,y+255),fill=TEXT,width=5); d.line((lx,y+105,lx+120,y+105),fill=TEXT,width=4); d.text((lx+130,y+82),"[ A ]",font=f,fill=TEXT); d.line((lx+220,y+105,lx+300,y+105),fill=TEXT,width=4); d.text((lx+310,y+82),"[ B ]",font=f,fill=TEXT); d.line((lx+400,y+105,rx,y+105),fill=TEXT,width=4); d.line((lx+280,y+105,lx+280,y+190),fill=TEXT,width=3); d.line((lx+280,y+190,lx+400,y+190),fill=TEXT,width=3); d.text((lx+310,y+167),"[ C ]",font=f,fill=TEXT); d.line((lx+400,y+190,lx+400,y+105),fill=TEXT,width=3); d.text((rx-85,y+82),"( Y )",font=f,fill=GREEN)
    elif qid=="IP-I-028":
        d.polygon([(cx-40,y+65),(cx-40,y+235),(cx+130,cy)],outline=TEXT); d.text((cx-22,y+105),"−",font=f,fill=TEXT); d.text((cx-22,y+190),"+",font=f,fill=TEXT); d.line((x0+100,y+115,cx-40,y+115),fill=TEXT,width=4); d.text((x0+110,y+78),"Vin   Rin=5 kΩ",font=fs,fill=TEXT); d.line((cx+130,cy,x1-90,cy),fill=TEXT,width=4); d.line((cx+100,cy,x1-150,y+45,x0+250,y+45,x0+250,y+115),fill=GREEN,width=4); d.text((cx-15,y+15),"Rf=20 kΩ",font=fs,fill=TEXT)
    elif qid=="IP-I-031":
        d.text((cx-90,y+45),"A ⊕ B",font=font(40,True),fill=GREEN); rows=[("A","B","Y"),("0","0","0"),("0","1","1"),("1","0","1"),("1","1","0")]; sy=y+105
        for r,row in enumerate(rows): d.text((cx-100,sy+r*32),"     ".join(row),font=fs,fill=TEXT)
    elif qid in {"IP-I-032","IP-I-033","IP-I-034"}:
        import math
        ox=x0+80; oy=y+240; d.line((ox,y+45,ox,oy),fill=TEXT,width=3); d.line((ox,oy,x1-60,oy),fill=TEXT,width=3); d.text((x1-120,oy+10),"f",font=fs,fill=TEXT)
        curves=[(GREEN,0),(GOLD,35),("#66736C",-30)]
        for ci,(col,off) in enumerate(curves):
            pts=[]
            for k in range(120):
                xx=ox+10+k*5.2; yy=oy-95-off-45*math.tanh((k-55)/22) if qid!="IP-I-034" else oy-30-1.1*k+0.006*k*k
                pts.append((xx,yy))
            d.line(pts,fill=col,width=4); d.text((x1-120,y+65+ci*35),f"TP{ci+1}",font=fs,fill=col)
        d.line((cx,y+45,cx,oy),fill="#AAB3AE",width=2); d.text((cx+8,oy-25),"2 Hz",font=fs,fill=TEXT)
    elif qid=="IP-I-036":
        d.line((x0+70,cy,cx-170,cy),fill=TEXT,width=4); d.ellipse((cx-170,cy-30,cx-110,cy+30),outline=TEXT,width=4); d.text((cx-154,cy-19),"Σ",font=f,fill=TEXT); d.line((cx-110,cy,cx-40,cy),fill=TEXT,width=4); d.rectangle((cx-40,cy-45,cx+145,cy+45),outline=GREEN,width=5); d.text((cx-5,cy-15),"G(s)=k/(s+1)",font=fs,fill=TEXT); d.line((cx+145,cy,x1-70,cy),fill=TEXT,width=4); d.line((cx+220,cy,cx+220,y+245,cx-140,y+245,cx-140,cy+30),fill=TEXT,width=3)
    elif qid=="IP-I-039":
        pts=[(x0+80,y+65),(x0+220,y+78),(x0+360,y+105),(x0+500,y+150),(x0+650,y+230)]; d.line(pts,fill=GREEN,width=7); d.ellipse((x0+213,y+71,x0+227,y+85),fill=GOLD); d.text((x0+190,y+38),"P",font=f,fill=TEXT); d.ellipse((x0+643,y+223,x0+657,y+237),fill=GOLD); d.text((x0+665,y+215),"F",font=f,fill=TEXT); d.text((cx-80,y+250),"intervalo P–F",font=fs,fill=TEXT)
    return y+h+32


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

    y = draw_visual(img, d, q, y, W, pad)

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
        return

    if index >= len(QUESTIONS):
        group_sessions.pop(chat_id, None)
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": (
                f"🏁 In Petro — Quiz concluído!\n\n"
                f"Fim das {len(QUESTIONS)} questões."
            )
        })
        return

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
        "allows_multiple_answers": "false",
        "open_period": str(seconds)
    })

    poll_id = result["poll"]["id"]
    group_sessions[chat_id]["poll_message_id"] = result["message_id"]
    group_sessions[chat_id]["current_poll_id"] = poll_id
    group_sessions[chat_id].setdefault("finished_polls", set())
    poll_map[poll_id] = {
        "mode": "group",
        "chat_id": chat_id,
        "session_id": session_id,
        "index": index
    }

    group_sessions[chat_id]["index"] = index

    # Telegram closes at open_period. At the same deadline, advance immediately.
    timer = threading.Timer(
        seconds,
        finish_group_question,
        args=(chat_id, poll_id, index, session_id)
    )
    timer.daemon = True
    timer.start()


def finish_group_question(chat_id, poll_id, index, session_id):
    current = group_sessions.get(chat_id)
    if not current or current.get("session_id") != session_id:
        return

    finished = current.setdefault("finished_polls", set())
    if poll_id in finished:
        return

    # Ignore a stale timer/update from an older question.
    if current.get("current_poll_id") != poll_id:
        return

    finished.add(poll_id)

    # Backup closure; Telegram normally closes it through open_period.
    try:
        message_id = current.get("poll_message_id")
        if message_id:
            tg("stopPoll", {
                "chat_id": chat_id,
                "message_id": message_id
            })
    except Exception:
        pass

    poll_map.pop(poll_id, None)
    current["current_poll_id"] = None

    # Immediate transition: no extra pause and no external resolution message.
    send_group_question(chat_id, index + 1, session_id)


def start_group_quiz(chat_id):
    old = group_sessions.get(chat_id)
    if old:
        for key in ("next_timer",):
            timer = old.get(key)
            try:
                if timer:
                    timer.cancel()
            except Exception:
                pass

    session_id = time.time_ns()
    group_sessions[chat_id] = {
        "session_id": session_id,
        "index": 0,
        "finished_polls": set(),
        "current_poll_id": None
    }

    tg("sendMessage", {
        "chat_id": chat_id,
        "text": (
            "🟢 In Petro — Quiz cronometrado\n\n"
            f"{len(QUESTIONS)} questões.\n"
            "⏱ O tempo varia automaticamente conforme o tipo e a dificuldade "
            "de cada questão.\n\n"
            "Questões diretas terão menos tempo; cálculos e análises terão mais."
        )
    })

    send_group_question(chat_id, 0, session_id)


def stop_group_quiz(chat_id):
    if chat_id in group_sessions:
        current = group_sessions.get(chat_id, {})
        timer = current.get("next_timer")
        try:
            if timer:
                timer.cancel()
        except Exception:
            pass
        group_sessions.pop(chat_id, None)
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": "⏹ Quiz cronometrado encerrado."
        })


def send_share_card_inline(inline_query):
    """Return the In Petro share card when the bot is invoked inline."""
    query_id = inline_query["id"]
    card_text = (
        "🎲 <b>Quiz ‘IN PETRO — Instrumentação’</b>\n"
        f"🖋 <b>{len(QUESTIONS)} perguntas</b>\n\n"
        "Teste seus conhecimentos em Instrumentação."
    )
    keyboard = {
        "inline_keyboard": [
            [{"text": "▶️ Iniciar este quiz", "url": "https://t.me/InPetroQuizBot?start=quiz"}],
            [{"text": "👥 Iniciar quiz no grupo", "url": "https://t.me/InPetroQuizBot?startgroup=quiz"}],
            [{"text": "↗️ Compartilhar quiz", "switch_inline_query_chosen_chat": {"query": "inpetro"}}]
        ]
    }
    result = {
        "type": "article",
        "id": "inpetro-instrumentacao-40",
        "title": "IN PETRO — Instrumentação",
        "description": f"{len(QUESTIONS)} perguntas • Quiz de Instrumentação",
        "input_message_content": {
            "message_text": card_text,
            "parse_mode": "HTML"
        },
        "reply_markup": keyboard
    }
    tg("answerInlineQuery", {
        "inline_query_id": query_id,
        "results": json.dumps([result], ensure_ascii=False),
        "cache_time": "1",
        "is_personal": "true"
    })


def handle_update(update):
    iq = update.get("inline_query")
    if iq:
        send_share_card_inline(iq)
        return

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
                        ["message", "poll_answer", "inline_query"]
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
