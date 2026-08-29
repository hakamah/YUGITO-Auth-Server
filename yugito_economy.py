from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import secrets
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

try:
    import psycopg2
    import psycopg2.extras
except Exception:  # local syntax tests may not have dependency
    psycopg2 = None

PARIS = ZoneInfo('Europe/Paris')
DATABASE_URL = (os.getenv('YUGITO_DATABASE_URL') or os.getenv('DATABASE_URL') or '').strip()
ADMIN_TOKEN = (os.getenv('YUGITO_ADMIN_TOKEN') or '').strip()
DEV_EMAIL = (os.getenv('YUGITO_DEV_EMAIL') or '').strip().casefold()
DEV_PSEUDO = (os.getenv('YUGITO_DEV_PSEUDO') or '').strip().casefold()
ROTATION_SECRET = (os.getenv('YUGITO_ROTATION_SECRET') or os.getenv('YUGITO_SESSION_SECRET') or os.getenv('GOOGLE_CLIENT_SECRET') or 'YUGITO-ROTATION-DEV').encode('utf-8')
PERMIT_SECRET = (os.getenv('YUGITO_SESSION_SECRET') or os.getenv('GOOGLE_CLIENT_SECRET') or 'YUGITO-PERMIT-DEV').encode('utf-8')
CATALOG_PATH = os.path.join(os.path.dirname(__file__), 'card_catalog.json')

WEEKLY_COUNTS = {3.5: 8, 4.0: 6, 4.5: 4, 5.0: 4}
BASE_STARS = 3.0
WIN_RULES = {
    'classic': {'base': 30, 'min': 30, 'max': 30},
    'ranked': {'base': 30, 'min': 30, 'max': 30},
}
PER_STAR_YT = 0
LOSS_NATURAL_YT = 10
ANTI_FARM_WINDOW_SECONDS = 6 * 60 * 60
ANTI_FARM_MULTIPLIERS = (1.0, 1.0, 1.0, 0.50, 0.25)


def now() -> int:
    return int(time.time())


def _load_catalog() -> list[dict]:
    with open(CATALOG_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise RuntimeError('card_catalog.json invalide')
    out=[]
    for raw in data:
        row=dict(raw)
        row['id']=str(row.get('id') or '')
        row['name']=str(row.get('name') or row['id'])
        row['stars']=float(row.get('stars') or 0)
        row['price_yt']=max(0,int(row.get('price_yt') or 0))
        if row['id']:
            out.append(row)
    return out

CATALOG = _load_catalog()
CATALOG_BY_ID = {c['id']: c for c in CATALOG}
BASE_CARD_IDS = tuple(c['id'] for c in CATALOG if abs(c['stars'] - BASE_STARS) < 0.01)


def available() -> bool:
    return bool(DATABASE_URL and psycopg2 is not None)


def dev_account_authorized(account: dict | None) -> bool:
    """Server-side DEV gate. The clients only receive a boolean and never the secret criteria."""
    if not account or not DEV_EMAIL or not DEV_PSEUDO:
        return False
    email=str(account.get('email') or '').strip().casefold()
    pseudo=' '.join(str(account.get('pseudo') or '').strip().split()).casefold()
    return hmac.compare_digest(email, DEV_EMAIL) and hmac.compare_digest(pseudo, DEV_PSEUDO)


def connect():
    if not DATABASE_URL:
        raise RuntimeError('Base économie YUGITO non configurée (YUGITO_DATABASE_URL/DATABASE_URL).')
    if psycopg2 is None:
        raise RuntimeError('Dépendance psycopg2 absente sur le serveur.')
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=8)
    return conn


