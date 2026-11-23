"""Faturamento real do Asaas pra comparar com o que a Meta e o Google contam. Usado pelo gerar_vendas.py.

config.json do dashboard (vendas/<slug>/config.json):
    "faturamento_real": {"fonte": "Asaas"}

Chave de API (Asaas > Integracoes > Chave de API), uma por cliente:
    ASAAS_<SLUG>_API_KEY   (ex: ASAAS_BJJ_PROTECH_API_KEY)
No GitHub fica nos secrets do repositorio; local, no .env desta pasta (fora do Git).

O que conta como faturamento: cobranca paga (RECEIVED, CONFIRMED, RECEIVED_IN_CASH), no dia em que o cliente pagou
(clientPaymentDate; no cartao e o dia da aprovacao, nao o dia em que o dinheiro cai). Cobranca estornada ou em
chargeback fica fora e aparece separada. Separa em assinatura, parcelada e avulsa, como o Rodrigo compila.
No cartao parcelado o Asaas cria uma cobranca por parcela, todas aprovadas no dia da venda: a venda inteira conta
naquele dia, igual ao pixel. No boleto parcelado, cada parcela conta no dia em que foi paga.

So numeros agregados saem daqui: nome, e-mail e documento do pagador nunca entram no payload.
"""

import datetime as dt
import os
import sys
import time
from pathlib import Path

import requests

AQUI = Path(__file__).resolve().parent
API = "https://api.asaas.com/v3"
PAGAS = ("RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH")
DEVOLVIDAS = ("REFUNDED", "REFUND_REQUESTED", "REFUND_IN_PROGRESS", "CHARGEBACK_REQUESTED",
              "CHARGEBACK_DISPUTE", "AWAITING_CHARGEBACK_REVERSAL")
TIPOS = ("assinatura", "parcelada", "avulsa")
# Assinatura gerada ate 45 dias antes do vencimento e paga dentro do periodo ainda entra.
FOLGA_CRIACAO = 45


def chave(slug):
    nome = f"ASAAS_{slug.upper().replace('-', '_')}_API_KEY"
    if os.environ.get(nome):
        return os.environ[nome].strip()
    if (AQUI / ".env").exists():
        for linha in (AQUI / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, v = linha.split("=", 1)
                if k.strip() == nome:
                    return v.strip().strip('"').strip("'")
    return None


class Asaas:
    def __init__(self, slug):
        self.h = {"access_token": chave(slug), "User-Agent": "c2pro-dashboards", "Content-Type": "application/json"}

    def get(self, caminho, **params):
        """GET com paginacao por offset e retry em limite de chamadas."""
        itens, params = [], {**params, "limit": 100, "offset": 0}
        while True:
            for tentativa in range(5):
                r = requests.get(f"{API}{caminho}", headers=self.h, params=params, timeout=60)
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(10 * (tentativa + 1))
                    continue
                break
            if r.status_code == 401:
                sys.exit("ERRO Asaas (401): chave de API invalida ou revogada")
            if r.status_code != 200:
                sys.exit(f"ERRO Asaas em {caminho} ({r.status_code}): {r.text[:200]}")
            corpo = r.json()
            itens += corpo.get("data", [])
            if not corpo.get("hasMore"):
                return itens
            params["offset"] += params["limit"]


def tipo(c):
    if c.get("subscription"):
        return "assinatura"
    if c.get("installment"):
        return "parcelada"
    return "avulsa"


def cobrancas(asaas, inicio, fim):
    """Cobrancas pagas ou devolvidas com dia de pagamento entre inicio e fim, so com o que o dashboard usa.
    Duas buscas, porque nenhum filtro do Asaas pega o dia do pagamento no cartao: pela data de criacao (com folga,
    pega cartao e assinatura) e pela data de recebimento (pega parcela de boleto criada ha meses)."""
    brutas = {}
    for c in asaas.get("/payments", **{"dateCreated[ge]": (inicio - dt.timedelta(days=FOLGA_CRIACAO)).isoformat(),
                                        "dateCreated[le]": fim.isoformat()}):
        brutas[c["id"]] = c
    for c in asaas.get("/payments", **{"paymentDate[ge]": inicio.isoformat(), "paymentDate[le]": fim.isoformat()}):
        brutas[c["id"]] = c
    linhas = []
    for c in brutas.values():
        if c.get("deleted") or c.get("status") not in PAGAS + DEVOLVIDAS:
            continue
        quando = c.get("clientPaymentDate") or c.get("confirmedDate") or c.get("paymentDate")
        if not quando:
            continue
        dia = dt.date.fromisoformat(quando[:10])
        if not inicio <= dia <= fim:
            continue
        linhas.append({
            "venda": c.get("installment") or c["id"],  # parcelas da mesma venda contam como uma venda
            "dia": dia,
            "paga": c["status"] in PAGAS,
            "tipo": tipo(c),
            "forma": c.get("billingType") or "",
            "valor": float(c.get("value") or 0),
            "liquido": float(c.get("netValue") or 0),
        })
    return linhas


def resumo(linhas, inicio, fim):
    doper = [l for l in linhas if inicio <= l["dia"] <= fim]
    pagas = [l for l in doper if l["paga"]]
    devolvidas = [l for l in doper if not l["paga"]]
    por_tipo = {t: {"valor": 0.0, "vendas": set()} for t in TIPOS}
    for l in pagas:
        por_tipo[l["tipo"]]["valor"] += l["valor"]
        por_tipo[l["tipo"]]["vendas"].add(l["venda"])
    return {
        "valor": round(sum(l["valor"] for l in pagas), 2),
        "liquido": round(sum(l["liquido"] for l in pagas), 2),
        "vendas": len({l["venda"] for l in pagas}),
        "por_tipo": {t: {"valor": round(v["valor"], 2), "vendas": len(v["vendas"])} for t, v in por_tipo.items()},
        "devolvidas": len(devolvidas),
        "valor_devolvido": round(sum(l["valor"] for l in devolvidas), 2),
    }
