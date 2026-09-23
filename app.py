import json, os, random, threading, time, shutil
from pathlib import Path
import requests
from flask import Flask, request, jsonify

BASE=Path(__file__).resolve().parent
TOKEN=os.environ.get('TELEGRAM_BOT_TOKEN','')
API=f'https://api.telegram.org/bot{TOKEN}' if TOKEN else ''
APP_URL=(os.getenv('WEBHOOK_URL') or os.getenv('RENDER_EXTERNAL_URL') or '').rstrip('/')
BOT_USERNAME=os.getenv('BOT_USERNAME','InPetroQuizBot')
QUESTIONS=json.load(open(BASE/'questions.json',encoding='utf-8'))
BANK={q['id']:q for q in QUESTIONS}
QUIZZES={'instrumentacao_01':{'title':'Simulado Instrumentação 01','description':'40 questões • padrão In Petro','question_ids':[q['id'] for q in QUESTIONS]}}
app=Flask(__name__)
lock=threading.RLock(); sessions={}; polls={}; groups={}

def card_path(qid):
    # GitHub mobile upload may flatten the cards folder into repository root.
    root = BASE / f"{qid}.png"
    nested = BASE / "cards" / f"{qid}.png"
    return root if root.exists() else nested

def validate():
    ids=[q['id'] for q in QUESTIONS]
    assert len(ids)==len(set(ids))==40
    for q in QUESTIONS:
        assert len(q['alternativas'])==5 and sum(bool(a.get('correta')) for a in q['alternativas'])==1
        assert (card_path(q['id'])).exists()
    for z in QUIZZES.values():
        assert all(x in BANK for x in z['question_ids'])
validate()

def tg(method,payload=None,files=None,timeout=40):
    r=requests.post(f'{API}/{method}',data=payload or {},files=files,timeout=timeout); r.raise_for_status(); d=r.json()
    if not d.get('ok'): raise RuntimeError(d)
    return d['result']

def kb(rows): return json.dumps({'inline_keyboard':rows},ensure_ascii=False)
def btn(text, callback_data=None, url=None):
    d={'text':text}; d['callback_data']=callback_data if callback_data is not None else d.get('callback_data');
    if url: d['url']=url
    if d.get('callback_data') is None: d.pop('callback_data',None)
    return d

def prepare(quiz_id):
    ids=QUIZZES[quiz_id]['question_ids'][:]; random.shuffle(ids); out=[]
    for qid in ids:
        q=BANK[qid]; alts=[dict(a) for a in q['alternativas']]; random.shuffle(alts)
        out.append({'id':qid,'options':[a['texto'] for a in alts],'correct':next(i for i,a in enumerate(alts) if a.get('correta'))})
    return out

def home(chat_id):
    share=f'https://t.me/share/url?url=https%3A%2F%2Ft.me%2F{BOT_USERNAME}%3Fstart%3Dquiz_instrumentacao_01&text=Simulado%20In%20Petro%20de%20Instrumenta%C3%A7%C3%A3o'
    add=f'https://t.me/{BOT_USERNAME}?startgroup=quiz_instrumentacao_01'
    text='📚 <b>In Petro • Plataforma do Conhecimento</b>\n\n<b>Simulado Instrumentação 01</b>\n40 questões • alternativas embaralhadas • ordem aleatória\n\nEscolha uma opção:'
    tg('sendMessage',{'chat_id':chat_id,'text':text,'parse_mode':'HTML','reply_markup':kb([[btn('▶️ Iniciar','start:instrumentacao_01')],[btn('↗️ Compartilhar',url=share),btn('👥 Adicionar a um grupo',url=add)]])})

def start_private(uid,chat_id,quiz_id='instrumentacao_01'):
    with lock: sessions[uid]={'chat_id':chat_id,'quiz_id':quiz_id,'items':prepare(quiz_id),'i':0,'ok':0,'wrong':0,'blank':0,'active_poll':None,'done':False,'awaiting_finish':False,'records':[],'started_at':time.time()}
    tg('sendMessage',{'chat_id':chat_id,'text':'🎯 <b>Simulado iniciado</b>\n40 questões. Sem cronômetro no modo individual.','parse_mode':'HTML'})
    send_private(uid)