def init_schema() -> None:
    if not available():
        return
    conn=connect()
    try:
        with conn.cursor() as cur:
            cur.execute('''
            CREATE TABLE IF NOT EXISTS yugito_players(
              account_id TEXT PRIMARY KEY,
              yt_balance INTEGER NOT NULL DEFAULT 0 CHECK(yt_balance >= 0),
              penalty_level INTEGER NOT NULL DEFAULT 0 CHECK(penalty_level >= 0),
              clean_games INTEGER NOT NULL DEFAULT 0 CHECK(clean_games >= 0 AND clean_games <= 2),
              penalty_until BIGINT NOT NULL DEFAULT 0,
              created_at BIGINT NOT NULL,
              updated_at BIGINT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS yugito_owned_cards(
              account_id TEXT NOT NULL,
              card_id TEXT NOT NULL,
              acquired_at BIGINT NOT NULL,
              source TEXT NOT NULL,
              price_paid INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(account_id, card_id)
            );
            CREATE TABLE IF NOT EXISTS yugito_card_catalog(
              card_id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              stars NUMERIC(3,1) NOT NULL,
              price_yt INTEGER NOT NULL CHECK(price_yt >= 0),
              purchasable BOOLEAN NOT NULL DEFAULT TRUE,
              updated_at BIGINT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS yugito_weekly_rotations(
              week_key TEXT PRIMARY KEY,
              starts_at BIGINT NOT NULL,
              ends_at BIGINT NOT NULL,
              card_ids JSONB NOT NULL,
              created_at BIGINT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS yugito_yt_transactions(
              id BIGSERIAL PRIMARY KEY,
              account_id TEXT NOT NULL,
              amount INTEGER NOT NULL,
              balance_before INTEGER NOT NULL,
              balance_after INTEGER NOT NULL,
              kind TEXT NOT NULL,
              ref_id TEXT,
              metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
              created_at BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS yugito_yt_tx_account_idx ON yugito_yt_transactions(account_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS yugito_matches(
              match_id TEXT PRIMARY KEY,
              mode TEXT NOT NULL,
              player1_id TEXT NOT NULL,
              player2_id TEXT NOT NULL,
              player1_deck JSONB NOT NULL,
              player2_deck JSONB NOT NULL,
              player1_stars NUMERIC(4,1) NOT NULL,
              player2_stars NUMERIC(4,1) NOT NULL,
              winner_id TEXT NOT NULL,
              loser_id TEXT NOT NULL,
              finish_reason TEXT NOT NULL,
              reward_winner INTEGER NOT NULL,
              reward_loser INTEGER NOT NULL,
              created_at BIGINT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS yugito_audit_logs(
              id BIGSERIAL PRIMARY KEY,
              account_id TEXT,
              event_type TEXT NOT NULL,
              ref_id TEXT,
              details JSONB NOT NULL DEFAULT '{}'::jsonb,
              created_at BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS yugito_audit_account_idx ON yugito_audit_logs(account_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS yugito_social_friends(
              owner_id TEXT NOT NULL,
              friend_id TEXT NOT NULL,
              pseudo TEXT NOT NULL DEFAULT '',
              active BOOLEAN NOT NULL DEFAULT TRUE,
              created_at BIGINT NOT NULL,
              updated_at BIGINT NOT NULL,
              PRIMARY KEY(owner_id, friend_id)
            );
            CREATE INDEX IF NOT EXISTS yugito_social_owner_idx ON yugito_social_friends(owner_id, active, updated_at DESC);
            CREATE TABLE IF NOT EXISTS yugito_account_profile(
              account_id TEXT PRIMARY KEY,
              ranked_matches INTEGER NOT NULL DEFAULT 0,
              wins INTEGER NOT NULL DEFAULT 0,
              losses INTEGER NOT NULL DEFAULT 0,
              best_elo INTEGER NOT NULL DEFAULT 100,
              updated_at BIGINT NOT NULL
            );
            ''')
            for c in CATALOG:
                cur.execute('''INSERT INTO yugito_card_catalog(card_id,name,stars,price_yt,purchasable,updated_at)
                    VALUES(%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(card_id) DO UPDATE SET name=EXCLUDED.name, stars=EXCLUDED.stars,
                    price_yt=yugito_card_catalog.price_yt, purchasable=yugito_card_catalog.purchasable, updated_at=EXCLUDED.updated_at''',
                    (c['id'],c['name'],c['stars'],c['price_yt'], c['stars'] > 3.0, now()))
        conn.commit()
    finally:
        conn.close()


def _ensure_player(cur, account_id: str):
    t=now()
    cur.execute('''INSERT INTO yugito_players(account_id,yt_balance,penalty_level,clean_games,penalty_until,created_at,updated_at)
                   VALUES(%s,0,0,0,0,%s,%s) ON CONFLICT(account_id) DO NOTHING''',(account_id,t,t))


def week_bounds(ts: int | None=None):
    d=datetime.fromtimestamp(ts or now(), PARIS)
    monday=d.date().fromordinal(d.date().toordinal()-d.weekday())
    start=datetime(monday.year,monday.month,monday.day,0,0,0,tzinfo=PARIS)
    next_start=datetime.fromtimestamp(start.timestamp()+7*24*3600, PARIS)
    # DST-safe: recreate local midnight for following date
    nd=monday.fromordinal(monday.toordinal()+7)
    next_start=datetime(nd.year,nd.month,nd.day,0,0,0,tzinfo=PARIS)
    return start, next_start


def week_key(ts: int | None=None) -> str:
    start,_=week_bounds(ts)
    iso=start.date().isocalendar()
    return f'{iso.year}-W{iso.week:02d}'


def _rotation_seed(key: str, stars: float) -> int:
    raw=hmac.new(ROTATION_SECRET,f'{key}|{stars:.1f}'.encode(),hashlib.sha256).digest()
    return int.from_bytes(raw[:8],'big')


def _generate_rotation(cur, key: str, previous_ids: set[str]) -> list[str]:
    selected=[]
    for stars,count in WEEKLY_COUNTS.items():
        pool=[c['id'] for c in CATALOG if abs(c['stars']-stars)<0.01]
        rng=random.Random(_rotation_seed(key,stars))
        fresh=[x for x in pool if x not in previous_ids]
        repeat=[x for x in pool if x in previous_ids]
        rng.shuffle(fresh); rng.shuffle(repeat)
        choices=(fresh+repeat)[:min(count,len(pool))]
        selected.extend(choices)
    return selected


