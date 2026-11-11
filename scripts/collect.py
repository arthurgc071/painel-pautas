"""
Coletor do Painel de Pautas.

O que este script faz, em ordem:
  1. Lê as fontes de sources.yaml e baixa as notícias (RSS ou Google News).
  2. Descarta o que tem mais de `janela_horas` e remove duplicadas.
  3. Agrupa matérias sobre o mesmo fato numa "história" e, com o Claude,
     escreve resumo, por que importa, ângulos de vídeo e ganchos.
  4. Busca complementos de cada história: matérias de contexto (Google News),
     vídeos do YouTube (se houver chave) e links de busca em TikTok/Instagram/X.
  5. Salva tudo em docs/data/news.json, que o painel (docs/index.html) lê.

Variáveis de ambiente:
  ANTHROPIC_API_KEY  obrigatória para resumos e ângulos (sem ela, o painel
                     funciona só com agrupamento simples)
  YOUTUBE_API_KEY    opcional, para listar vídeos do YouTube
  CLAUDE_MODEL       opcional, padrão claude-haiku-5-5
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, quote_plus

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "data"
UA = "Mozilla/5.0 (compatible; PainelDePautas/1.0)"
TIMEOUT = 20
NOW = datetime.now(timezone.utc)

# Usado só nos testes: troca um endereço por um conteúdo local
FETCH_OVERRIDE: dict[str, str] = {}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- utilidades

def google_news_url(query: str, dias: int) -> str:
    q = f"{query} when:{dias}d"
    return (
        "https://news.google.com/rss/search?q="
        + quote(q)
        + "&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    )


def clean_text(s: str | None, limit: int = 400) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def norm_tokens(title: str) -> set[str]:
    t = unicodedata.normalize("NFKD", title.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+", t)
    stop = {
        "de", "da", "do", "das", "dos", "a", "o", "as", "os", "e", "em", "no",
        "na", "nos", "nas", "para", "por", "com", "um", "uma", "que", "se",
        "ao", "the", "of", "to", "in", "and", "for", "on", "is", "after", "mais",
    }
    return {w for w in words if w not in stop and len(w) > 2}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def entry_time(e) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = e.get(key)
        if t:  # o feedparser já entrega em UTC
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def split_google_title(title: str, fallback_source: str) -> tuple[str, str]:
    """O Google News coloca ' - Veículo' no final do título."""
    m = re.match(r"^(.*)\s+-\s+([^-]{2,60})$", title)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return title, fallback_source


def fetch_feed(url: str):
    if url in FETCH_OVERRIDE:
        return feedparser.parse(FETCH_OVERRIDE[url])
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    return feedparser.parse(r.content)


# ---------------------------------------------------------------- 1. coleta

def collect_source(src: dict, frente: str, janela_h: int) -> tuple[list[dict], dict]:
    nome = src["nome"]
    is_google = "google" in src
    url = google_news_url(src["google"], max(1, janela_h // 24 + 1)) if is_google else src["rss"]
    status = {"nome": nome, "frente": frente, "ok": False, "itens": 0, "erro": None}
    items = []
    try:
        feed = fetch_feed(url)
        limite = NOW - timedelta(hours=janela_h)
        for e in feed.entries:
            pub = entry_time(e)
            if not pub or pub < limite or pub > NOW + timedelta(hours=2):
                continue
            title = clean_text(e.get("title"), 300)
            fonte = nome
            if is_google:
                src_title = (e.get("source") or {}).get("title")
                title, fonte = split_google_title(title, src_title or nome)
                if src_title:
                    fonte = src_title
            link = e.get("link", "")
            if not title or not link:
                continue
            items.append({
                "id": hashlib.md5(link.encode()).hexdigest()[:12],
                "titulo": title,
                "link": link,
                "fonte": fonte,
                "publicado": pub.isoformat(),
                "resumo": "" if is_google else clean_text(e.get("summary")),
                "frente": frente,
            })
        status["ok"] = True
        status["itens"] = len(items)
    except Exception as ex:  # noqa: BLE001
        status["erro"] = str(ex)[:200]
    return items, status


def dedupe(items: list[dict]) -> list[dict]:
    items = sorted(items, key=lambda x: x["publicado"])
    kept: list[dict] = []
    seen_links = set()
    for it in items:
        if it["link"] in seen_links:
            continue
        toks = norm_tokens(it["titulo"])
        if any(jaccard(toks, norm_tokens(k["titulo"])) > 0.75 for k in kept[-300:]):
            continue
        it["_tokens"] = toks
        kept.append(it)
        seen_links.add(it["link"])
    return kept


# ---------------------------------------------------------------- 2. agrupamento + Claude

PROMPT = """Você é editor de pautas de um criador de conteúdo brasileiro que faz vídeos curtos \
(Reels, TikTok, Shorts) explicando {frente_desc}.