def send_private(uid):
    with lock:
        s=sessions.get(uid)
        if not s or s['done']: return
        if s['i']>=len(s['items']): return prefinish_private(uid)
        item=s['items'][s['i']]; q=BANK[item['id']]; chat=s['chat_id']; pos=s['i']+1
    with open(card_path(item['id']),'rb') as f:
        tg('sendPhoto',{'chat_id':chat,'caption':f'Questão {pos}/40'},files={'photo':f})
    poll=tg('sendPoll',{'chat_id':chat,'question':f'Questão {pos}/40','options':json.dumps(item['options'],ensure_ascii=False),'type':'quiz','correct_option_id':item['correct'],'is_anonymous':'false','reply_markup':kb([[btn('⏭ Pular questão',f'skip:{uid}:{pos}')]])})
    with lock:
        if uid in sessions:
            sessions[uid]['active_poll']=poll['poll']['id']; polls[poll['poll']['id']]={'mode':'private','uid':uid,'pos':pos,'correct':item['correct'],'answered':False}

def prefinish_private(uid):
    with lock:
        s=sessions.get(uid)
        if not s or s['done'] or s.get('awaiting_finish'): return
        s['awaiting_finish']=True; chat=s['chat_id']
    tg('sendMessage',{'chat_id':chat,'text':'📚 <b>Você chegou ao fim das 40 questões.</b>\n\nAntes de finalizar, quer revisar? A revisão não altera suas respostas nem a pontuação.','parse_mode':'HTML','reply_markup':kb([[btn('❌ Revisar erradas',f'review:{uid}:wrong'),btn('⏭ Revisar em branco',f'review:{uid}:blank')],[btn('📋 Revisar todas',f'review:{uid}:all')],[btn('🏁 Finalizar simulado',f'finish:{uid}')]])})

def review_private(uid,kind):
    with lock:
        s=sessions.get(uid)
        if not s or s['done'] or not s.get('awaiting_finish'): return
        records=list(s['records']); chat=s['chat_id']
    if kind=='wrong': records=[r for r in records if r['status']=='wrong']
    elif kind=='blank': records=[r for r in records if r['status']=='blank']
    if not records:
        return tg('sendMessage',{'chat_id':chat,'text':'Nenhuma questão nessa categoria.','reply_markup':kb([[btn('🏁 Finalizar simulado',f'finish:{uid}')]])})
    for r in records:
        item=r['item']; qid=item['id']; correct=item['options'][item['correct']]
        marked='Em branco' if r['choice'] is None else item['options'][r['choice']]
        with open(card_path(qid),'rb') as f:
            tg('sendPhoto',{'chat_id':chat,'caption':f'🔎 Revisão • {qid}'},files={'photo':f})
        tg('sendMessage',{'chat_id':chat,'text':f'✍️ Sua resposta: <b>{marked}</b>\n✅ Correta: <b>{correct}</b>','parse_mode':'HTML'})
    tg('sendMessage',{'chat_id':chat,'text':'Fim da revisão.','reply_markup':kb([[btn('🏁 Finalizar simulado',f'finish:{uid}')]])})

def finish_private(uid):
    with lock:
        s=sessions.get(uid)
        if not s or s['done']: return
        s['done']=True; total=len(s['items']); ok=s['ok']; wrong=s['wrong']; blank=s['blank']; chat=s['chat_id']; pct=100*ok/total; elapsed=max(0,int(time.time()-s['started_at']))
    mm,ss=divmod(elapsed,60)
    tg('sendMessage',{'chat_id':chat,'text':f'🏁 <b>Resultado final</b>\n\n✅ Acertos: <b>{ok}</b>\n❌ Erros: <b>{wrong}</b>\n⏭ Em branco: <b>{blank}</b>\n📊 Aproveitamento: <b>{pct:.1f}%</b>\n⏱ Tempo total: <b>{mm:02d}:{ss:02d}</b>','parse_mode':'HTML','reply_markup':kb([[btn('🔁 Novo simulado','start:instrumentacao_01')],[btn('🏠 Início','home')]])})