def current_rotation(cur) -> dict:
    start,end=week_bounds()
    key=week_key()
    cur.execute('SELECT * FROM yugito_weekly_rotations WHERE week_key=%s',(key,))
    row=cur.fetchone()
    if row:
        ids=list(row[3] if not isinstance(row,dict) else row['card_ids'])
        return {'week_key':key,'starts_at':int(start.timestamp()),'ends_at':int(end.timestamp()),'card_ids':ids}
    prev_key=week_key(int(start.timestamp())-1)
    cur.execute('SELECT card_ids FROM yugito_weekly_rotations WHERE week_key=%s',(prev_key,))
    prev=cur.fetchone()
    previous_ids=set()
    if prev:
        previous_ids=set(prev[0] if not isinstance(prev,dict) else prev['card_ids'])
    ids=_generate_rotation(cur,key,previous_ids)
    cur.execute('INSERT INTO yugito_weekly_rotations(week_key,starts_at,ends_at,card_ids,created_at) VALUES(%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING',
                (key,int(start.timestamp()),int(end.timestamp()),json.dumps(ids),now()))
    return {'week_key':key,'starts_at':int(start.timestamp()),'ends_at':int(end.timestamp()),'card_ids':ids}


def _catalog_from_db(cur):
    cur.execute('SELECT card_id,name,stars,price_yt,purchasable FROM yugito_card_catalog ORDER BY stars,name')
    return [{'id':r[0],'name':r[1],'stars':float(r[2]),'price_yt':int(r[3]),'purchasable':bool(r[4])} for r in cur.fetchall()]


def _penalty_seconds(level: int) -> int:
    level=max(0,int(level))
    # no game-design cap; Python ints grow indefinitely. A huge level is represented to client without overflowing DB timestamps.
    if level > 40:
        return 300 * (2 ** level)
    return 300 * (2 ** level)


def state(account_id: str, include_catalog: bool=True) -> dict:
    conn=connect()
    try:
        with conn.cursor() as cur:
            _ensure_player(cur,account_id)
            rot=current_rotation(cur)
            cur.execute('SELECT yt_balance,penalty_level,clean_games,penalty_until FROM yugito_players WHERE account_id=%s',(account_id,))
            p=cur.fetchone()
            cur.execute('SELECT card_id FROM yugito_owned_cards WHERE account_id=%s',(account_id,))
            owned=[r[0] for r in cur.fetchall()]
            base=list(BASE_CARD_IDS)
            free=list(rot['card_ids'])
            available_ids=sorted(set(base+owned+free))
            result={
                'ok':True,'economy_available':True,
                'yt_balance':int(p[0]),'owned_card_ids':owned,'base_card_ids':base,
                'free_card_ids':free,'available_card_ids':available_ids,'rotation':rot,
                'penalty':{
                    'level':int(p[1]),'clean_games':int(p[2]),'until':int(p[3]),
                    'remaining_seconds':max(0,int(p[3])-now()),
                    'next_duration_seconds':_penalty_seconds(int(p[1])),
                }
            }
            if include_catalog: result['catalog']=_catalog_from_db(cur)
        conn.commit()
        return result
    finally: conn.close()


def audit(cur, account_id: str|None, event_type: str, ref_id: str='', details: dict|None=None):
    cur.execute('INSERT INTO yugito_audit_logs(account_id,event_type,ref_id,details,created_at) VALUES(%s,%s,%s,%s::jsonb,%s)',
                (account_id,event_type,ref_id or None,json.dumps(details or {},ensure_ascii=False),now()))


def purchase(account_id: str, card_id: str) -> dict:
    card_id=str(card_id or '')
    if card_id not in CATALOG_BY_ID: raise ValueError('Carte inconnue.')
    conn=connect()
    try:
        with conn.cursor() as cur:
            _ensure_player(cur,account_id)
            cur.execute('SELECT stars,price_yt,purchasable,name FROM yugito_card_catalog WHERE card_id=%s FOR UPDATE',(card_id,))
            c=cur.fetchone()
            if not c: raise ValueError('Carte inconnue.')
            if float(c[0]) <= 3.0: raise ValueError('Cette carte est déjà débloquée de base.')
            if not c[2]: raise ValueError("Cette carte n'est pas disponible à l'achat.")
            cur.execute('SELECT 1 FROM yugito_owned_cards WHERE account_id=%s AND card_id=%s',(account_id,card_id))
            if cur.fetchone(): raise ValueError('Cette carte est déjà possédée.')
            price=int(c[1])
            cur.execute('SELECT yt_balance FROM yugito_players WHERE account_id=%s FOR UPDATE',(account_id,))
            before=int(cur.fetchone()[0])
            if before < price: raise ValueError(f'Pas assez de YT : {price} YT requis.')
            after=before-price
            t=now()
            cur.execute('UPDATE yugito_players SET yt_balance=%s,updated_at=%s WHERE account_id=%s',(after,t,account_id))
            cur.execute('INSERT INTO yugito_owned_cards(account_id,card_id,acquired_at,source,price_paid) VALUES(%s,%s,%s,%s,%s)',(account_id,card_id,t,'shop_yt',price))
            ref='buy-'+secrets.token_hex(10)
            cur.execute('INSERT INTO yugito_yt_transactions(account_id,amount,balance_before,balance_after,kind,ref_id,metadata,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s)',
                        (account_id,-price,before,after,'purchase_card',ref,json.dumps({'card_id':card_id,'name':c[3]},ensure_ascii=False),t))
            audit(cur,account_id,'purchase_card',ref,{'card_id':card_id,'price':price,'balance_before':before,'balance_after':after})
        conn.commit()
        return {'ok':True,'purchase':{'card_id':card_id,'price_yt':price},'state':state(account_id,False)}
    except Exception:
        conn.rollback(); raise
    finally: conn.close()


