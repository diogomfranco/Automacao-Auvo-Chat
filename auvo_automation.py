import pandas as pd
import difflib
import unicodedata
import re
import requests
from playwright.sync_api import sync_playwright
from datetime import datetime,timedelta
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import pyperclip
import os
import time
import ctypes
import os
from dotenv import load_dotenv

DATA_REFERENCIA_SPEAKR = None


def _data_referencia_speakr():
    from datetime import date

    if DATA_REFERENCIA_SPEAKR:
        try:
            return datetime.strptime(str(DATA_REFERENCIA_SPEAKR), "%Y-%m-%d").date()
        except ValueError:
            pass

    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    except Exception:
        return date.today()


load_dotenv()

USUARIO_AUVO = os.getenv("AUVO_USUARIO")
SENHA_AUVO   = os.getenv("AUVO_SENHA")

user32 = ctypes.windll.user32
largura = user32.GetSystemMetrics(78)
altura = user32.GetSystemMetrics(79)

# --- USUÁRIOS ---
USUARIOS = {
    "1": {"nome": "Usuario1", "id": 000000},
    "2": {"nome": "Usuario2", "id": 000000},
    "3": {"nome": "Usuario3", "id": 000000},
    "4": {"nome": "Usuario4", "id": 000000},
    "5": {"nome": "Usuario5", "id": 000000},
    "6": {"nome": "Usuario6", "id": 000000},
}

# --- SELEÇÃO DO USUÁRIO NO INÍCIO ---
def selecionar_usuario():
    print("\n╔══════════════════════════════╗")
    print("║     Sistema Auvo - Suporte   ║")
    print("╠══════════════════════════════╣")
    for k, v in USUARIOS.items():
        print(f"║  {k}. {v['nome']:<25}║")
    print("╚══════════════════════════════╝")
    while True:
        escolha = input("\nDigite o número do seu usuário: ").strip()
        if escolha in USUARIOS:
            usuario = USUARIOS[escolha]
            print(f"\n✅ Olá, {usuario['nome']}! Sistema iniciando...\n")
            return usuario
        print("❌ Opção inválida. Tente novamente.")

USUARIO_ATUAL = selecionar_usuario()
ID_USER       = USUARIO_ATUAL["id"]
NOME_USER     = USUARIO_ATUAL["nome"]


# ---------------------------------------------------------------------------
# Timeout para chamada do Gemini (evita travar indefinidamente)
# ---------------------------------------------------------------------------
GEMINI_TIMEOUT = 120  # segundos máximos aguardando resposta do Gemini
AUVO_TOKEN_CACHE = {"token": None, "expires_at": 0}
AUVO_TOKEN_LOCK = threading.Lock()
AUVO_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
GEMINI_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemini_state.json")
HTTP_SESSION = requests.Session()
AUVO_TASKS_CACHE = {}
AUVO_TASKS_CACHE_LOCK = threading.Lock()


def _todas_paginas_cdp(browser):
    """Retorna todas as paginas abertas em todos os contextos do Chrome CDP."""
    paginas = []
    for context in browser.contexts:
        paginas.extend(context.pages)
    return paginas


def _pagina_parece_speakr(page):
    try:
        url = (page.url or "").lower()
        if "speakr" in url:
            return True
        return bool(page.evaluate("""
            () => {
                const temProtocolo = Array.from(document.querySelectorAll('span'))
                    .some(el => (el.textContent || '').trim().includes('Protocolo'));
                const temCopiar = Array.from(document.querySelectorAll('button'))
                    .some(btn => {
                        const texto = [btn.title, btn.getAttribute('aria-label'), btn.textContent]
                            .filter(Boolean).join(' ').toLowerCase();
                        return texto.includes('copiar') || texto.includes('copy');
                    });
                return temProtocolo || temCopiar;
            }
        """))
    except Exception:
        return False


def _selecionar_pagina_speakr(browser):
    paginas = _todas_paginas_cdp(browser)
    if not paginas:
        raise Exception("Nenhuma aba encontrada no Chrome 9222.")

    for page in paginas:
        try:
            if "speakr" in (page.url or "").lower():
                return page
        except Exception:
            pass

    for page in paginas:
        if _pagina_parece_speakr(page):
            return page

    urls = []
    for page in paginas:
        try:
            urls.append(page.url)
        except Exception:
            urls.append("<url indisponivel>")
    raise Exception("Aba do Speakr nao encontrada no Chrome 9222. Abas abertas: " + " | ".join(urls[:6]))