def start_group(chat_id,quiz_id='instrumentacao_01'):
    with lock:
        old=groups.get(chat_id)
        if old and old.get('timer'): old['timer'].cancel()
        groups[chat_id]={'quiz_id':quiz_id,'items':prepare(quiz_id),'i':0,'scores':{},'participants':{},'answered_count':{},'response_time':{},'active_poll':None,'timer':None,'stopped':False}
    tg('sendMessage',{'chat_id':chat_id,'text':'🏁 <b>Partida iniciada!</b>\n40 questões • 30 segundos por questão • ranking final.','parse_mode':'HTML'})
    send_group(chat_id)

def send_group(chat_id):
    with lock:
        g=groups.get(chat_id)
        if not g or g['stopped']: return
        if g['i']>=len(g['items']): return finish_group(chat_id)
        item=g['items'][g['i']]; pos=g['i']+1
    with open(card_path(item['id']),'rb') as f: tg('sendPhoto',{'chat_id':chat_id,'caption':f'Questão {pos}/40 • 30 s'},files={'photo':f})
    p=tg('sendPoll',{'chat_id':chat_id,'question':f'Questão {pos}/40','options':json.dumps(item['options'],ensure_ascii=False),'type':'quiz','correct_option_id':item['correct'],'is_anonymous':'false','open_period':30})
    pid=p['poll']['id']
    with lock:
        polls[pid]={'mode':'group','chat_id':chat_id,'pos':pos,'correct':item['correct'],'answered_users':set(),'sent_at':time.monotonic()}; groups[chat_id]['active_poll']=pid
        t=threading.Timer(31,advance_group,args=(chat_id,pid)); t.daemon=True; groups[chat_id]['timer']=t; t.start()

def advance_group(chat_id,pid):
    with lock:
        g=groups.get(chat_id)
        if not g or g['stopped'] or g.get('active_poll')!=pid: return
        g['i']+=1; g['active_poll']=None
    send_group(chat_id)

def finish_group(chat_id):
    with lock:
        g=groups.get(chat_id)
        if not g or g['stopped']: return
        g['stopped']=True
        ranking=sorted(g['participants'],key=lambda uid:(-g['scores'].get(uid,0),g['response_time'].get(uid,999999),g['participants'].get(uid,'')))
        names=g['participants'].copy(); scores=g['scores'].copy(); counts=g['answered_count'].copy(); times=g['response_time'].copy()
    if ranking:
        lines=['🏆 <b>Ranking final</b>','']+[f'{i}. {names.get(uid,"Participante")} — <b>{scores.get(uid,0)}/40</b> • {counts.get(uid,0)} resp. • ⏱ {times.get(uid,0):.1f}s' for i,uid in enumerate(ranking,1)]
    else: lines=['🏁 <b>Partida encerrada</b>','','Nenhuma resposta registrada.']
    tg('sendMessage',{'chat_id':chat_id,'text':'\n'.join(lines),'parse_mode':'HTML','reply_markup':kb([[btn('🔁 Jogar novamente','groupstart:instrumentacao_01')]])})

def on_poll_answer(pa):
    pid=pa['poll_id']; uid=pa['user']['id']; opts=pa.get('option_ids',[])
    with lock:
        m=polls.get(pid)
        if not m or not opts: return
        choice=opts[0]
        if m['mode']=='private':
            if m['answered']: return
            s=sessions.get(m['uid'])
            if not s or s.get('active_poll')!=pid: return
            m['answered']=True; status='ok' if choice==m['correct'] else 'wrong'; s[status]+=1; s['records'].append({'item':s['items'][s['i']],'choice':choice,'status':status}); s['i']+=1; s['active_poll']=None; target=m['uid']
        else:
            if uid in m['answered_users']: return
            m['answered_users'].add(uid); g=groups.get(m['chat_id'])
            if not g or g.get('active_poll')!=pid: return
            name=pa['user'].get('first_name') or pa['user'].get('username') or str(uid); g['participants'][uid]=name; g['scores'].setdefault(uid,0); g['answered_count'][uid]=g['answered_count'].get(uid,0)+1; g['response_time'][uid]=g['response_time'].get(uid,0.0)+min(30.0,max(0.0,time.monotonic()-m['sent_at']))
            if choice==m['correct']: g['scores'][uid]+=1
            return
    send_private(target)