def dev_grant_yt(account_id: str, amount: int) -> dict:
    amount=int(amount or 0)
    if amount <= 0: raise ValueError('Le montant YT doit être positif.')
    if amount > 10_000_000: raise ValueError('Maximum 10 000 000 YT par opération DEV.')
    conn=connect()
    try:
        with conn.cursor() as cur:
            _ensure_player(cur,account_id)
            cur.execute('SELECT yt_balance FROM yugito_players WHERE account_id=%s FOR UPDATE',(account_id,))
            before=int(cur.fetchone()[0]); after=before+amount; t=now(); ref='dev-yt-'+secrets.token_hex(10)
            cur.execute('UPDATE yugito_players SET yt_balance=%s,updated_at=%s WHERE account_id=%s',(after,t,account_id))
            cur.execute('INSERT INTO yugito_yt_transactions(account_id,amount,balance_before,balance_after,kind,ref_id,metadata,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s)',
                        (account_id,amount,before,after,'dev_grant',ref,json.dumps({'dev':True},ensure_ascii=False),t))
            audit(cur,account_id,'dev_grant_yt',ref,{'amount':amount,'balance_before':before,'balance_after':after})
        conn.commit()
        return {'ok':True,'amount':amount,'yt_balance':after,'state':state(account_id,False)}
    except Exception:
        conn.rollback(); raise
    finally: conn.close()


def dev_remove_card(account_id: str, card_id: str) -> dict:
    card_id=str(card_id or '')
    c=CATALOG_BY_ID.get(card_id)
    if not c: raise ValueError('Carte inconnue.')
    if float(c['stars']) <= BASE_STARS: raise ValueError('Les cartes 3★ restent débloquées de base pour tous les joueurs.')
    conn=connect()
    try:
        with conn.cursor() as cur:
            _ensure_player(cur,account_id)
            cur.execute('SELECT source,price_paid FROM yugito_owned_cards WHERE account_id=%s AND card_id=%s FOR UPDATE',(account_id,card_id))
            owned=cur.fetchone()
            if not owned: raise ValueError("Cette carte n'est pas possédée définitivement.")
            cur.execute('DELETE FROM yugito_owned_cards WHERE account_id=%s AND card_id=%s',(account_id,card_id))
            ref='dev-card-'+secrets.token_hex(10)
            audit(cur,account_id,'dev_remove_card',ref,{'card_id':card_id,'name':c['name'],'previous_source':owned[0],'previous_price_paid':int(owned[1])})
        conn.commit()
        return {'ok':True,'card_id':card_id,'state':state(account_id,False)}
    except Exception:
        conn.rollback(); raise
    finally: conn.close()


def dev_record_elo(account_id: str, amount: int, before: int, after: int) -> None:
    conn=connect()
    try:
        with conn.cursor() as cur:
            _ensure_player(cur,account_id)
            audit(cur,account_id,'dev_grant_elo','dev-elo-'+secrets.token_hex(10),{'amount':int(amount),'elo_before':int(before),'elo_after':int(after)})
        conn.commit()
    finally: conn.close()


def validate_deck_ids(card_ids: list[str], available_ids: set[str]|None=None, require_eight: bool=True) -> tuple[bool,str,float]:
    ids=[str(x) for x in (card_ids or [])]
    if require_eight and len(ids)!=8: return False,'Un deck multijoueur doit contenir exactement 8 cartes.',0.0
    if len(ids)>8: return False,'Maximum 8 cartes.',0.0
    if len(set(ids))!=len(ids): return False,'Une même carte ne peut pas être utilisée deux fois.',0.0
    cards=[]
    for cid in ids:
        c=CATALOG_BY_ID.get(cid)
        if not c: return False,f'Carte inconnue : {cid}',0.0
        if available_ids is not None and cid not in available_ids: return False,f'Carte non possédée : {c["name"]}',0.0
        cards.append(c)
    total=sum(float(c['stars']) for c in cards)
    if total>32.5001: return False,'Le deck dépasse 32,5★.',total
    limits={3.5:4,4.0:3,4.5:2,5.0:1}
    for s,lim in limits.items():
        if sum(1 for c in cards if abs(c['stars']-s)<0.01)>lim: return False,f'Trop de cartes {s:g}★.',total
    return True,'',round(total,1)