def chamar_gemini(prompt: str, tentativas=3) -> str:
    for i in range(tentativas):
        browser = None
        try:
            print(f"🤖 Tentativa {i+1}/{tentativas}: Acessando Gemini...")

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    channel="msedge",
                    headless=False,
                    args=[
                        "--disable-gpu",
                        "--no-sandbox",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-first-run",
                        "--no-default-browser-check",
                        f"--window-position={largura},{altura}",
                    ]
                )

                context = browser.new_context(
                    storage_state=GEMINI_STATE_PATH if os.path.exists(GEMINI_STATE_PATH) else None,
                    viewport={"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    java_script_enabled=True,
                )

                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
                    Object.defineProperty(navigator, 'languages', { get: () => ['pt-BR', 'pt', 'en-US'] });
                    window.chrome = { runtime: {} };
                """)

                page = context.new_page()
                page.evaluate("window.resizeTo(800, 600)")

                page.route("**/*", lambda route: route.abort()
                    if route.request.resource_type in ["image", "stylesheet", "font", "media", "other"]
                    else route.continue_())

                page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=60000)

                if "accounts.google" in page.url or "signin" in page.url:
                    print("⚠️ Sessão não encontrada. Faça login manualmente...")
                    browser.close()
                    browser = p.chromium.launch(channel="msedge", headless=False)
                    context = browser.new_context(viewport={"width": 1920, "height": 1080})
                    page = context.new_page()
                    page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=60000)
                    print("Após logar, pressione ENTER...")
                    input()
                    context.storage_state(path=GEMINI_STATE_PATH)
                    print("✅ Sessão salva!")

                input_box = page.locator('div[contenteditable="true"]').first
                input_box.wait_for(state="visible", timeout=30000)
                input_box.click()
                input_box.fill(prompt)
                input_box.press("Enter")

                print("⏳ Aguardando resposta do Gemini...")

                # ── Timeout global para não travar o servidor ──────────────
                try:
                    page.wait_for_selector('[data-test-id*="copy"]', timeout=GEMINI_TIMEOUT * 1000)
                except Exception:
                    print(f"⚠️ Gemini não respondeu em {GEMINI_TIMEOUT}s. Usando fallback.")
                    return ""

                espera_descricao = "###DESCRIÇÃO###" in prompt or "###DESCRICAO###" in prompt

                def resposta_tem_conteudo(texto_resp: str) -> bool:
                    texto_limpo = re.sub(r'#+\s*DESCRI[ÇC][ÃA]O\s*#+', '###DESCRICAO###', texto_resp or '', flags=re.IGNORECASE).strip()
                    if espera_descricao:
                        candidato = texto_limpo.split("###DESCRICAO###")[-1].strip() if "###DESCRICAO###" in texto_limpo else texto_limpo
                        if re.match(
                            r'^(Histórico|Regras|Você é|Máximo|Ignore|Descreva|Sem sugestões|Comece com|NÃO mencione|Precisa ser|Deixe resumido|###)',
                            candidato,
                            flags=re.IGNORECASE,
                        ):
                            return False
                        if re.search(r'\b(Cliente|problema|sistema|cálculo|calculo|banco de horas|atendimento|suporte|erro|dúvida|duvida)\b', candidato, flags=re.IGNORECASE):
                            return len(candidato) >= 25
                        return len(candidato) >= 60
                    return len(texto_limpo.strip()) > 10

                texto_anterior = ""
                estavel = 0
                for _ in range(max(40, int(GEMINI_TIMEOUT / 0.4))):
                    time.sleep(0.4)
                    try:
                        texto_atual = page.locator('model-response').last.inner_text()
                        tem_conteudo = resposta_tem_conteudo(texto_atual)
                        if texto_atual == texto_anterior and tem_conteudo:
                            estavel += 1
                            if estavel >= 3:
                                print(f"✅ Resposta estabilizou! ({len(texto_atual)} chars)")
                                break
                        else:
                            estavel = 0
                            status = "conteúdo pronto" if tem_conteudo else "aguardando descrição"
                            print(f"⏳ Gerando... ({len(texto_atual)} chars, {status})")
                        texto_anterior = texto_atual
                    except Exception:
                        pass
                else:
                    print(f"⚠️ Gemini não finalizou conteúdo útil em {GEMINI_TIMEOUT}s. Usando fallback.")
                    return ""
                ultimo_botao = page.locator('[data-test-id*="copy"]').last
                ultimo_botao.scroll_into_view_if_needed()
                ultimo_botao.wait_for(state="visible", timeout=5000)
                ultimo_botao.click(force=True)
                resposta = ""
                for _ in range(10):
                    time.sleep(0.1)
                    resposta = pyperclip.paste()
                    if resposta and len(resposta.strip()) >= 10:
                        break

                if not resposta or len(resposta.strip()) < 10:
                    print(f"⚠️ Tentativa {i+1}: Resposta vazia.")
                    continue

                context.storage_state(path=GEMINI_STATE_PATH)
                print(f"✅ Gemini OK ({len(resposta.strip())} chars)")
                return resposta.strip()

        except Exception as e:
            print(f"⚠️ Tentativa {i+1} falhou: {e}")
            try:
                page.screenshot(path=f"erro_gemini_tentativa_{i+1}.png")
            except Exception:
                pass
            time.sleep(0.8)

        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass

    return ""  # Retorna vazio para que o caller use o fallback adequado


# ---------------------------------------------------------------------------
# Limpeza de descrição
# ---------------------------------------------------------------------------
def extrair_nome_cliente(mensagens: list[str], nome_tecnico: str = None) -> str | None:
    padrao = re.compile(r'(?:meu nome é|me chamo|sou (?:a |o )?)\s*([A-ZÀ-Ú][a-zà-ú]+)', re.IGNORECASE)
    for msg in mensagens:
        if nome_tecnico and nome_tecnico.lower() in msg.lower():
            continue
        m = padrao.search(msg)
        if m:
            nome = m.group(1).strip()
            if nome_tecnico and nome.lower() == nome_tecnico.lower():
                continue
            return nome
    return None


def gerar_descricao_fallback_mensagens(mensagens: list[str] = None) -> str:
    if not mensagens:
        return DESCRICAO_FALLBACK if "DESCRICAO_FALLBACK" in globals() else "Cliente entrou em contato via chat."

    ignorar = re.compile(
        r"atendimento transferido|atendimento atribu[ií]do|alta demanda|anydesk|suporte em sistemas|"
        r"a[cç][aã]o autom[aá]tica|hor[aá]rio de atendimento|bom dia|boa tarde|boa noite|"
        r"me chamo|meu nome|iniciarei|protocolo|^hoje\b|^ontem\b",
        re.IGNORECASE,
    )
    marcador = re.compile(r"^\s*(?:hoje|ontem|\d{1,2}[/\-]\d{1,2})?\s*-?\s*\d{1,2}[h:]\d{2}\s*-\s*.*?disse:\s*", re.IGNORECASE)

    for msg in reversed(mensagens):
        texto = marcador.sub("", str(msg)).strip()
        texto = re.sub(r"\s+", " ", texto)
        if "➥" in texto or re.search(r"_\s*[^_]+\s*:_", texto) or ("NOME_USER" in globals() and NOME_USER.lower() in texto.lower()):
            continue
        if len(texto) < 18 or ignorar.search(texto):
            continue
        texto = texto.strip(" -–.,")
        if not texto:
            continue
        if len(texto) > 220:
            texto = texto[:217].rstrip() + "..."
        return f"Cliente relata: {texto}." if not texto.endswith(".") else f"Cliente relata: {texto}"

    return DESCRICAO_FALLBACK if "DESCRICAO_FALLBACK" in globals() else "Cliente entrou em contato via chat."
def pegar_descricao(texto, mensagens: list[str] = None):
    if not texto:
        return "Cliente entrou em contato via chat."

    texto = re.sub(r'Format:\s*Plain\s*text\.?', '', texto, flags=re.IGNORECASE)
    texto = re.sub(r'#+\s*DESCRI[ÇC][ÃA]O\s*#+', '###DESCRICAO###', texto, flags=re.IGNORECASE)

    if "###DESCRICAO###" in texto:
        partes_marcador = [p.strip() for p in texto.split("###DESCRICAO###") if p.strip()]
        texto_pos_marcador = partes_marcador[-1] if partes_marcador else ""
        if texto_pos_marcador and not re.search(
            r'^(Histórico|Regras|Você é|Máximo|Ignore|Descreva|Sem sugestões|Comece com|NÃO mencione|Precisa ser|Deixe resumido)',
            texto_pos_marcador,
            flags=re.IGNORECASE
        ):
            texto = texto_pos_marcador
        else:
            print("⚠️ Gemini retornou apenas o marcador da descrição. Usando fallback do histórico.")
            return gerar_descricao_fallback_mensagens(mensagens)

    if re.search(r'^\s*(Você é|Regras:|Histórico:)', texto, flags=re.IGNORECASE | re.MULTILINE):
        print("⚠️ Gemini retornou parte do prompt em vez da descrição. Usando fallback do histórico.")
        return gerar_descricao_fallback_mensagens(mensagens)

    if mensagens:
        nome = extrair_nome_cliente(mensagens)
        if nome:
            idx = texto.lower().find(nome.lower())
            if idx > 0:
                texto = texto[idx:]
            else:
                match_inicio = re.search(r'[A-ZÀ-Ú][a-zà-ú]', texto)
                if match_inicio and match_inicio.start() > 0:
                    texto = texto[match_inicio.start():]
        else:
            match_inicio = re.search(r'[A-ZÀ-Ú][a-zà-ú]', texto)
            if match_inicio and match_inicio.start() > 0:
                texto = texto[match_inicio.start():]

    match = re.search(r'Draft\s*1:\s*(.*?)\s*\(', texto, re.DOTALL | re.IGNORECASE)
    if match:
        descricao = match.group(1).strip()
    else:
        linhas = [l.strip() for l in texto.split("\n") if l.strip()]
        descricao = ' '.join(linhas) if linhas else texto

    descricao = descricao.strip()
    descricao = re.sub(r'^[\*\-\•\s]+', '', descricao)
    descricao = re.sub(r'#+\s*DESCRI[ÇC][ÃA]O\s*#+', '', descricao, flags=re.IGNORECASE).strip()
    descricao = descricao.replace('\n', ' ').strip()

    if not descricao or descricao in {".", ":"}:
        print("⚠️ Descrição inválida após limpeza. Usando fallback do histórico.")
        return gerar_descricao_fallback_mensagens(mensagens)

    partes = [p.strip() for p in descricao.split('. ') if p.strip()]
    vistas = []
    for parte in partes:
        if parte not in vistas:
            vistas.append(parte)
    descricao = '. '.join(vistas)
    if descricao and not descricao.endswith('.'):
        descricao += '.'

    meio = len(descricao) // 2
    if descricao[:meio].strip() == descricao[meio:].strip():
        descricao = descricao[:meio].strip()
    else:
        metade = len(descricao) // 2
        primeira = descricao[:metade].strip()
        segunda  = descricao[metade:].strip()
        similaridade = difflib.SequenceMatcher(None, primeira, segunda).ratio()
        if similaridade > 0.7:
            descricao = primeira

    return descricao[:500]


# ---------------------------------------------------------------------------
# Limpeza de relato
# ---------------------------------------------------------------------------
def limpar_relato(texto: str) -> str:
    if not texto:
        return "Foi realizado atendimento via chat."

    texto = re.sub(r'\*+', '', texto)
    texto = re.sub(r'Draft\s*\d+[\.\:]?', '', texto, flags=re.IGNORECASE)
    texto = re.sub(r'Relato\s*\d*[\:\.]?', '', texto, flags=re.IGNORECASE)
    texto = re.sub(r'\([\d\s\w]+caracteres?\)', '', texto, flags=re.IGNORECASE)
    texto = re.sub(r'Format:\s*Plain\s*text\.?', '', texto, flags=re.IGNORECASE)
    texto = re.sub(r'^[\-\•\s]+', '', texto, flags=re.MULTILINE)

    texto = ' '.join(linha.strip() for linha in texto.splitlines() if linha.strip())
    texto = re.sub(r'\s{2,}', ' ', texto).strip()

    if texto and not texto.endswith('.'):
        texto += '.'

    if texto and not texto.lower().startswith('foi'):
        texto = 'Foi ' + texto[0].lower() + texto[1:]

    return texto[:800]


# ---------------------------------------------------------------------------
# Pré-processamento do histórico de mensagens
# ---------------------------------------------------------------------------
def preprocessar_mensagens(mensagens: list[str]) -> str:
    linhas = []
    for msg in mensagens:
        if "➥" in msg or NOME_USER in msg or "iniciarei seu atendimento" in msg:
            linhas.append(f"[TÉCNICO] {msg.strip()}")
        elif any(p in msg for p in [
            "Atendimento transferido", "Atendimento atribuído",
            "alta demanda", "Seja bem vindo", "digit", "sair",
            "HORÁRIO DE ATENDIMENTO", "Suporte em Sistemas", "automação"
        ]):
            continue
        else:
            linhas.append(f"[CLIENTE] {msg.strip()}")
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Geração de descrição (curta)
# ---------------------------------------------------------------------------
DESCRICAO_FALLBACK = "Cliente entrou em contato via chat para atendimento."
RELATO_FALLBACK    = "Foi realizado atendimento via chat."

def gerar_descricao_com_ia(mensagens: list[str]) -> str:
    if not mensagens:
        return DESCRICAO_FALLBACK

    linhas = preprocessar_mensagens(mensagens)

    prompt = f"""Você é um técnico de suporte de TI resumindo um atendimento para registro interno.

Regras:
- Máximo 350 caracteres
- Ignore COMPLETAMENTE todas as mensagens marcadas como [TÉCNICO]
- Descreva APENAS o problema(s) do cliente, não inclua o que precisa fazer.
- Sem sugestões, causas, rascunhos, análise ou asteriscos
- Comece com o nome do cliente (primeira palavra da primeira mensagem [CLIENTE] que contenha um nome)
- NÃO mencione o técnico, AnyDesk, acesso remoto ou ações do suporte
- Precisa ser em português.
- Não precisa ser tão formal, faça como se tivesse descrevendo pra uma pessoa.
- Deixe resumido, no máximo duas linhas.

Histórico:
\"\"\"
{linhas}
\"\"\"

###DESCRIÇÃO###"""

    resultado = chamar_gemini(prompt)
    if not resultado:
        print("⚠️ Gemini sem resposta para descrição. Usando fallback.")
        return DESCRICAO_FALLBACK

    return pegar_descricao(resultado, mensagens)


# ---------------------------------------------------------------------------
# Geração de relato detalhado
# ---------------------------------------------------------------------------
def gerar_relato_com_ia(mensagens: list[str]) -> str:
    if not mensagens:
        return RELATO_FALLBACK

    linhas = preprocessar_mensagens(mensagens)

    prompt = f"""Você é um técnico de suporte de TI escrevendo o relato final de um atendimento para registro interno.

Escreva em português informal mas profissional — como um técnico contando o que fez pra um colega de trabalho.

Exemplos CORRETOS:
- "Foi explicado pro cliente como cadastrar funcionário no sistema RM, mostrando o passo a passo dos campos obrigatórios. Também foi acessado o computador via AnyDesk pra conferir uma configuração que tava errada. Problema resolvido."
- "Foi acessado o computador do cliente via AnyDesk. Identificado que as digitais do João e da Maria tavam corrompidas no relógio Henry. Apagado e recadastrado as digitais dos dois. Teste feito com sucesso."

Exemplos ERRADOS (nunca faça isso):
- "O cliente relatou que os funcionários não conseguiam registrar o ponto." (descreve o problema, não a ação)
- "Foi solicitado acesso ao servidor para verificar o problema." (vago, sem resultado)

Regras:
- Comece com "Foi"
- Fale só o que o técnico FEZ, não o problema do cliente
- Inclua o que foi feito, como foi feito e o resultado
- Remova parte relacionadas a acesso por Anydesk
- Máximo 600 caracteres

Histórico:
\"\"\"
{linhas}
\"\"\"

Relato:"""

    resultado = chamar_gemini(prompt)
    if not resultado:
        print("⚠️ Gemini sem resposta para relato. Usando fallback.")
        return RELATO_FALLBACK

    return limpar_relato(resultado)


# ---------------------------------------------------------------------------
# Resolução de data a partir de marcadores do chat
# ---------------------------------------------------------------------------
def resolver_data_marcador(mensagens, indice: int) -> str:
    """
    Varre as mensagens de 0 até `indice` procurando marcadores de data do Speakr.

    Formato real dos marcadores do Speakr:
      "Hoje - 13h08 - Nome disse:"          → hoje
      "Ontem - 13h08 - Nome disse:"         → ontem
      "05/05/2026 - 13h08 - Nome disse:"    → data explícita
      "Segunda-feira - 13h08 - Nome disse:" → dia da semana

    IMPORTANTE: o marcador e a hora estão na MESMA linha. Não usar `continue`
    após setar a data — a hora da linha deve ser processada na mesma iteração.
    """
    import re
    from datetime import date, timedelta

    hoje = _data_referencia_speakr()

    DIAS_SEMANA = {
        "segunda": 0, "segunda-feira": 0,
        "terca": 1, "terça": 1, "terca-feira": 1, "terça-feira": 1,
        "quarta": 2, "quarta-feira": 2,
        "quinta": 3, "quinta-feira": 3,
        "sexta": 4, "sexta-feira": 4,
        "sabado": 5, "sábado": 5,
        "domingo": 6,
    }

    padrao_hora        = re.compile(r'(\d{1,2})[h:](\d{2})')
    padrao_data_expl   = re.compile(r'(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?')

    data_contexto  = hoje
    ultima_hora_min = None

    for msg in mensagens[:indice + 1]:
        texto = msg.strip().lower()
        if not texto:
            continue

        nova_data = None   # data detectada nesta linha (None = sem marcador)

        # ── Marcador "Hoje" ou "Ontem" (com ou sem texto após) ───────────────
        if re.match(r'hoje\b', texto):
            nova_data = hoje
        elif re.match(r'ontem\b', texto):
            nova_data = hoje - timedelta(days=1)
        else:
            # ── Data explícita: começa com dígitos DD/MM ou DD/MM/YYYY ───────
            m = padrao_data_expl.match(texto)
            if m:
                try:
                    dia = int(m.group(1))
                    mes = int(m.group(2))
                    ano_raw = m.group(3)
                    ano = int(ano_raw) if ano_raw else hoje.year
                    if ano < 100:
                        ano += 2000
                    # Valida que parece uma data real (dia ≤ 31, mês ≤ 12)
                    if 1 <= dia <= 31 and 1 <= mes <= 12:
                        nova_data = date(ano, mes, dia)
                except (ValueError, TypeError):
                    pass

            # ── Dia da semana ─────────────────────────────────────────────────
            if nova_data is None:
                # Remove hora e caracteres especiais para comparar
                texto_sem_hora = re.sub(r'\d{1,2}[h:]\d{2}', '', texto)
                texto_limpo    = re.sub(r'[^a-záàâãéêíóôõúç\-]', ' ', texto_sem_hora).strip()
                for nome, weekday in DIAS_SEMANA.items():
                    if texto_limpo.startswith(nome + " ") or texto_limpo == nome:
                        delta = (hoje.weekday() - weekday) % 7 or 7
                        nova_data = hoje - timedelta(days=delta)
                        break

        # Atualiza contexto se encontrou marcador nesta linha
        if nova_data is not None:
            data_contexto   = nova_data
            ultima_hora_min = None   # reseta referência ao mudar de dia

        # ── Hora da linha ─────────────────────────────────────────────────────
        hora_match = padrao_hora.search(texto)
        if hora_match:
            hora_min = int(hora_match.group(1)) * 60 + int(hora_match.group(2))

            # Virada de dia sem marcador: hora cai > 3h em relação à anterior.
            # Sem marcador explícito + queda de hora = mensagens do dia atual
            # sem o "Hoje" que o Speakr às vezes omite. Avança direto para hoje.
            if (nova_data is None
                    and ultima_hora_min is not None
                    and hora_min < ultima_hora_min - 180
                    and data_contexto < hoje):
                data_contexto = hoje

            ultima_hora_min = hora_min

    return data_contexto.strftime("%Y-%m-%d")


def tem_marcador_data_perto(mensagens, indice: int, raio: int = 0) -> bool:
    """Retorna True se houver Hoje/Ontem/DD/MM perto da mensagem informada."""
    padrao = re.compile(r'(?:^|\b)(hoje|ontem)(?:\s|\-|$)|\d{1,2}[/\-]\d{1,2}', re.IGNORECASE)
    janela = mensagens[max(0, indice - raio):min(len(mensagens), indice + raio + 1)]
    return any(padrao.search(str(msg).strip()) for msg in janela)




def _data_por_rotulo_speakr(rotulo: str) -> str:
    from datetime import date, timedelta

    hoje = _data_referencia_speakr()
    texto = (rotulo or "").strip().lower()
    dias_semana = {
        "segunda": 0, "segunda-feira": 0,
        "terca": 1, "terça": 1, "terca-feira": 1, "terça-feira": 1,
        "quarta": 2, "quarta-feira": 2,
        "quinta": 3, "quinta-feira": 3,
        "sexta": 4, "sexta-feira": 4,
        "sabado": 5, "sábado": 5,
        "domingo": 6,
    }

    if texto == "ontem":
        return (hoje - timedelta(days=1)).strftime("%Y-%m-%d")
    if texto == "hoje" or not texto:
        return hoje.strftime("%Y-%m-%d")

    data_match = re.match(r"(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?$", texto)
    if data_match:
        dia = int(data_match.group(1))
        mes = int(data_match.group(2))
        ano_raw = data_match.group(3)
        ano = int(ano_raw) if ano_raw else hoje.year
        if ano < 100:
            ano += 2000
        try:
            return date(ano, mes, dia).strftime("%Y-%m-%d")
        except ValueError:
            return hoje.strftime("%Y-%m-%d")

    weekday = dias_semana.get(texto)
    if weekday is not None:
        delta = (hoje.weekday() - weekday) % 7 or 7
        return (hoje - timedelta(days=delta)).strftime("%Y-%m-%d")

    return hoje.strftime("%Y-%m-%d")


def encontrar_atribuicoes_atendimento(mensagens):
    """Retorna eventos "Atendimento atribuido ao atendente" com data/hora do cabecalho Speakr."""
    padrao_atribuicao = re.compile(r"atendimento\s+atribu[ií]do\s+ao\s+atendente", re.IGNORECASE)
    padrao_hora = re.compile(
        r"^\s*(?:(hoje|ontem|\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?|segunda|ter[cç]a|quarta|quinta|sexta|s[aá]bado|domingo)\s*[-–]\s*)?"
        r"(\d{1,2})[h:](\d{2})\s*[-–]",
        re.IGNORECASE,
    )
    padrao_inicio_hora = re.compile(
        r"(?=(?:^|\n|\s+)(?:(?:hoje|ontem|\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?|segunda|ter[cç]a|quarta|quinta|sexta|s[aá]bado|domingo)\s*[-–]\s*)?\d{1,2}[h:]\d{2}\s*[-–])",
        re.IGNORECASE,
    )

    blocos = []
    for indice_msg, msg in enumerate(mensagens):
        texto_msg = str(msg).strip()
        if not texto_msg:
            continue
        partes = [p.strip() for p in padrao_inicio_hora.split(texto_msg) if p and p.strip()]
        if not partes:
            partes = [texto_msg]
        for parte in partes:
            for linha in str(parte).splitlines():
                linha = linha.strip()
                if linha:
                    blocos.append({"texto": linha, "indice": indice_msg})

    eventos = []
    vistos = set()
    for pos, bloco in enumerate(blocos):
        texto = bloco["texto"]
        if not padrao_atribuicao.search(texto):
            continue

        linha_hora = texto
        indice_hora = bloco["indice"]
        hora_match = padrao_hora.search(linha_hora)
        if not hora_match:
            for anterior in range(pos - 1, max(-1, pos - 8), -1):
                linha_candidata = blocos[anterior]["texto"]
                hora_candidata = padrao_hora.search(linha_candidata)
                if hora_candidata:
                    linha_hora = linha_candidata
                    indice_hora = blocos[anterior]["indice"]
                    hora_match = hora_candidata
                    break

        if not hora_match:
            continue

        rotulo = hora_match.group(1)
        if not rotulo:
            for anterior in range(pos - 1, max(-1, pos - 4), -1):
                rotulo_anterior = re.match(
                    r"^\s*(hoje|ontem|\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?|segunda|ter[cç]a|quarta|quinta|sexta|s[aá]bado|domingo)\s*[-–]\s*$",
                    blocos[anterior]["texto"],
                    re.IGNORECASE,
                )
                if rotulo_anterior and blocos[anterior]["indice"] == indice_hora:
                    rotulo = rotulo_anterior.group(1)
                    linha_hora = f"{rotulo} - {linha_hora}"
                    break
        rotulo = rotulo or "hoje"
        hora = f"{hora_match.group(2).zfill(2)}:{hora_match.group(3)}"
        data = _data_por_rotulo_speakr(rotulo)
        chave = (data, hora, texto.lower())
        if chave in vistos:
            continue
        vistos.add(chave)

        eventos.append({
            "data": data,
            "hora": hora,
            "texto": texto[:100],
            "linha": linha_hora[:80],
            "indice": indice_hora,
        })

    return eventos
# ---------------------------------------------------------------------------
# Detecção de início do atendimento
# Retorna (hora "HH:MM", data "YYYY-MM-DD", msg_gatilho)
# ---------------------------------------------------------------------------
def encontrar_inicio_atendimento(mensagens):
    atribuicoes = encontrar_atribuicoes_atendimento(mensagens)
    if atribuicoes:
        evento = atribuicoes[-1]
        print(f"✅ Início (atendimento atribuído): {evento['data']} {evento['hora']} — '{evento['linha']}'")
        return evento["hora"], evento["data"], evento["texto"]
    """
    Busca o ULTIMO atendimento iniciado por apresentacao do tecnico.

    Prioridade:
      1. Ultimo bloco com "bom dia/boa tarde/boa noite" e "meu nome e/me chamo".
      2. Se nao houver apresentacao, usa gatilhos antigos como "iniciarei atendimento".

    A busca por bloco olha linhas vizinhas porque o Speakr costuma copiar
    o horario em uma linha e o texto do tecnico em outra.
    """
    from datetime import date

    padrao_cumprimento = re.compile(r"\b(bom\s+dia|boa\s+tarde|boa\s+noite)\b", re.IGNORECASE)
    padrao_nome = re.compile(r"\b(meu\s+nome\s+(?:e|é)|me\s+chamo)\b", re.IGNORECASE)
    padrao_gatilho_fallback = re.compile(
        r"me\s+chamo\s+\S+\s+e\s+iniciarei"
        r"|iniciarei.*atendimento"
        r"|vou\s+iniciar.*atendimento"
        r"|irei\s+iniciar.*atendimento",
        re.IGNORECASE
    )
    padrao_hora = re.compile(r"(\d{1,2})[h:](\d{2})")
    sistema = re.compile(
        r"HORARIO DE ATENDIMENTO|HORÁRIO DE ATENDIMENTO|transferido|atribuido|atribuído|automacao|automação|"
        r"Seja bem vindo|digit|#sair|ANYDESK|agilizar|alta demanda|"
        r"Por favor|setor desejado|Suporte em Sistemas",
        re.IGNORECASE
    )

    def ajustar_data_para_apresentacao_recente(data_inicio, hora, indice_gatilho, indice_hora):
        """
        Ajusta a data quando o Speakr mostra mensagens do dia atual sem o rótulo "Hoje".
        Marcador explícito só vale se estiver na própria linha da hora escolhida.
        """
        hoje = _data_referencia_speakr().strftime("%Y-%m-%d")

        if data_inicio >= hoje:
            return data_inicio

        linha_hora = str(mensagens[indice_hora]).strip() if 0 <= indice_hora < len(mensagens) else ""
        tem_marcador_na_linha = re.search(
            r'^\s*(?:hoje|ontem|\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?|segunda|ter[cç]a|quarta|quinta|sexta|s[aá]bado|domingo)\b',
            linha_hora,
            re.IGNORECASE,
        )
        if tem_marcador_na_linha:
            return data_inicio

        return hoje
    def buscar_hora_do_gatilho(i_gatilho):
        """
        Encontra a hora de início do atendimento a partir do índice do gatilho.

        Estratégia em 3 camadas (ordem de prioridade):

        1. MARCADOR IMEDIATO: varre para TRÁS a partir do gatilho procurando
           uma linha do tipo "Ontem - 16h17", "Hoje - 09h00", "05/05 - 14h30".
           Essas linhas são a fonte mais confiável — o Speakr as insere
           automaticamente e contêm exatamente a hora do início do bloco.

        2. LINHA COM HORA PRÓXIMA: se não houver marcador nas 5 linhas anteriores,
           pega a linha com hora mais próxima do gatilho (janela ±4 linhas),
           priorizando linhas com marcador de data (hoje/ontem/DD/MM).

        3. FALLBACK HISTÓRICO: varre todo o histórico para trás.
        """
        padrao_marcador_speakr = re.compile(
            r'(hoje|ontem|\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?|'
            r'segunda|ter[cç]a|quarta|quinta|sexta|s[aá]bado|domingo)'
            r'\s*[-–]\s*(\d{1,2})[h:](\d{2})',
            re.IGNORECASE
        )

        # ── Camada 1: marcador do PRÓPRIO BLOCO do técnico ──────────────────
        # O Speakr insere uma linha de marcador antes de cada bloco de mensagens.
        # Formato: "Ontem - 16h17 - NomePessoa disse:" ou "Hoje - 09h00 - Nome disse:"
        #
        # Estratégia: a partir do gatilho, varra para trás pulando apenas linhas
        # de texto puro (continuação do mesmo bloco). Para no primeiro marcador
        # encontrado — esse é o marcador do bloco que contém o gatilho.
        # Rejeita marcadores que identifiquem OUTRO nome (cliente), verificando
        # se entre o marcador e o gatilho há mensagens que claramente pertencem
        # a outra pessoa (padrão "X disse:" diferente).
        #
        # Identifica o nome do técnico a partir do gatilho
        nome_tecnico_match = re.search(
            r'(?:meu nome [ée]|me chamo)\s+([A-Za-zÀ-ú]+)',
            ' '.join(mensagens[i_gatilho:min(i_gatilho + 4, len(mensagens))]),
            re.IGNORECASE
        )
        nome_tecnico = nome_tecnico_match.group(1).lower() if nome_tecnico_match else NOME_USER.lower()

        padrao_disse = re.compile(r'disse\s*:', re.IGNORECASE)

        for idx in range(i_gatilho, max(-1, i_gatilho - 8), -1):
            msg = mensagens[idx]
            m = padrao_marcador_speakr.search(msg.strip().lower())
            if not m:
                continue

            h  = m.group(2).zfill(2)
            mi = m.group(3)

            # Verifica se o marcador é do técnico ou do cliente
            # Formato Speakr: "Data - HH:MM - NomePessoa disse:"
            nome_no_marcador_match = re.search(
                r'\d{1,2}[h:]\d{2}\s*-\s*([A-Za-zÀ-ú]+)(?:\s+[A-Za-zÀ-ú]+)*\s+disse',
                msg, re.IGNORECASE
            )
            if nome_no_marcador_match:
                nome_marcador = nome_no_marcador_match.group(1).lower()
                if nome_marcador != nome_tecnico:
                    # Marcador é de outra pessoa — ignora e continua procurando
                    print(f"    → Marcador [{idx}] ignorado (é de '{nome_marcador}', não do técnico)")
                    continue

            print(f"    → Hora pelo marcador Speakr [{idx}]: {h}:{mi} — '{msg[:60]}'")
            return f"{h}:{mi}", idx

        # ── Camada 2: linha com hora próxima (±4 linhas), com marcador preferido
        def tem_marcador(idx):
            return bool(re.search(
                r'^\s*(hoje|ontem|\d{1,2}[/\-]\d{1,2})\b',
                str(mensagens[idx]).strip().lower()
            ))
        candidatos = []
        for idx in range(max(0, i_gatilho - 4), min(len(mensagens), i_gatilho + 5)):
            if sistema.search(mensagens[idx]):
                continue
            hora_match = padrao_hora.search(mensagens[idx])
            if not hora_match:
                continue
            nome_no_marcador_match = re.search(
                r'\d{1,2}[h:]\d{2}\s*-\s*([A-Za-zÀ-ú]+)(?:\s+[A-Za-zÀ-ú]+)*\s+disse',
                mensagens[idx], re.IGNORECASE
            )
            if nome_no_marcador_match:
                nome_marcador = nome_no_marcador_match.group(1).lower()
                linha_tem_apresentacao = bool(padrao_cumprimento.search(mensagens[idx]) or padrao_nome.search(mensagens[idx]))
                if nome_marcador != nome_tecnico and not linha_tem_apresentacao:
                    print(f"    → Candidato [{idx}] ignorado (é de '{nome_marcador}', não do técnico)")
                    continue
            # Ignora horas dentro do texto da apresentação do técnico
            # (ex: "14:17 Boa tarde" onde 14:17 é parte da mensagem, não marcador)
            sem_marcador = 0 if tem_marcador(idx) else 1
            distancia = abs(idx - i_gatilho)
            antes = 0 if idx <= i_gatilho else 1
            h = hora_match.group(1).zfill(2)
            mi = hora_match.group(2)
            candidatos.append((sem_marcador, distancia, antes, f"{h}:{mi}", idx))

        if candidatos:
            candidatos.sort()
            _, _, _, hora, idx = candidatos[0]
            return hora, idx

        # ── Camada 3: fallback histórico ──────────────────────────────────────
        for idx in range(i_gatilho, -1, -1):
            if sistema.search(mensagens[idx]):
                continue
            hora_match = padrao_hora.search(mensagens[idx])
            if hora_match:
                h = hora_match.group(1).zfill(2)
                mi = hora_match.group(2)
                return f"{h}:{mi}", idx
        return None, None

    def buscar_inicio_por_apresentacao():
        """
        Varre o chat de trás pra frente procurando a ÚLTIMA apresentação do técnico.

        Identifica a linha exata que contém o cumprimento + nome (pode estar em
        linhas separadas dentro de um bloco de até 4 mensagens).

        O índice passado para buscar_hora_do_gatilho é o índice da linha que
        contém o cumprimento/nome — não o início do bloco — para que a Camada 1
        procure o marcador do técnico imediatamente antes dessa linha, e não
        o marcador de outra pessoa que possa estar no início do bloco.
        """
        for i in range(len(mensagens) - 1, -1, -1):
            # Janela de até 4 linhas a partir de i para detectar apresentações
            # que se espalham por múltiplas mensagens
            janela_idx = list(range(i, min(i + 4, len(mensagens))))
            bloco = " ".join(mensagens[j] for j in janela_idx)

            if not (padrao_cumprimento.search(bloco) and padrao_nome.search(bloco)):
                continue

            # Encontra o índice exato da linha que tem o cumprimento ou o nome
            # (a mais próxima do início do bloco que satisfaça um dos padrões)
            i_apresentacao = i
            for j in janela_idx:
                msg_j = mensagens[j]
                if padrao_cumprimento.search(msg_j) or padrao_nome.search(msg_j):
                    i_apresentacao = j
                    break

            hora, indice_hora = buscar_hora_do_gatilho(i_apresentacao)
            if not hora:
                continue

            data_inicio = resolver_data_marcador(mensagens, indice_hora)
            data_inicio = ajustar_data_para_apresentacao_recente(
                data_inicio, hora, i_apresentacao, indice_hora
            )
            print(f"✅ Início (ultima apresentacao): {data_inicio} {hora} — '{mensagens[indice_hora][:60]}'")
            return hora, data_inicio, bloco
        return None

    def buscar_inicio_por_fallback():
        ocorrencias = [i for i, msg in enumerate(mensagens) if padrao_gatilho_fallback.search(msg)]
        if not ocorrencias:
            return None

        i_gatilho = ocorrencias[-1]
        hora, indice_hora = buscar_hora_do_gatilho(i_gatilho)
        if not hora:
            return None

        data_inicio = resolver_data_marcador(mensagens, indice_hora)
        data_inicio = ajustar_data_para_apresentacao_recente(
            data_inicio, hora, i_gatilho, indice_hora
        )
        print(f"✅ Início (fallback iniciarei): {data_inicio} {hora} — '{mensagens[indice_hora][:60]}'")
        return hora, data_inicio, mensagens[i_gatilho]

    resultado = buscar_inicio_por_apresentacao()
    if resultado:
        return resultado

    resultado = buscar_inicio_por_fallback()
    if resultado:
        return resultado

    print("⚠️ Gatilho não encontrado. Usando primeira mensagem com hora...")
    for i, msg in enumerate(mensagens):
        hora_match = padrao_hora.search(msg)
        if hora_match:
            h, m = hora_match.group(1).zfill(2), hora_match.group(2)
            data_inicio = resolver_data_marcador(mensagens, i)
            data_inicio = ajustar_data_para_apresentacao_recente(
                data_inicio, f"{h}:{m}", i, i
            )
            print(f"✅ Fallback — {data_inicio} {h}:{m} — '{msg[:60]}'")
            return f"{h}:{m}", data_inicio, msg

    print("⚠️ Nenhuma hora encontrada no chat.")
    return None, None, None


def encontrar_opcoes_inicio_atendimento(mensagens):
    """Lista possiveis inicios por atribuicao ao atendente para escolha manual."""
    atribuicoes = encontrar_atribuicoes_atendimento(mensagens)
    if atribuicoes:
        return [
            {
                "id": idx,
                "data": evento["data"],
                "hora": evento["hora"],
                "texto": evento["texto"],
                "linha": evento["linha"],
            }
            for idx, evento in enumerate(atribuicoes)
        ]
    padrao_cumprimento = re.compile(r"\b(bom\s+dia|boa\s+tarde|boa\s+noite)\b", re.IGNORECASE)
    padrao_nome = re.compile(r"\b(meu\s+nome\s+(?:e|é)|me\s+chamo)\b", re.IGNORECASE)
    padrao_nome_tecnico = re.compile(r"(?:meu\s+nome\s+(?:e|é)|me\s+chamo)\s+([A-Za-zÀ-ú]+)", re.IGNORECASE)
    padrao_inicio_direto = re.compile(
        r"\b(?:meu\s+nome\s+(?:e|é)|me\s+chamo)\s+[A-Za-zÀ-ú]+.*\b"
        r"(?:iniciarei|vou\s+iniciar|irei\s+iniciar).*\batendimento\b",
        re.IGNORECASE
    )
    padrao_hora = re.compile(r"(\d{1,2})[h:](\d{2})")
    padrao_marcador = re.compile(
        r'(hoje|ontem|\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?|'
        r'segunda|ter[cç]a|quarta|quinta|sexta|s[aá]bado|domingo)'
        r'\s*[-–]\s*(\d{1,2})[h:](\d{2})',
        re.IGNORECASE
    )
    padrao_marcador_bloco = re.compile(
        r'(hoje|ontem|\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?|'
        r'segunda|ter[cç]a|quarta|quinta|sexta|s[aá]bado|domingo)'
        r'\s*[-–]\s*\d{1,2}[h:]\d{2}'
        r'(?:\s*[-–]\s*[A-Za-zÀ-ú\s]+disse\s*:)?',
        re.IGNORECASE
    )
    padrao_linha_opcao = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}\s*[-–]")
    sistema = re.compile(
        r"HORARIO DE ATENDIMENTO|HORÁRIO DE ATENDIMENTO|transferido|atribuido|atribuído|automacao|automação|"
        r"Seja bem vindo|digit|#sair|ANYDESK|agilizar|alta demanda|"
        r"Por favor|setor desejado|Suporte em Sistemas",
        re.IGNORECASE
    )

    def quebrar_linha_por_marcador(texto):
        partes = []
        pos = 0
        for marcador in padrao_marcador_bloco.finditer(texto):
            antes = texto[pos:marcador.start()].strip()
            if antes:
                partes.append(antes)
            partes.append(marcador.group(0).strip())
            pos = marcador.end()
        resto = texto[pos:].strip()
        if resto:
            partes.append(resto)
        return partes or ([texto.strip()] if texto.strip() else [])

    linhas = []
    for indice_msg, msg in enumerate(mensagens):
        partes = []
        for linha in str(msg).splitlines():
            partes.extend(quebrar_linha_por_marcador(linha))
        if not partes and str(msg).strip():
            partes = [str(msg).strip()]
        for parte in partes:
            linhas.append({"texto": parte, "indice_msg": indice_msg})

    def data_por_marcador(rotulo):
        from datetime import date, timedelta

        hoje = _data_referencia_speakr()
        texto = rotulo.strip().lower()
        dias_semana = {
            "segunda": 0, "segunda-feira": 0,
            "terca": 1, "terça": 1, "terca-feira": 1, "terça-feira": 1,
            "quarta": 2, "quarta-feira": 2,
            "quinta": 3, "quinta-feira": 3,
            "sexta": 4, "sexta-feira": 4,
            "sabado": 5, "sábado": 5,
            "domingo": 6,
        }

        if texto == "hoje":
            return hoje.strftime("%Y-%m-%d")
        if texto == "ontem":
            return (hoje - timedelta(days=1)).strftime("%Y-%m-%d")

        data_match = re.match(r"(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?$", texto)
        if data_match:
            dia = int(data_match.group(1))
            mes = int(data_match.group(2))
            ano_raw = data_match.group(3)
            ano = int(ano_raw) if ano_raw else hoje.year
            if ano < 100:
                ano += 2000
            try:
                return date(ano, mes, dia).strftime("%Y-%m-%d")
            except ValueError:
                return hoje.strftime("%Y-%m-%d")

        weekday = dias_semana.get(texto)
        if weekday is not None:
            delta = (hoje.weekday() - weekday) % 7 or 7
            return (hoje - timedelta(days=delta)).strftime("%Y-%m-%d")

        return hoje.strftime("%Y-%m-%d")

    def nome_no_marcador(texto):
        match = re.search(r"\d{1,2}[h:]\d{2}\s*-\s*([A-Za-zÀ-ú]+)(?:\s+[A-Za-zÀ-ú]+)*\s+disse", texto, re.IGNORECASE)
        return match.group(1).lower() if match else None

    def dados_marcador(texto):
        marcador = padrao_marcador.search(texto)
        if not marcador:
            return None
        return {
            "data": data_por_marcador(marcador.group(1)),
            "hora": f"{marcador.group(2).zfill(2)}:{marcador.group(3)}",
            "linha": texto[:80],
            "cabecalho": bool(re.search(r"\bdisse\s*:", texto, re.IGNORECASE)),
        }

    def texto_sem_marcador(texto):
        return padrao_marcador.sub("", texto, count=1).strip(" -–")

    def dados_hora_sem_data(texto):
        match = re.search(r"^\s*(\d{1,2})[h:](\d{2})\s*[-–]", str(texto), re.IGNORECASE)
        if not match:
            return None
        from datetime import date
        return {
            "data": _data_referencia_speakr().strftime("%Y-%m-%d"),
            "hora": f"{match.group(1).zfill(2)}:{match.group(2)}",
            "linha": str(texto)[:80],
            "cabecalho": bool(re.search(r"\bdisse\s*:", str(texto), re.IGNORECASE)),
        }
    apresentacoes_diretas = []
    for i, linha in enumerate(linhas):
        texto = linha["texto"]
        if padrao_marcador.search(texto) or padrao_linha_opcao.search(texto) or sistema.search(texto):
            continue
        if padrao_inicio_direto.search(texto) or (padrao_nome.search(texto) and re.search(r"\batendimento\b", texto, re.IGNORECASE)):
            apresentacoes_diretas.append(i)

    opcoes_diretas = []
    vistos_diretos = set()
    for pos, i_apresentacao in enumerate(apresentacoes_diretas):
        proxima_apresentacao = (
            apresentacoes_diretas[pos + 1]
            if pos + 1 < len(apresentacoes_diretas)
            else len(linhas)
        )

        marcador_anterior = None
        if i_apresentacao > 0:
            marcador_anterior = dados_hora_sem_data(linhas[i_apresentacao - 1]["texto"])
        if not marcador_anterior:
            marcador_anterior = dados_hora_sem_data(linhas[i_apresentacao]["texto"])
        limite_anterior = apresentacoes_diretas[pos - 1] if pos > 0 else -1
        if not marcador_anterior:
            for idx in range(i_apresentacao - 1, limite_anterior, -1):
                marcador_anterior = dados_marcador(linhas[idx]["texto"])
                if marcador_anterior:
                    break

        marcador_depois = None
        for idx in range(i_apresentacao + 1, proxima_apresentacao):
            marcador_depois = dados_marcador(linhas[idx]["texto"])
            if marcador_depois:
                break

        if marcador_anterior and marcador_anterior["cabecalho"]:
            marcador_escolhido = marcador_anterior
        else:
            marcador_escolhido = marcador_depois or marcador_anterior

        if not marcador_escolhido:
            continue

        partes_texto = []
        if i_apresentacao > 0:
            anterior = linhas[i_apresentacao - 1]["texto"]
            anterior_limpo = texto_sem_marcador(anterior) if padrao_marcador.search(anterior) else anterior
            if anterior_limpo and padrao_cumprimento.search(anterior_limpo):
                partes_texto.append(anterior_limpo)
        partes_texto.append(linhas[i_apresentacao]["texto"])
        bloco = " ".join(partes_texto).strip()

        nome_match = padrao_nome_tecnico.search(bloco)
        nome_tecnico = nome_match.group(1).lower() if nome_match else ""
        chave = (marcador_escolhido["data"], marcador_escolhido["hora"], nome_tecnico)
        if chave in vistos_diretos:
            continue
        vistos_diretos.add(chave)

        opcoes_diretas.append({
            "id": len(opcoes_diretas),
            "data": marcador_escolhido["data"],
            "hora": marcador_escolhido["hora"],
            "texto": bloco[:100],
            "linha": marcador_escolhido["linha"],
        })

    if opcoes_diretas:
        return opcoes_diretas

    def buscar_hora_proxima(i_gatilho, nome_tecnico, apresentacoes):
        candidatos_antes = []
        candidatos_depois = []
        apresentacoes_antes = [idx for idx in apresentacoes if idx < i_gatilho]
        apresentacoes_depois = [idx for idx in apresentacoes if idx > i_gatilho]
        limite_antes = (max(apresentacoes_antes) + 1) if apresentacoes_antes else 0
        limite_depois = min(apresentacoes_depois) if apresentacoes_depois else len(linhas)

        for idx in range(max(limite_antes, i_gatilho - 10), min(limite_depois, i_gatilho + 11)):
            msg = linhas[idx]["texto"]
            if sistema.search(msg) or padrao_linha_opcao.search(msg):
                continue
            marcador = padrao_marcador.search(msg)
            hora_sem_data = None if marcador else dados_hora_sem_data(msg)
            if marcador:
                h, m = marcador.group(2).zfill(2), marcador.group(3)
                data = data_por_marcador(marcador.group(1))
                tem_marcador = 0
            elif hora_sem_data:
                h, m = hora_sem_data["hora"].split(":")
                data = hora_sem_data["data"]
                tem_marcador = 1
            else:
                continue

            nome_marcador = nome_no_marcador(msg)
            nome_penalidade = 0 if nome_marcador == nome_tecnico else (1 if not nome_marcador else 2)

            candidato = (tem_marcador, nome_penalidade, abs(idx - i_gatilho), f"{h}:{m}", data, idx)
            if idx <= i_gatilho:
                candidatos_antes.append(candidato)
            else:
                candidatos_depois.append(candidato)

        candidatos = candidatos_depois or candidatos_antes
        if not candidatos:
            return None, None, None
        candidatos.sort()
        return candidatos[0][3], candidatos[0][4], candidatos[0][5]

    apresentacoes = []
    for i in range(len(linhas)):
        texto = linhas[i]["texto"]
        if padrao_marcador.search(texto) or padrao_linha_opcao.search(texto) or sistema.search(texto):
            continue
        if padrao_inicio_direto.search(texto) or (padrao_nome.search(texto) and re.search(r"\batendimento\b", texto, re.IGNORECASE)):
            apresentacoes.append(i)

    opcoes = []
    vistos = set()
    for i_apresentacao in apresentacoes:
        janela_texto = []
        if (i_apresentacao > 0
                and not padrao_marcador.search(linhas[i_apresentacao - 1]["texto"])
                and not padrao_linha_opcao.search(linhas[i_apresentacao - 1]["texto"])):
            if padrao_cumprimento.search(linhas[i_apresentacao - 1]["texto"]):
                janela_texto.append(i_apresentacao - 1)
        for j in range(i_apresentacao, min(i_apresentacao + 3, len(linhas))):
            if padrao_marcador.search(linhas[j]["texto"]) or padrao_linha_opcao.search(linhas[j]["texto"]):
                break
            if sistema.search(linhas[j]["texto"]):
                continue
            janela_texto.append(j)
        bloco = " ".join(linhas[j]["texto"] for j in janela_texto)

        nome_match = padrao_nome_tecnico.search(bloco)
        nome_tecnico = nome_match.group(1).lower() if nome_match else ""
        hora, data_inicio, indice_linha_hora = buscar_hora_proxima(i_apresentacao, nome_tecnico, apresentacoes)
        if not hora:
            continue

        chave = (data_inicio, hora, nome_tecnico)
        if chave in vistos:
            continue
        vistos.add(chave)

        trecho = bloco.strip().replace("\n", " ")[:100]
        opcoes.append({
            "id": len(opcoes),
            "data": data_inicio,
            "hora": hora,
            "texto": trecho,
            "linha": linhas[indice_linha_hora]["texto"][:80],
        })

    return opcoes

