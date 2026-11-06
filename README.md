# Painel de Pautas — Economia & Startups

Um painel que se atualiza sozinho a cada 4 horas. Ele mostra as notícias das últimas 50h
como um funil: **notícia → contexto para explicar → repercussão nas redes → pauta de vídeo**.

Tudo roda de graça no GitHub. O único custo é a API do Claude, que escreve os resumos e os
ângulos de vídeo.

---

## Como colocar no ar (uma vez só, ~20 minutos)

Você não precisa instalar nada no computador. Tudo é feito pelo navegador.

### 1. Crie uma conta no GitHub
Entre em **github.com** e clique em *Sign up*. O plano gratuito basta.

### 2. Crie o repositório
1. No canto superior direito, clique em **+** → **New repository**.
2. Em *Repository name*, escreva `painel-pautas`.
3. Marque **Public**. O GitHub Pages gratuito só publica repositórios públicos.
   Suas chaves continuam protegidas, porque ficam guardadas à parte, em *Secrets* (passo 5).
4. Clique em **Create repository**.

### 3. Envie os arquivos
1. Descompacte o `painel-pautas.zip` no seu computador.
2. Na página do repositório, clique em **uploading an existing file**
   (ou em **Add file → Upload files**).
3. Abra a pasta descompactada, selecione **tudo o que está dentro dela** e arraste para a página.
4. Clique em **Commit changes**.

> ⚠️ **A pasta `.github` é invisível em muitos computadores**, e é ela que contém o agendamento.
> No Mac, aperte `Cmd + Shift + .` no Finder para ver arquivos ocultos. No Windows, ative em
> Explorador → Exibir → Itens ocultos.
> Depois do envio, confira se o repositório mostra a pasta `.github`. Se ela não aparecer:
> clique em **Add file → Create new file**, escreva o nome `.github/workflows/atualizar.yml`,
> cole o conteúdo do arquivo `atualizar.yml` e clique em **Commit changes**.

### 4. Gere a chave da API do Claude
1. Entre em **console.anthropic.com** e crie uma conta.
2. Em **Billing**, coloque um crédito inicial. O painel usa o modelo mais barato
   (Haiku), e o uso deve ficar em poucos dólares por mês. Você acompanha o gasto ali mesmo,
   e pode definir um limite mensal para não ter surpresa.
3. Em **API Keys**, clique em **Create Key**, dê um nome (ex.: `painel`) e **copie a chave**.
   Ela só aparece uma vez.

### 5. Guarde a chave no GitHub
1. No repositório, vá em **Settings → Secrets and variables → Actions**.
2. Clique em **New repository secret**.
3. *Name*: `ANTHROPIC_API_KEY` · *Secret*: cole a chave → **Add secret**.

### 6. Ligue a publicação do site
1. Em **Settings → Pages**, no campo *Source*, escolha **GitHub Actions**.

### 7. Rode pela primeira vez
1. Vá na aba **Actions**. Se o GitHub pedir, clique em *I understand… enable workflows*.
2. Clique em **Atualizar painel** → **Run workflow** → **Run workflow**.
3. Espere de 2 a 4 minutos, até aparecer o ✅ verde.

### 8. Abra o painel
O endereço é:

```
https://SEU-USUARIO.github.io/painel-pautas/
```

Ele também aparece em **Settings → Pages**. Salve nos favoritos ou adicione à tela inicial
do celular. A partir daqui, o painel se atualiza sozinho.

---

## Opcional: vídeos do YouTube na coluna "Repercussão"

Sem essa chave, o painel já mostra links de busca prontos para TikTok, Instagram, YouTube e X.
Com ela, ele também lista vídeos recentes do YouTube sobre cada tema.

1. Entre em **console.cloud.google.com** e crie um projeto.
2. Em **APIs e serviços → Biblioteca**, ative a **YouTube Data API v3**.
3. Em **Credenciais → Criar credenciais → Chave de API**, copie a chave.
4. No GitHub, crie outro secret chamado `YOUTUBE_API_KEY` com essa chave (como no passo 5).

A cota gratuita é limitada. Por isso, o painel só busca vídeos para as 5 melhores histórias
de cada frente e reaproveita os resultados das rodadas anteriores.

---

## Como usar o painel

- **Economia / Startups**: troca de frente.
- **Publicadas há até 12h / 24h / 50h**: filtra pela notícia mais recente da história.
- **Clique numa história** para abrir o funil completo.
- **Copiar briefing**: copia resumo, ângulos, ganchos e links, pronto para colar no seu roteiro.
- **Marcar como usada**: deixa a história apagada no painel. Essa marcação fica salva só no
  navegador onde você clicou.
- **Situação das fontes** (no rodapé): mostra quais sites trouxeram notícias na última rodada.

### Sobre TikTok e Instagram
Essas redes não oferecem uma forma aberta de buscar posts por assunto. Por isso, o painel
monta **links de busca prontos** com o termo e a hashtag certos: um clique abre a busca já
feita. Se um dia quiser os posts listados dentro do painel, dá para plugar um serviço pago
de coleta (como o Apify) no `scripts/collect.py`.

---

## Personalizar

| O que | Onde |
|---|---|
| Adicionar ou remover sites | `sources.yaml` (edite pelo próprio GitHub, no ícone de lápis) |
| Mudar a janela de 50h ou o nº de histórias | topo do `sources.yaml` |
| Mudar a frequência de atualização | linha `cron` em `.github/workflows/atualizar.yml` |
| Usar um modelo mais forte nos ângulos | **Settings → Secrets and variables → Actions → Variables**: crie `CLAUDE_MODEL` = `claude-sonnet-5-5` (custa mais) |
| Ajustar o tom das sugestões | o texto `PROMPT` em `scripts/collect.py` |

## Manutenção (quase nenhuma)

- **Uma vez por mês**, abra o rodapé *Situação das fontes*. Se um site aparece sempre com
  0 matérias, provavelmente mudou o feed. Troque a linha dele por uma busca do tipo
  `google: "site:dominio.com.br"`.
- Se aparecer **"sem atualização há mais de 8h"** no topo, abra a aba **Actions** e veja a
  execução com ❌. Os motivos mais comuns são crédito da API acabou ou a chave expirou.
- O GitHub pode pausar agendamentos de repositórios parados por muito tempo. Como o robô salva
  os dados a cada rodada, isso não deve acontecer. Se acontecer, o GitHub avisa por e-mail,
  e basta clicar em *Enable workflow* na aba **Actions**.

## Testar no computador (opcional)

```bash
pip install -r requirements.txt
python scripts/teste_offline.py          # simula tudo sem internet, com dados fictícios
ANTHROPIC_API_KEY=sua-chave python scripts/collect.py   # rodada real
cd docs && python -m http.server 8000    # abra http://localhost:8000
```