def validate_deck(account_id: str, card_ids: list[str], require_eight: bool=True) -> dict:
    st=state(account_id,False)
    ok,msg,total=validate_deck_ids(card_ids,set(st['available_card_ids']),require_eight)
    return {'ok':ok,'error':msg if not ok else '', 'stars':total,'available_card_ids':st['available_card_ids']}


def _b64e(raw: bytes)->str:
    import base64
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')

def _b64d(s:str)->bytes:
    import base64
    return base64.urlsafe_b64decode(s+'='*((4-len(s)%4)%4))


def issue_permit(account_id: str, mode: str='classic') -> dict:
    mode=str(mode or 'classic').strip().lower()
    if mode not in ('classic','ranked','private','tournament'):
        raise ValueError('Mode multijoueur invalide.')
    st=state(account_id,False)
    # La sanction concerne le matchmaking Classic/Classé. Les duels privés et
    # tournois restent accessibles, tout en conservant la validation collection.
    if mode in ('classic','ranked') and st['penalty']['remaining_seconds']>0:
        raise ValueError(f"Matchmaking bloqué encore {st['penalty']['remaining_seconds']} s.")
    payload={'v':1,'aid':account_id,'mode':mode,'week':st['rotation']['week_key'],'available':st['available_card_ids'],'iat':now(),'exp':now()+1800,'nonce':secrets.token_hex(10)}
    body=_b64e(json.dumps(payload,separators=(',',':'),sort_keys=True).encode())
    sig=_b64e(hmac.new(PERMIT_SECRET,body.encode(),hashlib.sha256).digest())
    return {'ok':True,'permit':'yp1.'+body+'.'+sig,'expires_at':payload['exp']}


def verify_permit(token: str) -> dict|None:
    try:
        a,b,s=str(token).split('.',2)
        if a!='yp1': return None
        exp=_b64e(hmac.new(PERMIT_SECRET,b.encode(),hashlib.sha256).digest())
        if not hmac.compare_digest(s,exp): return None
        p=json.loads(_b64d(b).decode())
        if int(p.get('exp') or 0)<=now(): return None
        return p
    except Exception: return None


def _reward(mode: str, winner_stars: float, loser_stars: float) -> int:
    r=WIN_RULES[mode]
    amount=round(r['base']+(float(loser_stars)-float(winner_stars))*PER_STAR_YT)
    return max(r['min'],min(r['max'],int(amount)))


def _credit(cur, account_id: str, amount: int, kind: str, ref_id: str, metadata: dict):
    _ensure_player(cur,account_id)
    cur.execute('SELECT yt_balance FROM yugito_players WHERE account_id=%s FOR UPDATE',(account_id,))
    before=int(cur.fetchone()[0]); after=max(0,before+int(amount))
    cur.execute('UPDATE yugito_players SET yt_balance=%s,updated_at=%s WHERE account_id=%s',(after,now(),account_id))
    cur.execute('INSERT INTO yugito_yt_transactions(account_id,amount,balance_before,balance_after,kind,ref_id,metadata,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s)',
                (account_id,int(amount),before,after,kind,ref_id,json.dumps(metadata,ensure_ascii=False),now()))
    return before,after


def _clean_game(cur, account_id: str):
    _ensure_player(cur,account_id)
    cur.execute('SELECT penalty_level,clean_games FROM yugito_players WHERE account_id=%s FOR UPDATE',(account_id,))
    row=cur.fetchone()
    lvl=int(row[0]); clean=int(row[1])+1
    if clean >= 3:
        lvl=max(0,lvl-1); clean=0
    cur.execute('UPDATE yugito_players SET penalty_level=%s,clean_games=%s,updated_at=%s WHERE account_id=%s',(lvl,clean,now(),account_id))
    return {'level':lvl,'clean_games':clean}


def _offense(cur, account_id: str):
    _ensure_player(cur,account_id)
    cur.execute('SELECT penalty_level FROM yugito_players WHERE account_id=%s FOR UPDATE',(account_id,))
    level=int(cur.fetchone()[0])
    duration=_penalty_seconds(level)
    # BIGINT timestamp safety only; game-design level keeps increasing without a cap.
    penalty_until=min(9_223_372_036_854_775_000, now()+duration)
    cur.execute('UPDATE yugito_players SET penalty_level=%s,clean_games=0,penalty_until=%s,updated_at=%s WHERE account_id=%s',(level+1,penalty_until,now(),account_id))
    return {'applied_level':level,'next_level':level+1,'duration_seconds':duration,'until':penalty_until}