# ---------------------------------------------------------------------------
# API Auvo
# ---------------------------------------------------------------------------
def autenticar():
    agora = time.time()
    with AUVO_TOKEN_LOCK:
        if AUVO_TOKEN_CACHE["token"] and AUVO_TOKEN_CACHE["expires_at"] > agora:
            return AUVO_TOKEN_CACHE["token"]

    url     = "https://api.auvo.com.br/v2/login/"
    payload = {
        "apiKey":   "",
        "apiToken": ""
    }
    response = HTTP_SESSION.post(url, json=payload, timeout=15)
    response.raise_for_status()
    token = response.json()["result"]["accessToken"]

    with AUVO_TOKEN_LOCK:
        AUVO_TOKEN_CACHE["token"] = token
        AUVO_TOKEN_CACHE["expires_at"] = time.time() + 50 * 60

    return token


# ---------------------------------------------------------------------------
# Detecção de instabilidade do Auvo
# ---------------------------------------------------------------------------
AUVO_PALAVRAS_INSTABILIDADE = [
    "timeout", "timed out", "service unavailable", "502", "503", "504",
    "bad gateway", "gateway timeout", "internal server error", "500",
    "connection error", "connection refused", "temporarily unavailable",
]

def detectar_instabilidade_auvo(exception: Exception) -> bool:
    """Retorna True se o erro indica instabilidade do Auvo (não erro de lógica)."""
    msg = str(exception).lower()
    if msg.startswith("auvo_instavel:"):
        return True
    return any(p in msg for p in AUVO_PALAVRAS_INSTABILIDADE)

def montar_data_hora_auvo(data: str, hora: str) -> str:
    """
    Monta o taskDate com fuso local para a API do Auvo.
    Sem o offset, o Auvo pode interpretar como UTC e mostrar um inicio
    diferente do check-in lancado manualmente na UI.
    """
    return f"{data}T{hora}:00-03:00"




def _limpar_cache_tasks():
    with AUVO_TASKS_CACHE_LOCK:
        AUVO_TASKS_CACHE.clear()

def _status_task_auvo(t):
    for campo in ("status", "taskStatus", "taskStatusId", "statusId", "statusID", "idStatus"):
        valor = t.get(campo)
        if isinstance(valor, dict):
            valor = (
                valor.get("id")
                or valor.get("status")
                or valor.get("statusId")
                or valor.get("statusID")
                or valor.get("taskStatusId")
            )
        if valor is None:
            continue
        try:
            return int(valor)
        except (TypeError, ValueError):
            continue
    return None


def _buscar_entity_list_tasks(token, customer_id, dias=3, cache_ttl=4):
    if not customer_id:
        return []

    data_inicio = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d")
    data_hoje = datetime.now().strftime("%Y-%m-%d")
    filtros = {
        "customerId": int(customer_id),
        "startDate": data_inicio,
        "endDate": data_hoje,
        "idUserTo": ID_USER,
    }
    cache_key = (int(customer_id), ID_USER, data_inicio, data_hoje)
    agora = time.time()

    with AUVO_TASKS_CACHE_LOCK:
        item_cache = AUVO_TASKS_CACHE.get(cache_key)
        if item_cache and item_cache["expires_at"] > agora:
            return item_cache["entity_list"]

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    params = {"paramFilter": json.dumps(filtros)}
    url = "https://api.auvo.com.br/v2/tasks/"

    response = HTTP_SESSION.get(url, headers=headers, params=params, timeout=(5, 10))
    if response.status_code in (500, 502, 503, 504):
        raise Exception(f"HTTP {response.status_code}: Auvo instável")
    if response.status_code != 200:
        print(f"⚠️ Erro API Auvo ({response.status_code}): {response.text}")
        return []

    result = response.json().get("result", {})
    if isinstance(result, dict):
        entity_list = result.get("entityList", [])
    elif isinstance(result, list):
        entity_list = result
    else:
        print(f"⚠️ Formato inesperado de result: {type(result)}")
        return []

    with AUVO_TASKS_CACHE_LOCK:
        AUVO_TASKS_CACHE[cache_key] = {"expires_at": time.time() + cache_ttl, "entity_list": entity_list}
        for chave in list(AUVO_TASKS_CACHE):
            if AUVO_TASKS_CACHE[chave]["expires_at"] <= time.time():
                AUVO_TASKS_CACHE.pop(chave, None)

    return entity_list


def _formatar_task_resumo(t):
    return {
        "taskID": t["taskID"],
        "hora": (t.get("taskDate", "") or "")[11:16],
        "descricao": (t.get("orientation") or t.get("description") or "Sem descrição")[:80],
    }