def on_callback(cq):
    cid=cq['id']; data=cq.get('data',''); msg=cq.get('message',{}); chat=msg.get('chat',{}); uid=cq['from']['id']
    try: tg('answerCallbackQuery',{'callback_query_id':cid})
    except: pass
    if data=='home': return home(chat['id'])
    if data.startswith('start:'): return start_private(uid,chat['id'],data.split(':',1)[1])
    if data.startswith('groupstart:'): return start_group(chat['id'],data.split(':',1)[1])
    if data.startswith('review:'):
        _,su,kind=data.split(':',2)
        if int(su)==uid: return review_private(uid,kind)
        return
    if data.startswith('finish:'):
        _,su=data.split(':',1)
        if int(su)==uid: return finish_private(uid)
        return
    if data.startswith('skip:'):
        _,su,pos=data.split(':');
        if int(su)!=uid: return
        with lock:
            s=sessions.get(uid)
            if not s or s['done'] or s['i']+1!=int(pos): return
            pid=s.get('active_poll'); m=polls.get(pid)
            if not pid or not m or m.get('answered'): return
            m['answered']=True; s['blank']+=1; s['records'].append({'item':s['items'][s['i']],'choice':None,'status':'blank'}); s['i']+=1; s['active_poll']=None
        try: tg('stopPoll',{'chat_id':chat['id'],'message_id':msg['message_id']})
        except: pass
        return send_private(uid)

def handle_message(msg):
    chat=msg['chat']; text=(msg.get('text') or '').strip(); uid=msg.get('from',{}).get('id')
    if chat['type']=='private':
        if text.startswith('/start'):
            payload=text.partition(' ')[2]
            if payload.startswith('quiz_'): return start_private(uid,chat['id'],payload[5:])
            return home(chat['id'])
        if text.startswith('/quiz'): return start_private(uid,chat['id'])
        if text.startswith('/parar'):
            with lock:
                if uid in sessions: sessions[uid]['done']=True
            return tg('sendMessage',{'chat_id':chat['id'],'text':'Simulado encerrado.'})
    else:
        if text.startswith('/start'):
            payload=text.partition(' ')[2]; qid=payload[5:] if payload.startswith('quiz_') else 'instrumentacao_01'
            return tg('sendMessage',{'chat_id':chat['id'],'text':'📚 <b>In Petro Quiz</b>\nPronto para iniciar o simulado no grupo.','parse_mode':'HTML','reply_markup':kb([[btn('▶️ Iniciar partida',f'groupstart:{qid}')]])})
        if text.startswith('/quiz'): return start_group(chat['id'])
        if text.startswith('/parar'):
            with lock:
                g=groups.get(chat['id']);
                if g:
                    g['stopped']=True
                    if g.get('timer'): g['timer'].cancel()
            return tg('sendMessage',{'chat_id':chat['id'],'text':'Partida encerrada.'})

@app.route('/',methods=['GET'])
def health(): return 'In Petro Quiz Bot OK',200
@app.route('/webhook',methods=['POST'])
def webhook():
    u=request.get_json(force=True)
    try:
        if 'message' in u: handle_message(u['message'])
        elif 'callback_query' in u: on_callback(u['callback_query'])
        elif 'poll_answer' in u: on_poll_answer(u['poll_answer'])
    except Exception as e: print('UPDATE ERROR',repr(e),flush=True)
    return jsonify(ok=True)

if TOKEN and APP_URL and not os.getenv('INPETRO_TESTING'):
    try: tg('setWebhook',{'url':APP_URL+'/webhook','allowed_updates':json.dumps(['message','callback_query','poll_answer'])})
    except Exception as e: print('WEBHOOK ERROR',e,flush=True)

if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