def _same_pair_recent_match_count(cur, account_a: str, account_b: str, window_seconds: int = ANTI_FARM_WINDOW_SECONDS) -> int:
    cutoff = now() - int(window_seconds)
    cur.execute(
        """SELECT COUNT(*) FROM yugito_matches
           WHERE created_at >= %s
             AND ((player1_id=%s AND player2_id=%s) OR (player1_id=%s AND player2_id=%s))""",
        (cutoff, account_a, account_b, account_b, account_a),
    )
    row = cur.fetchone()
    return int(row[0] if row else 0)


def _anti_farm_multiplier(previous_pair_matches: int) -> float:
    idx = max(0, int(previous_pair_matches))
    if idx < len(ANTI_FARM_MULTIPLIERS):
        return float(ANTI_FARM_MULTIPLIERS[idx])
    return 0.0


def settle_match(caller_id: str, body: dict) -> dict:
    mode=str(body.get('mode') or '').strip().lower()
    if mode not in ('classic','ranked','private','tournament'):
        raise ValueError('Mode de match invalide.')
    match_id=str(body.get('match_id') or '')
    if not (12 <= len(match_id) <= 160): raise ValueError('Identifiant de match invalide.')
    p1=verify_permit(str(body.get('player1_permit') or ''))
    p2=verify_permit(str(body.get('player2_permit') or ''))
    if not p1 or not p2 or p1.get('aid')==p2.get('aid'):
        raise ValueError('Permis multijoueur invalides.')
    p1id,p2id=str(p1['aid']),str(p2['aid'])
    if caller_id not in (p1id,p2id): raise ValueError('Le compte appelant ne participe pas à ce match.')
    if str(p1.get('mode') or '') != mode or str(p2.get('mode') or '') != mode:
        raise ValueError('Permis délivrés pour un autre mode multijoueur.')
    winner_id=str(body.get('winner_account_id') or '')
    if winner_id not in (p1id,p2id): raise ValueError('Vainqueur invalide.')
    loser_id=p2id if winner_id==p1id else p1id
    reason=str(body.get('finish_reason') or 'natural').strip().lower()
    if reason not in ('natural','disconnect','abandon'): raise ValueError('Raison de fin invalide.')
    d1=[str(x) for x in (body.get('player1_deck') or [])]
    d2=[str(x) for x in (body.get('player2_deck') or [])]
    ok,msg,s1=validate_deck_ids(d1,set(p1.get('available') or []),True)
    if not ok: raise ValueError('Deck joueur 1 refusé : '+msg)
    ok,msg,s2=validate_deck_ids(d2,set(p2.get('available') or []),True)
    if not ok: raise ValueError('Deck joueur 2 refusé : '+msg)
    # Les deux joueurs doivent avoir commencé avec la même rotation. On ne
    # compare PAS à l'heure courante : un match démarré dimanche 23:55 doit
    # pouvoir se terminer normalement après le changement du lundi 00:00.
    if str(p1.get('week') or '') != str(p2.get('week') or ''):
        raise ValueError('Permis issus de rotations différentes.')
    winner_stars=s1 if winner_id==p1id else s2
    loser_stars=s2 if winner_id==p1id else s1

    conn=connect()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT match_id,reward_winner,reward_loser FROM yugito_matches WHERE match_id=%s',(match_id,))
            prior=cur.fetchone()
            if prior:
                conn.rollback()
                return {'ok':True,'duplicate':True,'match_id':match_id,'reward_winner':int(prior[1]),'reward_loser':int(prior[2]),'state':state(caller_id,False)}
            # Anti-farm same pair: 1-3 full rewards, 4th 50%, 5th 25%, 6th+ 0%
            # in a rolling 6-hour window.
            previous_pair_matches = _same_pair_recent_match_count(cur,p1id,p2id)
            anti_farm_multiplier = _anti_farm_multiplier(previous_pair_matches)
            reward_winner_base = _reward(mode,winner_stars,loser_stars) if mode in WIN_RULES else 0
            reward_loser_base = LOSS_NATURAL_YT if reason=='natural' and mode in WIN_RULES else 0
            reward_winner = int(round(reward_winner_base * anti_farm_multiplier))
            reward_loser = int(round(reward_loser_base * anti_farm_multiplier))

            # Serialize both player wallets/penalty rows before mutation.
            _ensure_player(cur,p1id); _ensure_player(cur,p2id)
            cur.execute('SELECT account_id FROM yugito_players WHERE account_id IN (%s,%s) FOR UPDATE',(p1id,p2id)); cur.fetchall()
            meta={'match_id':match_id,'mode':mode,'finish_reason':reason,'winner_stars':winner_stars,'loser_stars':loser_stars,
                  'anti_farm_multiplier':anti_farm_multiplier,'pair_matches_in_window_before':previous_pair_matches}
            if reward_winner:
                _credit(cur,winner_id,reward_winner,'match_win',match_id,meta)
            if reward_loser:
                _credit(cur,loser_id,reward_loser,'match_loss_complete',match_id,meta)
            if reason=='natural' and mode in WIN_RULES:
                clean_w=_clean_game(cur,winner_id); clean_l=_clean_game(cur,loser_id); penalty=None
            elif reason!='natural' and mode in WIN_RULES:
                # Matchmaking uniquement : le perdant qui quitte prend le palier suivant.
                penalty=_offense(cur,loser_id); clean_w=None; clean_l=None
            else:
                # Privé/tournoi : collection vérifiée, mais aucun farming YT ni nettoyage/aggravation de sanction matchmaking.
                penalty=None; clean_w=None; clean_l=None
            cur.execute('''INSERT INTO yugito_matches(match_id,mode,player1_id,player2_id,player1_deck,player2_deck,player1_stars,player2_stars,winner_id,loser_id,finish_reason,reward_winner,reward_loser,created_at)
                           VALUES(%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s)''',
                        (match_id,mode,p1id,p2id,json.dumps(d1),json.dumps(d2),s1,s2,winner_id,loser_id,reason,reward_winner,reward_loser,now()))
            audit(cur,winner_id,'match_complete',match_id,dict(meta,role='winner',reward_yt=reward_winner))
            audit(cur,loser_id,'match_complete',match_id,dict(meta,role='loser',reward_yt=reward_loser,penalty=penalty))
        conn.commit()
        return {'ok':True,'duplicate':False,'match_id':match_id,'reward_winner':reward_winner,'reward_loser':reward_loser,'penalty':penalty,'state':state(caller_id,False)}
    except Exception:
        conn.rollback(); raise
    finally: conn.close()