def buscar_task_criada_recentemente(token, customer_id, id_user_to, task_type, data_hora, descricao):
    """
    Quando o POST de criacao da task estoura timeout, o Auvo pode ter criado
    a task mesmo assim. Esta checagem evita esperar/recriar sem confirmar antes.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data_base = data_hora[:10]
    filtros = {
        "customerId": int(customer_id),
        "startDate": data_base,
        "endDate": data_base,
        "idUserTo": id_user_to,
    }
    params = {"paramFilter": json.dumps(filtros)}
    response = HTTP_SESSION.get("https://api.auvo.com.br/v2/tasks/", headers=headers, params=params, timeout=(5, 8))
    if response.status_code in (500, 502, 503, 504):
        raise Exception(f"HTTP {response.status_code}: Auvo instável ao confirmar task criada")
    if response.status_code != 200:
        print(f"⚠️ Não foi possível confirmar task após timeout ({response.status_code}): {response.text[:200]}")
        return None

    result = response.json().get("result", {})
    if isinstance(result, dict):
        entity_list = result.get("entityList", [])
    elif isinstance(result, list):
        entity_list = result
    else:
        return None

    data_hora_sem_fuso = data_hora[:16]
    descricao_base = (descricao or "").strip()[:80]
    candidatas = []
    for task in entity_list:
        task_date = (task.get("taskDate") or "")[:16]
        task_tipo = task.get("taskType") or task.get("taskTypeId") or task.get("idTaskType")
        task_desc = (task.get("orientation") or task.get("description") or "").strip()
        mesmo_tipo = str(task_tipo) == str(task_type) if task_tipo is not None else True
        mesma_data = task_date == data_hora_sem_fuso
        mesma_desc = not descricao_base or task_desc.startswith(descricao_base[:40])
        if mesma_data and mesmo_tipo and mesma_desc:
            candidatas.append(task)

    if not candidatas:
        return None

    candidatas.sort(key=lambda t: t.get("taskID") or 0, reverse=True)
    task_id = candidatas[0].get("taskID")
    if task_id:
        print(f"✅ Task #{task_id} encontrada após timeout da criação. Seguindo sem recriar.")
    return task_id
def criar_atendimento_com_checkin(token, customer_id, id_user_from, id_user_to,
                                   task_type, data_hora, descricao):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json"
    }
    payload_task = {
        "customerId":  customer_id,
        "idUserFrom":  id_user_from,
        "idUserTo":    id_user_to,
        "taskType":    task_type,
        "taskDate":    data_hora,
        "orientation": descricao
    }

    # Até 3 tentativas com confirmação após timeout para evitar recriar task sem necessidade.
    for tentativa in range(1, 4):
        try:
            response_task = HTTP_SESSION.post(
                "https://api.auvo.com.br/v2/tasks/",
                json=payload_task,
                headers=headers,
                timeout=(5, 12)
            )

            if response_task.status_code in (500, 502, 503, 504):
                raise Exception(f"HTTP {response_task.status_code}: Auvo instável")

            data = response_task.json()
            print(f"🔍 Resposta Auvo: {json.dumps(data, ensure_ascii=False)[:500]}")

            result = data.get("result")
            if isinstance(result, list):
                _limpar_cache_tasks()
                return result[0]["taskID"]
            elif isinstance(result, dict):
                _limpar_cache_tasks()
                return result["taskID"]
            else:
                raise Exception(f"Estrutura inesperada: {data}")

        except requests.exceptions.ReadTimeout as e:
            task_id_timeout = None
            for tentativa_confirmacao in range(1, 4):
                try:
                    task_id_timeout = buscar_task_criada_recentemente(
                        token, customer_id, id_user_to, task_type, data_hora, descricao
                    )
                    if task_id_timeout:
                        _limpar_cache_tasks()
                        return task_id_timeout
                except Exception as erro_confirmacao:
                    print(f"⚠️ Confirmação pós-timeout falhou ({tentativa_confirmacao}/3): {erro_confirmacao}")
                time.sleep(0.4)

            if tentativa < 3:
                espera = tentativa * 2
                print(f"⚠️ Timeout ao criar task e nenhuma task confirmada. Tentativa {tentativa}/3. Aguardando {espera}s...")
                time.sleep(espera)
            else:
                raise e

        except Exception as e:
            if tentativa < 3:
                espera = tentativa * 2
                print(f"⚠️ Tentativa {tentativa}/3 falhou ao criar task: {e}. Aguardando {espera}s...")
                time.sleep(espera)
            else:
                raise



def buscar_tasks_pausadas(token, customer_id):
    """Retorna tasks pausadas do cliente para permitir lançar Fim da Pausa."""
    if not customer_id:
        return []

    for tentativa in range(1, 4):
        try:
            entity_list = _buscar_entity_list_tasks(token, customer_id)
            pausadas = [t for t in entity_list if _status_task_auvo(t) == 6]
            for t in pausadas:
                hora_raw = t.get("taskDate", "")
                hora_fmt = hora_raw[11:16] if len(hora_raw) >= 16 else hora_raw
                print(f"⏸️ Task pausada: #{t.get('taskID')} | {hora_fmt} | status 6")
            return [_formatar_task_resumo(t) for t in pausadas]
        except Exception as e:
            if tentativa < 3:
                espera = tentativa * 3
                print(f"⚠️ Tentativa {tentativa}/3 falhou ao buscar tasks pausadas: {e}. Aguardando {espera}s...")
                time.sleep(espera)
            else:
                print(f"❌ Todas as tentativas falharam ao buscar tasks pausadas: {e}")
                raise


def buscar_tasks_abertas(token, customer_id):
    """
    Retorna lista de tasks abertas (com check-in mas sem check-out) do dia para o cliente.
    Cada item: {"taskID": int, "hora": str, "descricao": str}
    Retorna [] se não houver nenhuma. Levanta Exception em caso de instabilidade.
    """
    if not customer_id:
        return []

    for tentativa in range(1, 4):
        try:
            entity_list = _buscar_entity_list_tasks(token, customer_id)

            abertas = [t for t in entity_list if t.get("checkIn") and not t.get("checkOut")]
            pausadas = [t for t in abertas if _status_task_auvo(t) == 6]
            abertas_ativas = [t for t in abertas if _status_task_auvo(t) != 6]

            if not abertas and entity_list:
                pausadas = [t for t in entity_list if _status_task_auvo(t) == 6]
                abertas_ativas = [t for t in entity_list if _status_task_auvo(t) != 6]

            if pausadas and not abertas_ativas:
                for t in pausadas:
                    hora_raw = t.get("taskDate", "")
                    hora_fmt = hora_raw[11:16] if len(hora_raw) >= 16 else hora_raw
                    print(f"⏸️ Atendimento pausado: #{t.get('taskID')} | {hora_fmt} | status 6")
                return {"status": "atendimento_pausado", "tasks": pausadas}

            abertas = abertas_ativas
            for t in abertas:
                hora_raw = t.get("taskDate", "")
                hora_fmt = hora_raw[11:16] if len(hora_raw) >= 16 else hora_raw
                descricao = (t.get("orientation") or t.get("description") or "")[:60]
                print(f"✅ Task aberta: #{t['taskID']} | {hora_fmt} | {descricao}")

            if not abertas:
                print("⚠️ entityList vazia — sem atendimento aberto hoje para este cliente.")

            return [_formatar_task_resumo(t) for t in abertas]
        except Exception as e:
            if tentativa < 3:
                espera = tentativa * 3
                print(f"⚠️ Tentativa {tentativa}/3 falhou ao buscar tasks: {e}. Aguardando {espera}s...")
                time.sleep(espera)
            else:
                print(f"❌ Todas as tentativas falharam ao buscar tasks: {e}")
                raise


# ---------------------------------------------------------------------------
# Helpers do Playwright
# ---------------------------------------------------------------------------
def _abrir_browser_e_pagina(playwright):
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    context = (
        browser.new_context(storage_state=AUVO_STATE_PATH)
        if os.path.exists(AUVO_STATE_PATH)
        else browser.new_context()
    )
    page = context.new_page()
    page.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in ["image", "stylesheet", "font", "media", "other"]
        else route.continue_()
    )
    return browser, context, page


def _garantir_login(page, context, url_destino: str = None):
    """
    Faz login se necessário e, após autenticar, navega para url_destino
    (normalmente a URL da task) garantindo que o app está totalmente carregado.
    """
    if "login" not in page.url.lower():
        return  # já logado, nada a fazer

    print("⚠️ Sessão expirada. Logando...")
    page.locator('#usuario').wait_for(state="visible", timeout=15000)
    page.locator('#usuario').fill(USUARIO_AUVO)
    page.locator('#senha').fill(SENHA_AUVO)
    page.keyboard.press("Enter")

    # Aguarda sair da tela de login
    page.wait_for_function("!window.location.href.includes('login')", timeout=25000)
    context.storage_state(path=AUVO_STATE_PATH)
    print("✅ Login realizado.")

    # Após login o Auvo redireciona para a home — volta para a URL desejada
    if url_destino and url_destino not in page.url:
        print(f"↩️ Redirecionando para task após login...")
        page.goto(url_destino, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)
        # Aguarda o app SPA renderizar — espera um elemento da página de task aparecer
        try:
            page.locator('text=Controle de horas').first.wait_for(state="visible", timeout=10000)
        except Exception:
            pass  # segue; o próximo wait_for vai capturar se não carregou


def _ativar_edicao_secao(page, secao: str, timeout_ms: int = 15000) -> bool:
    """
    Ativa o modo de edição de uma seção específica da task do Auvo.
    
    Estratégia:
      1. Localiza o container que contém o texto da seção (ex: "Controle de horas")
      2. Dentro desse container, clica no botão Editar (fa-edit / texto "Editar")
      3. Confirma ativação aguardando o #adicionarAtividadeButton (horas) ou
         textarea (relato) ficarem visíveis dentro do mesmo container
    
    Retorna True se ativou com sucesso, False se esgotou tentativas.
    """
    seletor_confirmacao = {
        "Controle de horas": "#adicionarAtividadeButton",
        "Relato do usuário": "textarea, div[contenteditable='true']",
    }.get(secao, ".modo-edicao")

    for tentativa_edit in range(1, 5):  # até 4 tentativas internas de clicar no Editar
        # Clica no Editar via JS procurando dentro do container da seção
        clicou = page.evaluate(f"""
            () => {{
                // Encontra o elemento que contém o texto da seção
                const textoSecao = "{secao}";
                const todos = Array.from(document.querySelectorAll('*'));
                const header = todos.find(el =>
                    el.children.length < 5 &&
                    el.textContent.trim().startsWith(textoSecao) &&
                    el.offsetParent !== null
                );
                
                // Sobe na árvore para encontrar o container pai (card/panel)
                let container = header;
                for (let i = 0; i < 6 && container; i++) {{
                    const btn = container.querySelector(
                        'button[title="Editar"], a[title="Editar"], ' +
                        '.fa-edit, [data-original-title="Editar"]'
                    );
                    if (btn) {{
                        // Chega ao elemento clicável (pode ser o pai do ícone)
                        const clicavel = btn.closest('button, a') || btn;
                        clicavel.click();
                        return true;
                    }}
                    // Busca por texto "Editar" visível no container
                    const btnTexto = Array.from(container.querySelectorAll('button, a'))
                        .find(el => el.textContent.trim() === 'Editar' && el.offsetParent !== null);
                    if (btnTexto) {{
                        btnTexto.click();
                        return true;
                    }}
                    container = container.parentElement;
                }}
                
                // Fallback global: qualquer botão Editar visível
                const global = Array.from(document.querySelectorAll('button, a'))
                    .filter(el => el.offsetParent !== null &&
                        (el.textContent.trim() === 'Editar' ||
                         el.innerHTML.includes('fa-edit') ||
                         el.getAttribute('title') === 'Editar' ||
                         el.getAttribute('data-original-title') === 'Editar')
                    );
                if (global.length > 0) {{
                    global[global.length - 1].click();
                    return true;
                }}
                return false;
            }}
        """)

        if not clicou:
            print(f"    ⚠️ Editar não encontrado na tentativa {tentativa_edit}/4, aguardando...")
            time.sleep(0.35)
            continue

        # Aguarda confirmação que o modo edição ativou
        try:
            page.wait_for_selector(seletor_confirmacao, state="visible", timeout=timeout_ms // 2)
            print(f"    ✓ Modo edição '{secao}' ativo.")
            return True
        except Exception:
            print(f"    ⚠️ Modo edição não confirmado na tentativa {tentativa_edit}/4 — retentando...")
            time.sleep(0.4)

    print(f"    ❌ Não foi possível ativar edição da seção '{secao}' após 4 tentativas.")
    return False


def _clicar_editar(page, secao: str = None, timeout: int = 15000):
    """
    Wrapper de compatibilidade: ativa edição da seção indicada.
    Se secao for None, tenta ativar qualquer edição visível (comportamento legado).
    """
    if secao:
        ok = _ativar_edicao_secao(page, secao, timeout_ms=timeout)
        if not ok:
            raise Exception(f"Não foi possível ativar o modo de edição da seção '{secao}'")
    else:
        # Comportamento legado — clica no primeiro Editar visível
        page.evaluate("""
            async () => {
                for (let i = 0; i < 10; i++) {
                    const btn = Array.from(document.querySelectorAll('button, a'))
                        .find(el => el.offsetParent !== null && (
                            el.textContent.trim() === 'Editar' ||
                            el.innerHTML.includes('fa-edit')
                        ));
                    if (btn) { btn.click(); break; }
                    await new Promise(r => setTimeout(r, 400));
                }
            }
        """)


def _preencher_data_hora(page, data_checkin: str, hora: str):
    data_formatada = datetime.strptime(data_checkin, "%Y-%m-%d").strftime("%d/%m/%Y")

    campo_data = page.locator('input[name="datePicker"]:visible').last
    campo_hora = page.locator('input[name="clockPicker"]:visible').last

    campo_data.click()

    for tentativa_data in range(1, 4):
        if tentativa_data == 1:
            page.evaluate("""
                (valor) => {
                    const campos = Array.from(document.querySelectorAll('input[name="datePicker"]'));
                    const campo = campos.filter(el => el.offsetParent !== null).pop();
                    if (!campo) return;

                    if (window.jQuery) {
                        const $campo = window.jQuery(campo);
                        try { $campo.datepicker('update', valor); } catch (e) {}
                        try { $campo.datepicker('setDate', valor); } catch (e) {}
                        try { $campo.trigger('changeDate'); } catch (e) {}
                    }

                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(campo, valor);
                    campo.setAttribute('value', valor);
                    campo.dispatchEvent(new Event('input', { bubbles: true }));
                    campo.dispatchEvent(new Event('change', { bubbles: true }));
                }
            """, data_formatada)
        else:
            campo_data.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.keyboard.type(data_formatada, delay=25)
            page.evaluate("""
                () => {
                    const campos = Array.from(document.querySelectorAll('input[name="datePicker"]'));
                    const campo = campos.filter(el => el.offsetParent !== null).pop();
                    if (!campo) return;
                    campo.dispatchEvent(new Event('input', { bubbles: true }));
                    campo.dispatchEvent(new Event('change', { bubbles: true }));
                }
            """)

        time.sleep(0.12)
        valor_data = campo_data.input_value().strip()
        if valor_data == data_formatada:
            break
        print(f"    Data ainda nao aplicada no modal (tentativa {tentativa_data}/3): {valor_data}")
    else:
        raise Exception(f"Data nao aplicada no modal: esperado {data_formatada}, campo ficou {valor_data}")

    campo_hora.click()
    page.keyboard.press("Control+A")
    campo_hora.fill(hora)
    campo_hora.press("Tab")

    valor_data_final = campo_data.input_value().strip()
    if valor_data_final != data_formatada:
        raise Exception(f"Data alterada pelo modal apos preencher hora: esperado {data_formatada}, campo ficou {valor_data_final}")

    valor_hora = campo_hora.input_value().strip()
    if valor_hora != hora:
        raise Exception(f"Hora nao aplicada no modal: esperado {hora}, campo ficou {valor_hora}")

    print(f"    Data/hora aplicada no modal: {data_formatada} {hora}")


def _clicar_item_dropdown(page, texto_item: str, timeout: int = 10000):
    """
    Abre o dropdown #adicionarAtividadeButton e clica no item indicado
    de forma atômica via JavaScript, evitando que o dropdown feche por
    perda de foco antes do Playwright conseguir clicar no item.
    """
    clicou = page.evaluate(f"""
        async () => {{
            const texto = "{texto_item}";

            // Abre o dropdown via Bootstrap API se disponível
            const btn = document.querySelector('#adicionarAtividadeButton');
            if (!btn) return 'btn_nao_encontrado';

            // Tenta via Bootstrap toggle
            if (window.jQuery) {{
                jQuery(btn).dropdown('toggle');
            }} else {{
                btn.click();
            }}

            // Aguarda o menu abrir (até 0,8s), checando imediatamente primeiro
            for (let i = 0; i < 11; i++) {{
                const items = Array.from(document.querySelectorAll('.dropdown-menu a, .dropdown-menu li a'))
                    .filter(el => el.offsetParent !== null && el.textContent.trim().includes(texto));
                if (items.length > 0) {{
                    items[0].click();
                    return 'ok';
                }}
                await new Promise(r => setTimeout(r, 80));
            }}
            return 'item_nao_encontrado';
        }}
    """)

    if clicou != 'ok':
        # Fallback: clique normal do Playwright
        print(f"    ⚠️ JS dropdown falhou ({clicou}), tentando Playwright...")
        page.locator("#adicionarAtividadeButton").click()
        item = page.locator(f'a:has-text("{texto_item}"), li:has-text("{texto_item}")')
        item.first.wait_for(state="visible", timeout=timeout)
        item.first.click(force=True)
    else:
        print(f"    ✓ Item '{texto_item}' clicado via JS.")

# ---------------------------------------------------------------------------
def _selecionar_motivo_pausa(page, motivo: str):
    motivo = (motivo or "").strip()
    if motivo not in PAUSA_MOTIVOS:
        raise Exception(f"Motivo de pausa inválido: {motivo}")

    resultado = page.evaluate("""
        async (motivo) => {
            const visivel = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const st = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
            };
            const aguardar = (ms) => new Promise(r => setTimeout(r, ms));
            const normalizar = (txt) => (txt || '').trim().toLowerCase();

            const select = Array.from(document.querySelectorAll('select'))
                .find(el => (el.id || '').includes('motivoSelect') || (el.name || '').includes('motivoSelect'));

            if (select) {
                const opcao = Array.from(select.options || [])
                    .find(opt => opt.text.trim() === motivo || opt.value === motivo)
                    || Array.from(select.options || [])
                        .find(opt => normalizar(opt.text).includes(normalizar(motivo)));

                if (opcao) {
                    select.value = opcao.value;
                    opcao.selected = true;
                    select.dispatchEvent(new Event('input', { bubbles: true }));
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                    if (window.jQuery) {
                        try { window.jQuery(select).val(opcao.value).trigger('change'); } catch (e) {}
                        try { window.jQuery(select).trigger({ type: 'select2:select', params: { data: { id: opcao.value, text: opcao.text } } }); } catch (e) {}
                    }
                    await aguardar(300);
                    const rendered = document.querySelector('[id^="select2-motivoSelect"][id$="-container"]');
                    if (!rendered || normalizar(rendered.textContent).includes(normalizar(motivo))) {
                        return 'ok_select';
                    }
                }
            }

            const rendered = Array.from(document.querySelectorAll('span.select2-selection__rendered'))
                .filter(visivel)
                .find(el => (el.id || '').includes('select2-motivoSelect') || el.textContent.trim() === 'Selecionar...');
            if (!rendered) return 'select_nao_encontrado';

            const selection = rendered.closest('.select2-selection') || rendered;
            selection.scrollIntoView({ block: 'center', inline: 'center' });
            selection.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
            selection.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
            selection.click();

            for (let i = 0; i < 20; i++) {
                await aguardar(100);
                const campoBusca = Array.from(document.querySelectorAll('input.select2-search__field'))
                    .filter(visivel).pop();
                if (campoBusca) {
                    campoBusca.focus();
                    campoBusca.value = motivo;
                    campoBusca.dispatchEvent(new Event('input', { bubbles: true }));
                    campoBusca.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
                    break;
                }
            }

            for (let i = 0; i < 30; i++) {
                await aguardar(100);
                const opcoes = Array.from(document.querySelectorAll('.select2-results__option'))
                    .filter(visivel);
                const opcao = opcoes.find(el => el.textContent.trim() === motivo)
                    || opcoes.find(el => normalizar(el.textContent).includes(normalizar(motivo)));
                if (opcao) {
                    opcao.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                    opcao.click();
                    return 'ok_dropdown';
                }
            }
            return 'opcao_nao_encontrada';
        }
    """, motivo)

    if not str(resultado).startswith('ok'):
        raise Exception(f"Motivo de pausa não selecionado: {resultado}")

    print(f"    Motivo da pausa selecionado: {motivo}")

def realizar_inicio_pausa_ui_invisivel(task_id, data_pausa, hora_pausa, motivo):
    print(f"\n[UI] Iniciando pausa para Task #{task_id}...")

    for tentativa in range(1, 4):
        with sync_playwright() as playwright:
            browser, context, page = _abrir_browser_e_pagina(playwright)
            try:
                url_tarefa = f"https://app2.auvo.com.br/relatorioTarefas/DetalheTarefa/{task_id}"

                page.goto(url_tarefa, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)
                _garantir_login(page, context, url_destino=url_tarefa)

                if _pagina_com_erro_auvo(page):
                    raise Exception("auvo_instavel: página de erro HTTP detectada na UI")

                task_status[f"pausa_{task_id}"] = "abrindo_task"
                _clicar_editar(page, secao="Controle de horas", timeout=15000)

                btn_add = page.locator("#adicionarAtividadeButton")
                btn_add.wait_for(state="visible", timeout=8000)

                task_status[f"pausa_{task_id}"] = "preenchendo_pausa"
                _clicar_item_dropdown(page, "Início da Pausa")

                page.locator('input[name="datePicker"]:visible').last.wait_for(state="visible", timeout=12000)
                _preencher_data_hora(page, data_pausa, hora_pausa)
                _selecionar_motivo_pausa(page, motivo)

                task_status[f"pausa_{task_id}"] = "lancando_pausa"
                btn_salvar = page.locator("#edicao-salvar:visible").last
                btn_salvar.wait_for(state="visible", timeout=10000)
                btn_salvar.click(force=True)

                try:
                    page.wait_for_selector('#edicao-salvar', state="hidden", timeout=12000)
                except Exception:
                    page.evaluate("document.querySelector('#edicao-salvar')?.click()")
                    time.sleep(2.5)

                context.storage_state(path=AUVO_STATE_PATH)
                print(f"✓ Task #{task_id} — início da pausa salvo!")
                _limpar_cache_tasks()
                task_status[f"pausa_{task_id}"] = "done"
                return

            except Exception as e:
                print(f"[UI ERROR] Tentativa {tentativa}/3 — Falha ao iniciar pausa Task #{task_id}: {e}")
                try:
                    page.screenshot(path=f"erro_pausa_{task_id}_t{tentativa}.png")
                except Exception:
                    pass

                msg_erro = str(e).lower()
                instavel = detectar_instabilidade_auvo(e) or "instabilidade" in msg_erro
                eh_timeout_ui = ("timeout" in msg_erro and instavel is False) or any(x in msg_erro for x in [
                    "datepicker", "clockpicker", "edicao-salvar", "motivo", "select2",
                    "adicionaratividadebutton", "início da pausa", "inicio da pausa",
                    "modo-edicao", "controle de horas"
                ])
                if eh_timeout_ui:
                    instavel = False

                if tentativa < 3:
                    espera = tentativa * 8 if eh_timeout_ui else tentativa * 10
                    print(f"⚠️ Tentando novamente em {espera}s...")
                    time.sleep(espera)
                else:
                    task_status[f"pausa_{task_id}"] = "auvo_instavel" if instavel else "erro"
                    task_status[f"pausa_{task_id}_mensagem"] = str(e)
                    print(f"❌ Início da pausa falhou após {tentativa} tentativas.")

            finally:
                browser.close()


def realizar_fim_pausa_ui_invisivel(task_id, data_pausa, hora_pausa):
    print(f"\n[UI] Finalizando pausa para Task #{task_id}...")

    for tentativa in range(1, 4):
        with sync_playwright() as playwright:
            browser, context, page = _abrir_browser_e_pagina(playwright)
            try:
                url_tarefa = f"https://app2.auvo.com.br/relatorioTarefas/DetalheTarefa/{task_id}"

                page.goto(url_tarefa, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)
                _garantir_login(page, context, url_destino=url_tarefa)

                if _pagina_com_erro_auvo(page):
                    raise Exception("auvo_instavel: página de erro HTTP detectada na UI")

                task_status[f"despausa_{task_id}"] = "abrindo_task"
                _clicar_editar(page, secao="Controle de horas", timeout=15000)

                btn_add = page.locator("#adicionarAtividadeButton")
                btn_add.wait_for(state="visible", timeout=8000)

                task_status[f"despausa_{task_id}"] = "preenchendo_despausa"
                _clicar_item_dropdown(page, "Fim da Pausa")

                page.locator('input[name="datePicker"]:visible').last.wait_for(state="visible", timeout=12000)
                _preencher_data_hora(page, data_pausa, hora_pausa)

                task_status[f"despausa_{task_id}"] = "lancando_despausa"
                btn_salvar = page.locator("#edicao-salvar:visible").last
                btn_salvar.wait_for(state="visible", timeout=10000)
                btn_salvar.click(force=True)

                try:
                    page.wait_for_selector('#edicao-salvar', state="hidden", timeout=12000)
                except Exception:
                    page.evaluate("document.querySelector('#edicao-salvar')?.click()")
                    time.sleep(2.5)

                context.storage_state(path=AUVO_STATE_PATH)
                print(f"✓ Task #{task_id} — fim da pausa salvo!")
                _limpar_cache_tasks()
                task_status[f"despausa_{task_id}"] = "done"
                return

            except Exception as e:
                print(f"[UI ERROR] Tentativa {tentativa}/3 — Falha ao finalizar pausa Task #{task_id}: {e}")
                try:
                    page.screenshot(path=f"erro_despausa_{task_id}_t{tentativa}.png")
                except Exception:
                    pass

                msg_erro = str(e).lower()
                instavel = detectar_instabilidade_auvo(e) or "instabilidade" in msg_erro
                eh_timeout_ui = ("timeout" in msg_erro and instavel is False) or any(x in msg_erro for x in [
                    "datepicker", "clockpicker", "edicao-salvar", "adicionaratividadebutton",
                    "fim da pausa", "modo-edicao", "controle de horas"
                ])
                if eh_timeout_ui:
                    instavel = False

                if tentativa < 3:
                    espera = tentativa * 8 if eh_timeout_ui else tentativa * 10
                    print(f"⚠️ Tentando novamente em {espera}s...")
                    time.sleep(espera)
                else:
                    task_status[f"despausa_{task_id}"] = "auvo_instavel" if instavel else "erro"
                    task_status[f"despausa_{task_id}_mensagem"] = str(e)
                    print(f"❌ Fim da pausa falhou após {tentativa} tentativas.")

            finally:
                browser.close()
# Check-in (com retry em instabilidade do Auvo)
# ---------------------------------------------------------------------------
task_status = {}

PAUSA_MOTIVOS = [
    "Fim de Expediente",
    "Almoço",
    "Ausência do cliente",
    "Aguardando retorno dos desenvolvedores",
    "Aguardando retorno cliente",
    "Período para agendar retorno",
]

# Timeout máximo para operações de UI do Auvo
AUVO_UI_TIMEOUT = 60_000  # ms

def _pagina_com_erro_auvo(page) -> bool:
    """
    Retorna True apenas quando a página é genuinamente uma página de erro HTTP
    (502/503/504/etc), não apenas quando o HTML contém esses números em outros contextos.
    Checa o título da aba e elementos específicos de páginas de erro de servidor.
    """
    try:
        titulo = page.title().lower()
        # Títulos típicos de páginas de erro de servidor/proxy
        erros_titulo = ["502", "503", "504", "bad gateway", "service unavailable",
                        "error", "gateway timeout", "server error"]
        if any(e in titulo for e in erros_titulo):
            return True

        # Verifica se o body inteiro é curtíssimo (página de erro puro, sem app carregado)
        body_text = page.locator("body").inner_text(timeout=3000).strip()
        if len(body_text) < 120 and any(e in body_text.lower() for e in
                                         ["bad gateway", "service unavailable",
                                          "gateway timeout", "server error"]):
            return True
    except Exception:
        pass
    return False


def realizar_checkin_ui_invisivel(task_id, data_checkin, hora_checkin, inicio=None):
    print(f"\n[UI] Iniciando check-in para Task #{task_id}...")

    for tentativa in range(1, 4):
        with sync_playwright() as playwright:
            browser, context, page = _abrir_browser_e_pagina(playwright)
            try:
                url_tarefa = f"https://app2.auvo.com.br/relatorioTarefas/DetalheTarefa/{task_id}"

                page.goto(url_tarefa, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)
                _garantir_login(page, context, url_destino=url_tarefa)

                if url_tarefa not in page.url:
                    page.goto(url_tarefa, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)

                if _pagina_com_erro_auvo(page):
                    raise Exception("auvo_instavel: página de erro HTTP detectada na UI")

                _clicar_editar(page, secao="Controle de horas")

                btn_add = page.locator("#adicionarAtividadeButton")
                btn_add.wait_for(state="visible", timeout=10000)

                _clicar_item_dropdown(page, "Check-In")

                # Aguarda o modal de data/hora abrir
                page.locator('input[name="datePicker"]:visible').last.wait_for(state="visible", timeout=12000)
                _preencher_data_hora(page, data_checkin, hora_checkin)

                btn_salvar = page.locator("#edicao-salvar:visible").last
                btn_salvar.wait_for(state="visible", timeout=8000)
                btn_salvar.click(force=True)
                page.wait_for_selector('#edicao-salvar', state="hidden", timeout=10000)

                context.storage_state(path=AUVO_STATE_PATH)
                print(f"✓ Task #{task_id} — check-in salvo!")
                _limpar_cache_tasks()
                task_status[str(task_id)] = "done"

                if inicio:
                    total = time.time() - inicio
                    print(f"⏱️ Tempo total (clique → check-in salvo): {total:.1f}s")
                return  # sucesso — sai do loop

            except Exception as e:
                print(f"[UI ERROR] Tentativa {tentativa}/3 — Falha no check-in Task #{task_id}: {e}")
                try:
                    page.screenshot(path=f"erro_checkin_{task_id}_t{tentativa}.png")
                except Exception:
                    pass

                msg_erro = str(e).lower()
                instavel = detectar_instabilidade_auvo(e) or "instabilidade" in msg_erro
                # Timeouts de elementos de UI (modal, datepicker, botão) não são instabilidade do servidor
                eh_timeout_ui = ("timeout" in msg_erro and instavel is False) or                                 any(x in msg_erro for x in ["datepicker", "clockpicker", "edicao-salvar",
                                                              "adicionaratividadebutton", "check-in", "check-out",
                                                              "modo-edicao", "controle de horas"])
                if eh_timeout_ui:
                    instavel = False

                if tentativa < 3:
                    espera = tentativa * 8 if eh_timeout_ui else tentativa * 10
                    if instavel:
                        task_status[str(task_id)] = "auvo_instavel"
                        print(f"⚠️ Auvo com instabilidade. Aguardando {espera}s antes de tentar novamente...")
                    else:
                        print(f"⚠️ Timeout de UI (app lento). Aguardando {espera}s antes de tentar novamente...")
                    time.sleep(espera)
                else:
                    task_status[str(task_id)] = "auvo_instavel" if instavel else "erro"
                    print(f"❌ Check-in falhou após {tentativa} tentativas.")

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Check-out (com retry em instabilidade do Auvo)
# ---------------------------------------------------------------------------
def realizar_checkout_ui_invisivel(task_id, data_checkout, hora_checkout, relato):
    print(f"\n[UI] Iniciando check-out para Task #{task_id}...")

    for tentativa in range(1, 4):
        with sync_playwright() as playwright:
            browser, context, page = _abrir_browser_e_pagina(playwright)
            try:
                url_tarefa = f"https://app2.auvo.com.br/relatorioTarefas/DetalheTarefa/{task_id}"

                page.goto(url_tarefa, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)
                _garantir_login(page, context, url_destino=url_tarefa)

                if url_tarefa not in page.url:
                    page.goto(url_tarefa, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)

                if _pagina_com_erro_auvo(page):
                    raise Exception("auvo_instavel: página de erro HTTP detectada na UI")

                page.locator('text="Relato do usuário"').first.wait_for(state="visible", timeout=AUVO_UI_TIMEOUT)
                print(f"✅ Página pronta: {page.url}")

                # ── PASSO 1: Preenche e salva o relato ───────────────────────────
                print("Preenchendo relato...")
                _clicar_editar(page, secao="Relato do usuário")

                campo_relato = page.locator('textarea, div[contenteditable="true"]').first
                campo_relato.wait_for(state="visible", timeout=8000)
                campo_relato.click()
                page.keyboard.press("Control+A")
                campo_relato.fill(relato)

                # Salva o relato antes de ir para a seção de horas
                print("Salvando relato...")
                btn_salvar_relato = page.locator("#edicao-salvar:visible").first
                btn_salvar_relato.wait_for(state="visible", timeout=8000)
                btn_salvar_relato.click(force=True)

                relato_salvo = False
                # Camada 1: botão some normalmente
                try:
                    page.wait_for_selector('#edicao-salvar', state="hidden", timeout=8000)
                    relato_salvo = True
                    print("✓ Relato salvo.")
                except Exception:
                    pass

                if not relato_salvo:
                    # Camada 2: força via JS e aguarda textarea fechar
                    print("⚠️ Botão salvar relato não sumiu — forçando via JS...")
                    page.evaluate("document.querySelector('#edicao-salvar')?.click()")
                    try:
                        page.wait_for_selector('textarea:visible', state="hidden", timeout=5000)
                        relato_salvo = True
                        print("✓ Relato salvo (textarea fechou).")
                    except Exception:
                        pass

                if not relato_salvo:
                    # Camada 3: última espera — se o campo de relato não está editável, está salvo
                    time.sleep(2)
                    editavel = page.evaluate(
                        "() => document.querySelector('textarea') !== null && "
                        "document.querySelector('textarea').offsetParent !== null"
                    )
                    if not editavel:
                        relato_salvo = True
                        print("✓ Relato salvo (textarea não encontrado no DOM visível).")

                if not relato_salvo:
                    print("⚠️ Não foi possível confirmar salvamento do relato — prosseguindo.")

                # Aguarda DOM estabilizar antes de abrir próxima seção
                time.sleep(0.5)

                # ── PASSO 2: Abre "Controle de horas" e lança check-out ──────
                print("Lançando check-out...")
                # _clicar_editar já aguarda #adicionarAtividadeButton ficar visível
                _clicar_editar(page, secao="Controle de horas", timeout=15000)

                # Aguarda novamente com timeout generoso (confirmação dupla)
                btn_add = page.locator("#adicionarAtividadeButton")
                btn_add.wait_for(state="visible", timeout=8000)

                _clicar_item_dropdown(page, "Check-Out")

                # Aguarda o modal de data/hora abrir
                page.locator('input[name="datePicker"]:visible').last.wait_for(state="visible", timeout=12000)
                _preencher_data_hora(page, data_checkout, hora_checkout)

                # ── PASSO 3: Salva check-out com confirmação em camadas ──────
                btn_salvar = page.locator("#edicao-salvar:visible").last
                btn_salvar.wait_for(state="visible", timeout=10000)
                btn_salvar.click(force=True)

                salvo = False
                # Camada 1: botão some normalmente
                try:
                    page.wait_for_selector('#edicao-salvar', state="hidden", timeout=12000)
                    salvo = True
                    print("✓ Check-out confirmado (botão sumiu).")
                except Exception:
                    pass

                if not salvo:
                    # Camada 2: clica via JS e verifica linha na tabela de horas
                    print("⚠️ Botão não sumiu — segunda tentativa via JS...")
                    page.evaluate("document.querySelector('#edicao-salvar')?.click()")
                    time.sleep(2.5)
                    try:
                        page.locator('td:has-text("Check-Out"), span:has-text("Check-Out")').first.wait_for(
                            state="visible", timeout=6000
                        )
                        salvo = True
                        print("✓ Check-out confirmado (linha na tabela detectada).")
                    except Exception:
                        pass

                if not salvo:
                    # Camada 3: última espera pelo botão
                    try:
                        page.wait_for_selector('#edicao-salvar', state="hidden", timeout=6000)
                        salvo = True
                        print("✓ Check-out confirmado (botão sumiu na 3ª espera).")
                    except Exception:
                        pass

                if not salvo:
                    raise Exception("Não foi possível confirmar o salvamento do check-out após 3 tentativas de confirmação.")

                context.storage_state(path=AUVO_STATE_PATH)
                print(f"✓ Check-out Task #{task_id} salvo!")
                _limpar_cache_tasks()
                task_status[f"checkout_{task_id}"] = "done"
                return  # sucesso

            except Exception as e:
                print(f"[ERRO] Tentativa {tentativa}/3 — Falha no check-out Task #{task_id}: {e}")
                try:
                    page.screenshot(path=f"erro_checkout_{task_id}_t{tentativa}.png")
                except Exception:
                    pass

                msg_erro = str(e).lower()
                instavel = detectar_instabilidade_auvo(e) or "instabilidade" in msg_erro

                # Timeout do botão salvar = problema de UI/timing, não instabilidade do servidor
                # Aumenta espera mas tenta de novo (pode ser lentidão pontual do Auvo)
                eh_timeout_ui = "timeout" in msg_erro and "edicao-salvar" in msg_erro
                if eh_timeout_ui:
                    instavel = False  # não marca como instabilidade do servidor

                if tentativa < 3:
                    espera = tentativa * 8 if eh_timeout_ui else tentativa * 10
                    if instavel:
                        task_status[f"checkout_{task_id}"] = "auvo_instavel"
                        print(f"⚠️ Auvo com instabilidade. Aguardando {espera}s antes de tentar novamente...")
                    elif eh_timeout_ui:
                        print(f"⚠️ Timeout no salvamento (UI lenta). Aguardando {espera}s e tentando novamente...")
                    else:
                        print(f"⚠️ Erro genérico. Aguardando {espera}s antes de tentar novamente...")
                    time.sleep(espera)
                else:
                    task_status[f"checkout_{task_id}"] = "auvo_instavel" if instavel else "erro"
                    print(f"❌ Check-out falhou após {tentativa} tentativas.")

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Busca de empresa na planilha
# ---------------------------------------------------------------------------
def normalizar(texto):
    texto = texto.upper().strip()
    texto = unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('ASCII')
    for r in ["LTDA", "EIRELI", "ME", "EPP"]:
        texto = texto.replace(r, "")
    return texto.strip()

def limpar_nome_empresa(texto):
    return re.sub(r'^\d+\s*-\s*', '', texto.upper().strip())

df = pd.read_excel("clientes.xlsx", header=0, skiprows=[1])
df = df.rename(columns={df.columns[0]: "codigo", df.columns[2]: "nome"})
df["nome_normalizado"] = df["nome"].apply(normalizar)

def buscar_opcoes_empresas(nome_empresa, limite=8):
    if not nome_empresa:
        return []

    termo = normalizar(limpar_nome_empresa(nome_empresa))
    if not termo:
        return []

    exatas = df[df["nome_normalizado"] == termo]
    if not exatas.empty:
        return [
            {"codigo": int(row["codigo"]), "nome": str(row["nome"])}
            for _, row in exatas.head(limite).iterrows()
        ]

    contem = df[df["nome_normalizado"].str.contains(re.escape(termo), na=False)]
    if not contem.empty:
        return [
            {"codigo": int(row["codigo"]), "nome": str(row["nome"])}
            for _, row in contem.head(limite).iterrows()
        ]

    nomes = df["nome_normalizado"].tolist()
    proximos = difflib.get_close_matches(termo, nomes, n=limite, cutoff=0.7)
    opcoes = []
    codigos_vistos = set()
    for nome_norm in proximos:
        row = df[df["nome_normalizado"] == nome_norm].iloc[0]
        codigo = int(row["codigo"])
        if codigo in codigos_vistos:
            continue
        codigos_vistos.add(codigo)
        opcoes.append({"codigo": codigo, "nome": str(row["nome"])})
    return opcoes


def buscar_codigo_empresa(nome_empresa):
    opcoes = buscar_opcoes_empresas(nome_empresa)
    return opcoes[0]["codigo"] if len(opcoes) == 1 else None


def resolver_cliente_para_requisicao(nome_empresa, codigo_cliente=None):
    if codigo_cliente:
        return int(codigo_cliente), []

    opcoes = buscar_opcoes_empresas(nome_empresa)
    if len(opcoes) == 1:
        return opcoes[0]["codigo"], opcoes
    return None, opcoes


def pegar_hora_e_data_ultima_mensagem(mensagens):
    """
    Percorre as mensagens de trás pra frente buscando a última hora HH:MM/HHhMM.
    Usa resolver_data_marcador para determinar em qual data essa mensagem foi enviada.
    Retorna (hora "HH:MM", data "YYYY-MM-DD") ou (None, None).
    """
    padrao_hora = re.compile(r"(\d{1,2})[h:](\d{2})")
    for i in range(len(mensagens) - 1, -1, -1):
        hora_match = padrao_hora.search(mensagens[i])
        if hora_match:
            h, m = hora_match.group(1).zfill(2), hora_match.group(2)
            if tem_marcador_data_perto(mensagens, i):
                data = resolver_data_marcador(mensagens, i)
            else:
                data = datetime.now().strftime("%Y-%m-%d")
            print(f"✅ Checkout: {data} {h}:{m} — '{mensagens[i][:60]}'")
            return f"{h}:{m}", data
    return None, None


# Alias de compatibilidade para código que ainda usa o nome antigo
def pegar_hora_ultima_mensagem(mensagens):
    hora, _ = pegar_hora_e_data_ultima_mensagem(mensagens)
    return hora


# ---------------------------------------------------------------------------
# Captura do chat
# ---------------------------------------------------------------------------
def pegar_mensagens_chat():
    global DATA_REFERENCIA_SPEAKR

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            page = _selecionar_pagina_speakr(browser)

            DATA_REFERENCIA_SPEAKR = page.evaluate("""
                () => {
                    const agora = new Date();
                    const ano = agora.getFullYear();
                    const mes = String(agora.getMonth() + 1).padStart(2, '0');
                    const dia = String(agora.getDate()).padStart(2, '0');
                    return `${ano}-${mes}-${dia}`;
                }
            """)

            clicou_copiar = page.evaluate("""
                () => {
                    const visivel = (el) => {
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
                    };

                    const idsRobo = new Set(['botao-auvo', 'botao-auvo-checkout', 'botao-auvo-pausa', 'botao-auvo-despausa']);
                    const botoes = Array.from(document.querySelectorAll('button'))
                        .filter(btn => visivel(btn) && !idsRobo.has(btn.id));

                    const botaoClipboard = botoes.find(btn => {
                        const html = btn.innerHTML || '';
                        const svg = btn.querySelector('svg');
                        const titulo = [btn.title, btn.getAttribute('aria-label'), btn.textContent]
                            .filter(Boolean).join(' ').toLowerCase();

                        return (
                            html.includes('M15 14H9') ||
                            (html.includes('iconify--uil') && html.includes('viewBox="0 0 24 24"')) ||
                            titulo.includes('copiar') ||
                            titulo.includes('copy') ||
                            (svg && svg.classList && svg.classList.contains('iconify--uil') && html.includes('currentColor'))
                        );
                    });

                    if (!botaoClipboard) return false;
                    botaoClipboard.scrollIntoView({ block: 'center', inline: 'center' });
                    botaoClipboard.click();
                    return true;
                }
            """)
            if not clicou_copiar:
                raise Exception("Botão de copiar chat não encontrado")

            conteudo_chat = ""
            for tentativa_clipboard in range(1, 16):
                time.sleep(0.1)
                conteudo_chat = pyperclip.paste()
                if conteudo_chat and (len(conteudo_chat.strip()) > 20 or tentativa_clipboard >= 3):
                    break

            mensagens = [m.strip() for m in conteudo_chat.split('\n') if m.strip()]
            return mensagens
        except Exception as e:
            print(f"Erro ao capturar chat: {e}")
            return []


# ---------------------------------------------------------------------------
# Fluxo combinado: criar checkin + checkout em sequência
# (usado quando checkout é clicado sem atendimento aberto)
# ---------------------------------------------------------------------------
def fluxo_checkin_e_checkout(empresa, mensagens, codigo_cliente, inicio_escolhido=None):
    """
    Executa o ciclo completo:
      1. Gera descrição com IA
      2. Cria task no Auvo
      3. Faz check-in
      4. Faz check-out com relato
    Atualiza task_status["full_flow_{empresa}"] ao longo do processo.
    """
    chave = f"full_flow_{codigo_cliente}"
    try:
        task_status[chave] = "gerando_descricao"

        # --- Paralelo: descrição + hora de início ---
        descricao_resultado = [None]
        def _gerar_desc():
            descricao_resultado[0] = gerar_descricao_com_ia(mensagens)
            print(f"📝 Descrição: {descricao_resultado[0]}")

        t_desc = threading.Thread(target=_gerar_desc)
        t_desc.start()

        if inicio_escolhido:
            hora_inicio = inicio_escolhido.get("hora")
            data_inicio = inicio_escolhido.get("data")
        else:
            hora_inicio, data_inicio, _ = encontrar_inicio_atendimento(mensagens)
        hora_checkout, data_checkout  = pegar_hora_e_data_ultima_mensagem(mensagens)

        t_desc.join()

        if not hora_inicio:
            task_status[chave] = "erro"
            print("❌ Hora de início não encontrada.")
            return

        token = autenticar()

        task_status[chave] = "criando_task"
        task_id = criar_atendimento_com_checkin(
            token        = token,
            customer_id  = codigo_cliente,
            id_user_from = ID_USER,
            id_user_to   = ID_USER,
            task_type    = 200374,
            data_hora    = montar_data_hora_auvo(data_inicio, hora_inicio),
            descricao    = descricao_resultado[0]
        )
        print(f"✅ Task #{task_id} criada no fluxo combinado.")

        task_status[chave] = "checkin"
        realizar_checkin_ui_invisivel(task_id, data_inicio, hora_inicio)

        # Aguarda check-in confirmar antes de ir ao checkout
        for _ in range(30):
            if task_status.get(str(task_id)) == "done":
                break
            time.sleep(2)

        task_status[chave] = "gerando_relato"
        relato = gerar_relato_com_ia(mensagens)
        print(f"📝 Relato: {relato}")

        task_status[chave] = "checkout"
        realizar_checkout_ui_invisivel(task_id, data_checkout or data_inicio, hora_checkout, relato)

        # Aguarda checkout
        for _ in range(30):
            if task_status.get(f"checkout_{task_id}") == "done":
                break
            time.sleep(2)

        task_status[chave] = "done"
        task_status["full_flow_task_id"] = task_id
        print(f"✅ Fluxo completo concluído! Task #{task_id}")

    except Exception as e:
        instavel = detectar_instabilidade_auvo(e) or "instabilidade" in str(e).lower()
        task_status[chave] = "auvo_instavel" if instavel else "erro"
        task_status[f"{chave}_mensagem"] = str(e)
        print(f"❌ Erro no fluxo combinado: {e}")


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------
class AuvoHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/status/"):
            task_id = self.path.split("/")[-1]
            status  = task_status.get(task_id, "pending")
            extra   = {}

            # Passa task_id do fluxo completo, se houver
            if task_id.startswith("full_flow_") and "full_flow_task_id" in task_status:
                extra["task_id"] = task_status["full_flow_task_id"]

            # Passa mensagem de erro, se houver
            msg_key = f"{task_id}_mensagem"
            if msg_key in task_status:
                extra["mensagem"] = task_status[msg_key]

            self.send_response(200)
            self.send_header("Content-type",                "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": status, **extra}).encode())

    def do_POST(self):
        # ── CHECK-IN ────────────────────────────────────────────────────────
        if self.path == "/auvo":
            try:
                inicio = time.time()
                content_length = int(self.headers['Content-Length'])
                dados          = json.loads(self.rfile.read(content_length))
                empresa        = dados.get("empresa", "")
                codigo_cliente = dados.get("codigo_cliente")
                inicio_escolhido = dados.get("inicio_escolhido")

                print(f"\n>> Novo Lançamento: {empresa} | Usuário: {NOME_USER} (ID: {ID_USER})")

                codigo_cliente, opcoes_empresas = resolver_cliente_para_requisicao(empresa, codigo_cliente)
                if not codigo_cliente:
                    if opcoes_empresas:
                        self._enviar_json({
                            "status": "multiplas_empresas",
                            "empresas": opcoes_empresas,
                            "empresa": empresa,
                        })
                        return
                    raise Exception(f"Cliente '{empresa}' não encontrado.")

                mensagens    = pegar_mensagens_chat()

                if inicio_escolhido:
                    hora_chat = inicio_escolhido.get("hora")
                    data_chat = inicio_escolhido.get("data")
                else:
                    opcoes_inicio = encontrar_opcoes_inicio_atendimento(mensagens)
                    print(f">> Inícios de atendimento encontrados: {len(opcoes_inicio)}")
                    if len(opcoes_inicio) > 1:
                        self._enviar_json({
                            "status": "multiplos_inicios",
                            "inicios": opcoes_inicio,
                            "empresa": empresa,
                            "codigo_cliente": codigo_cliente,
                        })
                        return
                    hora_chat, data_chat, _ = encontrar_inicio_atendimento(mensagens)

                if not hora_chat:
                    raise Exception("Hora de início não encontrada no chat.")

                token_resultado = [None]
                token_erro = [None]

                def obter_token_auvo():
                    try:
                        token_resultado[0] = autenticar()
                    except Exception as erro_token:
                        token_erro[0] = erro_token

                thread_token = threading.Thread(target=obter_token_auvo)
                thread_token.start()

                print("🤖 Gerando descrição com IA...")
                descricao_ia = gerar_descricao_com_ia(mensagens)
                print(f"📝 Descrição:\n{descricao_ia}\n")

                thread_token.join()
                if token_erro[0]:
                    raise token_erro[0]

                token   = token_resultado[0]
                task_id = criar_atendimento_com_checkin(
                    token        = token,
                    customer_id  = codigo_cliente,
                    id_user_from = ID_USER,
                    id_user_to   = ID_USER,
                    task_type    = 200374,
                    data_hora    = montar_data_hora_auvo(data_chat, hora_chat),
                    descricao    = descricao_ia
                )

                print(f">> Task #{task_id} criada ({data_chat} {hora_chat}). Iniciando Check-in...")
                threading.Thread(
                    target=realizar_checkin_ui_invisivel,
                    args=(task_id, data_chat, hora_chat, inicio)
                ).start()

                resposta = {"status": "ok", "task_id": task_id, "descricao": descricao_ia, "empresa": empresa, "codigo_cliente": codigo_cliente}

            except Exception as e:
                instavel = detectar_instabilidade_auvo(e) or "instabilidade" in str(e).lower()
                print("❌ Erro:", e)
                resposta = {
                    "status":    "auvo_instavel" if instavel else "erro",
                    "mensagem":  str(e)
                }

            self._enviar_json(resposta)

        # ── CHECK-OUT ───────────────────────────────────────────────────────
        elif self.path == "/auvo-checkout":
            try:
                content_length = int(self.headers['Content-Length'])
                dados = json.loads(self.rfile.read(content_length))
                empresa   = dados.get("empresa", "")
                # Quando o usuário já escolheu a task (veio do seletor), usamos direto
                task_id_escolhido = dados.get("task_id_escolhido")

                print(f"\n>> Check-out por empresa: {empresa} | Usuário: {NOME_USER}")

                codigo_cliente = dados.get("codigo_cliente")
                codigo_cliente, opcoes_empresas = resolver_cliente_para_requisicao(empresa, codigo_cliente)
                if not codigo_cliente:
                    if opcoes_empresas:
                        self._enviar_json({
                            "status": "multiplas_empresas",
                            "empresas": opcoes_empresas,
                            "empresa": empresa,
                        })
                        return
                    raise Exception(f"Cliente '{empresa}' não encontrado.")

                mensagens      = pegar_mensagens_chat()
                hora_checkout, data_checkout = pegar_hora_e_data_ultima_mensagem(mensagens)
                if not hora_checkout:
                    raise Exception("Hora de checkout não encontrada no chat.")

                relato_resultado = [None]
                tasks_resultado  = [None]
                erro_busca       = [None]

                def gerar_relato():
                    print("🤖 Gerando relato detalhado com IA...")
                    relato_resultado[0] = gerar_relato_com_ia(mensagens)
                    print(f"📝 Relato:\n{relato_resultado[0]}\n")

                def buscar_tasks():
                    try:
                        token = autenticar()
                        tasks_resultado[0] = buscar_tasks_abertas(token, str(codigo_cliente))
                    except Exception as e:
                        erro_busca[0] = e

                t1 = threading.Thread(target=gerar_relato)
                t2 = threading.Thread(target=buscar_tasks)
                t1.start(); t2.start()
                t1.join(); t2.join()

                # Propaga erros de instabilidade da busca
                if erro_busca[0]:
                    raise erro_busca[0]

                resultado_tasks = tasks_resultado[0] or []
                if isinstance(resultado_tasks, dict) and resultado_tasks.get("status") == "atendimento_pausado":
                    self._enviar_json({
                        "status": "atendimento_pausado",
                        "codigo_cliente": codigo_cliente,
                        "empresa": empresa,
                        "mensagem": "Atendimento pausado. Retome o atendimento no Auvo antes de fazer o check-out.",
                    })
                    return

                tasks = resultado_tasks

                # ── Sem task aberta: sugere criar checkin + checkout ──────────
                if not tasks:
                    self._enviar_json({
                        "status":         "sem_atendimento",
                        "codigo_cliente": codigo_cliente,
                        "mensagem":       f"Nenhuma task encontrada hoje para '{empresa}'.",
                    })
                    return

                # ── Múltiplas tasks: pede que o usuário escolha ───────────────
                if len(tasks) > 1 and not task_id_escolhido:
                    # Guarda o relato já gerado para reaproveitar quando a escolha chegar
                    chave_relato = f"relato_pendente_{codigo_cliente}"
                    task_status[chave_relato] = relato_resultado[0]
                    self._enviar_json({
                        "status":         "multiplas_tasks",
                        "tasks":          tasks,
                        "hora":           hora_checkout,
                        "codigo_cliente": codigo_cliente,
                        "empresa":        empresa,
                    })
                    return

                # ── Task única ou já escolhida pelo usuário ───────────────────
                if task_id_escolhido:
                    task_id = int(task_id_escolhido)
                    # Tenta reutilizar relato já gerado (economiza chamada ao Gemini)
                    chave_relato = f"relato_pendente_{codigo_cliente}"
                    relato = task_status.pop(chave_relato, None) or relato_resultado[0]
                else:
                    task_id = tasks[0]["taskID"]
                    relato  = relato_resultado[0]

                task_status[f"checkout_{task_id}"] = "relato_ok"

                threading.Thread(
                    target=realizar_checkout_ui_invisivel,
                    args=(task_id, data_checkout, hora_checkout, relato)
                ).start()

                resposta = {"status": "ok", "task_id": task_id, "hora": hora_checkout, "data": data_checkout}

            except Exception as e:
                instavel = detectar_instabilidade_auvo(e) or "instabilidade" in str(e).lower()
                print("❌ Erro checkout:", e)
                resposta = {
                    "status":   "auvo_instavel" if instavel else "erro",
                    "mensagem": str(e)
                }

            self._enviar_json(resposta)

        # ── INÍCIO DA PAUSA ─────────────────────────────────────────────────
        elif self.path == "/auvo-pausa":
            try:
                content_length = int(self.headers['Content-Length'])
                dados = json.loads(self.rfile.read(content_length))
                empresa = dados.get("empresa", "")
                codigo_cliente = dados.get("codigo_cliente")
                task_id_escolhido = dados.get("task_id_escolhido")
                data_pausa = (dados.get("data_pausa") or "").strip()
                hora_pausa = (dados.get("hora_pausa") or "").strip()
                motivo = (dados.get("motivo") or "").strip()

                print(f"\n>> Início da pausa por empresa: {empresa} | Usuário: {NOME_USER}")

                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data_pausa):
                    raise Exception("Data da pausa inválida.")
                if not re.fullmatch(r"\d{1,2}:\d{2}", hora_pausa):
                    raise Exception("Hora da pausa inválida.")
                h, m = [int(x) for x in hora_pausa.split(":")]
                if h > 23 or m > 59:
                    raise Exception("Hora da pausa inválida.")
                hora_pausa = f"{h:02d}:{m:02d}"
                if motivo not in PAUSA_MOTIVOS:
                    raise Exception("Motivo da pausa inválido.")

                codigo_cliente, opcoes_empresas = resolver_cliente_para_requisicao(empresa, codigo_cliente)
                if not codigo_cliente:
                    if opcoes_empresas:
                        self._enviar_json({
                            "status": "multiplas_empresas",
                            "empresas": opcoes_empresas,
                            "empresa": empresa,
                        })
                        return
                    raise Exception(f"Cliente '{empresa}' não encontrado.")

                token = autenticar()
                resultado_tasks = buscar_tasks_abertas(token, str(codigo_cliente))
                if isinstance(resultado_tasks, dict) and resultado_tasks.get("status") == "atendimento_pausado":
                    self._enviar_json({
                        "status": "atendimento_pausado",
                        "codigo_cliente": codigo_cliente,
                        "empresa": empresa,
                        "mensagem": "Este atendimento já está pausado.",
                    })
                    return

                tasks = resultado_tasks or []
                if not tasks:
                    self._enviar_json({
                        "status": "sem_atendimento",
                        "codigo_cliente": codigo_cliente,
                        "mensagem": f"Nenhuma task aberta encontrada para '{empresa}'.",
                    })
                    return

                if len(tasks) > 1 and not task_id_escolhido:
                    self._enviar_json({
                        "status": "multiplas_tasks",
                        "tasks": tasks,
                        "codigo_cliente": codigo_cliente,
                        "empresa": empresa,
                    })
                    return

                task_id = int(task_id_escolhido) if task_id_escolhido else tasks[0]["taskID"]
                task_status[f"pausa_{task_id}"] = "iniciando"

                threading.Thread(
                    target=realizar_inicio_pausa_ui_invisivel,
                    args=(task_id, data_pausa, hora_pausa, motivo)
                ).start()

                resposta = {
                    "status": "ok",
                    "task_id": task_id,
                    "status_key": f"pausa_{task_id}",
                    "data": data_pausa,
                    "hora": hora_pausa,
                    "motivo": motivo,
                }

            except Exception as e:
                instavel = detectar_instabilidade_auvo(e) or "instabilidade" in str(e).lower()
                print("❌ Erro pausa:", e)
                resposta = {
                    "status": "auvo_instavel" if instavel else "erro",
                    "mensagem": str(e)
                }

            self._enviar_json(resposta)

        # ── FIM DA PAUSA ─────────────────────────────────────────────────────
        elif self.path == "/auvo-despausa":
            try:
                content_length = int(self.headers['Content-Length'])
                dados = json.loads(self.rfile.read(content_length))
                empresa = dados.get("empresa", "")
                codigo_cliente = dados.get("codigo_cliente")
                task_id_escolhido = dados.get("task_id_escolhido")
                data_pausa = (dados.get("data_pausa") or dados.get("data_despausa") or "").strip()
                hora_pausa = (dados.get("hora_pausa") or dados.get("hora_despausa") or "").strip()

                print(f"\n>> Fim da pausa por empresa: {empresa} | Usuário: {NOME_USER}")

                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data_pausa):
                    raise Exception("Data do fim da pausa inválida.")
                if not re.fullmatch(r"\d{1,2}:\d{2}", hora_pausa):
                    raise Exception("Hora do fim da pausa inválida.")
                h, m = [int(x) for x in hora_pausa.split(":")]
                if h > 23 or m > 59:
                    raise Exception("Hora do fim da pausa inválida.")
                hora_pausa = f"{h:02d}:{m:02d}"

                codigo_cliente, opcoes_empresas = resolver_cliente_para_requisicao(empresa, codigo_cliente)
                if not codigo_cliente:
                    if opcoes_empresas:
                        self._enviar_json({
                            "status": "multiplas_empresas",
                            "empresas": opcoes_empresas,
                            "empresa": empresa,
                        })
                        return
                    raise Exception(f"Cliente '{empresa}' não encontrado.")

                token = autenticar()
                tasks = buscar_tasks_pausadas(token, str(codigo_cliente)) or []
                if not tasks:
                    self._enviar_json({
                        "status": "sem_atendimento",
                        "codigo_cliente": codigo_cliente,
                        "mensagem": f"Nenhum atendimento pausado encontrado para '{empresa}'.",
                    })
                    return

                if len(tasks) > 1 and not task_id_escolhido:
                    self._enviar_json({
                        "status": "multiplas_tasks",
                        "tasks": tasks,
                        "codigo_cliente": codigo_cliente,
                        "empresa": empresa,
                    })
                    return

                task_id = int(task_id_escolhido) if task_id_escolhido else tasks[0]["taskID"]
                task_status[f"despausa_{task_id}"] = "iniciando"

                threading.Thread(
                    target=realizar_fim_pausa_ui_invisivel,
                    args=(task_id, data_pausa, hora_pausa)
                ).start()

                resposta = {
                    "status": "ok",
                    "task_id": task_id,
                    "status_key": f"despausa_{task_id}",
                    "data": data_pausa,
                    "hora": hora_pausa,
                }

            except Exception as e:
                instavel = detectar_instabilidade_auvo(e) or "instabilidade" in str(e).lower()
                print("❌ Erro fim da pausa:", e)
                resposta = {
                    "status": "auvo_instavel" if instavel else "erro",
                    "mensagem": str(e)
                }

            self._enviar_json(resposta)        # ── CRIAR CHECKIN + CHECKOUT (fluxo combinado) ───────────────────
        elif self.path == "/auvo-checkout-criar":
            try:
                content_length = int(self.headers['Content-Length'])
                dados = json.loads(self.rfile.read(content_length))
                empresa        = dados.get("empresa", "")
                codigo_cliente = dados.get("codigo_cliente")
                inicio_escolhido = dados.get("inicio_escolhido")

                codigo_cliente, opcoes_empresas = resolver_cliente_para_requisicao(empresa, codigo_cliente)
                if not codigo_cliente:
                    if opcoes_empresas:
                        self._enviar_json({
                            "status": "multiplas_empresas",
                            "empresas": opcoes_empresas,
                            "empresa": empresa,
                        })
                        return
                    raise Exception(f"Cliente '{empresa}' não encontrado.")

                mensagens = pegar_mensagens_chat()
                if not inicio_escolhido:
                    opcoes_inicio = encontrar_opcoes_inicio_atendimento(mensagens)
                    print(f">> Inícios de atendimento encontrados: {len(opcoes_inicio)}")
                    if len(opcoes_inicio) > 1:
                        self._enviar_json({
                            "status": "multiplos_inicios",
                            "inicios": opcoes_inicio,
                            "empresa": empresa,
                            "codigo_cliente": codigo_cliente,
                        })
                        return

                chave     = f"full_flow_{codigo_cliente}"
                task_status[chave] = "iniciando"

                threading.Thread(
                    target=fluxo_checkin_e_checkout,
                    args=(empresa, mensagens, codigo_cliente, inicio_escolhido)
                ).start()

                resposta = {
                    "status":         "ok",
                    "flow_key":       chave,
                    "codigo_cliente": codigo_cliente,
                }

            except Exception as e:
                instavel = detectar_instabilidade_auvo(e) or "instabilidade" in str(e).lower()
                print("❌ Erro fluxo combinado:", e)
                resposta = {
                    "status":   "auvo_instavel" if instavel else "erro",
                    "mensagem": str(e)
                }

            self._enviar_json(resposta)

    def _enviar_json(self, dados: dict):
        self.send_response(200)
        self.send_header("Content-type",                "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(dados, ensure_ascii=False).encode())


def iniciar_servidor():
    print(f"🌍 Servidor rodando em http://localhost:8765")
    ThreadingHTTPServer(("localhost", 8765), AuvoHandler).serve_forever()

threading.Thread(target=iniciar_servidor, daemon=True).start()


# ---------------------------------------------------------------------------
# Injeção do botão no Speakr
# ---------------------------------------------------------------------------
SCRIPT_BOTAO = r"""
(function() {
    // ── Notificação flutuante ─────────────────────────────────────────────
    function mostrarNotificacao(mensagem, tipo, acoes) {
        const existente = document.getElementById('auvo-notif');
        if (existente) existente.remove();

        const notif = document.createElement('div');
        notif.id = 'auvo-notif';
        const cores = { info: '#2980b9', aviso: '#e67e22', erro: '#e74c3c', sucesso: '#27ae60' };
        Object.assign(notif.style, {
            position: 'fixed', bottom: '24px', right: '24px', zIndex: '99999',
            background: cores[tipo] || '#2980b9', color: 'white',
            borderRadius: '10px', padding: '16px 20px', maxWidth: '360px',
            boxShadow: '0 6px 24px rgba(0,0,0,0.25)', fontFamily: 'sans-serif',
            fontSize: '14px', lineHeight: '1.5',
            animation: 'auvoSlideIn 0.3s ease',
        });

        // CSS de animação (injeta uma vez)
        if (!document.getElementById('auvo-style')) {
            const style = document.createElement('style');
            style.id = 'auvo-style';
            style.textContent = `
                @keyframes auvoSlideIn {
                    from { opacity:0; transform:translateY(20px); }
                    to   { opacity:1; transform:translateY(0); }
                }
            `;
            document.head.appendChild(style);
        }

        const texto = document.createElement('div');
        texto.textContent = mensagem;
        notif.appendChild(texto);

        if (acoes && acoes.length) {
            const btnWrapper = document.createElement('div');
            Object.assign(btnWrapper.style, { marginTop: '12px', display: 'flex', gap: '8px', flexWrap: 'wrap' });

            acoes.forEach(({ label, cor, callback }) => {
                const btn = document.createElement('button');
                btn.textContent = label;
                Object.assign(btn.style, {
                    padding: '6px 14px', border: 'none', borderRadius: '6px',
                    background: cor || 'rgba(255,255,255,0.25)', color: 'white',
                    cursor: 'pointer', fontWeight: 'bold', fontSize: '13px',
                });
                btn.onclick = () => { notif.remove(); callback(); };
                btnWrapper.appendChild(btn);
            });

            notif.appendChild(btnWrapper);
        } else {
            // Fecha automaticamente em 6s se não tiver botões
            setTimeout(() => { if (notif.parentNode) notif.remove(); }, 6000);
        }

        // Botão X para fechar manualmente
        const fechar = document.createElement('span');
        fechar.textContent = '✕';
        Object.assign(fechar.style, {
            position: 'absolute', top: '8px', right: '12px',
            cursor: 'pointer', opacity: '0.7', fontSize: '12px',
        });
        fechar.onclick = () => notif.remove();
        notif.style.position = 'fixed';  // garante posicionamento do X
        notif.appendChild(fechar);

        document.body.appendChild(notif);
        return notif;
    }


    // ── Seletor de tasks quando há múltiplos atendimentos abertos ────────
    function mostrarSeletorEmpresas(empresas, empresaOriginal, botao, aoSelecionar, aoCancelar) {
        const existente = document.getElementById('auvo-notif');
        if (existente) existente.remove();

        const notif = document.createElement('div');
        notif.id = 'auvo-notif';
        Object.assign(notif.style, {
            position: 'fixed', bottom: '24px', right: '24px', zIndex: '99999',
            background: '#2c3e50', color: 'white',
            borderRadius: '10px', padding: '16px 20px', maxWidth: '430px',
            boxShadow: '0 6px 24px rgba(0,0,0,0.3)', fontFamily: 'sans-serif',
            fontSize: '14px', lineHeight: '1.6',
        });

        const titulo = document.createElement('div');
        titulo.style.cssText = 'font-weight:bold; margin-bottom:10px; font-size:15px;';
        titulo.textContent = `Encontrei ${empresas.length} empresas parecidas com "${empresaOriginal}". Qual devo usar?`;
        notif.appendChild(titulo);

        empresas.forEach((empresa, idx) => {
            const btn = document.createElement('button');
            btn.textContent = `${empresa.codigo} - ${empresa.nome}`;
            Object.assign(btn.style, {
                display: 'block', width: '100%', margin: '4px 0',
                padding: '8px 12px', border: 'none', borderRadius: '6px',
                background: idx % 2 === 0 ? '#2980b9' : '#16a085',
                color: 'white', cursor: 'pointer', fontWeight: 'bold',
                textAlign: 'left', fontSize: '13px',
            });
            btn.onclick = () => {
                notif.remove();
                aoSelecionar(empresa);
            };
            notif.appendChild(btn);
        });

        const cancelar = document.createElement('button');
        cancelar.textContent = 'Cancelar';
        Object.assign(cancelar.style, {
            display: 'block', width: '100%', margin: '8px 0 0',
            padding: '6px', border: 'none', borderRadius: '6px',
            background: 'rgba(255,255,255,0.15)', color: 'white',
            cursor: 'pointer', fontSize: '13px',
        });
        cancelar.onclick = () => {
            notif.remove();
            if (aoCancelar) aoCancelar();
        };
        notif.appendChild(cancelar);

        document.body.appendChild(notif);
    }
    function mostrarSeletorInicios(inicios, botao, aoSelecionar, aoCancelar) {
        const existente = document.getElementById('auvo-notif');
        if (existente) existente.remove();

        const notif = document.createElement('div');
        notif.id = 'auvo-notif';
        Object.assign(notif.style, {
            position: 'fixed', bottom: '24px', right: '24px', zIndex: '99999',
            background: '#2c3e50', color: 'white',
            borderRadius: '10px', padding: '16px 20px', maxWidth: '450px',
            boxShadow: '0 6px 24px rgba(0,0,0,0.3)', fontFamily: 'sans-serif',
            fontSize: '14px', lineHeight: '1.6',
        });

        const titulo = document.createElement('div');
        titulo.style.cssText = 'font-weight:bold; margin-bottom:10px; font-size:15px;';
        titulo.textContent = `Encontrei ${inicios.length} possiveis inicios. Qual devo usar?`;
        notif.appendChild(titulo);

        inicios.forEach((inicio, idx) => {
            const btn = document.createElement('button');
            const texto = inicio.texto ? inicio.texto.substring(0, 70) : '';
            btn.textContent = `${inicio.data} ${inicio.hora} - ${texto}`;
            Object.assign(btn.style, {
                display: 'block', width: '100%', margin: '4px 0',
                padding: '8px 12px', border: 'none', borderRadius: '6px',
                background: idx % 2 === 0 ? '#2980b9' : '#16a085',
                color: 'white', cursor: 'pointer', fontWeight: 'bold',
                textAlign: 'left', fontSize: '13px',
            });
            btn.onclick = () => {
                notif.remove();
                aoSelecionar({ data: inicio.data, hora: inicio.hora });
            };
            notif.appendChild(btn);
        });

        const cancelar = document.createElement('button');
        cancelar.textContent = 'Cancelar';
        Object.assign(cancelar.style, {
            display: 'block', width: '100%', margin: '8px 0 0',
            padding: '6px', border: 'none', borderRadius: '6px',
            background: 'rgba(255,255,255,0.15)', color: 'white',
            cursor: 'pointer', fontSize: '13px',
        });
        cancelar.onclick = () => {
            notif.remove();
            if (aoCancelar) aoCancelar();
        };
        notif.appendChild(cancelar);

        document.body.appendChild(notif);
    }
    function mostrarSeletorTasks(tasks, hora, empresa, botaoOut, codigoCliente) {
        const existente = document.getElementById('auvo-notif');
        if (existente) existente.remove();

        const notif = document.createElement('div');
        notif.id = 'auvo-notif';
        Object.assign(notif.style, {
            position: 'fixed', bottom: '24px', right: '24px', zIndex: '99999',
            background: '#2c3e50', color: 'white',
            borderRadius: '10px', padding: '16px 20px', maxWidth: '400px',
            boxShadow: '0 6px 24px rgba(0,0,0,0.3)', fontFamily: 'sans-serif',
            fontSize: '14px', lineHeight: '1.6',
        });

        const titulo = document.createElement('div');
        titulo.style.cssText = 'font-weight:bold; margin-bottom:10px; font-size:15px;';
        titulo.textContent = `⚠️ Encontrei ${tasks.length} atendimentos abertos para ${empresa}. Qual devo finalizar?`;
        notif.appendChild(titulo);

        tasks.forEach((t, idx) => {
            const btn = document.createElement('button');
            const hora_exibida = t.hora ? t.hora.substring(0, 5) : '??:??';
            const desc = t.descricao ? t.descricao.substring(0, 60) : '';
            btn.textContent = `#${t.taskID} — ${hora_exibida}h  ${desc}`;
            Object.assign(btn.style, {
                display: 'block', width: '100%', margin: '4px 0',
                padding: '8px 12px', border: 'none', borderRadius: '6px',
                background: idx % 2 === 0 ? '#2980b9' : '#16a085',
                color: 'white', cursor: 'pointer', fontWeight: 'bold',
                textAlign: 'left', fontSize: '13px',
            });
            btn.onclick = () => {
                notif.remove();
                // Dispara checkout com a task escolhida
                setBotao(botaoOut, `📤 Checkout Task #${t.taskID}...`, '#e67e22', true);
                fetch('http://localhost:8765/auvo-checkout', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        empresa:          empresa,
                        task_id_escolhido: t.taskID,
                        codigo_cliente:   codigoCliente,
                    }),
                })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'auvo_instavel') {
                        setBotao(botaoOut, '⚠️ Auvo com Instabilidade', '#c0392b', false);
                        mostrarNotificacao('O Auvo ficou instável durante o check-out.', 'erro');
                        resetBotaoCheckout(botaoOut);
                        return;
                    }
                    if (data.status !== 'ok') throw new Error(data.mensagem || 'Erro desconhecido');

                    iniciarPolling(`checkout_${data.task_id}`, 1000, (s) => {
                        if (s.status === 'auvo_instavel') {
                            setBotao(botaoOut, '⚠️ Auvo com Instabilidade', '#c0392b', false);
                            mostrarNotificacao('Auvo instável durante check-out. Tente novamente.', 'erro');
                            resetBotaoCheckout(botaoOut);
                            return true;
                        }
                        if (s.status === 'relato_ok') {
                            setBotao(botaoOut, '📋 Lançando Check-Out...', '#2980b9', true);
                        }
                        if (s.status === 'done') {
                            setBotao(botaoOut, `✓ Check-Out ${data.hora}`, '#27ae60', true);
                            mostrarNotificacao(`✅ Check-out da Task #${data.task_id} salvo com sucesso!`, 'sucesso');
                            resetBotaoCheckout(botaoOut);
                            return true;
                        }
                        return false;
                    });
                })
                .catch(err => {
                    setBotao(botaoOut, '❌ Erro!', '#e74c3c', false);
                    mostrarNotificacao(`Erro: ${err.message}`, 'erro');
                    resetBotaoCheckout(botaoOut);
                });
            };
            notif.appendChild(btn);
        });

        const cancelar = document.createElement('button');
        cancelar.textContent = '✕ Cancelar';
        Object.assign(cancelar.style, {
            display: 'block', width: '100%', margin: '8px 0 0',
            padding: '6px', border: 'none', borderRadius: '6px',
            background: 'rgba(255,255,255,0.15)', color: 'white',
            cursor: 'pointer', fontSize: '13px',
        });
        cancelar.onclick = () => { notif.remove(); resetBotaoCheckout(botaoOut); };
        notif.appendChild(cancelar);

        document.body.appendChild(notif);
    }

    // ── Helpers de UI dos botões ──────────────────────────────────────────
    const AUVO_ICONS = {
        plane: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4Z"/></svg>',
        checkout: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>',
        pause: '<svg class="auvo-pause-neon" viewBox="0 0 24 24" aria-hidden="true"><rect x="4.7" y="3.4" width="5.8" height="17.2" rx="2.9"/><rect x="13.5" y="3.4" width="5.8" height="17.2" rx="2.9"/></svg>',
        play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7Z"/></svg>',
        check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
        error: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>',
        warning: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2 20h20Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>',
        lock: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>'
    };

    function garantirEstiloBotoesAuvo() {
        if (document.getElementById('auvo-botoes-style')) return;
        const style = document.createElement('style');
        style.id = 'auvo-botoes-style';
        style.textContent = `
            #botao-auvo .auvo-icon, #botao-auvo-checkout .auvo-icon, #botao-auvo-pausa .auvo-icon, #botao-auvo-despausa .auvo-icon {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 22px;
                height: 22px;
            }
            #botao-auvo .auvo-icon svg, #botao-auvo-checkout .auvo-icon svg, #botao-auvo-pausa .auvo-icon svg, #botao-auvo-despausa .auvo-icon svg {
                width: 22px;
                height: 22px;
                stroke: currentColor;
                stroke-width: 2;
                stroke-linecap: round;
                stroke-linejoin: round;
                fill: none;
            }
            #botao-auvo-pausa .auvo-icon svg.auvo-pause-neon {
                width: 22px;
                height: 22px;
                fill: none;
                stroke: currentColor;
                stroke-width: 2;
                stroke-linecap: round;
                stroke-linejoin: round;
            }
            #botao-auvo .auvo-progress, #botao-auvo-checkout .auvo-progress, #botao-auvo-pausa .auvo-progress, #botao-auvo-despausa .auvo-progress {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 34px;
                height: 34px;
                border-radius: 50%;
            }
            #botao-auvo .auvo-progress-inner, #botao-auvo-checkout .auvo-progress-inner, #botao-auvo-pausa .auvo-progress-inner, #botao-auvo-despausa .auvo-progress-inner {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 26px;
                height: 26px;
                border-radius: 50%;
                background: rgba(16,20,25,0.92);
                color: white;
                font: 700 9px/1 Arial, sans-serif;
            }
        `;
        document.head.appendChild(style);
    }

    function limparProgressoBotao(botao) {
        if (botao && botao.auvoProgressTimer) {
            clearInterval(botao.auvoProgressTimer);
            botao.auvoProgressTimer = null;
        }
    }

    function tipoBotao(botao) {
        if (botao && botao.id === 'botao-auvo-checkout') return 'checkout';
        if (botao && botao.id === 'botao-auvo-pausa') return 'pausa';
        if (botao && botao.id === 'botao-auvo-despausa') return 'despausa';
        return 'checkin';
    }

    function aplicarIconeBotao(botao, icone, label, cor, disabled) {
        limparProgressoBotao(botao);
        botao.innerHTML = `<span class="auvo-icon">${icone}</span>`;
        botao.style.backgroundColor = 'rgba(18,22,28,0.72)';
        botao.style.color = cor;
        botao.style.borderColor = cor;
        botao.disabled = disabled;
        botao.style.cursor = disabled ? 'not-allowed' : 'pointer';
        botao.style.opacity = disabled ? '0.78' : '1';
        botao.style.boxShadow = disabled ? 'none' : `0 0 0 1px ${cor}22, 0 4px 12px rgba(0,0,0,0.18)`;
        botao.style.transform = 'translateY(0)';
        botao.title = label;
        botao.setAttribute('aria-label', label);
    }

    // Percentuais fixos por etapa de cada fluxo
    const AUVO_ETAPAS_PROGRESSO = {
        // checkin
        'analisando':         10,
        'gerando_descricao':  30,
        'criando_task':       55,
        'checkin':            75,
        'fazendo_checkin':    75,
        // checkout
        'gerando_relato':     30,
        'relato_ok':          55,
        'checkout':           75,
        'lancando_checkout':  75,
        // fluxo completo
        'iniciando':          10,
        'criando_atendimento':40,
        // pausa
        'iniciando_pausa':    12,
        'abrindo_task':       35,
        'preenchendo_pausa':  62,
        'lancando_pausa':     82,
        'preenchendo_despausa': 62,
        'lancando_despausa':  82,
        // genérico
        'done':               100,
    };

    function atualizarProgressoCircular(botao, percentual, cor, label) {
        const p = Math.max(0, Math.min(100, Math.round(percentual)));
        const innerContent = `${p}%`;
        botao.innerHTML = `
            <span class="auvo-progress" style="background: conic-gradient(${cor} ${p * 3.6}deg, rgba(255,255,255,0.18) 0deg);">
                <span class="auvo-progress-inner">${innerContent}</span>
            </span>`;
        botao.title = label;
        botao.setAttribute('aria-label', `${label} ${p}%`);
    }

    function definirProgressoBotao(botao, percentual, cor, label) {
        limparProgressoBotao(botao);
        botao.style.backgroundColor = 'rgba(18,22,28,0.72)';
        botao.style.color = cor;
        botao.style.borderColor = cor;
        botao.disabled = true;
        botao.blur();
        botao.style.cursor = 'not-allowed';
        botao.style.opacity = '1';
        botao.style.boxShadow = 'none';
        botao.dataset.auvoProgress = String(percentual);
        atualizarProgressoCircular(botao, percentual, cor, label);
    }

    function iniciarProgressoCircular(botao, label, cor) {
        // Começa em 8% caso não tenha etapa definida — avança lentamente até 25%
        const atual = Math.max(8, Number(botao.dataset.auvoProgress || 0));
        definirProgressoBotao(botao, atual, cor, label);
        // Avança suavemente apenas até 25% enquanto aguarda a primeira etapa real
        botao.auvoProgressTimer = setInterval(() => {
            const p = Number(botao.dataset.auvoProgress || 0);
            if (p < 25) {
                const novo = Math.min(25, p + 3);
                botao.dataset.auvoProgress = String(novo);
                atualizarProgressoCircular(botao, novo, cor, label);
            }
        }, 800);
    }

    function setBotao(botao, texto, cor, disabled, percentualFixo) {
        const tipo = tipoBotao(botao);
        const label = texto || (tipo === 'checkout' ? 'Check-out' : tipo === 'pausa' ? 'Iniciar pausa' : tipo === 'despausa' ? 'Fim da pausa' : 'Lançar atendimento');
        const normalIcon = tipo === 'checkout' ? AUVO_ICONS.checkout : tipo === 'pausa' ? AUVO_ICONS.pause : tipo === 'despausa' ? AUVO_ICONS.play : AUVO_ICONS.plane;
        const lower = label.toLowerCase();

        if (lower.includes('salvo') || lower.includes('finalizado') || lower.includes('conclu')) {
            limparProgressoBotao(botao);
            botao.dataset.auvoProgress = '100';
            atualizarProgressoCircular(botao, 100, cor, label);
            setTimeout(() => aplicarIconeBotao(botao, AUVO_ICONS.check, label, cor, true), 450);
            return;
        }

        if (!disabled) {
            delete botao.dataset.auvoProgress;
        }

        if (lower.includes('erro')) {
            aplicarIconeBotao(botao, AUVO_ICONS.error, label, cor, disabled);
            return;
        }
        if (lower.includes('instabilidade') || lower.includes('pausado')) {
            aplicarIconeBotao(botao, AUVO_ICONS.warning, label, cor, disabled);
            return;
        }
        if (lower.includes('aguarde')) {
            aplicarIconeBotao(botao, AUVO_ICONS.lock, label, cor, disabled);
            return;
        }
        if (disabled) {
            if (percentualFixo !== undefined) {
                // Etapa real conhecida: avança diretamente para o percentual correto
                const anterior = Number(botao.dataset.auvoProgress || 0);
                const alvo = Math.max(anterior, percentualFixo);
                definirProgressoBotao(botao, alvo, cor, label);
            } else {
                iniciarProgressoCircular(botao, label, cor);
            }
            return;
        }

        aplicarIconeBotao(botao, normalIcon, label, cor, false);
    }
    function botaoCheckin() {
        return document.getElementById('botao-auvo');
    }

    function botaoCheckout() {
        return document.getElementById('botao-auvo-checkout');
    }

    function botaoPausa() {
        return document.getElementById('botao-auvo-pausa');
    }

    function botaoDespausa() {
        return document.getElementById('botao-auvo-despausa');
    }

    function bloquearAcaoOposta(tipo) {
        const checkin = botaoCheckin();
        const checkout = botaoCheckout();
        const pausa = botaoPausa();
        const despausa = botaoDespausa();
        window.auvoOperacaoAtiva = tipo;

        const bloqueados = [
            [checkin, 'Check-in'],
            [checkout, 'Check-out'],
            [pausa, 'Pausa'],
            [despausa, 'Despausar'],
        ].filter(([btn]) => btn && tipoBotao(btn) !== tipo);
        [checkin, checkout, pausa, despausa].forEach(btn => btn && btn.blur());

        bloqueados.forEach(([btn, nome]) => {
            setBotao(btn, `Aguarde ${tipo === 'checkin' ? 'o Check-in' : tipo === 'checkout' ? 'o Check-out' : 'a Pausa'}...`, '#7f8c8d', true);
            btn.title = `${nome} bloqueado enquanto a ação atual está em andamento.`;
        });    }

    function liberarAcoes() {
        window.auvoOperacaoAtiva = null;
        const checkin = botaoCheckin();
        const checkout = botaoCheckout();
        const pausa = botaoPausa();
        const despausa = botaoDespausa();
        if (checkin) setBotao(checkin, 'Lançar atendimento no Auvo', '#2ecc71', false);
        if (checkout) setBotao(checkout, 'Fazer check-out', '#e67e22', false);
        if (pausa) setBotao(pausa, 'Iniciar pausa', '#8e44ad', false);
        if (despausa) setBotao(despausa, 'Fim da pausa', '#16a085', false);
    }

    function resetBotaoCheckin(botao) {
        setTimeout(liberarAcoes, 5000);
    }

    function resetBotaoCheckout(botao) {
        setTimeout(liberarAcoes, 5000);
    }

    function resetBotaoPausa(botao) {
        setTimeout(liberarAcoes, 5000);
    }

    function resetBotaoDespausa(botao) {
        setTimeout(liberarAcoes, 5000);
    }

    // ── Polling de status ─────────────────────────────────────────────────
    function iniciarPolling(chave, intervaloMs, onStatus) {
        const intervalo = setInterval(async () => {
            try {
                const res = await fetch(`http://localhost:8765/status/${chave}`);
                const s   = await res.json();
                const parar = onStatus(s);
                if (parar) clearInterval(intervalo);
            } catch(e) { clearInterval(intervalo); }
        }, intervaloMs);
        return intervalo;
    }

    // ── Fluxo de checkout simples ─────────────────────────────────────────
    function iniciarFluxoCheckout(botaoOut, textoEmpresa, codigoCliente) {
        bloquearAcaoOposta('checkout');
        setBotao(botaoOut, 'Gerando relato...', '#8e44ad', true, AUVO_ETAPAS_PROGRESSO['gerando_relato']);

        fetch('http://localhost:8765/auvo-checkout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ empresa: textoEmpresa, codigo_cliente: codigoCliente || null }),
        })
        .then(r => r.json())
        .then(data => {
            // ── Sem atendimento aberto ────────────────────────────────────
            if (data.status === 'multiplas_empresas') {
                setBotao(botaoOut, 'Check-Out', '#e67e22', false);
                mostrarSeletorEmpresas(
                    data.empresas,
                    textoEmpresa,
                    botaoOut,
                    (empresaEscolhida) => iniciarFluxoCheckout(botaoOut, textoEmpresa, empresaEscolhida.codigo),
                    () => resetBotaoCheckout(botaoOut)
                );
                return;
            }
            if (data.status === 'sem_atendimento') {
                setBotao(botaoOut, 'Check-Out', '#e67e22', false);
                mostrarNotificacao(
                    '⚠️ Não foi encontrado um atendimento aberto para esta empresa hoje.\nDeseja criar o Check-in e já fazer o Check-out?',
                    'aviso',
                    [
                        {
                            label: '✅ Criar e finalizar',
                            cor: '#27ae60',
                            callback: () => iniciarFluxoCompleto(botaoOut, textoEmpresa, data.codigo_cliente),
                        },
                        {
                            label: '❌ Cancelar',
                            cor: 'rgba(255,255,255,0.2)',
                            callback: () => liberarAcoes(),
                        },
                    ]
                );
                return;
            }

            if (data.status === 'atendimento_pausado') {
                setBotao(botaoOut, 'Atendimento pausado', '#f39c12', true);
                mostrarNotificacao(
                    data.mensagem || 'Atendimento pausado. Retome o atendimento no Auvo antes de fazer o check-out.',
                    'aviso'
                );
                resetBotaoCheckout(botaoOut);
                return;
            }

            // ── Múltiplos atendimentos abertos: pede escolha ─────────────
            if (data.status === 'multiplas_tasks') {
                setBotao(botaoOut, 'Check-Out', '#e67e22', false);
                mostrarSeletorTasks(data.tasks, data.hora, textoEmpresa, botaoOut, data.codigo_cliente);
                return;
            }

            // ── Auvo com instabilidade ────────────────────────────────────
            if (data.status === 'auvo_instavel') {
                setBotao(botaoOut, '⚠️ Auvo com Instabilidade', '#c0392b', false);
                mostrarNotificacao('O Auvo está com instabilidade no momento. Tente novamente em alguns instantes.', 'erro');
                resetBotaoCheckout(botaoOut);
                return;
            }

            if (data.status !== 'ok') {
                throw new Error(data.mensagem || 'Erro desconhecido');
            }

            // ── Polling do checkout ───────────────────────────────────────
            iniciarPolling(`checkout_${data.task_id}`, 1000, (s) => {
                if (s.status === 'auvo_instavel') {
                    setBotao(botaoOut, '⚠️ Auvo com Instabilidade', '#c0392b', false);
                    mostrarNotificacao('O Auvo ficou instável durante o check-out. Verifique e tente novamente.', 'erro');
                    resetBotaoCheckout(botaoOut);
                    return true; // para polling
                }
                if (s.status === 'relato_ok') {
                    setBotao(botaoOut, 'Lançando Check-Out...', '#2980b9', true, AUVO_ETAPAS_PROGRESSO['relato_ok']);
                }
                if (s.status === 'done') {
                    setBotao(botaoOut, `Check-Out salvo ${data.hora}`, '#27ae60', true);
                    mostrarNotificacao(`Check-out concluído com sucesso às ${data.hora}.`, 'sucesso');
                    resetBotaoCheckout(botaoOut);
                    return true;
                }
                return false;
            });
        })
        .catch(err => {
            setBotao(botaoOut, '❌ Erro!', '#e74c3c', false);
            mostrarNotificacao(`Erro no checkout: ${err.message}`, 'erro');
            resetBotaoCheckout(botaoOut);
        });
    }

    // ── Fluxo completo (sem checkin → cria checkin + checkout) ───────────
    function iniciarFluxoCompleto(botaoOut, textoEmpresa, codigoCliente, inicioEscolhido) {
        bloquearAcaoOposta('checkout');
        setBotao(botaoOut, 'Criando atendimento...', '#8e44ad', true, AUVO_ETAPAS_PROGRESSO['iniciando']);

        fetch('http://localhost:8765/auvo-checkout-criar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ empresa: textoEmpresa, codigo_cliente: codigoCliente, inicio_escolhido: inicioEscolhido || null }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'multiplas_empresas') {
                setBotao(botaoOut, 'Check-Out', '#e67e22', false);
                mostrarSeletorEmpresas(
                    data.empresas,
                    textoEmpresa,
                    botaoOut,
                    (empresaEscolhida) => iniciarFluxoCompleto(botaoOut, textoEmpresa, empresaEscolhida.codigo),
                    () => resetBotaoCheckout(botaoOut)
                );
                return;
            }
            if (data.status === 'multiplos_inicios') {
                setBotao(botaoOut, 'Check-Out', '#e67e22', false);
                mostrarSeletorInicios(
                    data.inicios,
                    botaoOut,
                    (inicioEscolhido) => iniciarFluxoCompleto(botaoOut, textoEmpresa, data.codigo_cliente || codigoCliente, inicioEscolhido),
                    () => resetBotaoCheckout(botaoOut)
                );
                return;
            }
            if (data.status === 'auvo_instavel') {
                setBotao(botaoOut, '⚠️ Auvo com Instabilidade', '#c0392b', false);
                mostrarNotificacao('O Auvo está com instabilidade. Tente novamente em instantes.', 'erro');
                resetBotaoCheckout(botaoOut);
                return;
            }
            if (data.status !== 'ok') throw new Error(data.mensagem || 'Erro desconhecido');

            const flowKey = data.flow_key;

            // Labels por etapa do fluxo combinado
            const etapas = {
                'iniciando':          ['Iniciando...', '#8e44ad',       AUVO_ETAPAS_PROGRESSO['iniciando']],
                'gerando_descricao':  ['Gerando descrição...', '#8e44ad', AUVO_ETAPAS_PROGRESSO['gerando_descricao']],
                'criando_task':       ['Criando task...', '#2980b9',    AUVO_ETAPAS_PROGRESSO['criando_task']],
                'checkin':            ['Fazendo Check-in...', '#2980b9', AUVO_ETAPAS_PROGRESSO['checkin']],
                'gerando_relato':     ['Gerando relato...', '#8e44ad',  AUVO_ETAPAS_PROGRESSO['gerando_relato']],
                'checkout':           ['Fazendo Check-out...', '#e67e22', AUVO_ETAPAS_PROGRESSO['checkout']],
            };

            iniciarPolling(flowKey, 1000, (s) => {
                if (s.status === 'auvo_instavel') {
                    setBotao(botaoOut, '⚠️ Auvo com Instabilidade', '#c0392b', false);
                    mostrarNotificacao('O Auvo ficou instável durante o processo. Verifique e tente novamente.', 'erro');
                    resetBotaoCheckout(botaoOut);
                    return true;
                }
                if (etapas[s.status]) {
                    const [label, cor, pct] = etapas[s.status];
                    setBotao(botaoOut, label, cor, true, pct);
                }
                if (s.status === 'done') {
                    const tid = s.task_id || '';
                    setBotao(botaoOut, `Atendimento finalizado #${tid}`, '#27ae60', true);
                    mostrarNotificacao(`Atendimento criado, check-in e check-out concluídos. Task #${tid}.`, 'sucesso');
                    resetBotaoCheckout(botaoOut);
                    return true;
                }
                return false;
            });
        })
        .catch(err => {
            setBotao(botaoOut, '❌ Erro!', '#e74c3c', false);
            mostrarNotificacao(`Erro no fluxo completo: ${err.message}`, 'erro');
            resetBotaoCheckout(botaoOut);
        });
    }

    const AUVO_MOTIVOS_PAUSA = [
        'Fim de Expediente',
        'Almoço',
        'Ausência do cliente',
        'Aguardando retorno dos desenvolvedores',
        'Aguardando retorno cliente',
        'Período para agendar retorno',
    ];

    function obterDadosEmpresaAtual() {
        const spans = Array.from(document.querySelectorAll('span'));
        const spanEmpresa = spans.find(el => el.textContent.trim() === 'Empresa');
        return spanEmpresa && spanEmpresa.nextElementSibling
            ? spanEmpresa.nextElementSibling.innerText : 'Desconhecida';
    }

    function abrirModalPausa(aoConfirmar, aoCancelar) {
        const existente = document.getElementById('auvo-modal-pausa');
        if (existente) existente.remove();

        const agora = new Date();
        const dataHoje = `${agora.getFullYear()}-${String(agora.getMonth() + 1).padStart(2, '0')}-${String(agora.getDate()).padStart(2, '0')}`;
        const horaAgora = `${String(agora.getHours()).padStart(2, '0')}:${String(agora.getMinutes()).padStart(2, '0')}`;

        const overlay = document.createElement('div');
        overlay.id = 'auvo-modal-pausa';
        Object.assign(overlay.style, {
            position: 'fixed', inset: '0', zIndex: '100000',
            background: 'rgba(6,10,16,0.55)', display: 'flex',
            alignItems: 'center', justifyContent: 'center', fontFamily: 'sans-serif',
        });

        const modal = document.createElement('div');
        Object.assign(modal.style, {
            width: '360px', maxWidth: 'calc(100vw - 32px)', background: '#1f2933',
            color: 'white', borderRadius: '10px', padding: '18px',
            boxShadow: '0 20px 60px rgba(0,0,0,0.35)', border: '1px solid rgba(255,255,255,0.08)',
        });

        const titulo = document.createElement('div');
        titulo.textContent = 'Início da pausa';
        titulo.style.cssText = 'font-weight:700; font-size:16px; margin-bottom:14px;';
        modal.appendChild(titulo);

        const criarLabel = (texto) => {
            const label = document.createElement('label');
            label.textContent = texto;
            label.style.cssText = 'display:block; font-size:12px; opacity:.82; margin:10px 0 5px;';
            return label;
        };
        const estiloCampo = {
            width: '100%', boxSizing: 'border-box', background: '#111827', color: 'white',
            border: '1px solid rgba(255,255,255,0.16)', borderRadius: '7px', padding: '9px 10px',
            outline: 'none', fontSize: '14px',
        };

        const linha = document.createElement('div');
        linha.style.cssText = 'display:grid; grid-template-columns: 1fr 110px; gap:10px;';

        const grupoData = document.createElement('div');
        grupoData.appendChild(criarLabel('Data'));
        const inputData = document.createElement('input');
        inputData.type = 'date';
        inputData.value = dataHoje;
        Object.assign(inputData.style, estiloCampo);
        grupoData.appendChild(inputData);
        linha.appendChild(grupoData);

        const grupoHora = document.createElement('div');
        grupoHora.appendChild(criarLabel('Hora'));
        const inputHora = document.createElement('input');
        inputHora.type = 'time';
        inputHora.value = horaAgora;
        Object.assign(inputHora.style, estiloCampo);
        grupoHora.appendChild(inputHora);
        linha.appendChild(grupoHora);
        modal.appendChild(linha);

        modal.appendChild(criarLabel('Motivo'));
        const selectMotivo = document.createElement('select');
        Object.assign(selectMotivo.style, estiloCampo);
        AUVO_MOTIVOS_PAUSA.forEach(motivo => {
            const opt = document.createElement('option');
            opt.value = motivo;
            opt.textContent = motivo;
            selectMotivo.appendChild(opt);
        });
        modal.appendChild(selectMotivo);

        const botoes = document.createElement('div');
        botoes.style.cssText = 'display:flex; justify-content:flex-end; gap:8px; margin-top:16px;';

        const cancelar = document.createElement('button');
        cancelar.textContent = 'Cancelar';
        Object.assign(cancelar.style, {
            padding: '8px 12px', borderRadius: '7px', border: '1px solid rgba(255,255,255,0.12)',
            background: 'transparent', color: 'white', cursor: 'pointer',
        });
        cancelar.onclick = () => {
            overlay.remove();
            if (aoCancelar) aoCancelar();
        };

        const confirmar = document.createElement('button');
        confirmar.textContent = 'Salvar pausa';
        Object.assign(confirmar.style, {
            padding: '8px 12px', borderRadius: '7px', border: 'none',
            background: '#8e44ad', color: 'white', cursor: 'pointer', fontWeight: '700',
        });
        confirmar.onclick = () => {
            if (!inputData.value || !inputHora.value || !selectMotivo.value) {
                mostrarNotificacao('Preencha data, hora e motivo da pausa.', 'aviso');
                return;
            }
            overlay.remove();
            aoConfirmar({ data_pausa: inputData.value, hora_pausa: inputHora.value, motivo: selectMotivo.value });
        };

        botoes.appendChild(cancelar);
        botoes.appendChild(confirmar);
        modal.appendChild(botoes);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
    }

    function abrirModalDespausa(aoConfirmar, aoCancelar) {
        const existente = document.getElementById('auvo-modal-despausa');
        if (existente) existente.remove();

        const agora = new Date();
        const dataHoje = `${agora.getFullYear()}-${String(agora.getMonth() + 1).padStart(2, '0')}-${String(agora.getDate()).padStart(2, '0')}`;
        const horaAgora = `${String(agora.getHours()).padStart(2, '0')}:${String(agora.getMinutes()).padStart(2, '0')}`;

        const overlay = document.createElement('div');
        overlay.id = 'auvo-modal-despausa';
        Object.assign(overlay.style, {
            position: 'fixed', inset: '0', zIndex: '100000',
            background: 'rgba(6,10,16,0.55)', display: 'flex',
            alignItems: 'center', justifyContent: 'center', fontFamily: 'sans-serif',
        });

        const modal = document.createElement('div');
        Object.assign(modal.style, {
            width: '320px', background: '#111820', color: 'white', borderRadius: '8px',
            boxShadow: '0 18px 50px rgba(0,0,0,0.35)', padding: '18px',
            border: '1px solid rgba(255,255,255,0.14)',
        });

        const titulo = document.createElement('div');
        titulo.textContent = 'Fim da pausa';
        titulo.style.cssText = 'font-weight:700; font-size:16px; margin-bottom:14px;';
        modal.appendChild(titulo);

        const criarLabel = (texto) => {
            const label = document.createElement('label');
            label.textContent = texto;
            label.style.cssText = 'display:block; font-size:12px; opacity:.8; margin:10px 0 4px;';
            return label;
        };

        modal.appendChild(criarLabel('Data'));
        const inputData = document.createElement('input');
        inputData.type = 'date';
        inputData.value = dataHoje;
        inputData.style.cssText = 'width:100%; box-sizing:border-box; padding:8px; border-radius:6px; border:1px solid #34495e; background:#0b1117; color:white;';
        modal.appendChild(inputData);

        modal.appendChild(criarLabel('Hora'));
        const inputHora = document.createElement('input');
        inputHora.type = 'time';
        inputHora.value = horaAgora;
        inputHora.style.cssText = 'width:100%; box-sizing:border-box; padding:8px; border-radius:6px; border:1px solid #34495e; background:#0b1117; color:white;';
        modal.appendChild(inputHora);

        const botoes = document.createElement('div');
        botoes.style.cssText = 'display:flex; gap:10px; justify-content:flex-end; margin-top:16px;';

        const cancelar = document.createElement('button');
        cancelar.textContent = 'Cancelar';
        Object.assign(cancelar.style, { padding: '8px 12px', borderRadius: '7px', border: 'none', background: 'rgba(255,255,255,0.14)', color: 'white', cursor: 'pointer' });
        cancelar.onclick = () => { overlay.remove(); aoCancelar && aoCancelar(); };

        const confirmar = document.createElement('button');
        confirmar.textContent = 'Salvar fim';
        Object.assign(confirmar.style, { padding: '8px 12px', borderRadius: '7px', border: 'none', background: '#16a085', color: 'white', cursor: 'pointer', fontWeight: '700' });
        confirmar.onclick = () => {
            if (!inputData.value || !inputHora.value) {
                mostrarNotificacao('Preencha data e hora do fim da pausa.', 'aviso');
                return;
            }
            overlay.remove();
            aoConfirmar({ data_pausa: inputData.value, hora_pausa: inputHora.value });
        };

        botoes.appendChild(cancelar);
        botoes.appendChild(confirmar);
        modal.appendChild(botoes);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
    }
    function mostrarSeletorTasksPausa(tasks, empresa, botaoPausa, payloadBase) {
        const existente = document.getElementById('auvo-notif');
        if (existente) existente.remove();

        const notif = document.createElement('div');
        notif.id = 'auvo-notif';
        Object.assign(notif.style, {
            position: 'fixed', bottom: '24px', right: '24px', zIndex: '99999',
            background: '#2c3e50', color: 'white', borderRadius: '10px', padding: '16px 20px',
            maxWidth: '400px', boxShadow: '0 6px 24px rgba(0,0,0,0.3)',
            fontFamily: 'sans-serif', fontSize: '14px', lineHeight: '1.6',
        });

        const titulo = document.createElement('div');
        titulo.style.cssText = 'font-weight:bold; margin-bottom:10px; font-size:15px;';
        titulo.textContent = `Encontrei ${tasks.length} atendimentos abertos para ${empresa}. Qual devo pausar?`;
        notif.appendChild(titulo);

        tasks.forEach((t, idx) => {
            const btn = document.createElement('button');
            const hora = t.hora ? t.hora.substring(0, 5) : '??:??';
            const desc = t.descricao ? t.descricao.substring(0, 60) : '';
            btn.textContent = `#${t.taskID} - ${hora}h  ${desc}`;
            Object.assign(btn.style, {
                display: 'block', width: '100%', margin: '4px 0', padding: '8px 12px',
                border: 'none', borderRadius: '6px', background: idx % 2 === 0 ? '#2980b9' : '#16a085',
                color: 'white', cursor: 'pointer', fontWeight: 'bold', textAlign: 'left', fontSize: '13px',
            });
            btn.onclick = () => {
                notif.remove();
                iniciarFluxoPausa(botaoPausa, empresa, { ...payloadBase, task_id_escolhido: t.taskID });
            };
            notif.appendChild(btn);
        });

        const cancelar = document.createElement('button');
        cancelar.textContent = 'Cancelar';
        Object.assign(cancelar.style, {
            display: 'block', width: '100%', margin: '8px 0 0', padding: '6px',
            border: 'none', borderRadius: '6px', background: 'rgba(255,255,255,0.15)',
            color: 'white', cursor: 'pointer', fontSize: '13px',
        });
        cancelar.onclick = () => { notif.remove(); resetBotaoPausa(botaoPausa); };
        notif.appendChild(cancelar);
        document.body.appendChild(notif);
    }

    function mostrarSeletorTasksDespausa(tasks, empresa, botaoDespausa, payloadBase) {
        const existente = document.getElementById('auvo-notif');
        if (existente) existente.remove();

        const notif = document.createElement('div');
        notif.id = 'auvo-notif';
        Object.assign(notif.style, {
            position: 'fixed', top: '20px', right: '20px', zIndex: '99999',
            width: '330px', background: '#111820', color: 'white', borderRadius: '8px',
            boxShadow: '0 14px 36px rgba(0,0,0,0.35)', padding: '14px', fontFamily: 'sans-serif',
            border: '1px solid rgba(255,255,255,0.14)',
        });

        const titulo = document.createElement('div');
        titulo.textContent = `Encontrei ${tasks.length} atendimentos pausados para ${empresa}. Qual devo despausar?`;
        titulo.style.cssText = 'font-weight:700; font-size:13px; margin-bottom:10px;';
        notif.appendChild(titulo);

        tasks.forEach((t) => {
            const btn = document.createElement('button');
            const hora = t.hora ? t.hora.substring(0, 5) : '??:??';
            const desc = t.descricao ? t.descricao.substring(0, 60) : '';
            btn.textContent = `#${t.taskID} - ${hora} - ${desc}`;
            Object.assign(btn.style, {
                display: 'block', width: '100%', margin: '6px 0', padding: '8px',
                border: '1px solid rgba(255,255,255,0.16)', borderRadius: '6px',
                background: 'rgba(255,255,255,0.08)', color: 'white', cursor: 'pointer', textAlign: 'left',
            });
            btn.onclick = () => {
                notif.remove();
                iniciarFluxoDespausa(botaoDespausa, empresa, { ...payloadBase, task_id_escolhido: t.taskID });
            };
            notif.appendChild(btn);
        });

        const cancelar = document.createElement('button');
        cancelar.textContent = 'Cancelar';
        Object.assign(cancelar.style, { display: 'block', width: '100%', margin: '8px 0 0', padding: '7px', border: 'none', borderRadius: '6px', background: 'rgba(255,255,255,0.15)', color: 'white', cursor: 'pointer' });
        cancelar.onclick = () => { notif.remove(); resetBotaoDespausa(botaoDespausa); };
        notif.appendChild(cancelar);
        document.body.appendChild(notif);
    }

    function iniciarFluxoDespausa(botaoDespausa, textoEmpresa, dadosDespausa) {
        bloquearAcaoOposta('despausa');
        setBotao(botaoDespausa, 'Finalizando pausa...', '#16a085', true, AUVO_ETAPAS_PROGRESSO['iniciando_pausa']);

        fetch('http://localhost:8765/auvo-despausa', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ empresa: textoEmpresa, ...(dadosDespausa || {}) }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'multiplas_empresas') {
                setBotao(botaoDespausa, 'Fim da pausa', '#16a085', false);
                mostrarSeletorEmpresas(
                    data.empresas,
                    textoEmpresa,
                    botaoDespausa,
                    (empresaEscolhida) => iniciarFluxoDespausa(botaoDespausa, textoEmpresa, { ...(dadosDespausa || {}), codigo_cliente: empresaEscolhida.codigo }),
                    () => resetBotaoDespausa(botaoDespausa)
                );
                return;
            }
            if (data.status === 'multiplas_tasks') {
                setBotao(botaoDespausa, 'Fim da pausa', '#16a085', false);
                mostrarSeletorTasksDespausa(data.tasks, textoEmpresa, botaoDespausa, { ...(dadosDespausa || {}), codigo_cliente: data.codigo_cliente });
                return;
            }
            if (data.status === 'sem_atendimento') {
                setBotao(botaoDespausa, 'Fim da pausa', '#16a085', false);
                mostrarNotificacao(data.mensagem || 'Nenhum atendimento pausado encontrado.', 'aviso');
                resetBotaoDespausa(botaoDespausa);
                return;
            }
            if (data.status === 'auvo_instavel') {
                setBotao(botaoDespausa, 'Auvo com Instabilidade', '#c0392b', false);
                mostrarNotificacao('O Auvo está com instabilidade. Tente novamente em instantes.', 'erro');
                resetBotaoDespausa(botaoDespausa);
                return;
            }
            if (data.status !== 'ok') throw new Error(data.mensagem || 'Erro desconhecido');

            setBotao(botaoDespausa, 'Abrindo task...', '#16a085', true, AUVO_ETAPAS_PROGRESSO['abrindo_task']);
            iniciarPolling(data.status_key, 1000, (s) => {
                if (s.status === 'auvo_instavel') {
                    setBotao(botaoDespausa, 'Auvo com Instabilidade', '#c0392b', false);
                    mostrarNotificacao('O Auvo ficou instável durante o fim da pausa. Verifique e tente novamente.', 'erro');
                    resetBotaoDespausa(botaoDespausa);
                    return true;
                }
                if (s.status === 'abrindo_task') {
                    setBotao(botaoDespausa, 'Abrindo task...', '#16a085', true, AUVO_ETAPAS_PROGRESSO['abrindo_task']);
                }
                if (s.status === 'preenchendo_despausa') {
                    setBotao(botaoDespausa, 'Preenchendo fim...', '#16a085', true, AUVO_ETAPAS_PROGRESSO['preenchendo_despausa']);
                }
                if (s.status === 'lancando_despausa' || s.status === 'lancando') {
                    setBotao(botaoDespausa, 'Salvando fim...', '#16a085', true, AUVO_ETAPAS_PROGRESSO['lancando_despausa']);
                }
                if (s.status === 'erro') {
                    setBotao(botaoDespausa, 'Erro!', '#e74c3c', false);
                    mostrarNotificacao(s.mensagem || 'Erro ao lançar fim da pausa.', 'erro');
                    resetBotaoDespausa(botaoDespausa);
                    return true;
                }
                if (s.status === 'done') {
                    setBotao(botaoDespausa, `Fim salvo #${data.task_id}`, '#27ae60', true);
                    mostrarNotificacao(`Fim da pausa salvo. Task #${data.task_id}.`, 'sucesso');
                    resetBotaoDespausa(botaoDespausa);
                    return true;
                }
                return false;
            });
        })
        .catch(err => {
            setBotao(botaoDespausa, 'Erro!', '#e74c3c', false);
            mostrarNotificacao(`Erro no fim da pausa: ${err.message}`, 'erro');
            resetBotaoDespausa(botaoDespausa);
        });
    }
    function iniciarFluxoPausa(botaoPausa, textoEmpresa, dadosPausa) {
        bloquearAcaoOposta('pausa');
        setBotao(botaoPausa, 'Iniciando pausa...', '#8e44ad', true, AUVO_ETAPAS_PROGRESSO['iniciando_pausa']);

        fetch('http://localhost:8765/auvo-pausa', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ empresa: textoEmpresa, ...(dadosPausa || {}) }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'multiplas_empresas') {
                setBotao(botaoPausa, 'Iniciar pausa', '#8e44ad', false);
                mostrarSeletorEmpresas(
                    data.empresas,
                    textoEmpresa,
                    botaoPausa,
                    (empresaEscolhida) => iniciarFluxoPausa(botaoPausa, textoEmpresa, { ...(dadosPausa || {}), codigo_cliente: empresaEscolhida.codigo }),
                    () => resetBotaoPausa(botaoPausa)
                );
                return;
            }
            if (data.status === 'multiplas_tasks') {
                setBotao(botaoPausa, 'Iniciar pausa', '#8e44ad', false);
                mostrarSeletorTasksPausa(data.tasks, textoEmpresa, botaoPausa, { ...(dadosPausa || {}), codigo_cliente: data.codigo_cliente });
                return;
            }
            if (data.status === 'sem_atendimento') {
                setBotao(botaoPausa, 'Iniciar pausa', '#8e44ad', false);
                mostrarNotificacao(data.mensagem || 'Nenhum atendimento aberto encontrado para pausar.', 'aviso');
                resetBotaoPausa(botaoPausa);
                return;
            }
            if (data.status === 'atendimento_pausado') {
                setBotao(botaoPausa, 'Atendimento já pausado', '#f39c12', true);
                mostrarNotificacao(data.mensagem || 'Este atendimento já está pausado.', 'aviso');
                resetBotaoPausa(botaoPausa);
                return;
            }
            if (data.status === 'auvo_instavel') {
                setBotao(botaoPausa, 'Auvo com Instabilidade', '#c0392b', false);
                mostrarNotificacao('O Auvo está com instabilidade. Tente novamente em instantes.', 'erro');
                resetBotaoPausa(botaoPausa);
                return;
            }
            if (data.status !== 'ok') throw new Error(data.mensagem || 'Erro desconhecido');

            setBotao(botaoPausa, 'Abrindo task...', '#8e44ad', true, AUVO_ETAPAS_PROGRESSO['abrindo_task']);
            iniciarPolling(data.status_key, 1000, (s) => {
                if (s.status === 'auvo_instavel') {
                    setBotao(botaoPausa, 'Auvo com Instabilidade', '#c0392b', false);
                    mostrarNotificacao('O Auvo ficou instável durante a pausa. Verifique e tente novamente.', 'erro');
                    resetBotaoPausa(botaoPausa);
                    return true;
                }
                if (s.status === 'abrindo_task') {
                    setBotao(botaoPausa, 'Abrindo task...', '#8e44ad', true, AUVO_ETAPAS_PROGRESSO['abrindo_task']);
                }
                if (s.status === 'preenchendo_pausa') {
                    setBotao(botaoPausa, 'Preenchendo pausa...', '#8e44ad', true, AUVO_ETAPAS_PROGRESSO['preenchendo_pausa']);
                }
                if (s.status === 'lancando_pausa' || s.status === 'lancando') {
                    setBotao(botaoPausa, 'Lançando pausa...', '#8e44ad', true, AUVO_ETAPAS_PROGRESSO['lancando_pausa']);
                }
                if (s.status === 'erro') {
                    setBotao(botaoPausa, 'Erro!', '#e74c3c', false);
                    mostrarNotificacao(s.mensagem || 'Erro ao lançar início da pausa.', 'erro');
                    resetBotaoPausa(botaoPausa);
                    return true;
                }
                if (s.status === 'done') {
                    setBotao(botaoPausa, `Pausa salva #${data.task_id}`, '#27ae60', true);
                    mostrarNotificacao(`Início da pausa salvo. Task #${data.task_id}.`, 'sucesso');
                    resetBotaoPausa(botaoPausa);
                    return true;
                }
                return false;
            });
        })
        .catch(err => {
            setBotao(botaoPausa, 'Erro!', '#e74c3c', false);
            mostrarNotificacao(`Erro na pausa: ${err.message}`, 'erro');
            resetBotaoPausa(botaoPausa);
        });
    }
    // ── Inserção dos botões ───────────────────────────────────────────────
    function iniciarFluxoCheckin(botao, textoEmpresa, codigoCliente, inicioEscolhido) {
        bloquearAcaoOposta('checkin');
        setBotao(botao, 'Analisando chat...', '#2ecc71', true, AUVO_ETAPAS_PROGRESSO['analisando']);

        fetch('http://localhost:8765/auvo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ empresa: textoEmpresa, codigo_cliente: codigoCliente || null, inicio_escolhido: inicioEscolhido || null }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'multiplas_empresas') {
                setBotao(botao, 'Lancar no Auvo', '#2ecc71', false);
                mostrarSeletorEmpresas(
                    data.empresas,
                    textoEmpresa,
                    botao,
                    (empresaEscolhida) => iniciarFluxoCheckin(botao, textoEmpresa, empresaEscolhida.codigo),
                    () => resetBotaoCheckin(botao)
                );
                return;
            }

            if (data.status === 'multiplos_inicios') {
                setBotao(botao, 'Lancar no Auvo', '#2ecc71', false);
                mostrarSeletorInicios(
                    data.inicios,
                    botao,
                    (inicioEscolhido) => iniciarFluxoCheckin(botao, textoEmpresa, data.codigo_cliente || codigoCliente, inicioEscolhido),
                    () => resetBotaoCheckin(botao)
                );
                return;
            }
            if (data.status === 'auvo_instavel') {
                setBotao(botao, 'Auvo com Instabilidade', '#c0392b', false);
                mostrarNotificacao('O Auvo esta com instabilidade. Tente novamente em alguns instantes.', 'erro');
                resetBotaoCheckin(botao);
                return;
            }

            if (data.status !== 'ok') throw new Error(data.mensagem || 'Erro desconhecido');

            setBotao(botao, `Task #${data.task_id} - Fazendo Check-in...`, '#3498db', true, AUVO_ETAPAS_PROGRESSO['fazendo_checkin']);

            iniciarPolling(String(data.task_id), 1000, (s) => {
                if (s.status === 'auvo_instavel') {
                    setBotao(botao, 'Auvo com Instabilidade', '#c0392b', false);
                    mostrarNotificacao('O Auvo ficou instavel durante o check-in. Verifique a task e tente o check-in manualmente se necessario.', 'erro');
                    resetBotaoCheckin(botao);
                    return true;
                }
                if (s.status === 'done') {
                    setBotao(botao, `Check-in salvo #${data.task_id}`, '#27ae60', true);
                    mostrarNotificacao(`Check-in concluído com sucesso. Task #${data.task_id} - ${data.empresa || textoEmpresa}.`, 'sucesso');
                    resetBotaoCheckin(botao);
                    return true;
                }
                return false;
            });
        })
        .catch(err => {
            setBotao(botao, 'Erro!', '#e74c3c', false);
            mostrarNotificacao(`Erro no check-in: ${err.message}`, 'erro');
            resetBotaoCheckin(botao);
        });
    }
    function verificarEInserir() {
        garantirEstiloBotoesAuvo();
        if (document.getElementById('botao-auvo')) return;
        const spanProtocolo = Array.from(document.querySelectorAll('span'))
            .find(el => el.textContent.trim().includes('Protocolo'));
        if (!spanProtocolo) return;

        // --- BOTÃO CHECK-IN ---
        const botao = document.createElement('button');
        botao.id        = 'botao-auvo';
        botao.title = 'Lançar atendimento no Auvo';
        botao.setAttribute('aria-label', 'Lançar atendimento no Auvo');
        Object.assign(botao.style, {
            marginLeft: '0', width: '48px', height: '42px', padding: '0', minWidth: '48px',
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            backgroundColor: 'rgba(18,22,28,0.72)', color: '#2ecc71',
            border: '1px solid currentColor', borderRadius: '9px',
            cursor: 'pointer', fontWeight: '700',
            transition: 'background-color .18s ease, opacity .18s ease, box-shadow .18s ease, transform .18s ease',
            boxShadow: '0 2px 7px rgba(0,0,0,0.16)',
        });
        setBotao(botao, 'Lançar atendimento no Auvo', '#2ecc71', false);

        botao.onclick = () => {
            if (botao.disabled) return;
            const spans       = Array.from(document.querySelectorAll('span'));
            const spanEmpresa = spans.find(el => el.textContent.trim() === 'Empresa');
            const textoEmpresa = spanEmpresa && spanEmpresa.nextElementSibling
                ? spanEmpresa.nextElementSibling.innerText : 'Desconhecida';
            iniciarFluxoCheckin(botao, textoEmpresa);
        };
        // --- BOTÃO CHECK-OUT ---
        const botaoOut = document.createElement('button');
        botaoOut.id        = 'botao-auvo-checkout';
        botaoOut.title = 'Fazer check-out';
        botaoOut.setAttribute('aria-label', 'Fazer check-out');
        Object.assign(botaoOut.style, {
            width: '48px', height: '42px', padding: '0', minWidth: '48px',
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            backgroundColor: 'rgba(18,22,28,0.72)', color: '#e67e22',
            border: '1px solid currentColor', borderRadius: '9px',
            cursor: 'pointer', fontWeight: '700',
            transition: 'background-color .18s ease, opacity .18s ease, box-shadow .18s ease, transform .18s ease',
            boxShadow: '0 2px 7px rgba(0,0,0,0.16)',
        });
        setBotao(botaoOut, 'Fazer check-out', '#e67e22', false);

        botaoOut.onclick = () => {
            if (botaoOut.disabled) return;

            const spans       = Array.from(document.querySelectorAll('span'));
            const spanEmpresa = spans.find(el => el.textContent.trim() === 'Empresa');
            const textoEmpresa = spanEmpresa && spanEmpresa.nextElementSibling
                ? spanEmpresa.nextElementSibling.innerText : 'Desconhecida';

            iniciarFluxoCheckout(botaoOut, textoEmpresa);
        };

        // --- BOTÃO PAUSA ---
        const botaoPausa = document.createElement('button');
        botaoPausa.id = 'botao-auvo-pausa';
        botaoPausa.title = 'Iniciar pausa';
        botaoPausa.setAttribute('aria-label', 'Iniciar pausa');
        Object.assign(botaoPausa.style, {
            width: '48px', height: '42px', padding: '0', minWidth: '48px',
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            backgroundColor: 'rgba(18,22,28,0.72)', color: '#8e44ad',
            border: '1px solid currentColor', borderRadius: '9px',
            cursor: 'pointer', fontWeight: '700',
            transition: 'background-color .18s ease, opacity .18s ease, box-shadow .18s ease, transform .18s ease',
            boxShadow: '0 2px 7px rgba(0,0,0,0.16)',
        });
        setBotao(botaoPausa, 'Iniciar pausa', '#8e44ad', false);

        botaoPausa.onclick = () => {
            if (botaoPausa.disabled) return;
            const textoEmpresa = obterDadosEmpresaAtual();
            abrirModalPausa(
                (dadosPausa) => iniciarFluxoPausa(botaoPausa, textoEmpresa, dadosPausa),
                () => resetBotaoPausa(botaoPausa)
            );
        };

        // --- BOTÃO FIM DA PAUSA ---
        const botaoDespausa = document.createElement('button');
        botaoDespausa.id = 'botao-auvo-despausa';
        botaoDespausa.title = 'Fim da pausa';
        botaoDespausa.setAttribute('aria-label', 'Fim da pausa');
        Object.assign(botaoDespausa.style, {
            width: '48px', height: '42px', padding: '0', minWidth: '48px',
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            backgroundColor: 'rgba(18,22,28,0.72)', color: '#16a085',
            border: '1px solid currentColor', borderRadius: '9px',
            cursor: 'pointer', fontWeight: '700',
            transition: 'background-color .18s ease, opacity .18s ease, box-shadow .18s ease, transform .18s ease',
            boxShadow: '0 2px 7px rgba(0,0,0,0.16)',
        });
        setBotao(botaoDespausa, 'Fim da pausa', '#16a085', false);

        botaoDespausa.onclick = () => {
            if (botaoDespausa.disabled) return;
            const textoEmpresa = obterDadosEmpresaAtual();
            abrirModalDespausa(
                (dadosDespausa) => iniciarFluxoDespausa(botaoDespausa, textoEmpresa, dadosDespausa),
                () => resetBotaoDespausa(botaoDespausa)
            );
        };
        const wrapper = document.createElement('span');
        wrapper.style.cssText = 'display:inline-flex; align-items:center; gap:10px; margin-left:14px; vertical-align:middle; pointer-events:none; flex:0 0 auto;';
        botao.style.pointerEvents = 'auto';
        botaoOut.style.pointerEvents = 'auto';
        botaoPausa.style.pointerEvents = 'auto';
        botaoDespausa.style.pointerEvents = 'auto';
        wrapper.appendChild(botao);
        wrapper.appendChild(botaoPausa);
        wrapper.appendChild(botaoDespausa);
        wrapper.appendChild(botaoOut);
        spanProtocolo.parentElement.insertAdjacentElement('afterend', wrapper);
    }

    setInterval(verificarEInserir, 1000);
})();
"""

with sync_playwright() as p:
    try:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = _selecionar_pagina_speakr(browser)
        context = page.context

        try:
            context.storage_state(path=AUVO_STATE_PATH)
        except Exception:
            pass

        page.evaluate(SCRIPT_BOTAO)
        print("✓ Botão injetado com sucesso!")
        print("🚀 Sistema pronto. Aguardando cliques no Speakr...")

        falhas_consecutivas = 0
        while True:
            try:
                if page.is_closed() or not _pagina_parece_speakr(page):
                    page = _selecionar_pagina_speakr(browser)
                page.evaluate(SCRIPT_BOTAO)
                falhas_consecutivas = 0
                time.sleep(5)
            except Exception as e:
                falhas_consecutivas += 1
                espera = min(10 * falhas_consecutivas, 60)
                print(f"⚠️ Falha ao reinjetar botão (#{falhas_consecutivas}): {e}")
                print(f"   Aguardando {espera}s antes de tentar novamente...")
                time.sleep(espera)

    except Exception as e:
        print(f"Erro ao conectar: {e}")
        input("Pressione Enter para fechar...")