Abaixo estão matérias publicadas nas últimas {janela} horas. Cada linha tem um código entre colchetes.

{lista}

Tarefa:
1. Agrupe as matérias que tratam do MESMO fato em uma "história". Matérias sem relação ficam sozinhas.
2. Escolha as até {maximo} histórias com MAIOR potencial de virar vídeo. {criterios}
3. Para cada história escolhida, escreva em português do Brasil.

Entregue o resultado chamando a ferramenta "salvar_historias". Cada história segue este modelo:
{{"historias": [
  {{
    "codigos": ["e3", "e17"],
    "titulo": "título curto e claro da história, máx. 90 caracteres",
    "resumo": "2 a 3 frases explicando o fato, para quem não leu nada",
    "por_que_importa": "1 a 2 frases sobre o impacto",
    "nota": 8,
    "angulos": ["ângulo de vídeo 1", "ângulo 2", "ângulo 3"],
    "ganchos": ["frase de abertura que prende nos 3 primeiros segundos", "outra opção"],
    "perguntas": ["dúvida que o público provavelmente tem", "outra dúvida"],
    "busca_contexto": "{dica_busca}",
    "busca_social": "termo curto de 2 a 4 palavras para buscar vídeos sobre o tema",
    "hashtags": ["tag1", "tag2", "tag3"]{campos_extras}
  }}
]}}

"nota" vai de 1 a 10 e mede o potencial de virar um bom vídeo. Hashtags sem o símbolo #, sem espaços.{regras_extras}"""

FRENTES = {
    "ECONOMIA": {
        "frente_desc": "economia, mercado financeiro, juros, inflação e finanças",
        "criterios": "Priorize relevância para o público brasileiro, novidade e capacidade de gerar "
                     "curiosidade. Ignore fofoca, esporte, política partidária sem impacto econômico e "
                     "notas sem substância.",
        "dica_busca": "consulta de 3 a 6 palavras para achar matérias de contexto/explicação",
        "campos_extras": "",
        "regras_extras": "",
    },
    "STARTUPS": {
        "frente_desc": "cases de startups e empresas de tecnologia: o que a empresa faz, como "
                       "funciona e por que o momento dela importa",
        "criterios": "Priorize histórias sobre UMA empresa específica (startup ou scale-up, de "
                     "preferência brasileira ou com atuação no Brasil) num momento marcante: rodada de "
                     "investimento, virar unicórnio, aquisição, IPO, expansão internacional, lançamento "
                     "relevante, virada no modelo de negócio, crise ou demissões. O vídeo vai explicar o "
                     "case. Ignore notícias genéricas de tecnologia, big techs sem ângulo de case, "
                     "eventos, prêmios, rankings, listas e artigos de opinião. Os ângulos devem explicar "
                     "a empresa (como funciona, como ganha dinheiro, o que a diferencia), não só o fato.",
        "dica_busca": "o nome da empresa, exatamente como aparece nas manchetes",
        "campos_extras": """,
    "empresa": "nome da empresa protagonista",
    "raio_x": {
      "o_que_faz": "em 1 a 2 frases simples: o problema que resolve e para quem",
      "como_funciona": "em 1 a 2 frases: como o produto funciona na prática",
      "como_ganha_dinheiro": "modelo de receita, em 1 frase",
      "o_momento": "o fato novo desta notícia, com os números citados nas manchetes",
      "a_checar": ["informação que precisa ser confirmada antes de gravar"]
    },
    "estrutura_video": ["Gancho: ...", "O problema: ...", "Como a empresa resolve: ...", "O que aconteceu agora: ...", "Fechamento: ..."]""",
        "regras_extras": "\n\nRegras do raio_x: use só o que está nas manchetes ou o que você sabe "
                         "com segurança sobre a empresa. NUNCA invente números, datas ou nomes. Se não "
                         "tiver certeza de algo, escreva a confirmar no campo e coloque a dúvida em "
                         "a_checar.",
    },
}


