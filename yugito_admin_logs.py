#!/usr/bin/env python3
from __future__ import annotations
import json, os, sys, urllib.parse, urllib.request

BASE=(os.getenv('YUGITO_PUBLIC_BASE_URL') or 'https://yugito-auth-server.onrender.com').rstrip('/')
TOKEN=(os.getenv('YUGITO_ADMIN_TOKEN') or '').strip()

def main():
    if len(sys.argv)<2:
        raise SystemExit('Usage: python yugito_admin_logs.py <account_id> [limit]')
    if not TOKEN:
        raise SystemExit('Définis YUGITO_ADMIN_TOKEN dans ton environnement avant de lancer cet outil.')
    aid=sys.argv[1]
    limit=int(sys.argv[2]) if len(sys.argv)>2 else 200
    url=BASE+'/api/admin/account-logs?'+urllib.parse.urlencode({'account_id':aid,'limit':limit})
    req=urllib.request.Request(url,headers={'X-YUGITO-ADMIN-TOKEN':TOKEN,'User-Agent':'YUGITO-Admin/1.5.0'})
    with urllib.request.urlopen(req,timeout=20) as r:
        data=json.loads(r.read().decode('utf-8'))
    print(json.dumps(data,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
