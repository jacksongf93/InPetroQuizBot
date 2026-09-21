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


def draw_visual(d, q, x, y, w=936, h=300):
    """Draw the technical support used by the original In Petro 40-question bank."""
    qid = q["id"]
    bg = "#F8FAFC"; ink = "#334155"; blue = "#2563EB"; red = "#DC2626"
    d.rounded_rectangle((x, y, x+w, y+h), radius=20, fill=bg, outline="#DDE5EA", width=2)
    cx=x+w//2
    def line(points, fill=ink, width=4): d.line(points, fill=fill, width=width)
    def txt(px,py,t,size=22,bold=False,fill="#0F172A",anchor="mm"):
        d.text((px,py),t,font=font(size,bold),fill=fill,anchor=anchor)
    if qid=="IP-I-002":
        txt(cx,y+40,"Incertezas padrão",25,True)
        vals=[("u(x)","0,09"),("u(y)","0,12"),("u(z)","0,12")]
        for i,(a,b) in enumerate(vals):
            xx=x+150+i*220
            d.rounded_rectangle((xx,y+90,xx+190,y+215),radius=12,fill="white",outline="#CBD5E1",width=3)
            txt(xx+95,y+125,a,22,True); txt(xx+95,y+180,b,28,False,blue)
    elif qid=="IP-I-007":
        line((x+90,y+145,x+w-90,y+145),width=6); d.ellipse((cx-62,y+83,cx+62,y+207),fill="white",outline="#0F172A",width=6)
        line((cx-45,y+100,cx+45,y+190),"#0F172A",8); line((cx+45,y+100,cx-45,y+190),"#0F172A",8)
        txt(cx,y+255,"Símbolo de válvula na linha de processo",20)
    elif qid=="IP-I-008":
        d.ellipse((cx-90,y+55,cx+90,y+235),fill="#CBD5E1",outline=ink,width=9); d.ellipse((cx-68,y+77,cx+68,y+213),fill="#64748B")
        d.rounded_rectangle((cx-70,y+128,cx+70,y+166),radius=18,fill=bg); line((cx,y+55,cx,y+25),ink,8); line((cx,y+25,cx+120,y+25),ink,10)
    elif qid=="IP-I-014":
        txt(cx,y+28,"Votação 2oo3",24,True)
        for i,xx in enumerate((x+220,cx,x+w-220),1):
            d.ellipse((xx-42,y+55,xx+42,y+139),fill="#DBEAFE",outline=blue,width=4); txt(xx,y+97,f"PT-{i}",17); line((xx,y+139,cx,y+190),"#64748B",3)
        d.rounded_rectangle((cx-75,y+185,cx+75,y+245),radius=12,fill="#FDE68A",outline="#CA8A04",width=4); txt(cx,y+215,"2oo3",21,True)
        line((cx,y+245,cx,y+280),"#64748B",4)
    elif qid=="IP-I-015":
        d.rounded_rectangle((cx-75,y+110,cx+75,y+190),radius=12,fill="#FDE68A",outline="#A16207",width=4); txt(cx,y+150,"SWITCH",20)
        pts=[(x+150,y+65),(x+w-150,y+65),(x+150,y+235),(x+w-150,y+235)]; labels=["CLP","IHM","E/S","SCADA"]
        for (px,py),lab in zip(pts,labels):
            d.ellipse((px-42,py-42,px+42,py+42),fill="#DBEAFE",outline=blue,width=4); txt(px,py,lab,16); line((px,py,cx,y+150),"#64748B",4)
    elif qid=="IP-I-016":
        txt(cx,y+25,"ADC ideal de 3 bits • 0–8 V",23,True); line((x+100,y+70,x+100,y+245),width=4); line((x+100,y+245,x+w-80,y+245),width=4)
        for i in range(8):
            xx=x+125+i*92; top=y+215-i*20
            d.rectangle((xx,top,xx+72,y+245),fill="#BFDBFE",outline=blue,width=2); txt(xx+36,y+270,format(i,'03b'),14)
        xx=x+125+5*92+36; line((xx,y+55,xx,y+245),red,4); txt(xx,y+42,"5,1 V",18,fill="#B91C1C")
    elif qid=="IP-I-017":
        d.rounded_rectangle((x+70,y+70,x+280,y+225),radius=12,fill="#E2E8F0",outline="#475569",width=5); txt(x+175,y+150,"TANQUE",22)
        for px,lab in ((x+370,"LT-101"),(x+555,"LIC-101")):
            d.ellipse((px-45,y+70,px+45,y+160),fill="white",outline="#0F172A",width=4); txt(px,y+115,lab,15)
        line((x+280,y+115,x+325,y+115),blue,3); line((x+415,y+115,x+510,y+115),blue,3); line((x+280,y+225,x+w-70,y+225),width=5)
        vx=x+w-190; d.polygon([(vx-35,y+195),(vx,y+225),(vx-35,y+255)],fill="white",outline="#0F172A"); d.polygon([(vx+35,y+195),(vx,y+225),(vx+35,y+255)],fill="white",outline="#0F172A"); txt(vx,y+282,"LV-101",16)
        line((x+555,y+160,vx,y+195),blue,3)
    elif qid=="IP-I-023":
        d.rectangle((x+70,y+75,x+w-70,y+225),fill="#DBEAFE",outline=ink,width=5); line((cx-20,y+75,cx-20,y+160,cx+80,y+160),"#B91C1C",6); d.ellipse((cx+72,y+152,cx+88,y+168),fill="#B91C1C")
        txt(cx+80,y+195,"Pitot",17); txt(cx,y+35,"Δp = 3.600 Pa • ρ = 800 kg/m³",20)
        line((x+150,y+150,x+330,y+150),blue,7); d.polygon([(x+330,y+150),(x+300,y+135),(x+300,y+165)],fill=blue)
    elif qid=="IP-I-025":
        for xx,fillc,lab,sub in ((x+90,"#DBEAFE","TT","0–100 °C"),(x+w-300,"#FEF3C7","INDICADOR","")):
            d.rounded_rectangle((xx,y+90,xx+210,y+205),radius=12,fill=fillc,outline=blue if lab=="TT" else "#CA8A04",width=4); txt(xx+105,y+130,lab,20); txt(xx+105,y+170,sub,16)
        line((x+300,y+147,x+w-300,y+147),width=5); txt(cx,y+125,"12 mA",18)
    elif qid=="IP-I-026":
        line((x+80,y+45,x+80,y+255),"#0F172A",5); line((x+w-80,y+45,x+w-80,y+255),"#0F172A",5); line((x+80,y+150,x+190,y+150),"#0F172A",4); txt(x+225,y+125,"A",18)
        line((x+190,y+150,x+265,y+150),"#0F172A",4); line((x+265,y+95,x+265,y+205),"#0F172A",4); line((x+265,y+95,x+520,y+95),"#0F172A",4); line((x+265,y+205,x+520,y+205),"#0F172A",4); txt(x+385,y+78,"B",18); txt(x+385,y+188,"C",18); line((x+520,y+95,x+520,y+205),"#0F172A",4); line((x+520,y+150,x+w-170,y+150),"#0F172A",4); d.ellipse((x+w-170,y+115,x+w-100,y+185),outline="#0F172A",width=4); txt(x+w-135,y+150,"Y",18); line((x+w-100,y+150,x+w-80,y+150),"#0F172A",4)
    elif qid=="IP-I-027":
        txt(cx,y+28,"Matriz de causa e efeito",23,True); cols=[x+250,x+450,x+650,x+850]; rows=[y+85,y+145,y+205,y+265]
        labels=["Causa","ESD","Alarme","Fechar XV"]
        for j,lab in enumerate(labels): txt(cols[j],y+65,lab,15,True)
        causes=[("HH pressão",["X","X","X"]),("LL nível",["X","X","X"]),("Falha bomba",["","X",""])]
        for i,(lab,vals) in enumerate(causes):
            yy=y+110+i*65; txt(x+95,yy,lab,15,anchor="lm")
            for j,v in enumerate(vals): d.rectangle((cols[j+1]-35,yy-24,cols[j+1]+35,yy+24),fill="white",outline="#CBD5E1",width=2); txt(cols[j+1],yy,v,18,True,fill=red if v else ink)
    elif qid=="IP-I-028":
        d.polygon([(x+110,y+150),(x+190,y+105),(x+190,y+195)],fill="#94A3B8"); line((x+190,y+150,x+300,y+150),"#0F172A",4); d.ellipse((x+300,y+95,x+410,y+205),fill="white",outline="#0F172A",width=4); txt(x+355,y+150,"−A",20)
        line((x+410,y+150,x+w-120,y+150),"#0F172A",4); line((x+245,y+150,x+245,y+65,x+w-220,y+65,x+w-220,y+150),blue,4); txt(cx,y+42,"Rf = 20 kΩ",18); txt(x+220,y+130,"Rin = 5 kΩ",16,anchor="rm"); txt(x+110,y+235,"Vin = +0,5 V",17,anchor="lm")
    elif qid=="IP-I-031":
        pts=[(x+310,y+80),(x+440,y+150),(x+310,y+220),(x+350,y+150)]
        d.polygon(pts,fill="#DBEAFE",outline=blue); line((x+80,y+115,x+300,y+115),width=5); line((x+80,y+185,x+300,y+185),width=5); line((x+440,y+150,x+w-170,y+150),width=5); d.ellipse((x+w-160,y+125,x+w-110,y+175),fill="#FEF08A",outline="#CA8A04",width=4); txt(x+55,y+115,"A",18); txt(x+55,y+185,"B",18); txt(x+w-135,y+205,"LED Y",16)
    elif qid in ("IP-I-032","IP-I-033"):
        phase=qid=="IP-I-032"; line((x+80,y+250,x+w-70,y+250),width=4); line((x+80,y+250,x+80,y+45),width=4); txt(x+100,y+30,"Fase" if phase else "Magnitude (dB)",16,anchor="lm"); txt(x+w-80,y+275,"Frequência",15,anchor="rm")
        # three distinct curves; TP2 is near +90° in phase, TP3 near 0 dB in magnitude at 2 Hz.
        curves=[("#2563EB",[(x+90,y+220),(x+250,y+205),(x+430,y+145),(x+w-100,y+70)]),("#16A34A",[(x+90,y+210),(x+250,y+180),(x+430,y+115),(x+w-100,y+90)]),("#DC2626",[(x+90,y+150),(x+250,y+150),(x+430,y+150),(x+w-100,y+150)])] if not phase else [("#2563EB",[(x+90,y+230),(x+250,y+210),(x+430,y+150),(x+w-100,y+80)]),("#16A34A",[(x+90,y+215),(x+250,y+180),(x+430,y+110),(x+w-100,y+65)]),("#DC2626",[(x+90,y+180),(x+250,y+160),(x+430,y+120),(x+w-100,y+85)])]
        for k,(c,pts) in enumerate(curves,1): line(pts,c,5); txt(x+w-145,y+35+24*k,f"TP{k}",14,fill=c,anchor="lm")
        xx=x+430; line((xx,y+45,xx,y+250),"#94A3B8",2); txt(xx,y+270,"2 Hz",14)
    elif qid=="IP-I-034":
        line((x+80,y+245,x+w-70,y+245),width=4); line((x+80,y+245,x+80,y+45),width=4); line((x+110,y+220,x+w-100,y+70),"#64748B",4)
        line([(x+110,y+245),(x+220,y+225),(x+330,y+190),(x+470,y+150),(x+w-100,y+95)],blue,6); line((x+w-250,y+105,x+w-250,y+130),red,3); txt(x+w-230,y+130,"erro constante",15,fill="#B91C1C",anchor="lm")
    elif qid=="IP-I-036":
        d.ellipse((x+100,y+115,x+160,y+175),fill="white",outline="#0F172A",width=4); txt(x+130,y+145,"Σ",20); line((x+35,y+145,x+100,y+145),width=4); d.rounded_rectangle((x+280,y+95,x+520,y+195),radius=12,fill="#DBEAFE",outline=blue,width=4); txt(x+400,y+145,"G(s)=k/(s+1)",20); line((x+160,y+145,x+280,y+145),width=4); line((x+520,y+145,x+w-70,y+145),width=4); line((x+w-150,y+145,x+w-150,y+245,x+130,y+245,x+130,y+175),"#64748B",4); txt(cx,y+275,"H(s)=1 • realimentação negativa",17)
    elif qid=="IP-I-039":
        line((x+70,y+245,x+w-70,y+245),width=4); line((x+70,y+245,x+70,y+45),width=4); line([(x+110,y+70),(x+260,y+85),(x+430,y+135),(x+620,y+205),(x+w-110,y+240)],blue,6); p=(x+330,y+105); f=(x+w-110,y+240); d.ellipse((p[0]-8,p[1]-8,p[0]+8,p[1]+8),fill="#F59E0B"); txt(p[0],p[1]-22,"P",18); d.ellipse((f[0]-8,f[1]-8,f[0]+8,f[1]+8),fill=red); txt(f[0],f[1]-25,"F",18); line((p[0],y+275,f[0],y+275),"#64748B",3); txt((p[0]+f[0])//2,y+288,"Intervalo P–F",16)
    else:
        txt(cx,y+h//2,"Recurso visual da questão",22)
    return y+h


def render_card(q, shuffled):
    W=1080; pad=72
    img=Image.new("RGB",(W,2400),"white"); d=ImageDraw.Draw(img)
    logo=Image.open(BASE/"logo_inpetro.png").convert("RGBA")
    scale=min(300/logo.width,150/logo.height); logo=logo.resize((int(logo.width*scale),int(logo.height*scale)),Image.LANCZOS)
    img.paste(logo,(pad,28),logo)
    badge_f=font(31,True); badge=q["id"]; bb=d.textbbox((0,0),badge,font=badge_f); bw=bb[2]-bb[0]+48
    bx=W-pad-bw; by=58; d.rounded_rectangle((bx,by,bx+bw,by+60),radius=22,fill=GREEN); d.text((bx+24,by+11),badge,font=badge_f,fill="white")
    d.rounded_rectangle((pad,195,W-pad,205),radius=5,fill=GREEN); d.rounded_rectangle((pad,205,pad+210,211),radius=3,fill=GOLD)
    theme_f=font(27,True); q_f=font(35); alt_f=font(30); alt_letter_f=font(30,True)
    y=250; d.text((pad,y),f'{q["tema"]} • {q["dificuldade"]}',font=theme_f,fill=GREEN); y+=58
    y=draw_wrapped(d,q["enunciado"],(pad,y),q_f,W-2*pad,TEXT,13); y+=28
    if q.get("visual"):
        y=draw_visual(d,q,pad,y,W-2*pad,300)+30
    for i,alt in enumerate(shuffled):
        lines=wrap_by_pixels(d,alt["texto"],alt_f,W-2*pad-92); lh=alt_f.getbbox("Ag")[3]-alt_f.getbbox("Ag")[1]; bh=max(70,len(lines)*(lh+9)+28)
        d.rounded_rectangle((pad,y,W-pad,y+bh),radius=18,outline="#D8D8D8",width=2); d.rounded_rectangle((pad+16,y+14,pad+68,y+66),radius=16,fill="#F2F7F4"); d.text((pad+29,y+18),LETTERS[i],font=alt_letter_f,fill=GREEN)
        ty=y+18
        for ln in lines: d.text((pad+90,ty),ln,font=alt_f,fill=TEXT); ty+=lh+9
        y+=bh+16
    fy=y+16; d.line((pad,fy,W-pad,fy),fill="#E8E8E8",width=2); d.text((pad,fy+24),"In Petro • Plataforma do Conhecimento",font=font(23),fill=MUTED); y=fy+75
    return img.crop((0,0,W,min(max(y,1000),2400)))

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

def individual_result(chat_id, user_id):
    st=sessions.pop(user_id,None) or {}
    correct=st.get("correct",0); wrong=st.get("wrong",0); skipped=st.get("skipped",0)
    total=correct+wrong+skipped; pct=round(100*correct/total) if total else 0
    tg("sendMessage",{"chat_id":chat_id,"text":(
        "🏁 In Petro — Resultado final\n\n"
        f"✅ Acertos: {correct}\n❌ Erros: {wrong}\n⏭ Em branco: {skipped}\n"
        f"📊 Aproveitamento: {pct}%\n\nTotal: {total}/{len(QUESTIONS)} questões."
    )})


def send_individual_question(chat_id,user_id,index):
    st=sessions.get(user_id)
    if not st: return
    if index>=len(QUESTIONS): individual_result(chat_id,user_id); return
    q=QUESTIONS[index]; shuffled,correct_index=shuffled_question(q); send_card(chat_id,q,shuffled,index)
    keyboard={"inline_keyboard":[[{"text":"⏭ Pular questão","callback_data":f"skip:{user_id}:{index}"}]]}
    result=tg("sendPoll",{
        "chat_id":chat_id,"question":q["id"],"options":json.dumps(LETTERS,ensure_ascii=False),"type":"quiz","is_anonymous":"false",
        "correct_option_id":str(correct_index),"explanation":short_explanation(q),"allows_multiple_answers":"false",
        "reply_markup":json.dumps(keyboard,ensure_ascii=False)
    })
    poll_id=result["poll"]["id"]
    poll_map[poll_id]={"mode":"individual","chat_id":chat_id,"user_id":user_id,"index":index,"next_index":index+1,"correct_index":correct_index,"message_id":result["message_id"]}
    st.update({"index":index,"poll_id":poll_id,"poll_message_id":result["message_id"]})


def start_individual_quiz(chat_id,user_id):
    sessions[user_id]={"index":0,"correct":0,"wrong":0,"skipped":0,"answered":set()}
    tg("sendMessage",{"chat_id":chat_id,"text":(
        "🟢 In Petro — Simulado de Instrumentação\n\n"
        f"{len(QUESTIONS)} questões. Sem cronômetro no modo individual.\n"
        "Responda para avançar ou use ⏭ Pular questão."
    )})
    send_individual_question(chat_id,user_id,0)


def skip_individual(callback):
    data=callback.get("data","").split(":")
    if len(data)!=3: return
    _,uid_s,idx_s=data
    try: uid=int(uid_s); idx=int(idx_s)
    except: return
    caller=callback["from"]["id"]; cqid=callback["id"]
    if caller!=uid:
        tg("answerCallbackQuery",{"callback_query_id":cqid,"text":"Esse botão pertence ao participante que iniciou o quiz."}); return
    st=sessions.get(uid)
    if not st or st.get("index")!=idx:
        tg("answerCallbackQuery",{"callback_query_id":cqid,"text":"Essa questão já foi encerrada."}); return
    poll_id=st.get("poll_id"); info=poll_map.pop(poll_id,None)
    if info:
        try: tg("stopPoll",{"chat_id":info["chat_id"],"message_id":info["message_id"]})
        except Exception: pass
    st["skipped"]+=1; st["index"]=idx+1
    tg("answerCallbackQuery",{"callback_query_id":cqid,"text":"Questão pulada."})
    send_individual_question(callback["message"]["chat"]["id"],uid,idx+1)


# -------------------------
# GROUP MODE — fixed 30 s per question
# -------------------------
GROUP_SECONDS=30

def group_result(chat_id,session):
    scores=session.get("scores",{})
    if not scores:
        text="🏁 In Petro — Quiz concluído!\n\nNenhuma resposta foi registrada."
    else:
        ranking=sorted(scores.values(),key=lambda x:(-x["correct"],-x["answered"],x["name"].lower()))
        lines=["🏁 In Petro — Resultado final do grupo","",f"{len(QUESTIONS)} questões • {GROUP_SECONDS}s por questão",""]
        medals=["🥇","🥈","🥉"]
        for i,r in enumerate(ranking[:20]):
            prefix=medals[i] if i<3 else f"{i+1}."
            lines.append(f'{prefix} {r["name"]}: {r["correct"]} acertos ({r["answered"]} respondidas)')
        text="\n".join(lines)
    tg("sendMessage",{"chat_id":chat_id,"text":text})


def send_group_question(chat_id,index,session_id):
    current=group_sessions.get(chat_id)
    if not current or current.get("session_id")!=session_id:return
    if index>=len(QUESTIONS):
        group_sessions.pop(chat_id,None); group_result(chat_id,current); return
    q=QUESTIONS[index]; shuffled,correct_index=shuffled_question(q); send_card(chat_id,q,shuffled,index)
    result=tg("sendPoll",{
        "chat_id":chat_id,"question":f'{q["id"]} • {GROUP_SECONDS}s',"options":json.dumps(LETTERS,ensure_ascii=False),"type":"quiz","is_anonymous":"false",
        "correct_option_id":str(correct_index),"explanation":short_explanation(q),"allows_multiple_answers":"false","open_period":str(GROUP_SECONDS)
    })
    poll_id=result["poll"]["id"]; current["poll_message_id"]=result["message_id"]; current["current_poll_id"]=poll_id; current["index"]=index
    poll_map[poll_id]={"mode":"group","chat_id":chat_id,"session_id":session_id,"index":index,"correct_index":correct_index,"answered_users":set()}
    timer=threading.Timer(GROUP_SECONDS+0.5,finish_group_question,args=(chat_id,poll_id,index,session_id)); timer.daemon=True; current["timer"]=timer; timer.start()


def finish_group_question(chat_id,poll_id,index,session_id):
    current=group_sessions.get(chat_id)
    if not current or current.get("session_id")!=session_id or current.get("current_poll_id")!=poll_id:return
    try:
        mid=current.get("poll_message_id")
        if mid: tg("stopPoll",{"chat_id":chat_id,"message_id":mid})
    except Exception: pass
    poll_map.pop(poll_id,None); current["current_poll_id"]=None
    send_group_question(chat_id,index+1,session_id)


def start_group_quiz(chat_id):
    old=group_sessions.get(chat_id)
    if old and old.get("timer"):
        try: old["timer"].cancel()
        except: pass
    sid=time.time_ns(); group_sessions[chat_id]={"session_id":sid,"index":0,"current_poll_id":None,"scores":{}}
    tg("sendMessage",{"chat_id":chat_id,"text":(
        "🟢 In Petro — Quiz cronometrado\n\n"
        f"{len(QUESTIONS)} questões • ⏱ {GROUP_SECONDS} segundos por questão.\n"
        "A próxima questão entra automaticamente quando o tempo termina. O quiz não espera todos responderem."
    )})
    send_group_question(chat_id,0,sid)


def stop_group_quiz(chat_id):
    current=group_sessions.pop(chat_id,None)
    if current:
        try:
            if current.get("timer"): current["timer"].cancel()
        except: pass
        tg("sendMessage",{"chat_id":chat_id,"text":"⏹ Quiz cronometrado encerrado."})

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
    iq=update.get("inline_query")
    if iq: send_share_card_inline(iq); return
    cb=update.get("callback_query")
    if cb:
        if str(cb.get("data","")).startswith("skip:"): skip_individual(cb)
        return
    msg=update.get("message")
    if msg and msg.get("text"):
        text=msg["text"].strip(); chat_id=msg["chat"]["id"]; chat_type=msg["chat"].get("type","private"); user_id=msg["from"]["id"]
        if text.startswith("/start") or text.startswith("/quiz"):
            start_group_quiz(chat_id) if chat_type in ("group","supergroup") else start_individual_quiz(chat_id,user_id)
        elif text.startswith("/grupo"): start_group_quiz(chat_id)
        elif text.startswith("/parar"):
            if chat_type in ("group","supergroup"): stop_group_quiz(chat_id)
            else:
                sessions.pop(user_id,None); tg("sendMessage",{"chat_id":chat_id,"text":"⏹ Simulado individual encerrado."})
        elif text.startswith("/reiniciar"):
            start_group_quiz(chat_id) if chat_type in ("group","supergroup") else start_individual_quiz(chat_id,user_id)
        elif text.startswith("/ajuda"):
            tg("sendMessage",{"chat_id":chat_id,"text":"/quiz — inicia\n/grupo — quiz de grupo (30 s)\n/parar — encerra\n/reiniciar — recomeça"})
    pa=update.get("poll_answer")
    if not pa:return
    poll_id=pa["poll_id"]; info=poll_map.get(poll_id)
    if not info or not pa.get("option_ids"):return
    chosen=pa["option_ids"][0]; user=pa.get("user",{}); uid=user.get("id")
    if info["mode"]=="individual":
        if uid!=info["user_id"]: return
        st=sessions.get(uid)
        if not st or st.get("poll_id")!=poll_id:return
        poll_map.pop(poll_id,None)
        if chosen==info["correct_index"]: st["correct"]+=1
        else: st["wrong"]+=1
        st["index"]=info["next_index"]
        time.sleep(0.8); send_individual_question(info["chat_id"],uid,info["next_index"])
    elif info["mode"]=="group":
        answered=info["answered_users"]
        if uid in answered:return
        answered.add(uid); sess=group_sessions.get(info["chat_id"])
        if not sess or sess.get("session_id")!=info["session_id"]:return
        name=(user.get("first_name") or "Participante")+(f' {user.get("last_name")}' if user.get("last_name") else '')
        rec=sess["scores"].setdefault(uid,{"name":name,"correct":0,"answered":0}); rec["answered"]+=1
        if chosen==info["correct_index"]: rec["correct"]+=1


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
                        ["message", "poll_answer", "inline_query", "callback_query"]
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