def claude_stories(items: list[dict], frente: str, janela: int, maximo: int) -> list[dict] | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log("  sem ANTHROPIC_API_KEY: usando agrupamento simples")
        return None
    import anthropic

    pool = sorted(items, key=lambda x: x["publicado"], reverse=True)[:160]
    codes = {f"e{i}": it for i, it in enumerate(pool)}
    lista = "\n".join(
        f"[{c}] {it['titulo']} ({it['fonte']})"
        + (f" — {it['resumo'][:160]}" if it["resumo"] else "")
        for c, it in codes.items()
    )
    prompt = PROMPT.format(janela=janela, lista=lista, maximo=maximo, **FRENTES[frente])
    client = anthropic.Anthropic(api_key=key)
    model = os.environ.get("CLAUDE_MODEL") or "claude-haiku-5-5"
    ferramenta = {
        "name": "salvar_historias",
        "description": "Salva as histórias escolhidas para o painel de pautas.",
        "input_schema": schema_historias(frente),
    }
    for tentativa in range(2):
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=16000,
                tools=[ferramenta],
                tool_choice={"type": "tool", "name": "salvar_historias"},
                messages=[{"role": "user", "content": prompt}],
            )
            if getattr(msg, "stop_reason", "") == "max_tokens":
                log("  aviso: resposta do Claude chegou no limite de tamanho")
            data = next(
                (b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None
            )
            if isinstance(data, str):
                data = json.loads(data)
            if not data or not data.get("historias"):
                raise ValueError("o Claude não devolveu histórias")
            stories = []
            for h in data["historias"]:
                membros = [codes[c] for c in h.get("codigos", []) if c in codes]
                if not membros:
                    continue
                stories.append(build_story(membros, frente, h))
            if not stories:
                raise ValueError("nenhuma história com códigos válidos")
            return stories
        except Exception as ex:  # noqa: BLE001
            log(f"  erro no Claude (tentativa {tentativa + 1}): {ex}")
            time.sleep(5)
    return None


def schema_historias(frente: str) -> dict:
    txt = {"type": "string"}
    lista = {"type": "array", "items": {"type": "string"}}
    props = {
        "codigos": lista, "titulo": txt, "resumo": txt, "por_que_importa": txt,
        "nota": {"type": "integer", "minimum": 1, "maximum": 10},
        "angulos": lista, "ganchos": lista, "perguntas": lista,
        "busca_contexto": txt, "busca_social": txt, "hashtags": lista,
    }
    obrig = ["codigos", "titulo", "resumo", "nota", "angulos", "ganchos",
             "busca_contexto", "busca_social", "hashtags"]
    if frente == "STARTUPS":
        props["empresa"] = txt
        props["raio_x"] = {
            "type": "object",
            "properties": {
                "o_que_faz": txt, "como_funciona": txt, "como_ganha_dinheiro": txt,
                "o_momento": txt, "a_checar": lista,
            },
        }
        props["estrutura_video"] = lista
        obrig += ["empresa", "raio_x", "estrutura_video"]
    return {
        "type": "object",
        "properties": {
            "historias": {
                "type": "array",
                "items": {"type": "object", "properties": props, "required": obrig},
            }
        },
        "required": ["historias"],
    }


def heuristic_stories(items: list[dict], frente: str, maximo: int) -> list[dict]:
    groups: list[list[dict]] = []
    for it in sorted(items, key=lambda x: x["publicado"], reverse=True):
        for g in groups:
            if jaccard(it["_tokens"], g[0]["_tokens"]) > 0.3:
                g.append(it)
                break
        else:
            groups.append([it])
    groups.sort(key=lambda g: (len({m["fonte"] for m in g}), g[0]["publicado"]), reverse=True)
    out = []
    for g in groups[:maximo]:
        t = g[0]["titulo"]
        out.append(build_story(g, frente, {
            "titulo": t,
            "resumo": g[0]["resumo"],
            "nota": min(10, 3 + len({m["fonte"] for m in g})),
            "busca_contexto": " ".join(list(g[0]["_tokens"])[:5]),
            "busca_social": " ".join(list(g[0]["_tokens"])[:3]),
        }))
    return out


def build_story(membros: list[dict], frente: str, h: dict) -> dict:
    membros = sorted(membros, key=lambda m: m["publicado"])
    sid = hashlib.md5("|".join(sorted(m["id"] for m in membros)).encode()).hexdigest()[:10]
    return {
        "id": sid,
        "frente": frente,
        "titulo": h.get("titulo") or membros[0]["titulo"],
        "resumo": h.get("resumo", ""),
        "por_que_importa": h.get("por_que_importa", ""),
        "nota": int(h.get("nota") or 5),
        "angulos": h.get("angulos", []),
        "ganchos": h.get("ganchos", []),
        "perguntas": h.get("perguntas", []),
        "hashtags": [re.sub(r"[#\s]", "", t) for t in h.get("hashtags", [])][:6],
        "busca_contexto": h.get("busca_contexto", ""),
        "busca_social": h.get("busca_social", ""),
        "empresa": h.get("empresa", ""),
        "raio_x": h.get("raio_x") or {},
        "estrutura_video": h.get("estrutura_video", []),
        "primeira_publicacao": membros[0]["publicado"],
        "ultima_publicacao": membros[-1]["publicado"],
        "materias": [
            {k: m[k] for k in ("titulo", "link", "fonte", "publicado")} for m in membros
        ],
        "contexto": [],
        "videos": [],
        "buscas_sociais": [],
    }


# ---------------------------------------------------------------- 3. complementos

def add_context(story: dict) -> None:
    if story.get("empresa"):
        # Para cases: matérias sobre a empresa no último ano (histórico, rodadas anteriores, perfil)
        q, dias = '"' + story["empresa"] + '"', 365
    else:
        q, dias = story["busca_contexto"] or story["titulo"], 30
    proprios = {m["link"] for m in story["materias"]}
    proprios_tok = [norm_tokens(m["titulo"]) for m in story["materias"]]
    try:
        feed = fetch_feed(google_news_url(q, dias))
        out = []
        for e in feed.entries:
            title, fonte = split_google_title(clean_text(e.get("title"), 300), "")
            src_title = (e.get("source") or {}).get("title")
            link = e.get("link", "")
            if link in proprios:
                continue
            tok = norm_tokens(title)
            if any(jaccard(tok, p) > 0.6 for p in proprios_tok):
                continue  # é a mesma matéria em outro veículo
            pub = entry_time(e)
            out.append({
                "titulo": title,
                "link": link,
                "fonte": src_title or fonte,
                "publicado": pub.isoformat() if pub else None,
            })
            if len(out) >= (6 if story.get("empresa") else 5):
                break
        story["contexto"] = out
    except Exception as ex:  # noqa: BLE001
        log(f"  contexto falhou para '{q}': {ex}")


def add_youtube(story: dict) -> None:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        return
    q = story["busca_social"] or story["titulo"]
    try:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "key": key, "part": "snippet", "q": q, "type": "video",
                "maxResults": 4, "order": "relevance", "regionCode": "BR",
                "relevanceLanguage": "pt",
                "publishedAfter": (NOW - timedelta(days=21)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        for it in r.json().get("items", []):
            sn = it["snippet"]
            story["videos"].append({
                "titulo": html.unescape(sn["title"]),
                "canal": sn["channelTitle"],
                "link": f"https://www.youtube.com/watch?v={it['id']['videoId']}",
                "thumb": sn["thumbnails"].get("medium", {}).get("url"),
                "publicado": sn["publishedAt"],
            })
    except Exception as ex:  # noqa: BLE001
        log(f"  YouTube falhou para '{q}': {ex}")


def add_social_links(story: dict) -> None:
    termo = story["busca_social"] or story["titulo"][:60]
    tags = story["hashtags"]
    story["buscas_sociais"] = [
        {"rede": "TikTok", "termo": termo,
         "link": "https://www.tiktok.com/search?q=" + quote_plus(termo)},
        {"rede": "Instagram", "termo": "#" + (tags[0] if tags else termo.replace(" ", "")),
         "link": "https://www.instagram.com/explore/tags/"
                 + quote(tags[0] if tags else termo.replace(" ", "").lower()) + "/"},
        {"rede": "YouTube", "termo": termo,
         "link": "https://www.youtube.com/results?search_query=" + quote_plus(termo)},
        {"rede": "X", "termo": termo,
         "link": "https://x.com/search?f=live&q=" + quote_plus(termo)},
    ]


# ---------------------------------------------------------------- principal

def main() -> int:
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))
    janela = int(cfg.get("janela_horas", 50))
    maximo = int(cfg.get("max_historias_por_frente", 15))
    frentes = [f for f in ("ECONOMIA", "STARTUPS") if f in cfg]

    # Reaproveita vídeos já buscados em rodadas anteriores (economiza cota do YouTube)
    anteriores = {}
    try:
        prev = json.loads((OUT_DIR / "news.json").read_text(encoding="utf-8"))
        if not prev.get("exemplo"):
            for ss in prev.get("frentes", {}).values():
                for s in ss:
                    anteriores[s["id"]] = s
    except Exception:  # noqa: BLE001
        pass

    resultado = {
        "gerado_em": NOW.isoformat(),
        "janela_horas": janela,
        "modo": "claude" if os.environ.get("ANTHROPIC_API_KEY") else "simples",
        "frentes": {},
        "fontes": [],
    }

    for frente in frentes:
        log(f"== {frente}")
        with ThreadPoolExecutor(max_workers=8) as ex:
            res = list(ex.map(lambda s: collect_source(s, frente, janela), cfg[frente]))
        items = [i for its, _ in res for i in its]
        resultado["fontes"] += [st for _, st in res]
        items = dedupe(items)
        log(f"  {len(items)} matérias únicas na janela de {janela}h")
        if not items:
            resultado["frentes"][frente] = []
            continue
        stories = claude_stories(items, frente, janela, maximo)
        if stories is None:
            stories = heuristic_stories(items, frente, maximo)
        stories.sort(key=lambda s: (s["nota"], s["ultima_publicacao"]), reverse=True)
        for s in stories:
            if s["id"] in anteriores:
                s["videos"] = anteriores[s["id"]].get("videos", [])
        # YouTube só para as 5 melhores histórias sem vídeos ainda (a cota grátis é limitada)
        sem_video = [s for s in stories if not s["videos"]][:5]
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(add_context, stories))
            list(ex.map(add_youtube, sem_video))
        for s in stories:
            add_social_links(s)
        resultado["frentes"][frente] = stories
        log(f"  {len(stories)} histórias")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = sum(len(v) for v in resultado["frentes"].values())
    if total == 0 and (OUT_DIR / "news.json").exists():
        log("Nenhuma história nova; mantendo o painel anterior.")
        prev = json.loads((OUT_DIR / "news.json").read_text(encoding="utf-8"))
        prev["fontes"] = resultado["fontes"]
        prev["ultima_tentativa"] = NOW.isoformat()
        resultado = prev
    (OUT_DIR / "news.json").write_text(
        json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    falhas = [f["nome"] for f in resultado["fontes"] if not f["ok"] or f["itens"] == 0]
    if falhas:
        log("Fontes sem notícias nesta rodada: " + ", ".join(falhas))
    log(f"Pronto: {total} histórias em docs/data/news.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