def record_event(account_id: str, event_type: str, ref_id: str='', details: dict|None=None) -> None:
    """Best-effort persistent account audit event. Never logs Google tokens/secrets."""
    if not available() or not account_id:
        return
    conn=connect()
    try:
        with conn.cursor() as cur:
            _ensure_player(cur,str(account_id))
            audit(cur,str(account_id),str(event_type or 'event')[:80],str(ref_id or '')[:160],details or {})
        conn.commit()
    finally:
        conn.close()


def account_logs(account_id: str, limit: int=200) -> dict:
    limit=max(1,min(1000,int(limit or 200)))
    conn=connect()
    try:
        with conn.cursor() as cur:
            _ensure_player(cur,account_id)
            cur.execute('SELECT yt_balance,penalty_level,clean_games,penalty_until FROM yugito_players WHERE account_id=%s',(account_id,))
            p=cur.fetchone()
            cur.execute('SELECT card_id,acquired_at,source,price_paid FROM yugito_owned_cards WHERE account_id=%s ORDER BY acquired_at DESC',(account_id,))
            cards=[{'card_id':r[0],'acquired_at':int(r[1]),'source':r[2],'price_paid':int(r[3])} for r in cur.fetchall()]
            cur.execute('SELECT id,amount,balance_before,balance_after,kind,ref_id,metadata,created_at FROM yugito_yt_transactions WHERE account_id=%s ORDER BY created_at DESC,id DESC LIMIT %s',(account_id,limit))
            tx=[{'id':r[0],'amount':r[1],'before':r[2],'after':r[3],'kind':r[4],'ref_id':r[5],'metadata':r[6],'created_at':r[7]} for r in cur.fetchall()]
            cur.execute('SELECT id,event_type,ref_id,details,created_at FROM yugito_audit_logs WHERE account_id=%s ORDER BY created_at DESC,id DESC LIMIT %s',(account_id,limit))
            logs=[{'id':r[0],'event_type':r[1],'ref_id':r[2],'details':r[3],'created_at':r[4]} for r in cur.fetchall()]
        conn.commit()
        return {'ok':True,'account_id':account_id,'player':{'yt_balance':int(p[0]),'penalty_level':int(p[1]),'clean_games':int(p[2]),'penalty_until':int(p[3])},'owned_cards':cards,'transactions':tx,'logs':logs}
    finally: conn.close()


def public_weekly_rotation() -> dict:
    """Public read-only weekly rotation; exposes no account/session data."""
    conn=connect()
    try:
        with conn.cursor() as cur:
            rot=current_rotation(cur)
            ids=list(rot.get('card_ids') or [])
            cards=[]
            for cid in ids:
                c=CATALOG_BY_ID.get(str(cid))
                if c:
                    cards.append({'id':c['id'],'name':c['name'],'stars':float(c['stars'])})
            return {
                'ok':True,
                'source':'YUGITO-Auth-Server',
                'week_key':rot['week_key'],
                'starts_at':rot['starts_at'],
                'ends_at':rot['ends_at'],
                'card_ids':ids,
                'cards':cards,
                'counts':{'3.5':8,'4.0':6,'4.5':4,'5.0':4},
            }
    finally:
        conn.close()


def health() -> dict:
    out={'economy_configured':bool(DATABASE_URL),'economy_driver':bool(psycopg2),'economy_available':False,'catalog_cards':len(CATALOG),'weekly_free_counts':{'3.5':8,'4':6,'4.5':4,'5':4}}
    if not available(): return out
    try:
        conn=connect()
        with conn.cursor() as cur: cur.execute('SELECT 1'); cur.fetchone()
        conn.close(); out['economy_available']=True
    except Exception as exc:
        out['economy_error']=str(exc)[:180]
    return out


