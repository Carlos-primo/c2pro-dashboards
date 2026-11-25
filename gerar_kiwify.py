"""Vendas reais da Kiwify pra comparar com as compras que o pixel da Meta registrou. Mesmo formato do
gerar_hotmart.py, usado pelo gerar_vendas.py no quadro "Vendas reais".

config.json do dashboard (vendas/<slug>/config.json):
    "kiwify": {"produtos": {"<id do produto>": "<nome>"}, "excluir_planos": ["Renova"]}
"excluir_planos": trechos do nome do plano/oferta que nao contam (Wescley: "Renova" sao renovacoes da base,
nao vem de anuncio). Sem acento e sem diferenciar maiuscula.
"planos": se existir, so contam vendas desses planos (nome exato do plano na Kiwify). Wescley: os planos dos
checkouts para onde as paginas dos anuncios mandam (lp-vsl-3-1c e oferta-vsl1-reforma -> Fiedl94 "Plano Anual - 12x"
e HIFbNyG "Plano Anual - Pix"; vsl-demo e oferta-2027-roleta -> ZFLqQz7 "OFF Oferta Roleta - 12x"), conferido em 23/11/2026.

"todos_produtos": true: conta todos os produtos da conta Kiwify (o "produtos" do config so da nome), pra o
faturamento total do cliente no dia, de qualquer origem. Wescley (25/11/2026): ROAS principal = faturamento da
Kiwify (sem renovacoes) / investimento das duas contas de anuncio. A Hotmart dele e da Black (dashboard proprio).

Credencial (Kiwify > Apps > API > Criar API Key, so com Vendas e Produtos marcados), uma por cliente:
    KIWIFY_<SLUG>_CLIENT_ID, KIWIFY_<SLUG>_CLIENT_SECRET e KIWIFY_<SLUG>_ACCOUNT_ID   (ex: KIWIFY_WESCLEY_CLIENT_ID)
No GitHub ficam nos secrets do repositorio; local, no .env desta pasta (fora do Git).

Valor = net_amount (liquido, ja sem a taxa da Kiwify), que vem na listagem sem precisar abrir venda por venda.
Data = created_at no fuso da conta do Meta (pix e cartao aprovam em segundos).
So numeros agregados saem daqui: nome, e-mail e documento do comprador nunca entram no payload.
"""

import datetime as dt
import os
import sys
import time
import unicodedata
from pathlib import Path

import requests

import gerar_hotmart

AQUI = Path(__file__).resolve().parent
API = "https://public-api.kiwify.com/v1"

# Status da Kiwify -> status no formato do gerar_hotmart.resumo (que conta APPROVED como venda e o resto como devolvida).
# refused e waiting_payment nao foram vendas e ficam de fora.
STATUS = {"paid": "APPROVED", "refunded": "REFUNDED", "chargedback": "CHARGEBACK"}


def _norm(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()


def credencial(slug):
    prefixo = f"KIWIFY_{slug.upper().replace('-', '_')}_"
    local = {}
    if (AQUI / ".env").exists():
        for linha in (AQUI / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, v = linha.split("=", 1)
                local[k.strip()] = v.strip().strip('"')
    valor = lambda k: (os.environ.get(prefixo + k) or local.get(prefixo + k) or "").strip()
    cid, segredo, conta = valor("CLIENT_ID"), valor("CLIENT_SECRET"), valor("ACCOUNT_ID")
    if not cid or not segredo or not conta:
        return None
    return cid, segredo, conta


class Kiwify:
    def __init__(self, slug):
        cid, segredo, conta = credencial(slug)
        r = requests.post(f"{API}/oauth/token", data={"client_id": cid, "client_secret": segredo}, timeout=60)
        if r.status_code != 200:
            sys.exit(f"ERRO Kiwify no token ({r.status_code}): confira a credencial")
        self.h = {"Authorization": f"Bearer {r.json()['access_token']}", "x-kiwify-account-id": conta}

    def vendas(self, inicio, fim):
        """Todas as vendas criadas entre inicio e fim (datas), paginando de 100 em 100."""
        itens, pagina = [], 1
        while True:
            params = {"start_date": inicio.isoformat(), "end_date": fim.isoformat(),
                      "page_size": 100, "page_number": pagina}
            for tentativa in range(5):
                r = requests.get(f"{API}/sales", headers=self.h, params=params, timeout=60)
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(10 * (tentativa + 1))
                    continue
                break
            if r.status_code != 200:
                sys.exit(f"ERRO Kiwify em /sales ({r.status_code}): {r.text[:200]}")
            lote = r.json().get("data", [])
            itens += lote
            if len(lote) < 100:
                return itens
            pagina += 1


def vendas(kw, produtos, inicio, fim, fuso, excluir_planos=(), planos=()):
    """Vendas do periodo (pelo dia no fuso da conta do Meta), so com o que o dashboard usa.
    produtos=None: todos os produtos da conta."""
    excluir = [_norm(e) for e in excluir_planos]
    so = {_norm(p) for p in planos}
    linhas = []
    # Um dia de folga nas pontas: a Kiwify filtra em UTC e o dia certo sai do fuso abaixo.
    for v in kw.vendas(inicio - dt.timedelta(days=1), fim + dt.timedelta(days=1)):
        p = v.get("product") or {}
        status = STATUS.get(v.get("status"))
        if (produtos is not None and p.get("id") not in produtos) or not status:
            continue
        plano = _norm(f"{p.get('plan_name') or ''} {p.get('offer_name') or ''}")
        if any(e in plano for e in excluir):
            continue
        if so and _norm(p.get("plan_name") or p.get("offer_name")) not in so:
            continue
        dia = dt.datetime.fromisoformat(v["created_at"].replace("Z", "+00:00")).astimezone(fuso).date()
        if not inicio <= dia <= fim:
            continue
        linhas.append({
            "transacao": v["id"],
            "produto": p.get("id"),
            "produto_nome": (p.get("name") or "").strip(),
            "dia": dia,
            "status": status,
            "valor": (v.get("net_amount") or 0) / 100 if v.get("currency", "BRL") == "BRL" else 0.0,
            "origem": "",
        })
    return linhas


def resumo(linhas, inicio, fim):
    r = gerar_hotmart.resumo(linhas, inicio, fim)
    r["origens"] = []  # a listagem da Kiwify nao traz UTM; sem quadro de origem por enquanto
    return r
