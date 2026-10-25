"""Vendas reais da Hotmart pra comparar com as compras que o pixel da Meta registrou. Usado pelo gerar_leads.py.

config.json do dashboard (leads/<slug>/config.json):
    "hotmart": {"produtos": {"8514445": "Raio-X da MP 1.376"}, "desde": "2026-08-12"}
"desde" e o inicio do acumulado: o primeiro dia de venda do produto nesta conta da Hotmart. Antes disso o pixel
tem compras de outro checkout (Rocha Mazzo: antes de 18/09/2026, primeira venda na Hotmart, o low ticket vendia pela Hubla).

Credencial (Hotmart > Ferramentas > Credenciais Hotmart), uma por cliente, porque cada um tem a sua conta:
    HOTMART_<SLUG>_CLIENT_ID e HOTMART_<SLUG>_CLIENT_SECRET   (ex: HOTMART_ROCHA_MAZZO_CLIENT_ID)
    HOTMART_<SLUG>_BASIC opcional: e o base64 de "client_id:client_secret", calculado se faltar.
No GitHub ficam nos secrets do repositorio; local, no .env desta pasta (fora do Git).

So numeros agregados saem daqui: nome, e-mail e documento do comprador nunca entram no payload.
"""

import base64
import datetime as dt
import os
import sys
import time
from pathlib import Path

import requests

AQUI = Path(__file__).resolve().parent
TOKEN_URL = "https://api-sec-vlc.hotmart.com/security/oauth/token"
API = "https://developers.hotmart.com/payments/api/v1"

# Venda que conta: aprovada ou concluida (passou da garantia). Reembolso e chargeback foram vendas
# que o pixel registrou na hora e depois voltaram, por isso aparecem separados.
VENDIDAS = ("APPROVED", "COMPLETE")
DEVOLVIDAS = ("REFUNDED", "PARTIALLY_REFUNDED", "CHARGEBACK", "PROTESTED")


def credencial(slug):
    prefixo = f"HOTMART_{slug.upper().replace('-', '_')}_"
    local = {}
    if (AQUI / ".env").exists():
        for linha in (AQUI / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, v = linha.split("=", 1)
                local[k.strip()] = v.strip().strip('"')
    valor = lambda k: (os.environ.get(prefixo + k) or local.get(prefixo + k) or "").strip()
    cid, segredo = valor("CLIENT_ID"), valor("CLIENT_SECRET")
    if not cid or not segredo:
        return None
    basic = valor("BASIC").removeprefix("Basic ").strip() or base64.b64encode(f"{cid}:{segredo}".encode()).decode()
    return cid, segredo, basic


class Hotmart:
    def __init__(self, slug):
        cid, segredo, basic = credencial(slug)
        r = requests.post(TOKEN_URL, params={"grant_type": "client_credentials", "client_id": cid,
                                             "client_secret": segredo},
                          headers={"Authorization": f"Basic {basic}"}, timeout=60)
        if r.status_code != 200:
            sys.exit(f"ERRO Hotmart no token ({r.status_code}): confira a credencial")
        self.h = {"Authorization": f"Bearer {r.json()['access_token']}", "Content-Type": "application/json"}

    def get(self, caminho, **params):
        """GET com paginacao por page_token e retry em limite de chamadas."""
        itens, params = [], {**params, "max_results": 500}
        while True:
            for tentativa in range(5):
                r = requests.get(f"{API}{caminho}", headers=self.h, params=params, timeout=60)
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(10 * (tentativa + 1))
                    continue
                break
            if r.status_code != 200:
                sys.exit(f"ERRO Hotmart em {caminho} ({r.status_code}): {r.text[:200]}")
            corpo = r.json()
            itens += corpo.get("items", [])
            proxima = corpo.get("page_info", {}).get("next_page_token")
            if not proxima:
                return itens
            params["page_token"] = proxima


FONTES = {"ig": "Instagram", "fb": "Facebook", "organic": "Orgânico", "an": "Audience Network"}


def origem(tracking):
    """Origem legivel a partir do sck/src do checkout. A LP repassa as UTMs como
    "utm_source:ig|utm_medium:paid|...". Macro da Meta que chegou sem preencher ({{...}}) fica de fora."""
    bruto = tracking.get("source_sck") or tracking.get("source") or ""
    if "utm_source:" not in bruto:
        return bruto or "sem origem"
    utm = dict(p.split(":", 1) for p in bruto.split("|") if ":" in p)
    limpo = lambda k: "" if "{{" in utm.get(k, "") else utm.get(k, "").strip()
    partes = [FONTES.get(limpo("utm_source"), limpo("utm_source")) or "Meta",
              "anúncio" if limpo("utm_medium") == "paid" else limpo("utm_medium"), limpo("utm_content")]
    return " · ".join(p for p in partes if p)


def ms(data, fuso, fim=False):
    hora = dt.time(23, 59, 59, 999000) if fim else dt.time()
    return int(dt.datetime.combine(data, hora, tzinfo=fuso).timestamp() * 1000)


def vendas(hm, produtos, inicio, fim, fuso):
    """Transacoes do periodo (pela data de aprovacao, quando o pixel dispara, no fuso da conta do Meta),
    so com o que o dashboard usa."""
    linhas = []
    for produto in produtos:
        for status in VENDIDAS + DEVOLVIDAS:
            for v in hm.get("/sales/history", product_id=produto, transaction_status=status,
                            start_date=ms(inicio, fuso), end_date=ms(fim, fuso, fim=True)):
                p = v.get("purchase", {})
                quando = p.get("approved_date") or p.get("order_date")
                if not quando:
                    continue
                preco = p.get("price") or {}
                linhas.append({
                    "transacao": p.get("transaction"),
                    "dia": dt.datetime.fromtimestamp(quando / 1000, fuso).date(),
                    "status": p.get("status", status),
                    "valor": float(preco.get("value") or 0) if preco.get("currency_code", "BRL") == "BRL" else 0.0,
                    "origem": origem(p.get("tracking") or {}),
                })
    # A mesma transacao nao pode aparecer em dois status; se a API repetir, fica uma so.
    return list({l["transacao"]: l for l in linhas}.values())


def resumo(linhas, inicio, fim):
    doper = [l for l in linhas if inicio <= l["dia"] <= fim]
    vendidas = [l for l in doper if l["status"] in VENDIDAS]
    devolvidas = [l for l in doper if l["status"] not in VENDIDAS]
    origens = {}
    for l in vendidas:
        o = origens.setdefault(l["origem"], {"nome": l["origem"], "vendas": 0})
        o["vendas"] += 1
    return {
        "vendas": len(vendidas),
        "faturamento": round(sum(l["valor"] for l in vendidas), 2),
        "devolvidas": len(devolvidas),
        "origens": sorted(origens.values(), key=lambda o: -o["vendas"])[:10],
    }