def admin_authorized(headers) -> bool:
    if not ADMIN_TOKEN: return False
    got=str(headers.get('X-YUGITO-ADMIN-TOKEN') or '')
    return bool(got and hmac.compare_digest(got,ADMIN_TOKEN))


# ---------------------------------------------------------------------------
# YUGITO 1.6.7 — portable account profile + friends by canonical account_id
# ---------------------------------------------------------------------------
def social_merge(account_id: str, local_friends: list[dict] | None) -> dict:
    """Merge-only bootstrap. Existing tombstones are never resurrected by an old device."""
    owner=str(account_id or '')
    rows=[]
    seen=set()
    for raw in (local_friends or [])[:300]:
        if not isinstance(raw,dict):
            continue
        fid=str(raw.get('account_id') or raw.get('friend_id') or '').strip()
        pseudo=' '.join(str(raw.get('pseudo') or 'Shinobi').strip().split())[:20]
        if not fid or fid==owner or fid in seen:
            continue
        seen.add(fid); rows.append((fid,pseudo or 'Shinobi'))
    conn=connect()
    try:
        with conn.cursor() as cur:
            t=now()
            for fid,pseudo in rows:
                cur.execute('SELECT active FROM yugito_social_friends WHERE owner_id=%s AND friend_id=%s',(owner,fid))
                ex=cur.fetchone()
                if ex is None:
                    cur.execute('INSERT INTO yugito_social_friends(owner_id,friend_id,pseudo,active,created_at,updated_at) VALUES(%s,%s,%s,TRUE,%s,%s)',(owner,fid,pseudo,t,t))
                elif bool(ex[0]):
                    cur.execute('UPDATE yugito_social_friends SET pseudo=%s,updated_at=%s WHERE owner_id=%s AND friend_id=%s',(pseudo,t,owner,fid))
            cur.execute('SELECT friend_id,pseudo,updated_at FROM yugito_social_friends WHERE owner_id=%s AND active=TRUE ORDER BY lower(pseudo),friend_id',(owner,))
            friends=[{'account_id':r[0],'pseudo':r[1],'updated_at':int(r[2])} for r in cur.fetchall()]
        conn.commit()
        return {'ok':True,'friends':friends,'social_sync':True}
    finally:
        conn.close()


def social_set(account_id: str, friend_id: str, pseudo: str='', active: bool=True) -> dict:
    owner=str(account_id or ''); fid=str(friend_id or '').strip()
    if not fid or fid==owner:
        raise ValueError('Ami invalide.')
    pseudo=' '.join(str(pseudo or 'Shinobi').strip().split())[:20] or 'Shinobi'
    conn=connect()
    try:
        with conn.cursor() as cur:
            t=now()
            cur.execute('INSERT INTO yugito_social_friends(owner_id,friend_id,pseudo,active,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(owner_id,friend_id) DO UPDATE SET pseudo=EXCLUDED.pseudo,active=EXCLUDED.active,updated_at=EXCLUDED.updated_at', (owner,fid,pseudo,bool(active),t,t))
        conn.commit()
    finally:
        conn.close()
    return social_merge(owner,[])


def profile_sync(account_id: str, profile: dict | None, elo: int=100) -> dict:
    p=dict(profile or {}); aid=str(account_id or '')
    incoming=(
        max(0,int(p.get('ranked_matches') or 0)),
        max(0,int(p.get('wins') or p.get('ranked_wins') or 0)),
        max(0,int(p.get('losses') or p.get('ranked_losses') or 0)),
        max(max(0,int(elo or 100)),max(0,int(p.get('best_elo') or elo or 100))),
    )
    conn=connect()
    try:
        with conn.cursor() as cur:
            t=now()
            cur.execute('INSERT INTO yugito_account_profile(account_id,ranked_matches,wins,losses,best_elo,updated_at) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(account_id) DO UPDATE SET ranked_matches=GREATEST(yugito_account_profile.ranked_matches,EXCLUDED.ranked_matches), wins=GREATEST(yugito_account_profile.wins,EXCLUDED.wins), losses=GREATEST(yugito_account_profile.losses,EXCLUDED.losses), best_elo=GREATEST(yugito_account_profile.best_elo,EXCLUDED.best_elo), updated_at=EXCLUDED.updated_at', (aid,incoming[0],incoming[1],incoming[2],incoming[3],t))
            cur.execute('SELECT ranked_matches,wins,losses,best_elo,updated_at FROM yugito_account_profile WHERE account_id=%s',(aid,))
            r=cur.fetchone()
        conn.commit()
        return {'ranked_matches':int(r[0]),'wins':int(r[1]),'losses':int(r[2]),'best_elo':int(r[3]),'updated_at':int(r[4])}
    finally:
        conn.close()


def profile_state(account_id: str, elo: int=100) -> dict:
    return profile_sync(account_id,{},elo)
