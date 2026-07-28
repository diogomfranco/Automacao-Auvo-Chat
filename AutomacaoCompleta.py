import pandas as pd
import difflib
import unicodedata
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from playwright.sync_api import sync_playwright
from datetime import datetime,timedelta
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import pyperclip
import os
import time
import ctypes
from ctypes import wintypes

# ---------------------------------------------------------------------------
# Bandeja do sistema / controle do prompt
# ---------------------------------------------------------------------------
def _get_console_hwnd():
    try:
        return ctypes.windll.kernel32.GetConsoleWindow()
    except Exception:
        return 0

def _janela_existe(hwnd):
    return bool(hwnd and ctypes.windll.user32.IsWindow(hwnd))


def _texto_janela(hwnd):
    try:
        tamanho = ctypes.windll.user32.GetWindowTextLengthW(hwnd) + 1
        buffer = ctypes.create_unicode_buffer(tamanho)
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, tamanho)
        return buffer.value
    except Exception:
        return ""


def _classe_janela(hwnd):
    try:
        buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value
    except Exception:
        return ""


def _capturar_janela_prompt():
    global _PROMPT_HWND

    hwnd = ctypes.windll.user32.GetForegroundWindow()
    classe = _classe_janela(hwnd).lower()
    titulo = _texto_janela(hwnd).lower()
    parece_prompt = (
        "consolewindowclass" in classe
        or "cascadia" in classe
        or "powershell" in titulo
        or "prompt" in titulo
        or "cmd" in titulo
        or "sistema" in titulo
        or "python" in titulo
    )
    if hwnd and parece_prompt:
        _PROMPT_HWND = hwnd
        return hwnd

    _PROMPT_HWND = _get_console_hwnd()
    return _PROMPT_HWND


def _get_prompt_hwnd():
    if _janela_existe(_PROMPT_HWND):
        return _PROMPT_HWND
    return _get_console_hwnd()


def _mostrar_console(icon=None, item=None):
    hwnd = _get_prompt_hwnd()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)


def _ocultar_console(icon=None, item=None):
    hwnd = _get_prompt_hwnd()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE


def _alternar_console(icon=None, item=None):
    hwnd = _get_prompt_hwnd()
    if not hwnd:
        return
    if ctypes.windll.user32.IsWindowVisible(hwnd):
        _ocultar_console()
    else:
        _mostrar_console()


def _desabilitar_botao_fechar_console():
    hwnd = _get_prompt_hwnd()
    if not hwnd:
        return
    try:
        hmenu = ctypes.windll.user32.GetSystemMenu(hwnd, False)
        if hmenu:
            ctypes.windll.user32.DeleteMenu(hmenu, 0xF060, 0)  # SC_CLOSE / MF_BYCOMMAND
            ctypes.windll.user32.DrawMenuBar(hwnd)
    except Exception:
        pass


def _console_esta_minimizado(hwnd):
    if ctypes.windll.user32.IsIconic(hwnd):
        return True

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint),
            ("flags", ctypes.c_uint),
            ("showCmd", ctypes.c_uint),
            ("ptMinPosition", POINT),
            ("ptMaxPosition", POINT),
            ("rcNormalPosition", RECT),
        ]

    placement = WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(WINDOWPLACEMENT)
    if ctypes.windll.user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
        return placement.showCmd == 2  # SW_SHOWMINIMIZED
    return False


def _monitorar_console_minimizado():
    while True:
        hwnd = _get_prompt_hwnd()
        if hwnd and ctypes.windll.user32.IsWindowVisible(hwnd) and _console_esta_minimizado(hwnd):
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        time.sleep(0.05)


def _criar_icone_bandeja():
    from PIL import Image, ImageDraw, ImageFont

    if os.path.exists(APP_ICON_PATH):
        return Image.open(APP_ICON_PATH)

    img = Image.new("RGBA", (64, 64), (22, 126, 191, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=(255, 255, 255, 235))
    draw.text((24, 19), "A", fill=(22, 126, 191, 255), font=ImageFont.load_default())
    return img


def _iniciar_bandeja_sistema():
    global _TRAY_STARTED
    if _TRAY_STARTED:
        return
    _TRAY_STARTED = True
    try:
        import pystray
    except Exception as e:
        _TRAY_STARTED = False
        print(f"Aviso: bandeja do sistema indisponivel ({e}). Instale 'pystray' e 'pillow'.")
        return

    def sair(icon, item):
        try:
            icon.stop()
        finally:
            os._exit(0)

    _capturar_janela_prompt()
    _desabilitar_botao_fechar_console()
    icon = pystray.Icon(
        "Sistema Auvo",
        _criar_icone_bandeja(),
        "Sistema Auvo",
        menu=pystray.Menu(
            pystray.MenuItem("Mostrar/Ocultar prompt", _alternar_console, default=True),
            pystray.MenuItem("Sair", sair),
        ),
    )
    threading.Thread(target=icon.run, daemon=True).start()
    # Monitor por polling: detecta minimização e oculta a janela imediatamente
    threading.Thread(target=_monitorar_console_minimizado, daemon=True).start()
    # Oculta o console 1.5s após iniciar (tempo para o usuário ver a mensagem de pronto)
    threading.Timer(1.5, _ocultar_console).start()

_PROMPT_HWND = 0
_TRAY_STARTED = False
APP_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sistema_auvo.ico")
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


# ---------------------------------------------------------------------------
# Configuração de acesso Auvo via Supabase
# ---------------------------------------------------------------------------
# SUPABASE_URL e SUPABASE_ANON_KEY vêm de variáveis de ambiente (arquivo .env,
# veja .env.example). A proteção real fica a cargo da política de RLS da
# tabela "credenciais_auvo" (só usuário autenticado lê), mas a URL/chave do
# projeto não devem ser versionadas de qualquer forma.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv é opcional; as variáveis também podem vir do ambiente do SO

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")

_PASTA_CONFIG = os.path.dirname(os.path.abspath(__file__))
CONFIG_SUPABASE_PATH = os.environ.get(
    "CONFIG_SUPABASE_PATH",
    os.path.join(_PASTA_CONFIG, "config_supabase.json"),
)
ERRO_INICIALIZACAO_LOG = os.path.join(_PASTA_CONFIG, "erro_inicializacao.log")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise SystemExit(
        "❌ SUPABASE_URL e/ou SUPABASE_ANON_KEY não configurados. "
        "Defina-os no ambiente ou em um arquivo .env (veja .env.example)."
    )

_CREDENCIAIS_AUVO_CACHE = {}


def _falha_fatal_inicializacao(mensagem: str):
    """Registra o erro em arquivo (o console some após alguns segundos) e encerra o programa."""
    print(f"❌ {mensagem}")
    try:
        with open(ERRO_INICIALIZACAO_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] {mensagem}\n")
    except Exception:
        pass
    try:
        input("\nPressione Enter para fechar...")
    except Exception:
        pass
    raise SystemExit(1)


def _carregar_config_supabase() -> dict:
    if not os.path.exists(CONFIG_SUPABASE_PATH):
        _falha_fatal_inicializacao(
            f"Arquivo de configuração não encontrado: {CONFIG_SUPABASE_PATH}. "
            "Verifique se o Iniciar_Auvo.bat sincronizou o config_supabase.json do servidor."
        )
    try:
        with open(CONFIG_SUPABASE_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        if "email" not in config or "senha" not in config:
            raise ValueError("config_supabase.json precisa conter as chaves 'email' e 'senha'.")
        return config
    except Exception as e:
        _falha_fatal_inicializacao(f"Erro ao ler config_supabase.json: {e}")


def _login_supabase(config: dict):
    from supabase import create_client

    try:
        cliente = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    except Exception as e:
        _falha_fatal_inicializacao(f"Não foi possível criar o cliente Supabase: {e}")

    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            cliente.auth.sign_in_with_password({
                "email": config["email"],
                "password": config["senha"],
            })
            return cliente
        except Exception as e:
            ultimo_erro = e
            if tentativa < 3:
                print(f"⚠️ Falha ao autenticar no Supabase ({tentativa}/3): {e}. Tentando novamente...")
                time.sleep(3)
    _falha_fatal_inicializacao(
        f"Não foi possível autenticar no Supabase após 3 tentativas: {ultimo_erro}. "
        "Verifique a internet e se a conta de automação está ativa."
    )


def _obter_credenciais_auvo(cliente) -> dict:
    if _CREDENCIAIS_AUVO_CACHE:
        return _CREDENCIAIS_AUVO_CACHE

    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            resp = (
                cliente.table("credenciais_auvo")
                .select("usuario, senha, api_key, api_token")
                .limit(1)
                .execute()
            )
            if not resp.data:
                raise RuntimeError("Nenhuma linha encontrada na tabela credenciais_auvo.")
            _CREDENCIAIS_AUVO_CACHE.update(resp.data[0])
            return _CREDENCIAIS_AUVO_CACHE
        except Exception as e:
            ultimo_erro = e
            if tentativa < 3:
                print(f"⚠️ Falha ao buscar credenciais Auvo ({tentativa}/3): {e}. Tentando novamente...")
                time.sleep(3)
    _falha_fatal_inicializacao(
        f"Não foi possível obter as credenciais da Auvo após 3 tentativas: {ultimo_erro}"
    )


_CREDENCIAL_IA_CACHE = {}


def _obter_credencial_claude(cliente) -> str:
    """Busca a chave da API do Claude numa tabela própria (credenciais_ia),
    isolada da tabela de credenciais do Auvo por princípio de menor privilégio."""
    if _CREDENCIAL_IA_CACHE.get("anthropic_api_key"):
        return _CREDENCIAL_IA_CACHE["anthropic_api_key"]

    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            resp = (
                cliente.table("credenciais_ia")
                .select("anthropic_api_key")
                .limit(1)
                .execute()
            )
            if not resp.data:
                raise RuntimeError("Nenhuma linha encontrada na tabela credenciais_ia.")
            chave = (resp.data[0].get("anthropic_api_key") or "").strip()
            if not chave:
                raise RuntimeError("Coluna anthropic_api_key vazia na tabela credenciais_ia.")
            _CREDENCIAL_IA_CACHE["anthropic_api_key"] = chave
            return chave
        except Exception as e:
            ultimo_erro = e
            if tentativa < 3:
                print(f"⚠️ Falha ao buscar a chave da IA ({tentativa}/3): {e}. Tentando novamente...")
                time.sleep(3)
    _falha_fatal_inicializacao(
        f"Não foi possível obter a chave da API Anthropic (tabela credenciais_ia) após 3 tentativas: {ultimo_erro}"
    )


# ---------------------------------------------------------------------------
# Credencial da conta de serviço do Google (tabela credenciais_google) —
# usada só pra gravar equipamentos não vinculados no Google Sheets.
# Buscada sob demanda (não trava a inicialização se faltar/falhar).
# ---------------------------------------------------------------------------
_CREDENCIAL_GOOGLE_CACHE = {}


def _obter_credencial_google(cliente) -> dict:
    """Busca o JSON da conta de serviço do Google (tabela credenciais_google)
    e devolve como dict, pronto pra usar com google-auth. Não trava a
    inicialização se falhar — a verificação de equipamento fica indisponível,
    mas o resto do sistema continua funcionando."""
    if _CREDENCIAL_GOOGLE_CACHE.get("info"):
        return _CREDENCIAL_GOOGLE_CACHE["info"]

    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            resp = (
                cliente.table("credenciais_google")
                .select("service_account_json")
                .limit(1)
                .execute()
            )
            if not resp.data:
                raise RuntimeError("Nenhuma linha encontrada na tabela credenciais_google.")
            bruto = (resp.data[0].get("service_account_json") or "").strip()
            if not bruto:
                raise RuntimeError("Coluna service_account_json vazia.")
            info = json.loads(bruto)
            _CREDENCIAL_GOOGLE_CACHE["info"] = info
            return info
        except Exception as e:
            ultimo_erro = e
            if tentativa < 3:
                print(f"⚠️ Falha ao buscar credencial Google ({tentativa}/3): {e}. Tentando novamente...")
                time.sleep(3)
    print(f"⚠️ Não foi possível obter a credencial do Google Sheets: {ultimo_erro}. "
          f"Verificação de equipamento não vinculado ficará desativada.")
    return {}


# --- CONFIGURAÇÕES DE ACESSO AUVO (buscadas do Supabase na inicialização) ---
_config_supabase = _carregar_config_supabase()
_supabase_client = _login_supabase(_config_supabase)
_creds_auvo = _obter_credenciais_auvo(_supabase_client)

USUARIO_AUVO   = _creds_auvo["usuario"]
SENHA_AUVO     = _creds_auvo["senha"]
AUVO_API_KEY   = _creds_auvo["api_key"]
AUVO_API_TOKEN = _creds_auvo["api_token"]

ANTHROPIC_API_KEY = _obter_credencial_claude(_supabase_client)

user32 = ctypes.windll.user32
largura = user32.GetSystemMetrics(78)
altura = user32.GetSystemMetrics(79)

# --- USUÁRIOS ---
# Carregado de um arquivo externo (não versionado) para não expor nomes/IDs
# reais da equipe no código público. Veja usuarios.example.json para o formato.
_USUARIOS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usuarios.json")


def _carregar_usuarios() -> dict:
    if not os.path.exists(_USUARIOS_PATH):
        raise SystemExit(
            f"❌ Arquivo de usuários não encontrado: {_USUARIOS_PATH}. "
            "Copie usuarios.example.json para usuarios.json e preencha com os dados da sua equipe."
        )
    with open(_USUARIOS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


USUARIOS = _carregar_usuarios()

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
_iniciar_bandeja_sistema()


# ---------------------------------------------------------------------------
# Configuração da API do Claude (Anthropic)
# ---------------------------------------------------------------------------
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
CLAUDE_MODEL = "claude-sonnet-5"
CLAUDE_TIMEOUT = 60  # segundos máximos aguardando resposta do Claude
CLAUDE_MAX_TOKENS = 2048

AUVO_TOKEN_CACHE = {"token": None, "expires_at": 0}
AUVO_TOKEN_LOCK = threading.Lock()
AUVO_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
AUVO_CONNECT_TIMEOUT = 8
AUVO_LOGIN_TIMEOUT = (AUVO_CONNECT_TIMEOUT, 25)
AUVO_LIST_TIMEOUT = (AUVO_CONNECT_TIMEOUT, 25)
AUVO_CONFIRM_TIMEOUT = (AUVO_CONNECT_TIMEOUT, 30)
AUVO_CREATE_TIMEOUT = (AUVO_CONNECT_TIMEOUT, 45)
AUVO_CONFIRM_RETRIES = 5
AUVO_CONFIRM_BASE_WAIT = 1.5

HTTP_SESSION = requests.Session()
HTTP_SESSION.mount('https://api.auvo.com.br', HTTPAdapter(max_retries=Retry(
    total=2,
    connect=2,
    read=1,
    status=2,
    backoff_factor=0.8,
    status_forcelist=(500, 502, 503, 504),
    allowed_methods=frozenset(['GET']),
)))
AUVO_TASKS_CACHE = {}
AUVO_TASKS_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Google Sheets — planilha "Equipamentos não Vinculados", usada pra registrar
# quando um equipamento citado no chat não bate com nada cadastrado pro
# cliente na Auvo. Requer: pip install gspread google-auth --break-system-packages
# ---------------------------------------------------------------------------
GOOGLE_SHEET_ID = "1VVWZxkHj2OY5oMI7HW_aVAqzjM97ziDbDcVKfBcR5Us"
GOOGLE_SHEET_ABA = "Página1"

_GSPREAD_CLIENT_CACHE = {"cliente": None}


def _montar_cliente_gspread():
    """Monta (e cacheia) o client do gspread autenticado via conta de serviço.
    Retorna None se a credencial não estiver disponível — quem chamar deve
    tratar esse caso sem derrubar o fluxo de checkout."""
    if _GSPREAD_CLIENT_CACHE["cliente"] is not None:
        return _GSPREAD_CLIENT_CACHE["cliente"]

    info = _obter_credencial_google(_supabase_client)
    if not info:
        return None

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        escopos = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        credenciais = Credentials.from_service_account_info(info, scopes=escopos)
        cliente_gspread = gspread.authorize(credenciais)
        _GSPREAD_CLIENT_CACHE["cliente"] = cliente_gspread
        return cliente_gspread
    except Exception as e:
        print(f"⚠️ Não foi possível autenticar no Google Sheets: {e}")
        return None


def gravar_equipamento_nao_vinculado(nome_cliente: str, nome_equipamento: str, task_id: str = "") -> bool:
    """Adiciona uma linha [data/hora, técnico, cliente, equipamento, task] na planilha de equipamentos
    não vinculados. Nunca lança exceção pro chamador — registra o erro no
    console e retorna False, pra não travar o checkout por causa disso."""
    try:
        cliente_gspread = _montar_cliente_gspread()
        if not cliente_gspread:
            print("⚠️ Google Sheets indisponível; equipamento não vinculado não foi registrado.")
            return False

        planilha = cliente_gspread.open_by_key(GOOGLE_SHEET_ID)
        aba = planilha.worksheet(GOOGLE_SHEET_ABA)
        aba.append_row(
            [
                datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                NOME_USER or "",
                nome_cliente or "",
                nome_equipamento or "",
                str(task_id or ""),
            ],
            value_input_option="USER_ENTERED",
        )
        print(f"📋 Registrado na planilha: {nome_cliente} — {nome_equipamento} — Task {task_id or '-'}")
        return True
    except Exception as e:
        print(f"⚠️ Erro ao gravar equipamento não vinculado no Sheets: {e}")
        return False


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
def chamar_claude(prompt: str, tentativas: int = 5) -> str:
    """Chama a API do Claude (Anthropic) diretamente via HTTPS.

    Substitui a antiga automação de navegador contra o Gemini: sem browser,
    sem estado de sessão em disco, resposta em segundos e sem depender da
    UI web do Gemini permanecer estável.
    """
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }

    for i in range(tentativas):
        try:
            print(f"tentativa {i+1}/{tentativas}: chamando API do Claude...")
            resp = requests.post(
                ANTHROPIC_API_URL,
                headers=headers,
                json=payload,
                timeout=CLAUDE_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            print(f"timeout na tentativa {i+1} ({CLAUDE_TIMEOUT}s excedido).")
            time.sleep(1.5)
            continue
        except requests.exceptions.RequestException as e:
            print(f"falha de conexao na tentativa {i+1}: {e}")
            time.sleep(1.5)
            continue

        if resp.status_code == 200:
            try:
                dados = resp.json()
                texto = "".join(
                    bloco.get("text", "")
                    for bloco in dados.get("content", [])
                    if bloco.get("type") == "text"
                ).strip()
            except Exception as e:
                print(f"resposta da API em formato inesperado ({e}).")
                dados = {}
                texto = ""

            if texto:
                stop_reason = dados.get("stop_reason", "")
                if stop_reason == "max_tokens":
                    print(f"⚠️ Claude cortou a resposta por limite de tokens (stop_reason=max_tokens). Aumentar CLAUDE_MAX_TOKENS pode resolver.")
                print(f"Claude OK ({len(texto)} chars)")
                return texto
            stop_reason = dados.get("stop_reason", "desconhecido") if isinstance(dados, dict) else "desconhecido"
            print(
                f"tentativa {i+1}: resposta vazia da API (stop_reason={stop_reason}). "
                f"JSON bruto (amostra): {json.dumps(dados, ensure_ascii=False)[:300]!r}"
            )

        elif resp.status_code == 401:
            print("chave da API do Claude invalida, revogada ou expirada. Verifique a coluna anthropic_api_key no Supabase. Abortando tentativas.")
            break  # nao adianta insistir com uma chave invalida

        elif resp.status_code == 429:
            espera = 5 * (i + 1)
            print(f"tentativa {i+1}: limite de requisicoes (429) atingido. Aguardando {espera}s...")
            time.sleep(espera)
            continue

        elif resp.status_code >= 500:
            print(f"tentativa {i+1}: erro no servidor da Anthropic ({resp.status_code}). Tentando novamente...")

        else:
            print(f"tentativa {i+1}: erro HTTP {resp.status_code} - {resp.text[:200]}")

        time.sleep(1.5)

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


def anexar_sistema_descricao(descricao: str, sistema: str = None) -> str:
    sistema = re.sub(r"\s+", " ", str(sistema or "")).strip(" -–|,.;")
    if not sistema:
        return descricao
    info = f"Sistema utilizado pela cliente: {sistema}."
    descricao = (descricao or DESCRICAO_FALLBACK).strip()
    if info.lower() in descricao.lower():
        return descricao
    return f"{descricao.rstrip()} {info}"[:500]



def ultima_mensagem_indica_inatividade(mensagens: list[str]) -> bool:
    if not mensagens:
        return False
    marcador = re.compile(r"^\s*(?:hoje|ontem|\d{1,2}[/\-]\d{1,2})?\s*-?\s*\d{1,2}[h:]\d{2}\s*-\s*.*?(?:disse|diz):\s*", re.IGNORECASE)
    for msg in reversed(mensagens):
        texto = marcador.sub("", str(msg or "")).strip().lower()
        texto = re.sub(r"\s+", " ", texto)
        if texto:
            return "inatividade" in texto
    return False


def anexar_finalizacao_inatividade(relato: str, mensagens: list[str]) -> str:
    frase = "Atendimento finalizado por inatividade."
    relato = (relato or RELATO_FALLBACK).strip()
    if ultima_mensagem_indica_inatividade(mensagens) and frase.lower() not in relato.lower():
        relato = f"{relato.rstrip('.')} . {frase}"
        relato = relato.replace(" .", ".")
    return relato


def inserir_linha_equipamento(relato: str, nome_equipamento: str, problema_equipamento: str = "") -> str:
    """Garante que o relato tenha a linha 'Equipamento relatado: <nome>. Problema: <problema>.'
    Usado quando o atendente seleciona o equipamento manualmente (fluxo do
    modal), já que nesse caso a IA não tinha citado equipamento nenhum."""
    relato = (relato or RELATO_FALLBACK).strip()
    if not nome_equipamento or "equipamento relatado:" in relato.lower():
        return relato

    linha = f"Equipamento relatado: {nome_equipamento}."

    frase_inatividade = "Atendimento finalizado por inatividade."
    if relato.lower().endswith(frase_inatividade.lower()):
        base = relato[: -len(frase_inatividade)].rstrip()
        return f"{base} {linha} {frase_inatividade}"

    return f"{relato.rstrip()} {linha}"


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
def limpar_prefixo_gemini(texto: str) -> str:
    texto = str(texto or "").strip()
    if not texto:
        return ""
    nucleo_gemini = r'(?:Gemini\s+(?:disse|respondeu)|Resposta\s+do\s+Gemini)\s*[:\-\u2013]?\s*'
    # 1) "Foi o Gemini disse X" ou "Foi Gemini disse X" -> remove tudo, incluindo o "Foi"
    #    (o "Foi" pertencia ao artefato do Gemini, não ao texto real)
    texto = re.sub(r'^\s*Foi\s+(?:o\s+)?' + nucleo_gemini, '', texto, flags=re.IGNORECASE)
    # 2) "Gemini disse X" no inicio -> remove
    texto = re.sub(r'^\s*' + nucleo_gemini, '', texto, flags=re.IGNORECASE)
    # 3) ocorrencias no meio/fim da frase ("Solene precisa Gemini disse atualizar")
    texto = re.sub(r'\s+' + nucleo_gemini, ' ', texto, flags=re.IGNORECASE)
    # 4) apos pontuacao: ". Gemini disse X" -> ". X"
    texto = re.sub(r'(?<=[.!?])\s*' + nucleo_gemini, ' ', texto, flags=re.IGNORECASE)
    return re.sub(r'\s{2,}', ' ', texto).strip()

def texto_placeholder_ia(texto: str) -> bool:
    texto = re.sub(r"\s+", " ", str(texto or "")).strip().strip('"').strip("'")
    texto_sem_pontos = texto.replace(".", "").replace("…", "").strip()
    if not texto or not texto_sem_pontos:
        return True
    return bool(re.fullmatch(
        r"(?:\.\.\.|…|texto(?: real)?(?: d[ao] .*)?|descri[cç][aã]o|relato|exemplo|preencher|n/?a|não informado|nao informado|sem informa[cç][aã]o)",
        texto,
        flags=re.IGNORECASE,
    ))


def pegar_descricao(texto, mensagens: list[str] = None):
    if not texto or texto_placeholder_ia(texto):
        return gerar_descricao_fallback_mensagens(mensagens)

    texto = re.sub(r'Format:\s*Plain\s*text\.?', '', texto, flags=re.IGNORECASE)
    texto = re.sub(r'\bMonday\b', 'segunda-feira', texto, flags=re.IGNORECASE)
    texto = re.sub(r'\bTuesday\b', 'terça-feira', texto, flags=re.IGNORECASE)
    texto = re.sub(r'\bWednesday\b', 'quarta-feira', texto, flags=re.IGNORECASE)
    texto = re.sub(r'\bThursday\b', 'quinta-feira', texto, flags=re.IGNORECASE)
    texto = re.sub(r'\bFriday\b', 'sexta-feira', texto, flags=re.IGNORECASE)
    texto = re.sub(r'\bSaturday\b', 'sábado', texto, flags=re.IGNORECASE)
    texto = re.sub(r'\bSunday\b', 'domingo', texto, flags=re.IGNORECASE)
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

    if texto_eco_prompt(texto) or re.search(r'^\s*(Você é|Regras:|Histórico:)', texto, flags=re.IGNORECASE | re.MULTILINE):
        print("⚠️ Gemini retornou parte do prompt em vez da descrição. Usando fallback do histórico.")
        return gerar_descricao_fallback_mensagens(mensagens)

    # Remove preâmbulos do tipo "Gemini disse", "Aqui está:", etc.
    texto = re.sub(
        r'^.*?\b(?:disse|diz)[:\s]+',
        '',
        texto,
        flags=re.IGNORECASE | re.DOTALL,
        count=1,
    ).strip()

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

    if not descricao or texto_placeholder_ia(descricao) or descricao in {".", ":"}:
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
def texto_eco_prompt(texto: str) -> bool:
    """Detecta quando o Gemini (ou a captura do clipboard) devolveu o próprio
    prompt/instruções em vez de uma resposta real."""
    texto = (texto or "").strip()
    if not texto:
        return False
    if re.search(
        r'^\s*(Você é|Voce e|Regras:|Histórico:|Historico:|Escreva em português|'
        r'Exemplos CORRETOS|Exemplos ERRADOS|Responda SOMENTE em JSON)',
        texto,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(r'\bExemplos CORRETOS\b.{0,40}\bExemplos ERRADOS\b', texto, flags=re.IGNORECASE | re.DOTALL):
        return True
    # Marcadores exclusivos dos prompts internos: nunca apareceriam numa resposta real
    marcadores_unicos = (
        "<<<HISTORICO>>>", "<<<FIM_HISTORICO>>>",
        "preparando dois textos para registrar um atendimento",
        "texto real da descrição", "texto real do relato",
        "Regras para \"descricao\"", "Regras para \"relato\"",
    )
    texto_lower = texto.lower()
    if any(marcador.lower() in texto_lower for marcador in marcadores_unicos):
        return True
    return False


def limpar_relato(texto: str) -> str:
    if not texto or texto_placeholder_ia(texto):
        return "Foi realizado atendimento via chat."

    if texto_eco_prompt(texto):
        print("⚠️ Gemini retornou o próprio prompt em vez do relato. Usando fallback.")
        return "Foi realizado atendimento via chat."

    # Remove preâmbulos do tipo "O Gemini disse", "Aqui está o relato:", etc.
    texto = re.sub(
        r'^.*?(?:disse[:\s]+|(?:aqui\s+(?:está|esta)\s+(?:o|um)\s+)?relato[:\s]+)',
        '',
        texto,
        flags=re.IGNORECASE | re.DOTALL,
        count=1,
    ).strip()

    # Se ainda restar lixo antes do "Foi", descarta tudo antes dele
    match = re.search(r'\bFoi\b', texto, re.IGNORECASE)
    if match and match.start() > 0:
        texto = texto[match.start():]

    # Remove a linha/frase "Problema: ..." que a IA às vezes escreve dentro
    # do próprio relato (esse dado já vem separado no campo
    # problema_equipamento e não deve aparecer duplicado no texto do relato).
    texto = re.sub(r'\s*Problema:\s*[^.\n]*\.?', '', texto, flags=re.IGNORECASE).strip()

    return texto.strip()


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

_PROMPTS_IA_CACHE = {}


def _obter_prompts_ia(cliente) -> dict:
    """Busca os textos dos prompts (descricao/relato/combinado) na tabela
    prompts_ia do Supabase. Se a tabela/linha não existir ou a busca falhar,
    usa os prompts padrão embutidos no código — nunca trava a inicialização
    por causa disso, já que não é um dado sensível como a API key."""
    if _PROMPTS_IA_CACHE:
        return _PROMPTS_IA_CACHE

    try:
        resp = cliente.table("prompts_ia").select("chave, texto").execute()
        for linha in resp.data or []:
            chave = (linha.get("chave") or "").strip()
            texto = linha.get("texto") or ""
            if chave and texto.strip():
                _PROMPTS_IA_CACHE[chave] = texto
        if _PROMPTS_IA_CACHE:
            print(f"✅ Prompts carregados do Supabase: {', '.join(_PROMPTS_IA_CACHE.keys())}")
    except Exception as e:
        print(f"⚠️ Não foi possível buscar prompts_ia no Supabase, usando prompts padrão do código: {e}")

    return _PROMPTS_IA_CACHE


# Carrega os prompts do Supabase assim que possível na inicialização
# (definido aqui, mas _supabase_client já existe desde o topo do módulo).
_obter_prompts_ia(_supabase_client)


def _prompt_descricao(linhas: str) -> str:
    """O texto do prompt vem da tabela prompts_ia (chave 'descricao') no
    Supabase — não há mais fallback embutido no código."""
    template = _PROMPTS_IA_CACHE.get("descricao")
    if not template:
        raise RuntimeError(
            "Prompt 'descricao' não encontrado na tabela prompts_ia do Supabase. "
            "Cadastre a linha (chave='descricao') antes de gerar descrições."
        )
    return template.replace("@@HISTORICO@@", linhas)


def _prompt_relato(linhas: str) -> str:
    """O texto do prompt vem da tabela prompts_ia (chave 'relato') no
    Supabase — inclui as regras de equipamento_relacionado/equipamento_citado/
    problema_equipamento em JSON. Não há mais fallback embutido no código."""
    template = _PROMPTS_IA_CACHE.get("relato")
    if not template:
        raise RuntimeError(
            "Prompt 'relato' não encontrado na tabela prompts_ia do Supabase. "
            "Cadastre a linha (chave='relato') antes de gerar relatos."
        )
    return _injetar_keywords_equipamentos_prompt(template.replace("@@HISTORICO@@", linhas))


def _prompt_combinado(linhas: str) -> str:
    """O texto do prompt vem da tabela prompts_ia (chave 'combinado') no
    Supabase — inclui os campos equipamento_relacionado/equipamento_citado/
    problema_equipamento em JSON. Não há mais fallback embutido no código."""
    template = _PROMPTS_IA_CACHE.get("combinado")
    if not template:
        raise RuntimeError(
            "Prompt 'combinado' não encontrado na tabela prompts_ia do Supabase. "
            "Cadastre a linha (chave='combinado') antes de usar o fluxo combinado."
        )
    return _injetar_keywords_equipamentos_prompt(template.replace("@@HISTORICO@@", linhas))


def _bool_ia(valor) -> bool:
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return bool(valor)
    if isinstance(valor, str):
        texto = valor.strip().lower()
        if texto in {"true", "1", "sim", "s", "yes", "y"}:
            return True
        if texto in {"false", "0", "nao", "não", "n", "no", ""}:
            return False
    return False


def _injetar_keywords_equipamentos_prompt(prompt: str) -> str:
    apelidos = _obter_equipamentos_keywords(_supabase_client)
    if not apelidos:
        return prompt

    linhas = [
        "Use tambem estes apelidos de equipamentos para identificar equipamento_relacionado e equipamento_citado:",
        *[f"- {apelido} -> {oficial}" for apelido, oficial in sorted(apelidos.items())],
    ]
    bloco = "\n".join(linhas)
    if "@@EQUIPAMENTOS_KEYWORDS@@" in prompt:
        return prompt.replace("@@EQUIPAMENTOS_KEYWORDS@@", bloco)
    return f"{prompt}\n\n{bloco}"


def gerar_descricao_com_ia(mensagens: list[str], sistema: str = None) -> str:
    if not mensagens:
        return anexar_sistema_descricao(DESCRICAO_FALLBACK, sistema)

    linhas = preprocessar_mensagens(mensagens)
    prompt = _prompt_descricao(linhas)

    resultado = chamar_claude(prompt)
    if not resultado:
        print("⚠️ Claude sem resposta para descrição. Usando fallback.")
        return anexar_sistema_descricao(DESCRICAO_FALLBACK, sistema)

    return anexar_sistema_descricao(pegar_descricao(resultado, mensagens), sistema)


# ---------------------------------------------------------------------------
# Geração de relato detalhado
# ---------------------------------------------------------------------------
def gerar_relato_estruturado_com_ia(mensagens: list[str]) -> tuple[str, bool, str, str]:
    """Retorna (relato, equipamento_relacionado, equipamento_citado, problema_equipamento)."""
    if not mensagens:
        relato = anexar_finalizacao_inatividade(RELATO_FALLBACK, mensagens)
        return relato, False, "", ""

    linhas = preprocessar_mensagens(mensagens)
    prompt = _prompt_relato(linhas)

    resultado = chamar_claude(prompt)
    if not resultado:
        print("⚠️ Claude sem resposta para relato. Usando fallback.")
        relato = anexar_finalizacao_inatividade(RELATO_FALLBACK, mensagens)
        return relato, False, "", ""

    dados = _extrair_json_gemini(resultado)
    relato_bruto = (dados.get("relato") or "").strip()
    equipamento_relacionado = _bool_ia(dados.get("equipamento_relacionado"))
    equipamento_citado = (dados.get("equipamento_citado") or "").strip()
    problema_equipamento = (dados.get("problema_equipamento") or "").strip()

    if not relato_bruto or texto_placeholder_ia(relato_bruto):
        # fallback: se a IA não devolveu JSON, tenta tratar a resposta bruta
        # como texto puro (compatibilidade com prompt customizado no Supabase)
        relato_bruto = resultado if not texto_eco_prompt(resultado) else RELATO_FALLBACK
        equipamento_relacionado = False
        equipamento_citado = ""
        problema_equipamento = ""

    relato = anexar_finalizacao_inatividade(limpar_relato(relato_bruto), mensagens)
    return relato, equipamento_relacionado, equipamento_citado, problema_equipamento


def gerar_relato_com_ia(mensagens: list[str]) -> str:
    """Mantida por compatibilidade com chamadas existentes que só querem o
    texto do relato. Chamadas novas no checkout devem usar
    gerar_relato_estruturado_com_ia diretamente, pra ter acesso também aos
    campos de equipamento."""
    relato, _, _, _ = gerar_relato_estruturado_com_ia(mensagens)
    return relato


def _encontrar_objetos_json_balanceados(texto: str):
    """Varre o texto e retorna todas as substrings que formam objetos JSON
    com chaves balanceadas, ignorando chaves que estejam dentro de strings."""
    objetos = []
    profundidade = 0
    inicio = None
    dentro_string = False
    escapando = False
    for i, ch in enumerate(texto):
        if dentro_string:
            if escapando:
                escapando = False
            elif ch == "\\":
                escapando = True
            elif ch == '"':
                dentro_string = False
            continue
        if ch == '"':
            dentro_string = True
            continue
        if ch == "{":
            if profundidade == 0:
                inicio = i
            profundidade += 1
        elif ch == "}":
            if profundidade > 0:
                profundidade -= 1
                if profundidade == 0 and inicio is not None:
                    objetos.append(texto[inicio:i + 1])
                    inicio = None
    return objetos


def _extrair_campos_json_via_regex(texto: str) -> dict:
    """Último recurso: extrai campos direto do texto bruto,
    mesmo que o JSON ao redor esteja malformado (aspas quebradas, texto extra etc.)."""
    dados = {}
    for chave in ("descricao", "relato", "equipamento_citado", "problema_equipamento"):
        m = re.search(rf'"{chave}"\s*:\s*"((?:[^"\\]|\\.)*)"', texto, flags=re.IGNORECASE | re.DOTALL)
        if m:
            valor = m.group(1)
            try:
                valor = json.loads(f'"{valor}"')
            except Exception:
                valor = valor.replace('\\"', '"').replace("\\n", " ")
            dados[chave] = valor
            
    m_bool = re.search(r'"equipamento_relacionado"\s*:\s*(true|false)', texto, flags=re.IGNORECASE)
    if m_bool:
        dados["equipamento_relacionado"] = m_bool.group(1).lower() == "true"
        
    if not dados.get("equipamento_citado"):
        m_eq = re.search(r'Equipamento relatado:\s*([^.\n]*)', texto, flags=re.IGNORECASE)
        if m_eq:
            dados["equipamento_citado"] = m_eq.group(1).strip()
            dados["equipamento_relacionado"] = True
            
    if not dados.get("problema_equipamento"):
        m_prob = re.search(r'Problema:\s*([^\n]*)', texto, flags=re.IGNORECASE)
        if m_prob:
            dados["problema_equipamento"] = m_prob.group(1).strip()
            
    return dados



def _extrair_json_gemini(texto: str):
    texto_original = (texto or "").strip()
    if not texto_original:
        return {}
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto_original, flags=re.IGNORECASE | re.DOTALL).strip()

    candidatos = [texto]
    # Tenta todos os objetos JSON balanceados encontrados no texto (do maior pro menor),
    # pois pode haver texto explicativo antes/depois ou chaves soltas em exemplos.
    objetos = sorted(_encontrar_objetos_json_balanceados(texto), key=len, reverse=True)
    candidatos.extend(objetos)
    # Fallback simples (comportamento anterior)
    ini = texto.find("{")
    fim = texto.rfind("}")
    if ini != -1 and fim != -1 and fim > ini:
        candidatos.append(texto[ini:fim + 1])

    melhor_placeholder = None
    for candidato in candidatos:
        try:
            dados = json.loads(candidato)
            if isinstance(dados, dict) and (dados.get("descricao") or dados.get("relato")):
                descricao_tmp = (dados.get("descricao") or "").strip()
                relato_tmp = (dados.get("relato") or "").strip()
                if not texto_placeholder_ia(descricao_tmp) and not texto_placeholder_ia(relato_tmp):
                    return dados
                melhor_placeholder = melhor_placeholder or dados
        except Exception:
            continue

    if melhor_placeholder:
        return melhor_placeholder
    # Último recurso: regex direto nas chaves, mesmo com JSON malformado ao redor
    dados_regex = _extrair_campos_json_via_regex(texto)
    if dados_regex:
        print("⚠️ JSON da IA malformado; campos extraídos via regex de recuperação.")
        return dados_regex

    print(f"⚠️ Não foi possível extrair JSON da resposta da IA. Amostra: {texto_original[:300]!r}")
    return {}


def gerar_descricao_e_relato_com_ia(mensagens: list[str], sistema: str = None):
    """Retorna (descricao, relato, equipamento_relacionado, equipamento_citado, problema_equipamento).
    ATENÇÃO: antes retornava só (descricao, relato) — todo ponto de chamada
    precisa desempacotar os 5 valores agora."""
    if not mensagens:
        descricao = anexar_sistema_descricao(DESCRICAO_FALLBACK, sistema)
        relato = anexar_finalizacao_inatividade(RELATO_FALLBACK, mensagens)
        return descricao, relato, False, "", ""

    linhas = preprocessar_mensagens(mensagens)
    prompt = _prompt_combinado(linhas)

    resultado = chamar_claude(prompt)
    dados = _extrair_json_gemini(resultado)

    if resultado and texto_eco_prompt(resultado) and not dados:
        print(f"⚠️ Claude ecoou o próprio prompt em vez do JSON. Amostra: {resultado[:300]!r}")
        resultado = ""

    descricao = (dados.get("descricao") or "").strip()
    relato = (dados.get("relato") or "").strip()
    equipamento_relacionado = _bool_ia(dados.get("equipamento_relacionado"))
    equipamento_citado = (dados.get("equipamento_citado") or "").strip()
    problema_equipamento = (dados.get("problema_equipamento") or "").strip()

    if not descricao or texto_placeholder_ia(descricao):
        print("⚠️ Gemini não retornou descrição válida no JSON combinado. Usando fallback.")
        descricao = gerar_descricao_fallback_mensagens(mensagens)
    if not relato or texto_placeholder_ia(relato):
        print("⚠️ Gemini não retornou relato válido no JSON combinado. Usando fallback.")
        relato = RELATO_FALLBACK
        equipamento_relacionado = False
        equipamento_citado = ""
        problema_equipamento = ""

    descricao = anexar_sistema_descricao(pegar_descricao(descricao, mensagens), sistema)
    relato = anexar_finalizacao_inatividade(limpar_relato(relato), mensagens)
    return descricao, relato, equipamento_relacionado, equipamento_citado, problema_equipamento


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
        "apiKey":   AUVO_API_KEY,
        "apiToken": AUVO_API_TOKEN,
    }
    ultimo_erro = None
    for tentativa in range(1, 5):
        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Connection": "close"},
                timeout=AUVO_LOGIN_TIMEOUT,
            )
            if response.status_code in (500, 502, 503, 504):
                raise Exception(f"HTTP {response.status_code}: Auvo instável no login")
            response.raise_for_status()
            token = response.json()["result"]["accessToken"]

            with AUVO_TOKEN_LOCK:
                AUVO_TOKEN_CACHE["token"] = token
                AUVO_TOKEN_CACHE["expires_at"] = time.time() + 50 * 60

            return token
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            ultimo_erro = e
            if tentativa < 4:
                espera = tentativa * 2
                print(f"⚠️ Login Auvo falhou ({tentativa}/4): {e}. Aguardando {espera}s...")
                time.sleep(espera)
            else:
                raise
        except Exception as e:
            ultimo_erro = e
            if tentativa < 4 and detectar_instabilidade_auvo(e):
                espera = tentativa * 2
                print(f"⚠️ Login Auvo instável ({tentativa}/4): {e}. Aguardando {espera}s...")
                time.sleep(espera)
            else:
                raise

    raise ultimo_erro or Exception("Falha desconhecida ao autenticar no Auvo")

# ---------------------------------------------------------------------------
# Detecção de instabilidade do Auvo
# ---------------------------------------------------------------------------
AUVO_PALAVRAS_INSTABILIDADE = [
    "timeout", "timed out", "service unavailable", "502", "503", "504",
    "bad gateway", "gateway timeout", "internal server error", "500",
    "connection error", "connection refused", "temporarily unavailable",
    "connection aborted", "remotedisconnected", "remote end closed connection",
    "protocolerror",
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

def _normalizar_texto_segmento(texto):
    texto = re.sub(r"\s+", " ", str(texto or "")).strip().upper()
    texto = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("ASCII")
    return texto


def _extrair_resultado_auvo(data):
    resultado = (data or {}).get("result", data)
    if isinstance(resultado, dict) and "entityList" in resultado:
        lista = resultado.get("entityList") or []
        return lista[0] if lista else {}
    if isinstance(resultado, list):
        return resultado[0] if resultado else {}
    return resultado if isinstance(resultado, dict) else {}


def buscar_cliente_auvo(token, customer_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Connection": "close"}
    urls = [
        f"https://api.auvo.com.br/v2/customers/{customer_id}",
        "https://api.auvo.com.br/v2/customers/",
    ]
    for url in urls:
        params = None
        if url.endswith("/customers/"):
            params = {"paramFilter": json.dumps({"id": int(customer_id)})}
        response = HTTP_SESSION.get(url, headers=headers, params=params, timeout=AUVO_LIST_TIMEOUT)
        if response.status_code in (500, 502, 503, 504):
            raise Exception(f"HTTP {response.status_code}: Auvo instável ao consultar cliente")
        if response.status_code == 404:
            continue
        if response.status_code != 200:
            print(f"⚠️ Erro ao consultar cliente Auvo ({response.status_code}): {response.text[:200]}")
            continue
        cliente = _extrair_resultado_auvo(response.json())
        if cliente:
            return cliente
    return {}


def buscar_segmento_auvo(token, segment_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Connection": "close"}
    url = f"https://api.auvo.com.br/v2/segments/{segment_id}"
    response = HTTP_SESSION.get(url, headers=headers, timeout=AUVO_LIST_TIMEOUT)
    if response.status_code != 200:
        print(f"⚠️ Erro ao consultar segmento Auvo ({response.status_code}): {response.text[:200]}")
        return {}
    return _extrair_resultado_auvo(response.json())


def buscar_equipamentos_cliente(token, customer_id) -> list[dict]:
    """Busca os equipamentos cadastrados para um cliente na Auvo.
    Retorna [{"nome": "...", "serial": "..."}]."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Connection": "close"}
    url = "https://api.auvo.com.br/v2/equipments/"
    customer_id_int = int(customer_id)
    params = {
        # O campo de filtro no paramFilter tem que ser o nome real da
        # propriedade retornada pela API de equipamentos: "associatedCustomerId"
        # (confirmado pelo diagnóstico — o item não tem "customerId" nem
        # "idCustomer", só "associatedCustomerId"). Usar "customerId" aqui
        # fazia a Auvo ignorar o filtro e devolver a lista inteira.
        "paramFilter": json.dumps({"associatedCustomerId": customer_id_int}),
        "pageSize": 100,
        "page": 1,
    }

    try:
        response = HTTP_SESSION.get(url, headers=headers, params=params, timeout=AUVO_LIST_TIMEOUT)
        if response.status_code != 200:
            print(f"⚠️ Erro ao consultar equipamentos do cliente ({response.status_code}): {response.text[:200]}")
            return []

        data = response.json()
        resultado = data.get("result", {}) or {}
        lista = resultado.get("entityList") or resultado.get("equipmentList") or []
        if not lista and isinstance(resultado, list):
            lista = resultado

        equipamentos = []
        ignorados_de_outro_cliente = 0
        
        # Diagnóstico: imprime as chaves do primeiro item pra confirmar como a API retorna o cliente
        if lista:
            primeiro = lista[0] if isinstance(lista[0], dict) else {}
            print(f"🔍 [DIAGNÓSTICO equipamentos] Total retornado pela API: {len(lista)}. Chaves do 1º item: {list(primeiro.keys())}. customer solicitado: {customer_id_int}. Valores de identificação do cliente no item: associatedCustomerId={primeiro.get('associatedCustomerId')}, customerId={primeiro.get('customerId')}, idCustomer={primeiro.get('idCustomer')}")
        
        for item in lista:
            if not isinstance(item, dict):
                continue

            # Defesa extra: mesmo com o paramFilter corrigido, nunca confia
            # cegamente no filtro remoto. Se o item trouxer o id do cliente
            # dono do equipamento e ele não bater com o cliente pedido,
            # descarta — isso garante que a lista jamais mistura
            # equipamentos de clientes diferentes, mesmo que a Auvo devolva
            # algo fora do esperado. O campo real é "associatedCustomerId"
            # (customerId/idCustomer são mantidos como fallback só por
            # segurança, caso a API mude de novo).
            id_cliente_item = item.get(
                "associatedCustomerId",
                item.get("customerId", item.get("idCustomer")),
            )
            if id_cliente_item is not None:
                try:
                    if int(id_cliente_item) != customer_id_int:
                        ignorados_de_outro_cliente += 1
                        continue
                except (TypeError, ValueError):
                    pass

            nome = (
                item.get("description")
                or item.get("name")
                or item.get("equipmentDescription")
                or item.get("productDescription")
                or ""
            ).strip()
            serial = (item.get("serialNumber") or item.get("serial") or "").strip()
            if nome:
                equipamentos.append({"nome": nome, "serial": serial})

        if ignorados_de_outro_cliente:
            print(
                f"⚠️ API Auvo retornou {ignorados_de_outro_cliente} equipamento(s) de outro "
                f"cliente para customer_id={customer_id_int}; descartados antes de exibir."
            )

        # Deduplica por nome (preserva a ordem de aparição)
        vistos = set()
        unicos = []
        for eq in equipamentos:
            chave = eq["nome"].upper()
            if chave not in vistos:
                vistos.add(chave)
                unicos.append(eq)
        return unicos
    except Exception as e:
        print(f"⚠️ Erro ao buscar equipamentos do cliente {customer_id}: {e}")
        return []


def verificar_segmento_cliente(token, customer_id, continuar_sem_segmento=False, continuar_sem_contrato=False):
    """
    Verifica, na hora do check-in, o segmento cadastrado no cliente:
      - Sem segmento (segmentId vazio): pede confirmação ("sem segmento cadastrado").
      - Segmento com descrição "SEM CONTRATO": pede confirmação ("sem contrato").
      - Qualquer outro segmento: ok, segue o check-in normalmente.
    """
    cliente = buscar_cliente_auvo(token, customer_id)
    segment_id = cliente.get("segmentId")

    if not segment_id:
        if continuar_sem_segmento:
            print(f"⚠️ Continuando sem segmento cadastrado para cliente {customer_id}.")
            return {"ok": True, "status": "continuar_sem_segmento"}
        return {"ok": False, "status": "cliente_sem_segmento"}

    segmento = buscar_segmento_auvo(token, segment_id)
    descricao_segmento = _normalizar_texto_segmento(segmento.get("description"))

    if descricao_segmento == "SEM CONTRATO":
        if continuar_sem_contrato:
            print(f"⚠️ Continuando sem contrato para cliente {customer_id} (segmento: SEM CONTRATO).")
            return {"ok": True, "status": "continuar_sem_contrato"}
        return {"ok": False, "status": "cliente_sem_contrato"}

    print(f"✅ Cliente {customer_id} possui segmento: {segmento.get('description')}")
    return {"ok": True, "status": "com_segmento", "segmento": segmento.get("description")}


def resposta_cliente_sem_segmento(empresa, codigo_cliente, status):
    mensagens = {
        "cliente_sem_segmento": "Cliente sem segmento cadastrado, deseja continuar o checkin",
        "cliente_sem_contrato": "Cliente sem contrato, deseja continuar o checkin",
    }
    return {
        "status": status,
        "empresa": empresa,
        "codigo_cliente": codigo_cliente,
        "mensagem": mensagens.get(status, "Verifique o cadastro do cliente. Deseja continuar o checkin?"),
    }





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

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Connection": "close"}
    params = {"paramFilter": json.dumps(filtros)}
    url = "https://api.auvo.com.br/v2/tasks/"

    response = HTTP_SESSION.get(url, headers=headers, params=params, timeout=AUVO_LIST_TIMEOUT)
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


def _formatar_data_task_auvo(task_date):
    task_date = (task_date or "").strip()
    data_iso = task_date[:10] if len(task_date) >= 10 else ""
    hora = task_date[11:16] if len(task_date) >= 16 else ""
    data_br = data_iso
    try:
        data_br = datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        pass
    return data_iso, data_br, hora


def _formatar_task_resumo(t):
    task_date = t.get("taskDate", "") or ""
    data_iso, data_br, hora = _formatar_data_task_auvo(task_date)
    return {
        "taskID": t["taskID"],
        "data": data_iso,
        "data_formatada": data_br,
        "hora": hora,
        "taskDate": task_date,
        "descricao": (t.get("orientation") or t.get("description") or "Sem descrição")[:80],
    }

def buscar_task_criada_recentemente(token, customer_id, id_user_to, task_type, data_hora, descricao):
    """
    Quando o POST de criacao da task estoura timeout, o Auvo pode ter criado
    a task mesmo assim. Esta checagem evita esperar/recriar sem confirmar antes.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Connection": "close"}
    data_base = data_hora[:10]
    filtros = {
        "customerId": int(customer_id),
        "startDate": data_base,
        "endDate": data_base,
        "idUserTo": id_user_to,
    }
    params = {"paramFilter": json.dumps(filtros)}
    response = HTTP_SESSION.get("https://api.auvo.com.br/v2/tasks/", headers=headers, params=params, timeout=AUVO_CONFIRM_TIMEOUT)
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
        "Content-Type":  "application/json",
        "Connection":    "close"
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
                timeout=AUVO_CREATE_TIMEOUT
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

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            task_id_timeout = None
            for tentativa_confirmacao in range(1, AUVO_CONFIRM_RETRIES + 1):
                try:
                    task_id_timeout = buscar_task_criada_recentemente(
                        token, customer_id, id_user_to, task_type, data_hora, descricao
                    )
                    if task_id_timeout:
                        _limpar_cache_tasks()
                        return task_id_timeout
                except Exception as erro_confirmacao:
                    print(f"⚠️ Confirmação pós-timeout falhou ({tentativa_confirmacao}/{AUVO_CONFIRM_RETRIES}): {erro_confirmacao}")
                time.sleep(AUVO_CONFIRM_BASE_WAIT * tentativa_confirmacao)

            if tentativa < 3:
                espera = tentativa * 4
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
    Retorna lista de tasks abertas (com check-in mas sem check-out) dos últimos 10 dias para o cliente.
    Cada item: {"taskID": int, "data": str, "data_formatada": str, "hora": str, "descricao": str}
    Retorna [] se não houver nenhuma. Levanta Exception em caso de instabilidade.
    """
    if not customer_id:
        return []

    for tentativa in range(1, 4):
        try:
            entity_list = _buscar_entity_list_tasks(token, customer_id, dias=10)

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
                print("⚠️ entityList vazia — sem atendimento aberto nos últimos 10 dias para este cliente.")

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

def realizar_inicio_pausa_ui_invisivel(task_id, data_pausa, hora_pausa, motivo, cancel_key=None):
    print(f"\n[UI] Iniciando pausa para Task #{task_id}...")
    status_key = f"pausa_{task_id}"
    cancel_keys = (status_key, cancel_key)

    for tentativa in range(1, 4):
        verificar_cancelamento(*cancel_keys)
        with sync_playwright() as playwright:
            browser, context, page = _abrir_browser_e_pagina(playwright)
            try:
                url_tarefa = f"https://app2.auvo.com.br/relatorioTarefas/DetalheTarefa/{task_id}"

                page.goto(url_tarefa, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)
                _garantir_login(page, context, url_destino=url_tarefa)

                # Aguarda o SPA renderizar antes de checar erros HTTP.
                try:
                    page.locator('text="Controle de horas"').first.wait_for(state="visible", timeout=8000)
                except Exception:
                    pass  # se não aparecer, _pagina_com_erro_auvo decide a seguir

                if _pagina_com_erro_auvo(page):
                    raise Exception("auvo_instavel: página de erro HTTP detectada na UI")

                verificar_cancelamento(*cancel_keys)
                task_status[status_key] = "abrindo_task"
                _clicar_editar(page, secao="Controle de horas", timeout=15000)

                btn_add = page.locator("#adicionarAtividadeButton")
                btn_add.wait_for(state="visible", timeout=8000)

                verificar_cancelamento(*cancel_keys)
                task_status[status_key] = "preenchendo_pausa"
                _clicar_item_dropdown(page, "Início da Pausa")

                page.locator('input[name="datePicker"]:visible').last.wait_for(state="visible", timeout=12000)
                _preencher_data_hora(page, data_pausa, hora_pausa)
                _selecionar_motivo_pausa(page, motivo)

                verificar_cancelamento(*cancel_keys)
                task_status[status_key] = "lancando_pausa"
                verificar_cancelamento(*cancel_keys)
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
                task_status[status_key] = "done"
                limpar_cancelamento(status_key)
                return

            except ProcessoCancelado:
                task_status[status_key] = "cancelado"
                print(f"⛔ Início da pausa cancelado para Task #{task_id}.")
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
                    dormir_com_cancelamento(espera, *cancel_keys)
                else:
                    task_status[status_key] = "auvo_instavel" if instavel else "erro"
                    task_status[f"{status_key}_mensagem"] = str(e)
                    print(f"❌ Início da pausa falhou após {tentativa} tentativas.")

            finally:
                browser.close()


def realizar_fim_pausa_ui_invisivel(task_id, data_pausa, hora_pausa, cancel_key=None):
    print(f"\n[UI] Finalizando pausa para Task #{task_id}...")
    status_key = f"despausa_{task_id}"
    cancel_keys = (status_key, cancel_key)

    for tentativa in range(1, 4):
        verificar_cancelamento(*cancel_keys)
        with sync_playwright() as playwright:
            browser, context, page = _abrir_browser_e_pagina(playwright)
            try:
                url_tarefa = f"https://app2.auvo.com.br/relatorioTarefas/DetalheTarefa/{task_id}"

                page.goto(url_tarefa, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)
                _garantir_login(page, context, url_destino=url_tarefa)

                # Aguarda o SPA renderizar antes de checar erros HTTP.
                try:
                    page.locator('text="Controle de horas"').first.wait_for(state="visible", timeout=8000)
                except Exception:
                    pass  # se não aparecer, _pagina_com_erro_auvo decide a seguir

                if _pagina_com_erro_auvo(page):
                    raise Exception("auvo_instavel: página de erro HTTP detectada na UI")

                verificar_cancelamento(*cancel_keys)
                task_status[status_key] = "abrindo_task"
                _clicar_editar(page, secao="Controle de horas", timeout=15000)

                btn_add = page.locator("#adicionarAtividadeButton")
                btn_add.wait_for(state="visible", timeout=8000)

                verificar_cancelamento(*cancel_keys)
                task_status[status_key] = "preenchendo_despausa"
                _clicar_item_dropdown(page, "Fim da Pausa")

                page.locator('input[name="datePicker"]:visible').last.wait_for(state="visible", timeout=12000)
                _preencher_data_hora(page, data_pausa, hora_pausa)

                verificar_cancelamento(*cancel_keys)
                task_status[status_key] = "lancando_despausa"
                verificar_cancelamento(*cancel_keys)
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
                task_status[status_key] = "done"
                limpar_cancelamento(status_key)
                return

            except ProcessoCancelado:
                task_status[status_key] = "cancelado"
                print(f"⛔ Fim da pausa cancelado para Task #{task_id}.")
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
                    dormir_com_cancelamento(espera, *cancel_keys)
                else:
                    task_status[status_key] = "auvo_instavel" if instavel else "erro"
                    task_status[f"{status_key}_mensagem"] = str(e)
                    print(f"❌ Fim da pausa falhou após {tentativa} tentativas.")

            finally:
                browser.close()
# Check-in (com retry em instabilidade do Auvo)
# ---------------------------------------------------------------------------
task_status = {}
task_cancelados = set()
task_cancelados_lock = threading.Lock()

class ProcessoCancelado(Exception):
    pass


def solicitar_cancelamento(chave):
    chave = str(chave or '').strip()
    if not chave:
        return False
    with task_cancelados_lock:
        task_cancelados.add(chave)
    task_status[chave] = "cancelado"
    return True


def limpar_cancelamento(chave):
    chave = str(chave or '').strip()
    if not chave:
        return
    with task_cancelados_lock:
        task_cancelados.discard(chave)


def cancelamento_solicitado(*chaves):
    chaves_normalizadas = [str(chave) for chave in chaves if chave is not None]
    with task_cancelados_lock:
        return any(chave in task_cancelados for chave in chaves_normalizadas)


def verificar_cancelamento(*chaves):
    if cancelamento_solicitado(*chaves):
        raise ProcessoCancelado("Processo cancelado pelo usuário.")


def dormir_com_cancelamento(segundos, *chaves):
    fim = time.time() + segundos
    while time.time() < fim:
        verificar_cancelamento(*chaves)
        time.sleep(min(0.5, fim - time.time()))


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
        url_atual = page.url

        # Títulos genuínos de páginas de erro HTTP de servidor/proxy.
        # ATENÇÃO: NÃO usar substring simples ("502" in titulo) pois o título
        # normal do Auvo inclui o ID da task, ex: "auvo # detalhes da tarefa 75023805"
        # — o ID 75023805 contém "502" e causaria falso positivo.
        # A checagem usa regex de palavra-isolada para os códigos numéricos,
        # e substring apenas para as frases textuais que nunca aparecem em títulos normais.
        import re as _re
        # Códigos HTTP só disparam se aparecerem como "palavra" isolada no título
        # (ex: título "502 Bad Gateway" bate; "tarefa 75023805" não bate)
        if _re.search(r'\b(502|503|504)\b', titulo):
            return True
        # Frases textuais de erro — substring é segura aqui pois são únicas
        erros_titulo_texto = ["bad gateway", "service unavailable", "gateway timeout", "server error"]
        if any(e in titulo for e in erros_titulo_texto):
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


def realizar_checkin_ui_invisivel(task_id, data_checkin, hora_checkin, inicio=None, cancel_key=None):
    print(f"\n[UI] Iniciando check-in para Task #{task_id}...")
    status_key = str(task_id)
    cancel_keys = (status_key, cancel_key)

    for tentativa in range(1, 4):
        verificar_cancelamento(*cancel_keys)
        with sync_playwright() as playwright:
            browser, context, page = _abrir_browser_e_pagina(playwright)
            try:
                url_tarefa = f"https://app2.auvo.com.br/relatorioTarefas/DetalheTarefa/{task_id}"

                page.goto(url_tarefa, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)
                _garantir_login(page, context, url_destino=url_tarefa)

                if url_tarefa not in page.url:
                    page.goto(url_tarefa, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)

                # Aguarda o SPA renderizar antes de checar erros HTTP.
                try:
                    page.locator('text="Controle de horas"').first.wait_for(state="visible", timeout=8000)
                except Exception:
                    pass  # se não aparecer, _pagina_com_erro_auvo decide a seguir

                if _pagina_com_erro_auvo(page):
                    raise Exception("auvo_instavel: página de erro HTTP detectada na UI")

                verificar_cancelamento(*cancel_keys)
                _clicar_editar(page, secao="Controle de horas")

                btn_add = page.locator("#adicionarAtividadeButton")
                btn_add.wait_for(state="visible", timeout=10000)

                verificar_cancelamento(*cancel_keys)
                _clicar_item_dropdown(page, "Check-In")

                # Aguarda o modal de data/hora abrir
                page.locator('input[name="datePicker"]:visible').last.wait_for(state="visible", timeout=12000)
                _preencher_data_hora(page, data_checkin, hora_checkin)

                verificar_cancelamento(*cancel_keys)
                btn_salvar = page.locator("#edicao-salvar:visible").last
                btn_salvar.wait_for(state="visible", timeout=8000)
                btn_salvar.click(force=True)
                page.wait_for_selector('#edicao-salvar', state="hidden", timeout=10000)

                context.storage_state(path=AUVO_STATE_PATH)
                print(f"✓ Task #{task_id} — check-in salvo!")
                _limpar_cache_tasks()
                task_status[status_key] = "done"
                limpar_cancelamento(status_key)

                if inicio:
                    total = time.time() - inicio
                    print(f"⏱️ Tempo total (clique → check-in salvo): {total:.1f}s")
                return  # sucesso — sai do loop

            except ProcessoCancelado:
                task_status[status_key] = "cancelado"
                print(f"⛔ Check-in cancelado para Task #{task_id}.")
                return

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
                        task_status[status_key] = "auvo_instavel"
                        print(f"⚠️ Auvo com instabilidade. Aguardando {espera}s antes de tentar novamente...")
                    else:
                        print(f"⚠️ Timeout de UI (app lento). Aguardando {espera}s antes de tentar novamente...")
                    dormir_com_cancelamento(espera, *cancel_keys)
                else:
                    task_status[status_key] = "auvo_instavel" if instavel else "erro"
                    print(f"❌ Check-in falhou após {tentativa} tentativas.")

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Check-out (com retry em instabilidade do Auvo)
# ---------------------------------------------------------------------------
def realizar_checkout_ui_invisivel(task_id, data_checkout, hora_checkout, relato, cancel_key=None):
    print(f"\n[UI] Iniciando check-out para Task #{task_id}...")
    status_key = f"checkout_{task_id}"
    cancel_keys = (status_key, cancel_key)

    for tentativa in range(1, 4):
        verificar_cancelamento(*cancel_keys)
        with sync_playwright() as playwright:
            browser, context, page = _abrir_browser_e_pagina(playwright)
            try:
                url_tarefa = f"https://app2.auvo.com.br/relatorioTarefas/DetalheTarefa/{task_id}"

                page.goto(url_tarefa, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)
                _garantir_login(page, context, url_destino=url_tarefa)

                if url_tarefa not in page.url:
                    page.goto(url_tarefa, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)

                # Aguarda o SPA renderizar antes de checar erros HTTP.
                try:
                    page.locator('text="Relato do usuário"').first.wait_for(state="visible", timeout=8000)
                except Exception:
                    pass  # se não aparecer, _pagina_com_erro_auvo decide a seguir

                if _pagina_com_erro_auvo(page):
                    raise Exception("auvo_instavel: página de erro HTTP detectada na UI")

                verificar_cancelamento(*cancel_keys)
                page.locator('text="Relato do usuário"').first.wait_for(state="visible", timeout=AUVO_UI_TIMEOUT)
                print(f"✅ Página pronta: {page.url}")

                # ── PASSO 1: Preenche e salva o relato ───────────────────────────
                print("Preenchendo relato...")
                verificar_cancelamento(*cancel_keys)
                _clicar_editar(page, secao="Relato do usuário")

                campo_relato = page.locator('textarea, div[contenteditable="true"]').first
                campo_relato.wait_for(state="visible", timeout=8000)
                campo_relato.click()
                page.keyboard.press("Control+A")
                campo_relato.fill(relato)

                verificar_cancelamento(*cancel_keys)

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

                verificar_cancelamento(*cancel_keys)

                # ── PASSO 2: Abre "Controle de horas" e lança check-out ──────
                print("Lançando check-out...")
                # _clicar_editar já aguarda #adicionarAtividadeButton ficar visível
                _clicar_editar(page, secao="Controle de horas", timeout=15000)

                # Aguarda novamente com timeout generoso (confirmação dupla)
                btn_add = page.locator("#adicionarAtividadeButton")
                btn_add.wait_for(state="visible", timeout=8000)

                verificar_cancelamento(*cancel_keys)
                _clicar_item_dropdown(page, "Check-Out")

                # Aguarda o modal de data/hora abrir
                page.locator('input[name="datePicker"]:visible').last.wait_for(state="visible", timeout=12000)
                _preencher_data_hora(page, data_checkout, hora_checkout)

                verificar_cancelamento(*cancel_keys)

                # ── PASSO 3: Salva check-out com confirmação em camadas ──────
                verificar_cancelamento(*cancel_keys)
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
                task_status[status_key] = "done"
                limpar_cancelamento(status_key)
                return  # sucesso

            except ProcessoCancelado:
                task_status[status_key] = "cancelado"
                print(f"⛔ Check-out cancelado para Task #{task_id}.")
                return

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
                        task_status[status_key] = "auvo_instavel"
                        print(f"⚠️ Auvo com instabilidade. Aguardando {espera}s antes de tentar novamente...")
                    elif eh_timeout_ui:
                        print(f"⚠️ Timeout no salvamento (UI lenta). Aguardando {espera}s e tentando novamente...")
                    else:
                        print(f"⚠️ Erro genérico. Aguardando {espera}s antes de tentar novamente...")
                    dormir_com_cancelamento(espera, *cancel_keys)
                else:
                    task_status[status_key] = "auvo_instavel" if instavel else "erro"
                    print(f"❌ Check-out falhou após {tentativa} tentativas.")

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Apelidos de equipamento (tabela equipamentos_keywords no Supabase) e
# comparação fuzzy contra o que está cadastrado pro cliente na Auvo.
# ---------------------------------------------------------------------------
_EQUIPAMENTOS_KEYWORDS_CACHE = {}


def _obter_equipamentos_keywords(cliente) -> dict:
    """Busca a tabela de apelidos (equipamentos_keywords) no Supabase.
    Chave = apelido normalizado, valor = nome oficial do equipamento.
    Se a tabela não existir/falhar, segue sem apelidos (usa o nome cru)."""
    if _EQUIPAMENTOS_KEYWORDS_CACHE:
        return _EQUIPAMENTOS_KEYWORDS_CACHE

    try:
        resp = cliente.table("equipamentos_keywords").select("apelido, equipamento_oficial").execute()
        for linha in resp.data or []:
            apelido = _normalizar_equipamento(linha.get("apelido") or "")
            oficial = (linha.get("equipamento_oficial") or "").strip()
            if apelido and oficial:
                _EQUIPAMENTOS_KEYWORDS_CACHE[apelido] = oficial
        if _EQUIPAMENTOS_KEYWORDS_CACHE:
            print(f"✅ Apelidos de equipamento carregados do Supabase: {len(_EQUIPAMENTOS_KEYWORDS_CACHE)} entradas")
    except Exception as e:
        print(f"⚠️ Não foi possível buscar equipamentos_keywords no Supabase: {e}")

    return _EQUIPAMENTOS_KEYWORDS_CACHE


def _normalizar_equipamento(texto: str) -> str:
    texto = (texto or "").upper().strip()
    texto = unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('ASCII')
    texto = re.sub(r'[^A-Z0-9 ]', '', texto)
    texto = re.sub(r'\s+', ' ', texto).strip()
    return texto


_obter_equipamentos_keywords(_supabase_client)


def aplicar_apelido_equipamento(nome_citado: str) -> str:
    """Traduz um apelido/menção do chat (ex: 'kurumin') pro nome oficial
    cadastrado no Auvo, usando a tabela de apelidos. Match exato primeiro,
    depois parcial (apelido contido no texto ou vice-versa). Se não achar
    nada, devolve o nome citado original sem alterar."""
    if not nome_citado:
        return nome_citado

    apelidos = _obter_equipamentos_keywords(_supabase_client)
    chave = _normalizar_equipamento(nome_citado)

    if chave in apelidos:
        return apelidos[chave]

    for apelido, oficial in apelidos.items():
        if apelido in chave or chave in apelido:
            return oficial

    return nome_citado


def equipamento_esta_vinculado(nome_equipamento: str, equipamentos_cliente: list[dict], limiar: float = 0.5) -> bool:
    """Verifica (por fuzzy match) se nome_equipamento corresponde a algum
    equipamento já cadastrado pro cliente no Auvo. Usado tanto pra apelidos
    conhecidos quanto pra atribuições livres (ex: 'Kurumin' batendo com
    'RELOGIO DE PONTO KURUMIN' cadastrado no cliente)."""
    if not nome_equipamento or not equipamentos_cliente:
        return False

    alvo = _normalizar_equipamento(nome_equipamento)
    for eq in equipamentos_cliente:
        nome_cadastrado = _normalizar_equipamento(eq.get("nome", ""))
        if not nome_cadastrado:
            continue
        if alvo in nome_cadastrado or nome_cadastrado in alvo:
            return True
        similaridade = difflib.SequenceMatcher(None, alvo, nome_cadastrado).ratio()
        if similaridade >= limiar:
            return True
    return False


def verificar_equipamento_checkout(token, customer_id, nome_cliente: str,
                                     equipamento_relacionado: bool,
                                     equipamento_citado: str,
                                     task_id: str = "") -> dict:
    """
    Chamar no fluxo de checkout, depois de gerar o relato e ANTES de mostrar
    a confirmação final pro atendente. Retorna um dict com um destes status:
      - {"status": "nao_aplica"}
          → atendimento não é sobre equipamento, segue o checkout normal.
      - {"status": "selecionar", "opcoes": [nomes...]}
          → é sobre equipamento mas ninguém citou qual; o front-end deve
            abrir o modal de seleção com essas opções (equipamentos
            cadastrados do cliente no Auvo). Se a lista vier vazia, o
            front-end deve liberar um campo de texto livre pro atendente
            digitar o nome — esse texto entra direto como "não vinculado".
      - {"status": "vinculado", "equipamento": nome}
          → equipamento identificado E já cadastrado pro cliente. Segue normal.
      - {"status": "nao_vinculado", "equipamento": nome}
          → equipamento identificado mas NÃO cadastrado pro cliente.
            Já foi gravado na planilha de equipamentos não vinculados.
    """
    if not equipamento_relacionado:
        return {"status": "nao_aplica"}

    equipamentos_cliente = buscar_equipamentos_cliente(token, customer_id)
    opcoes = [eq["nome"] for eq in equipamentos_cliente]

    if not equipamento_citado:
        return {"status": "selecionar", "opcoes": opcoes}

    nome_oficial = aplicar_apelido_equipamento(equipamento_citado)

    if equipamento_esta_vinculado(nome_oficial, equipamentos_cliente):
        return {"status": "vinculado", "equipamento": nome_oficial}

    # Se a IA citou um equipamento mas ele não bate com nenhum dos vinculados, 
    # abre a lista para o usuário confirmar qual é de fato (ou registrar novo manualmente).
    return {"status": "selecionar", "opcoes": opcoes}


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
def fluxo_checkin_e_checkout(empresa, mensagens, codigo_cliente, inicio_escolhido=None, sistema=None, cancel_key=None):
    """
    Executa o ciclo completo:
      1. Gera descrição com IA
      2. Cria task no Auvo
      3. Faz check-in
      4. Faz check-out com relato
    Atualiza task_status["full_flow_{empresa}"] ao longo do processo.
    """
    chave = f"full_flow_{codigo_cliente}"
    task_id = None
    try:
        limpar_cancelamento(chave)
        verificar_cancelamento(chave, cancel_key)
        task_status[chave] = "gerando_textos"

        # --- Uma chamada ao Gemini: descrição + relato ---
        descricao_resultado = [None]
        relato_resultado = [None]
        equipamento_relacionado_resultado = [False]
        equipamento_citado_resultado = [""]
        problema_equipamento_resultado = [""]
        def _gerar_textos():
            (descricao_resultado[0], relato_resultado[0],
             equipamento_relacionado_resultado[0], equipamento_citado_resultado[0],
             problema_equipamento_resultado[0]) = gerar_descricao_e_relato_com_ia(mensagens, sistema)
            print(f"📝 Descrição: {descricao_resultado[0]}")
            print(f"📝 Relato: {relato_resultado[0]}")

        t_desc = threading.Thread(target=_gerar_textos)
        t_desc.start()

        if inicio_escolhido:
            hora_inicio = inicio_escolhido.get("hora")
            data_inicio = inicio_escolhido.get("data")
        else:
            hora_inicio, data_inicio, _ = encontrar_inicio_atendimento(mensagens)
        hora_checkout, data_checkout  = pegar_hora_e_data_ultima_mensagem(mensagens)

        t_desc.join()
        verificar_cancelamento(chave, cancel_key)

        descricao_confirmada, relato_confirmado = aguardar_confirmacao_textos_fluxo(
            chave,
            descricao_resultado[0],
            relato_resultado[0],
            cancel_key=cancel_key,
        )
        verificar_cancelamento(chave, cancel_key)

        if not hora_inicio:
            task_status[chave] = "erro"
            print("❌ Hora de início não encontrada.")
            return

        token = autenticar()
        verificar_cancelamento(chave, cancel_key)

        task_status[chave] = "criando_task"
        task_id = criar_atendimento_com_checkin(
            token        = token,
            customer_id  = codigo_cliente,
            id_user_from = ID_USER,
            id_user_to   = ID_USER,
            task_type    = 200374,
            data_hora    = montar_data_hora_auvo(data_inicio, hora_inicio),
            descricao    = descricao_confirmada
        )
        print(f"✅ Task #{task_id} criada no fluxo combinado.")
        verificar_cancelamento(chave, cancel_key, str(task_id))

        task_status[chave] = "checkin"
        realizar_checkin_ui_invisivel(task_id, data_inicio, hora_inicio, cancel_key=cancel_key or chave)

        # Aguarda check-in confirmar antes de ir ao checkout
        for _ in range(30):
            verificar_cancelamento(chave, cancel_key, str(task_id))
            if task_status.get(str(task_id)) == "cancelado":
                raise ProcessoCancelado("Processo cancelado pelo usuário.")
            if task_status.get(str(task_id)) == "done":
                break
            dormir_com_cancelamento(2, chave, cancel_key, str(task_id))

        verificar_cancelamento(chave, cancel_key, str(task_id))

        # --- Verificacao de equipamento antes do checkout ---
        # O token pode ter expirado durante check-in; autenticar() renova se necessario.
        _token_equip = autenticar()
        verificacao_equipamento = verificar_equipamento_checkout(
            _token_equip,
            codigo_cliente,
            empresa,
            equipamento_relacionado_resultado[0],
            equipamento_citado_resultado[0],
            task_id,
        )

        if verificacao_equipamento.get("status") == "selecionar":
            # Equipamento nao identificado -> abre modal de selecao pro atendente
            relato = aguardar_confirmacao_relato_checkout(
                task_id,
                data_checkout or data_inicio,
                hora_checkout,
                relato_confirmado,
                cancel_key=cancel_key or chave,
                flow_key=chave,
                verificacao_equipamento=verificacao_equipamento,
            )
        else:
            relato = relato_confirmado

        task_status[f"checkout_{task_id}_relato"] = relato
        verificar_cancelamento(chave, cancel_key, str(task_id), f"checkout_{task_id}")

        task_status[chave] = "checkout"
        realizar_checkout_ui_invisivel(task_id, data_checkout or data_inicio, hora_checkout, relato, cancel_key=cancel_key or chave)

        # Aguarda checkout
        for _ in range(30):
            verificar_cancelamento(chave, cancel_key, f"checkout_{task_id}")
            if task_status.get(f"checkout_{task_id}") == "cancelado":
                raise ProcessoCancelado("Processo cancelado pelo usuário.")
            if task_status.get(f"checkout_{task_id}") == "done":
                break
            dormir_com_cancelamento(2, chave, cancel_key, f"checkout_{task_id}")

        verificar_cancelamento(chave, cancel_key, f"checkout_{task_id}")
        task_status[chave] = "done"
        task_status["full_flow_task_id"] = task_id
        limpar_cancelamento(chave)
        print(f"✅ Fluxo completo concluído! Task #{task_id}")

    except ProcessoCancelado:
        task_status[chave] = "cancelado"
        if task_id:
            task_status[str(task_id)] = "cancelado"
            task_status[f"checkout_{task_id}"] = "cancelado"
        print(f"⛔ Fluxo completo cancelado para {empresa}.")

    except Exception as e:
        instavel = detectar_instabilidade_auvo(e) or "instabilidade" in str(e).lower()
        task_status[chave] = "auvo_instavel" if instavel else "erro"
        task_status[f"{chave}_mensagem"] = str(e)
        print(f"❌ Erro no fluxo combinado: {e}")


# ---------------------------------------------------------------------------
# Confirmação de relato antes do check-out
# ---------------------------------------------------------------------------
def preparar_confirmacao_relato_checkout(task_id, data_checkout, hora_checkout, relato, cancel_key=None, flow_key=None, verificacao_equipamento=None):
    status_key = f"checkout_{task_id}"
    task_status[status_key] = "aguardando_confirmacao_relato"
    task_status[f"{status_key}_relato"] = relato or RELATO_FALLBACK
    task_status[f"{status_key}_data"] = data_checkout
    task_status[f"{status_key}_hora"] = hora_checkout
    task_status[f"{status_key}_cancel_key"] = cancel_key
    task_status[f"{status_key}_verificacao_equipamento"] = verificacao_equipamento
    if flow_key:
        task_status[flow_key] = "aguardando_confirmacao_relato"
        task_status[f"{flow_key}_checkout_key"] = status_key
        task_status[f"{flow_key}_relato"] = relato or RELATO_FALLBACK
        task_status[f"{flow_key}_data_checkout"] = data_checkout
        task_status[f"{flow_key}_hora_checkout"] = hora_checkout
        task_status[f"{flow_key}_task_id"] = task_id
        task_status[f"{flow_key}_verificacao_equipamento"] = verificacao_equipamento
    return {
        "status": "confirmar_relato",
        "task_id": task_id,
        "status_key": status_key,
        "flow_key": flow_key,
        "relato": relato or RELATO_FALLBACK,
        "data": data_checkout,
        "hora": hora_checkout,
        "verificacao_equipamento": verificacao_equipamento,
    }


def aguardar_confirmacao_relato_checkout(task_id, data_checkout, hora_checkout, relato, cancel_key=None, flow_key=None, verificacao_equipamento=None):
    status_key = f"checkout_{task_id}"
    preparar_confirmacao_relato_checkout(task_id, data_checkout, hora_checkout, relato, cancel_key=cancel_key, flow_key=flow_key, verificacao_equipamento=verificacao_equipamento)
    while True:
        verificar_cancelamento(flow_key, cancel_key, status_key)
        if task_status.get(status_key) == "cancelado":
            raise ProcessoCancelado("Processo cancelado pelo usuário.")
        confirmado = task_status.get(f"{status_key}_relato_confirmado")
        if confirmado is not None:
            return confirmado
        dormir_com_cancelamento(1, flow_key, cancel_key, status_key)


def preparar_confirmacao_textos_fluxo(flow_key, descricao, relato):
    task_status[flow_key] = "aguardando_confirmacao_textos"
    task_status[f"{flow_key}_descricao"] = descricao or DESCRICAO_FALLBACK
    task_status[f"{flow_key}_relato"] = relato or RELATO_FALLBACK


def aguardar_confirmacao_textos_fluxo(flow_key, descricao, relato, cancel_key=None):
    preparar_confirmacao_textos_fluxo(flow_key, descricao, relato)
    while True:
        verificar_cancelamento(flow_key, cancel_key)
        confirmado = task_status.get(f"{flow_key}_textos_confirmados")
        if isinstance(confirmado, dict):
            return (
                (confirmado.get("descricao") or descricao or DESCRICAO_FALLBACK).strip(),
                (confirmado.get("relato") or relato or RELATO_FALLBACK).strip(),
            )
        dormir_com_cancelamento(1, flow_key, cancel_key)


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

            # Passa dados do fluxo completo, se houver
            if task_id.startswith("full_flow_"):
                if f"{task_id}_task_id" in task_status:
                    extra["task_id"] = task_status[f"{task_id}_task_id"]
                elif "full_flow_task_id" in task_status:
                    extra["task_id"] = task_status["full_flow_task_id"]
                for origem, destino in [
                    (f"{task_id}_checkout_key", "status_key"),
                    (f"{task_id}_relato", "relato"),
                    (f"{task_id}_data_checkout", "data"),
                    (f"{task_id}_hora_checkout", "hora"),
                    (f"{task_id}_verificacao_equipamento", "verificacao_equipamento"),
                ]:
                    if origem in task_status:
                        extra[destino] = task_status[origem]

            if task_id.startswith("checkout_"):
                for origem, destino in [
                    (f"{task_id}_relato", "relato"),
                    (f"{task_id}_data", "data"),
                    (f"{task_id}_hora", "hora"),
                ]:
                    if origem in task_status:
                        extra[destino] = task_status[origem]

            if task_id.startswith("full_flow_"):
                for origem, destino in [
                    (f"{task_id}_descricao", "descricao"),
                    (f"{task_id}_relato", "relato"),
                ]:
                    if origem in task_status:
                        extra[destino] = task_status[origem]

            # Passa mensagem de erro, se houver
            msg_key = f"{task_id}_mensagem"
            if msg_key in task_status:
                extra["mensagem"] = task_status[msg_key]

            if cancelamento_solicitado(task_id):
                status = "cancelado"

            self.send_response(200)
            self.send_header("Content-type",                "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": status, **extra}).encode())

    def do_POST(self):
        # ── CANCELAMENTO ────────────────────────────────────────────────────
        if self.path == "/cancel":
            try:
                content_length = int(self.headers.get('Content-Length', 0) or 0)
                dados = json.loads(self.rfile.read(content_length) or b"{}")
                chave = dados.get("chave") or dados.get("key") or dados.get("task_id")
                if not solicitar_cancelamento(chave):
                    raise Exception("Chave de processo inválida para cancelamento.")
                resposta = {"status": "ok", "chave": str(chave)}
            except Exception as e:
                resposta = {"status": "erro", "mensagem": str(e)}
            self._enviar_json(resposta)

        # ── CONFIRMAR RELATO DO CHECK-OUT ──────────────────────────────────
        elif self.path == "/auvo-checkout-confirmar":
            try:
                content_length = int(self.headers.get('Content-Length', 0) or 0)
                dados = json.loads(self.rfile.read(content_length) or b"{}")
                task_id = int(dados.get("task_id"))
                status_key = dados.get("status_key") or f"checkout_{task_id}"
                flow_key = dados.get("flow_key")
                relato = (dados.get("relato") or "").strip()
                data_checkout = dados.get("data") or dados.get("data_checkout") or task_status.get(f"{status_key}_data")
                hora_checkout = dados.get("hora") or dados.get("hora_checkout") or task_status.get(f"{status_key}_hora")
                cancel_key = dados.get("cancel_key") or task_status.get(f"{status_key}_cancel_key")

                if not relato:
                    raise Exception("Relato vazio. Revise o texto antes de confirmar o check-out.")
                if not data_checkout or not hora_checkout:
                    raise Exception("Data ou hora do check-out não encontrada.")

                task_status[f"{status_key}_relato_confirmado"] = relato
                task_status[f"{status_key}_relato"] = relato
                task_status[status_key] = "relato_ok"

                if flow_key:
                    task_status[f"{flow_key}_relato"] = relato
                    task_status[flow_key] = "checkout"
                else:
                    threading.Thread(
                        target=realizar_checkout_ui_invisivel,
                        args=(task_id, data_checkout, hora_checkout, relato, cancel_key)
                    ).start()

                resposta = {"status": "ok", "task_id": task_id, "status_key": status_key, "hora": hora_checkout, "data": data_checkout}

            except ProcessoCancelado as e:
                resposta = {"status": "cancelado", "mensagem": str(e)}

            except Exception as e:
                instavel = detectar_instabilidade_auvo(e) or "instabilidade" in str(e).lower()
                print("❌ Erro ao confirmar relato do checkout:", e)
                resposta = {
                    "status": "auvo_instavel" if instavel else "erro",
                    "mensagem": str(e)
                }

            self._enviar_json(resposta)

        # ── SELECIONAR EQUIPAMENTO (quando a IA não identificou qual é) ────
        elif self.path == "/auvo-checkout-selecionar-equipamento":
            try:
                content_length = int(self.headers.get('Content-Length', 0) or 0)
                dados = json.loads(self.rfile.read(content_length) or b"{}")
                codigo_cliente = dados.get("codigo_cliente")
                status_key = dados.get("status_key")
                task_id = dados.get("task_id")
                empresa = (dados.get("empresa") or "").strip()
                equipamento_selecionado = (dados.get("equipamento") or "").strip()
                relato_editado = (dados.get("relato") or "").strip()

                if not codigo_cliente:
                    raise Exception("codigo_cliente não informado.")
                if not equipamento_selecionado:
                    raise Exception("Nenhum equipamento informado/selecionado.")

                contexto = task_status.get(f"checkout_contexto_{codigo_cliente}") or {}
                if task_id:
                    contexto["task_id"] = task_id
                relato_atual = relato_editado or contexto.get("relato") or (task_status.get(f"{status_key}_relato") if status_key else None) or RELATO_FALLBACK
                problema_equipamento = contexto.get("problema_equipamento") or ""
                nome_cliente = empresa or contexto.get("empresa") or ""

                token = autenticar()
                equipamentos_cliente = buscar_equipamentos_cliente(token, codigo_cliente)
                nome_oficial = aplicar_apelido_equipamento(equipamento_selecionado)
                vinculado = equipamento_esta_vinculado(nome_oficial, equipamentos_cliente)

                if not vinculado:
                    gravar_equipamento_nao_vinculado(nome_cliente, nome_oficial, task_id or contexto.get("task_id") or "")

                relato_atualizado = inserir_linha_equipamento(relato_atual, nome_oficial, problema_equipamento)

                contexto["relato"] = relato_atualizado
                contexto["verificacao_equipamento"] = {
                    "status": "vinculado" if vinculado else "nao_vinculado",
                    "equipamento": nome_oficial,
                }
                task_status[f"checkout_contexto_{codigo_cliente}"] = contexto
                if status_key:
                    task_status[f"{status_key}_relato"] = relato_atualizado

                resposta = {
                    "status": "ok",
                    "relato": relato_atualizado,
                    "equipamento": nome_oficial,
                    "vinculado": vinculado,
                }

            except Exception as e:
                print("❌ Erro ao selecionar equipamento:", e)
                resposta = {"status": "erro", "mensagem": str(e)}

            self._enviar_json(resposta)

        # ── CONFIRMAR TEXTOS DO FLUXO COMPLETO ─────────────────────────────
        elif self.path == "/auvo-checkout-criar-confirmar-textos":
            try:
                content_length = int(self.headers.get('Content-Length', 0) or 0)
                dados = json.loads(self.rfile.read(content_length) or b"{}")
                flow_key = dados.get("flow_key")
                descricao = (dados.get("descricao") or "").strip()
                relato = (dados.get("relato") or "").strip()
                if not flow_key:
                    raise Exception("Chave do fluxo não informada.")
                if not descricao:
                    raise Exception("Descrição vazia. Revise antes de confirmar.")
                if not relato:
                    raise Exception("Relato vazio. Revise antes de confirmar.")
                task_status[f"{flow_key}_textos_confirmados"] = {
                    "descricao": descricao,
                    "relato": relato,
                }
                task_status[f"{flow_key}_descricao"] = descricao
                task_status[f"{flow_key}_relato"] = relato
                task_status[flow_key] = "criando_task"
                resposta = {"status": "ok", "flow_key": flow_key}
            except ProcessoCancelado as e:
                resposta = {"status": "cancelado", "mensagem": str(e)}
            except Exception as e:
                resposta = {"status": "erro", "mensagem": str(e)}
            self._enviar_json(resposta)

        # ── CHECK-IN ────────────────────────────────────────────────────────
        elif self.path == "/auvo":
            try:
                inicio = time.time()
                content_length = int(self.headers['Content-Length'])
                dados          = json.loads(self.rfile.read(content_length))
                empresa        = dados.get("empresa", "")
                codigo_cliente = dados.get("codigo_cliente")
                inicio_escolhido = dados.get("inicio_escolhido")
                sistema_cliente = dados.get("sistema")
                cancel_key = dados.get("cancel_key")
                if cancel_key:
                    limpar_cancelamento(cancel_key)

                print(f"\n>> Novo Lançamento: {empresa} | Usuário: {NOME_USER} (ID: {ID_USER})")
                print(f">> Sistema capturado: {sistema_cliente or '<nenhum>'}")

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
                verificar_cancelamento(cancel_key)

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

                token = autenticar()
                verificar_cancelamento(cancel_key)
                segmento = verificar_segmento_cliente(
                    token,
                    codigo_cliente,
                    continuar_sem_segmento=bool(dados.get("continuar_sem_segmento")),
                    continuar_sem_contrato=bool(dados.get("continuar_sem_contrato"))
                )
                if not segmento.get("ok"):
                    self._enviar_json(resposta_cliente_sem_segmento(empresa, codigo_cliente, segmento.get("status")))
                    return

                print("🤖 Gerando descrição com IA...")
                verificar_cancelamento(cancel_key)
                descricao_ia = gerar_descricao_com_ia(mensagens, sistema_cliente)
                print(f"📝 Descrição:\n{descricao_ia}\n")

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
                limpar_cancelamento(str(task_id))
                threading.Thread(
                    target=realizar_checkin_ui_invisivel,
                    args=(task_id, data_chat, hora_chat, inicio, cancel_key)
                ).start()

                resposta = {"status": "ok", "task_id": task_id, "descricao": descricao_ia, "empresa": empresa, "codigo_cliente": codigo_cliente}

            except ProcessoCancelado as e:
                resposta = {"status": "cancelado", "mensagem": str(e)}

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
                cancel_key = dados.get("cancel_key")
                if cancel_key:
                    limpar_cancelamento(cancel_key)

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

                contexto_checkout = task_status.get(f"checkout_contexto_{codigo_cliente}") if codigo_cliente else None
                confirmou_task_checkout = bool(dados.get("confirmar_task_checkout"))
                if task_id_escolhido and confirmou_task_checkout and isinstance(contexto_checkout, dict):
                    task_id = int(task_id_escolhido)
                    contexto_checkout["task_id"] = task_id
                    relato = contexto_checkout.get("relato") or RELATO_FALLBACK
                    hora_checkout = contexto_checkout.get("hora_checkout")
                    data_checkout = contexto_checkout.get("data_checkout")
                    verificacao_equipamento = contexto_checkout.get("verificacao_equipamento")
                    if isinstance(verificacao_equipamento, dict) and verificacao_equipamento.get("registro_pendente"):
                        gravar_equipamento_nao_vinculado(
                            contexto_checkout.get("empresa") or empresa,
                            verificacao_equipamento.get("equipamento") or "",
                            task_id,
                        )
                        verificacao_equipamento["registrado"] = True
                        verificacao_equipamento["registro_pendente"] = False
                    if not hora_checkout:
                        raise Exception("Hora de checkout não encontrada no contexto reaproveitado.")
                    limpar_cancelamento(f"checkout_{task_id}")
                    resposta = preparar_confirmacao_relato_checkout(
                        task_id,
                        data_checkout,
                        hora_checkout,
                        relato,
                        cancel_key=cancel_key,
                        verificacao_equipamento=verificacao_equipamento,
                    )
                    resposta["codigo_cliente"] = codigo_cliente
                    resposta["empresa"] = empresa
                    self._enviar_json(resposta)
                    return

                mensagens      = pegar_mensagens_chat()
                verificar_cancelamento(cancel_key)
                hora_checkout, data_checkout = pegar_hora_e_data_ultima_mensagem(mensagens)
                if not hora_checkout:
                    raise Exception("Hora de checkout não encontrada no chat.")

                relato_resultado = [None]
                equipamento_relacionado_resultado = [False]
                equipamento_citado_resultado = [""]
                problema_equipamento_resultado = [""]

                def gerar_relato():
                    print("🤖 Gerando relato detalhado com IA...")
                    (relato_resultado[0], equipamento_relacionado_resultado[0],
                     equipamento_citado_resultado[0], problema_equipamento_resultado[0]) = gerar_relato_estruturado_com_ia(mensagens)
                    print(f"📝 Relato:\n{relato_resultado[0]}\n")

                # ── Primeiro verifica no Auvo se existe task aberta ───────────
                # (antes de gastar tokens gerando o relato com IA). Se não
                # existir, o usuário vai para o fluxo combinado, que gera seu
                # próprio texto — gerar aqui também seria desperdício.
                token = autenticar()
                tasks_resultado = buscar_tasks_abertas(token, str(codigo_cliente))
                verificar_cancelamento(cancel_key)

                if isinstance(tasks_resultado, dict) and tasks_resultado.get("status") == "atendimento_pausado":
                    self._enviar_json({
                        "status": "atendimento_pausado",
                        "codigo_cliente": codigo_cliente,
                        "empresa": empresa,
                        "mensagem": "Atendimento pausado. Retome o atendimento no Auvo antes de fazer o check-out.",
                    })
                    return

                tasks = tasks_resultado or []

                # ── Sem task aberta: sugere criar checkin + checkout ──────────
                # Não gera relato com IA aqui — economiza a chamada, já que o
                # fluxo combinado (checkin + checkout) vai gerar seu próprio texto.
                if not tasks:
                    self._enviar_json({
                        "status":         "sem_atendimento",
                        "codigo_cliente": codigo_cliente,
                        "mensagem":       f"Nenhuma task encontrada nos últimos 10 dias para '{empresa}'.",
                    })
                    return

                # ── Task(s) encontrada(s): agora sim vale a pena gerar o relato ──
                gerar_relato()

                # ── Verifica se o atendimento é sobre equipamento e, se for,
                # se o equipamento citado está cadastrado pro cliente no Auvo ──
                task_id_verificacao = task_id_escolhido or (tasks[0].get("taskID") if len(tasks) == 1 else "")
                verificacao_equipamento = verificar_equipamento_checkout(
                    token,
                    codigo_cliente,
                    empresa,
                    equipamento_relacionado_resultado[0],
                    equipamento_citado_resultado[0],
                    task_id_verificacao,
                )

                task_status[f"checkout_contexto_{codigo_cliente}"] = {
                    "mensagens": mensagens,
                    "relato": relato_resultado[0],
                    "hora_checkout": hora_checkout,
                    "data_checkout": data_checkout,
                    "empresa": empresa,
                    "sistema": dados.get("sistema"),
                    "equipamento_relacionado": equipamento_relacionado_resultado[0],
                    "equipamento_citado": equipamento_citado_resultado[0],
                    "problema_equipamento": problema_equipamento_resultado[0],
                    "verificacao_equipamento": verificacao_equipamento,
                    "task_id": task_id_verificacao,
                }


                if not task_id_escolhido:
                    chave_relato = f"relato_pendente_{codigo_cliente}"
                    task_status[chave_relato] = relato_resultado[0]
                    if len(tasks) > 1:
                        self._enviar_json({
                            "status":         "multiplas_tasks",
                            "tasks":          tasks,
                            "hora":           hora_checkout,
                            "codigo_cliente": codigo_cliente,
                            "empresa":        empresa,
                            "verificacao_equipamento": verificacao_equipamento,
                        })
                        return

                    self._enviar_json({
                        "status":         "confirmar_task_checkout",
                        "task":           tasks[0],
                        "task_id":        tasks[0]["taskID"],
                        "hora":           hora_checkout,
                        "data":           data_checkout,
                        "codigo_cliente": codigo_cliente,
                        "empresa":        empresa,
                        "verificacao_equipamento": verificacao_equipamento,
                    })
                    return

                if task_id_escolhido and not confirmou_task_checkout:
                    raise Exception("Confirme se deseja lançar o check-out nesta task ou criar um fluxo novo.")

                # ── Task escolhida e confirmada pelo usuário ──────────────────
                if task_id_escolhido:
                    task_id = int(task_id_escolhido)
                    # Tenta reutilizar relato já gerado (economiza chamada ao Gemini)
                    chave_relato = f"relato_pendente_{codigo_cliente}"
                    relato = task_status.pop(chave_relato, None) or relato_resultado[0]
                else:
                    task_id = tasks[0]["taskID"]
                    relato  = relato_resultado[0]

                limpar_cancelamento(f"checkout_{task_id}")
                resposta = preparar_confirmacao_relato_checkout(
                    task_id,
                    data_checkout,
                    hora_checkout,
                    relato,
                    cancel_key=cancel_key,
                    verificacao_equipamento=verificacao_equipamento,
                )
                resposta["codigo_cliente"] = codigo_cliente
                resposta["empresa"] = empresa

            except ProcessoCancelado as e:
                resposta = {"status": "cancelado", "mensagem": str(e)}

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
                cancel_key = dados.get("cancel_key")
                if cancel_key:
                    limpar_cancelamento(cancel_key)

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

                verificar_cancelamento(cancel_key)
                token = autenticar()
                resultado_tasks = buscar_tasks_abertas(token, str(codigo_cliente))
                verificar_cancelamento(cancel_key)
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
                limpar_cancelamento(f"pausa_{task_id}")
                task_status[f"pausa_{task_id}"] = "iniciando"

                threading.Thread(
                    target=realizar_inicio_pausa_ui_invisivel,
                    args=(task_id, data_pausa, hora_pausa, motivo, cancel_key)
                ).start()

                resposta = {
                    "status": "ok",
                    "task_id": task_id,
                    "status_key": f"pausa_{task_id}",
                    "data": data_pausa,
                    "hora": hora_pausa,
                    "motivo": motivo,
                }

            except ProcessoCancelado as e:
                resposta = {"status": "cancelado", "mensagem": str(e)}

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
                cancel_key = dados.get("cancel_key")
                if cancel_key:
                    limpar_cancelamento(cancel_key)

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

                verificar_cancelamento(cancel_key)
                token = autenticar()
                tasks = buscar_tasks_pausadas(token, str(codigo_cliente)) or []
                verificar_cancelamento(cancel_key)
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
                limpar_cancelamento(f"despausa_{task_id}")
                task_status[f"despausa_{task_id}"] = "iniciando"

                threading.Thread(
                    target=realizar_fim_pausa_ui_invisivel,
                    args=(task_id, data_pausa, hora_pausa, cancel_key)
                ).start()

                resposta = {
                    "status": "ok",
                    "task_id": task_id,
                    "status_key": f"despausa_{task_id}",
                    "data": data_pausa,
                    "hora": hora_pausa,
                }

            except ProcessoCancelado as e:
                resposta = {"status": "cancelado", "mensagem": str(e)}

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
                sistema_cliente = dados.get("sistema")
                cancel_key = dados.get("cancel_key")
                if cancel_key:
                    limpar_cancelamento(cancel_key)
                print(f">> Sistema capturado: {sistema_cliente or '<nenhum>'}")

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

                contexto_checkout = task_status.get(f"checkout_contexto_{codigo_cliente}") if codigo_cliente else None
                if isinstance(contexto_checkout, dict) and contexto_checkout.get("mensagens"):
                    mensagens = contexto_checkout.get("mensagens") or []
                    print("♻️ Reaproveitando chat já capturado para o fluxo completo.")
                else:
                    mensagens = pegar_mensagens_chat()
                verificar_cancelamento(cancel_key)
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

                token = autenticar()
                segmento = verificar_segmento_cliente(
                    token,
                    codigo_cliente,
                    continuar_sem_segmento=bool(dados.get("continuar_sem_segmento")),
                    continuar_sem_contrato=bool(dados.get("continuar_sem_contrato"))
                )
                if not segmento.get("ok"):
                    self._enviar_json(resposta_cliente_sem_segmento(empresa, codigo_cliente, segmento.get("status")))
                    return

                chave     = f"full_flow_{codigo_cliente}"
                limpar_cancelamento(chave)
                task_status[chave] = "iniciando"

                threading.Thread(
                    target=fluxo_checkin_e_checkout,
                    args=(empresa, mensagens, codigo_cliente, inicio_escolhido, sistema_cliente, cancel_key)
                ).start()

                resposta = {
                    "status":         "ok",
                    "flow_key":       chave,
                    "codigo_cliente": codigo_cliente,
                }

            except ProcessoCancelado as e:
                resposta = {"status": "cancelado", "mensagem": str(e)}

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
        if (window.auvoNotificacaoLimpar) {
            try { window.auvoNotificacaoLimpar(); } catch (e) {}
            window.auvoNotificacaoLimpar = null;
        }
        const existente = document.getElementById('auvo-notif');
        if (existente) existente.remove();
        const barraExistente = document.getElementById('auvo-notif-bar');
        if (barraExistente) barraExistente.remove();

        const notif = document.createElement('div');
        notif.id = 'auvo-notif';

        const tema = {
            info:    { cor: '#3498db', titulo: 'Informação Auvo', icone: 'i' },
            aviso:   { cor: '#f39c12', titulo: 'Atenção Auvo', icone: '!' },
            erro:    { cor: '#e74c3c', titulo: 'Erro Auvo', icone: '!' },
            sucesso: { cor: '#2ee86f', titulo: 'PROCESSO CONCLUÍDO', icone: '✓' },
        }[tipo] || { cor: '#3498db', titulo: 'Aviso Auvo', icone: 'i' };
        const temAcoes = !!(acoes && acoes.length);
        let barraTopo = null;
        let ajustarBarra = null;

        Object.assign(notif.style, {
            position: 'fixed', zIndex: '2147483647',
            width: temAcoes ? 'min(520px, calc(100vw - 32px))' : 'min(340px, calc(100vw - 32px))',
            boxSizing: 'border-box',
            background: 'rgba(42,44,46,0.97)',
            color: '#f5f7f8', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.12)',
            padding: temAcoes ? '10px 36px 10px 12px' : '8px 34px 8px 12px',
            boxShadow: '0 4px 16px rgba(0,0,0,0.45)',
            fontFamily: 'Arial, sans-serif', fontSize: '13px', lineHeight: '1.25',
            letterSpacing: '0', whiteSpace: 'pre-line',
            animation: 'auvoToastIn 0.18s ease-out',
            top: '-9999px', left: '-9999px',
        });

        // CSS de animacao
        let style = document.getElementById('auvo-style');
        if (!style) {
            style = document.createElement('style');
            style.id = 'auvo-style';
            document.head.appendChild(style);
        }
        style.textContent = `
            @keyframes auvoToastIn {
                from { opacity:0; transform:translateY(-6px) scale(0.98); }
                to   { opacity:1; transform:translateY(0) scale(1); }
            }
            #auvo-notif::before { display: none !important; content: none !important; }
        `;

        function medirAreaBotoesAuvo() {
            const ids = ['botao-auvo', 'botao-auvo-checkout', 'botao-auvo-pausa', 'botao-auvo-despausa'];
            const rects = ids
                .map(id => document.getElementById(id))
                .filter(btn => btn && btn.offsetParent !== null)
                .map(btn => btn.getBoundingClientRect())
                .filter(r => r.width > 0 && r.height > 0);

            if (!rects.length) return null;
            return rects.reduce((acc, r) => ({
                left: Math.min(acc.left, r.left),
                right: Math.max(acc.right, r.right),
                bottom: Math.max(acc.bottom, r.bottom),
            }), { left: rects[0].left, right: rects[0].right, bottom: rects[0].bottom });
        }

        function fecharNotificacao() {
            if (barraTopo) barraTopo.remove();
            if (ajustarBarra) window.removeEventListener('resize', ajustarBarra);
            if (notif.parentNode) notif.remove();
            if (window.auvoNotificacaoLimpar === fecharNotificacao) window.auvoNotificacaoLimpar = null;
        }
        window.auvoNotificacaoLimpar = fecharNotificacao;

        // Posiciona o popup colado embaixo dos botões Auvo
        ajustarBarra = () => {
            const area = medirAreaBotoesAuvo();
            if (!area) {
                // fallback: centro do topo
                notif.style.top = '80px';
                notif.style.left = '50%';
                notif.style.transform = 'translateX(-50%)';
                return;
            }
            const margem = 8;
            const notifW = notif.offsetWidth || 340;
            // alinha o popup pelo centro do grupo de botões
            let left = area.left + (area.right - area.left) / 2 - notifW / 2;
            // mantém dentro da viewport
            left = Math.max(margem, Math.min(window.innerWidth - notifW - margem, left));
            const top = area.bottom + 8;
            notif.style.top = `${top}px`;
            notif.style.left = `${left}px`;
            notif.style.transform = 'none';
        };
        // Roda após render para ter offsetWidth correto
        requestAnimationFrame(() => { ajustarBarra(); });
        window.addEventListener('resize', ajustarBarra);

        const conteudo = document.createElement('div');
        Object.assign(conteudo.style, { display: 'flex', alignItems: 'center', gap: '8px' });

        const icone = document.createElement('span');
        icone.textContent = tema.icone;
        Object.assign(icone.style, {
            width: '28px', height: '28px', borderRadius: '50%', display: 'inline-flex',
            alignItems: 'center', justifyContent: 'center', color: tema.cor,
            border: `2px solid ${tema.cor}`, fontSize: '16px', fontWeight: '800',
            lineHeight: '1', flex: '0 0 auto', boxSizing: 'border-box',
        });

        const textoWrapper = document.createElement('div');
        Object.assign(textoWrapper.style, { minWidth: '0', flex: '1 1 auto' });

        const titulo = document.createElement('strong');
        titulo.textContent = tema.titulo;
        Object.assign(titulo.style, {
            display: 'block', color: '#ffffff', fontSize: '13px', lineHeight: '1.1',
            fontWeight: '700', marginBottom: '2px', letterSpacing: '0.3px',
        });
        textoWrapper.appendChild(titulo);

        const texto = document.createElement('div');
        texto.textContent = mensagem;
        Object.assign(texto.style, {
            color: '#a0a8b0', fontWeight: '400', fontSize: '11px', overflowWrap: 'anywhere',
        });
        textoWrapper.appendChild(texto);

        conteudo.appendChild(icone);
        conteudo.appendChild(textoWrapper);
        notif.appendChild(conteudo);

        const tituloNativo = tema.titulo;
        try {
            const tituloOriginal = document.title;
            let alternar = false;
            if (window.auvoTituloIntervalo) clearInterval(window.auvoTituloIntervalo);
            window.auvoTituloIntervalo = setInterval(() => {
                alternar = !alternar;
                document.title = alternar ? `AUVO: ${tituloNativo}` : tituloOriginal;
            }, 900);
            setTimeout(() => {
                clearInterval(window.auvoTituloIntervalo);
                window.auvoTituloIntervalo = null;
                document.title = tituloOriginal;
            }, 14000);
        } catch (e) {}

        try {
            if ('Notification' in window) {
                const mostrarNativa = () => {
                    if (Notification.permission === 'granted') {
                        new Notification(tituloNativo, { body: String(mensagem || '').replace(/\s+/g, ' ').slice(0, 180), silent: false });
                    }
                };
                if (Notification.permission === 'granted') mostrarNativa();
                else if (Notification.permission === 'default') Notification.requestPermission().then(mostrarNativa).catch(() => {});
            }
        } catch (e) {}

        if (temAcoes) {
            const btnWrapper = document.createElement('div');
            Object.assign(btnWrapper.style, { marginTop: '14px', display: 'flex', gap: '8px', flexWrap: 'wrap', paddingLeft: '70px' });

            acoes.forEach(({ label, cor, callback }) => {
                const btn = document.createElement('button');
                btn.textContent = label;
                Object.assign(btn.style, {
                    padding: '7px 14px', border: `1px solid ${cor || 'rgba(255,255,255,0.26)'}`, borderRadius: '6px',
                    background: cor || 'rgba(255,255,255,0.12)', color: 'white',
                    cursor: 'pointer', fontWeight: 'bold', fontSize: '13px',
                });
                btn.onclick = () => { fecharNotificacao(); callback(); };
                btnWrapper.appendChild(btn);
            });

            notif.appendChild(btnWrapper);
        } else {
            // Fecha automaticamente depois de 20s se nao tiver botoes.
            setTimeout(fecharNotificacao, 20000);
        }

        // Botao X para fechar manualmente
        const fechar = document.createElement('span');
        fechar.textContent = '×';
        Object.assign(fechar.style, {
            position: 'absolute', top: '6px', right: '10px',
            cursor: 'pointer', opacity: '0.6', fontSize: '18px', lineHeight: '1',
            color: '#b6bdc4', fontWeight: '300',
        });
        fechar.onclick = fecharNotificacao;
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
    const relatosCheckoutEmConfirmacao = new Set();
    const textosFluxoEmConfirmacao = new Set();

    function mostrarPopupTextosFluxo(dados, botaoOut, aoConfirmar, aoCancelar) {
        const existente = document.getElementById('auvo-notif');
        if (existente) existente.remove();

        const notif = document.createElement('div');
        notif.id = 'auvo-notif';
        Object.assign(notif.style, {
            position: 'fixed', bottom: '24px', right: '24px', zIndex: '99999',
            background: '#2c3e50', color: 'white',
            borderRadius: '10px', padding: '16px 20px', width: 'min(620px, calc(100vw - 48px))',
            boxShadow: '0 6px 24px rgba(0,0,0,0.3)', fontFamily: 'sans-serif',
            fontSize: '14px', lineHeight: '1.5',
        });

        const titulo = document.createElement('div');
        titulo.style.cssText = 'font-weight:bold; margin-bottom:8px; font-size:15px;';
        titulo.textContent = 'Descricao e relato gerados. Confira ou edite antes de lancar no Auvo.';
        notif.appendChild(titulo);

        const criarLabel = (texto) => {
            const label = document.createElement('label');
            label.textContent = texto;
            label.style.cssText = 'display:block; font-size:12px; opacity:.85; margin:10px 0 5px;';
            return label;
        };

        const estiloCampo = {
            width: '100%', resize: 'vertical',
            border: '1px solid rgba(255,255,255,0.25)', borderRadius: '6px',
            padding: '10px', boxSizing: 'border-box', fontFamily: 'sans-serif',
            fontSize: '13px', lineHeight: '1.45', color: '#1f2d3d', background: '#fff',
            outline: 'none',
        };

        notif.appendChild(criarLabel('Descricao da task'));
        const textareaDescricao = document.createElement('textarea');
        textareaDescricao.value = dados.descricao || '';
        Object.assign(textareaDescricao.style, estiloCampo, { minHeight: '82px' });
        notif.appendChild(textareaDescricao);

        notif.appendChild(criarLabel('Relato do check-out'));
        const textareaRelato = document.createElement('textarea');
        textareaRelato.value = dados.relato || '';
        Object.assign(textareaRelato.style, estiloCampo, { minHeight: '170px' });
        notif.appendChild(textareaRelato);

        const botoes = document.createElement('div');
        botoes.style.cssText = 'display:flex; gap:8px; margin-top:10px; justify-content:flex-end;';

        const cancelar = document.createElement('button');
        cancelar.textContent = 'Cancelar';
        Object.assign(cancelar.style, {
            padding: '8px 12px', border: 'none', borderRadius: '6px',
            background: 'rgba(255,255,255,0.15)', color: 'white',
            cursor: 'pointer', fontWeight: 'bold',
        });
        cancelar.onclick = () => {
            notif.remove();
            textosFluxoEmConfirmacao.delete(dados.flow_key);
            if (aoCancelar) aoCancelar();
        };

        const confirmar = document.createElement('button');
        confirmar.textContent = 'Esta certo, lancar';
        Object.assign(confirmar.style, {
            padding: '8px 12px', border: 'none', borderRadius: '6px',
            background: '#27ae60', color: 'white',
            cursor: 'pointer', fontWeight: 'bold',
        });
        confirmar.onclick = () => {
            const descricaoEditada = textareaDescricao.value.trim();
            const relatoEditado = textareaRelato.value.trim();
            if (!descricaoEditada) {
                mostrarNotificacao('A descricao nao pode ficar vazia.', 'erro');
                textareaDescricao.focus();
                return;
            }
            if (!relatoEditado) {
                mostrarNotificacao('O relato nao pode ficar vazio.', 'erro');
                textareaRelato.focus();
                return;
            }
            notif.remove();
            aoConfirmar(descricaoEditada, relatoEditado);
        };

        botoes.appendChild(cancelar);
        botoes.appendChild(confirmar);
        notif.appendChild(botoes);
        document.body.appendChild(notif);
        textareaDescricao.focus();
        textareaDescricao.setSelectionRange(textareaDescricao.value.length, textareaDescricao.value.length);
    }

    function confirmarTextosFluxo(dados, botaoOut, opcoes = {}) {
        const flowKey = dados.flow_key;
        if (!flowKey) return;
        if (textosFluxoEmConfirmacao.has(flowKey)) return;
        textosFluxoEmConfirmacao.add(flowKey);
        setBotao(botaoOut, 'Conferir textos...', '#8e44ad', true, AUVO_ETAPAS_PROGRESSO['relato_ok']);
        mostrarPopupTextosFluxo(
            dados,
            botaoOut,
            (descricaoEditada, relatoEditado) => {
                setBotao(botaoOut, 'Criando task...', '#2980b9', true, AUVO_ETAPAS_PROGRESSO['criando_task']);
                fetch('http://localhost:8765/auvo-checkout-criar-confirmar-textos', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        flow_key: flowKey,
                        descricao: descricaoEditada,
                        relato: relatoEditado,
                    }),
                })
                .then(r => r.json())
                .then(resp => {
                    textosFluxoEmConfirmacao.delete(flowKey);
                    if (tratarRespostaCancelada(resp, botaoOut, () => resetBotaoCheckout(botaoOut), 'Processo cancelado.')) return;
                    if (resp.status !== 'ok') throw new Error(resp.mensagem || 'Erro ao confirmar textos.');
                    if (opcoes.aoConfirmado) opcoes.aoConfirmado(resp);
                })
                .catch(err => {
                    textosFluxoEmConfirmacao.delete(flowKey);
                    setBotao(botaoOut, 'Erro nos textos', '#e74c3c', false);
                    mostrarNotificacao(`Erro ao confirmar textos: ${err.message}`, 'erro');
                    resetBotaoCheckout(botaoOut);
                    liberarAcoes();
                });
            },
            () => {
                fetch('http://localhost:8765/cancel', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chave: flowKey }),
                }).finally(() => {
                    resetBotaoCheckout(botaoOut);
                    liberarAcoes();
                });
            }
        );
    }

    function mostrarPopupRelatoCheckout(dados, botaoOut, aoConfirmar, aoCancelar) {
        const existente = document.getElementById('auvo-notif');
        if (existente) existente.remove();

        const notif = document.createElement('div');
        notif.id = 'auvo-notif';
        Object.assign(notif.style, {
            position: 'fixed', bottom: '24px', right: '24px', zIndex: '99999',
            background: '#2c3e50', color: 'white',
            borderRadius: '10px', padding: '16px 20px', width: 'min(560px, calc(100vw - 48px))',
            boxShadow: '0 6px 24px rgba(0,0,0,0.3)', fontFamily: 'sans-serif',
            fontSize: '14px', lineHeight: '1.5',
        });

        const titulo = document.createElement('div');
        titulo.style.cssText = 'font-weight:bold; margin-bottom:8px; font-size:15px;';
        titulo.textContent = `Relato gerado para check-out da Task #${dados.task_id}. Confira ou edite antes de lançar.`;
        notif.appendChild(titulo);

        const textarea = document.createElement('textarea');
        textarea.value = dados.relato || '';
        Object.assign(textarea.style, {
            width: '100%', minHeight: '180px', resize: 'vertical',
            border: '1px solid rgba(255,255,255,0.25)', borderRadius: '6px',
            padding: '10px', boxSizing: 'border-box', fontFamily: 'sans-serif',
            fontSize: '13px', lineHeight: '1.45', color: '#1f2d3d', background: '#fff',
            outline: 'none',
        });
        notif.appendChild(textarea);

        const botoes = document.createElement('div');
        botoes.style.cssText = 'display:flex; gap:8px; margin-top:10px; justify-content:flex-end;';

        const cancelar = document.createElement('button');
        cancelar.textContent = 'Cancelar';
        Object.assign(cancelar.style, {
            padding: '8px 12px', border: 'none', borderRadius: '6px',
            background: 'rgba(255,255,255,0.15)', color: 'white',
            cursor: 'pointer', fontWeight: 'bold',
        });
        cancelar.onclick = () => {
            notif.remove();
            relatosCheckoutEmConfirmacao.delete(dados.status_key || `checkout_${dados.task_id}`);
            if (aoCancelar) aoCancelar();
        };

        const confirmar = document.createElement('button');
        confirmar.textContent = 'Está certo, lançar';
        Object.assign(confirmar.style, {
            padding: '8px 12px', border: 'none', borderRadius: '6px',
            background: '#27ae60', color: 'white',
            cursor: 'pointer', fontWeight: 'bold',
        });
        confirmar.onclick = () => {
            const relatoEditado = textarea.value.trim();
            if (!relatoEditado) {
                mostrarNotificacao('O relato não pode ficar vazio.', 'erro');
                textarea.focus();
                return;
            }
            notif.remove();
            relatosCheckoutEmConfirmacao.delete(dados.status_key || `checkout_${dados.task_id}`);
            aoConfirmar(relatoEditado);
        };

        botoes.appendChild(cancelar);
        botoes.appendChild(confirmar);
        notif.appendChild(botoes);
        document.body.appendChild(notif);
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    }

    function mostrarSelecaoEquipamento(dados, botao, aoConfirmar, aoCancelar) {
    const opcoes = dados.opcoes || [];

    // Sem nenhum equipamento cadastrado pro cliente: pede pra digitar direto
    if (opcoes.length === 0) {
        const digitado = prompt('Cliente sem equipamento cadastrado no Auvo.\nDigite o nome do equipamento:');
        if (digitado && digitado.trim()) {
            setBotao(botao, 'Registrando equipamento...', '#f39c12', true);
            if (aoConfirmar) aoConfirmar(digitado.trim());
        } else {
            if (aoCancelar) aoCancelar();
            liberarAcoes();
        }
        return;
    }

    // Remove popup anterior
    const anteriorPopup = document.getElementById('auvo-equip-popup');
    if (anteriorPopup) anteriorPopup.remove();

    const overlay = document.createElement('div');
    overlay.id = 'auvo-equip-popup';
    Object.assign(overlay.style, {
        position: 'fixed', inset: '0', zIndex: '2147483647',
        background: 'rgba(0,0,0,0.55)', display: 'flex',
        alignItems: 'center', justifyContent: 'center',
    });

    const caixa = document.createElement('div');
    Object.assign(caixa.style, {
        background: '#ffffff', color: '#111111',
        borderRadius: '10px', border: '1px solid #d0d0d0',
        boxShadow: '0 8px 32px rgba(0,0,0,0.25)',
        padding: '18px 20px 16px', fontFamily: 'Arial, sans-serif',
        width: 'min(340px, calc(100vw - 32px))', boxSizing: 'border-box',
    });

    const titulo = document.createElement('div');
    titulo.textContent = 'Qual equipamento foi atendido?';
    Object.assign(titulo.style, {
        fontWeight: '700', fontSize: '13px', color: '#111111',
        marginBottom: '12px', lineHeight: '1.3',
    });
    caixa.appendChild(titulo);

    const select = document.createElement('select');
    Object.assign(select.style, {
        width: '100%', padding: '7px 10px', borderRadius: '6px',
        border: '1px solid #bbbbbb',
        background: '#ffffff', color: '#111111',
        fontSize: '12px', boxSizing: 'border-box', marginBottom: '12px',
        cursor: 'pointer', outline: 'none',
    });

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = '— selecione o equipamento —';
    placeholder.disabled = true;
    placeholder.selected = true;
    select.appendChild(placeholder);

    opcoes.forEach(nome => {
        const op = document.createElement('option');
        op.value = nome;
        op.textContent = nome;
        select.appendChild(op);
    });

    const opOutro = document.createElement('option');
    opOutro.value = '__outro__';
    opOutro.textContent = '✏ Outro (digitar manualmente)';
    select.appendChild(opOutro);

    caixa.appendChild(select);

    // Campo de texto pra digitar o nome do equipamento quando "Outro" for
    // selecionado. Fica escondido por padrão e só aparece nesse caso —
    // evita depender do prompt() nativo do navegador, que o Playwright
    // costuma descartar automaticamente e nunca deixava o usuário digitar.
    const inputOutro = document.createElement('input');
    inputOutro.type = 'text';
    inputOutro.placeholder = 'Digite o nome do equipamento...';
    Object.assign(inputOutro.style, {
        width: '100%', padding: '7px 10px', borderRadius: '6px',
        border: '1px solid #bbbbbb',
        background: '#ffffff', color: '#111111',
        fontSize: '12px', boxSizing: 'border-box', marginBottom: '12px',
        outline: 'none', display: 'none',
    });
    caixa.appendChild(inputOutro);

    select.addEventListener('change', () => {
        if (select.value === '__outro__') {
            inputOutro.style.display = 'block';
            inputOutro.focus();
        } else {
            inputOutro.style.display = 'none';
            inputOutro.style.borderColor = '#bbbbbb';
        }
    });

    const botoes = document.createElement('div');
    Object.assign(botoes.style, { display: 'flex', gap: '8px', justifyContent: 'flex-end' });

    const btnCancelar = document.createElement('button');
    btnCancelar.textContent = 'Cancelar';
    Object.assign(btnCancelar.style, {
        padding: '6px 14px', borderRadius: '6px', border: '1px solid rgba(255,255,255,0.2)',
        background: 'rgba(255,255,255,0.1)', color: '#ccc',
        cursor: 'pointer', fontSize: '12px', fontWeight: '600',
    });
    btnCancelar.onclick = () => { overlay.remove(); if (aoCancelar) aoCancelar(); liberarAcoes(); };

    const btnConfirmar = document.createElement('button');
    btnConfirmar.textContent = 'Confirmar';
    Object.assign(btnConfirmar.style, {
        padding: '6px 14px', borderRadius: '6px', border: '1px solid #27ae60',
        background: '#27ae60', color: 'white',
        cursor: 'pointer', fontSize: '12px', fontWeight: '700',
    });
    btnConfirmar.onclick = () => {
        const val = select.value;
        if (!val) { select.style.borderColor = '#e74c3c'; return; }
        if (val === '__outro__') {
            const digitado = inputOutro.value.trim();
            if (!digitado) { inputOutro.style.borderColor = '#e74c3c'; inputOutro.focus(); return; }
            overlay.remove();
            setBotao(botao, 'Registrando equipamento...', '#f39c12', true);
            if (aoConfirmar) aoConfirmar(digitado);
        } else {
            overlay.remove();
            setBotao(botao, 'Registrando equipamento...', '#f39c12', true);
            if (aoConfirmar) aoConfirmar(val);
        }
    };

    botoes.appendChild(btnCancelar);
    botoes.appendChild(btnConfirmar);
    caixa.appendChild(botoes);
    overlay.appendChild(caixa);
    document.body.appendChild(overlay);
}
    
    function mostrarConfirmacaoSegmento(dados, botao, aoContinuar, aoParar) {
        const mensagem = dados.mensagem || 'Verifique o cadastro do cliente. Deseja continuar o check-in?';
        mostrarNotificacao(
            mensagem,
            'aviso',
            [
                {
                    label: 'Continuar check-in',
                    cor: '#27ae60',
                    callback: () => {
                        setBotao(botao, 'Continuando...', '#f39c12', true);
                        if (aoContinuar) aoContinuar();
                    },
                },
                {
                    label: 'Parar',
                    cor: 'rgba(255,255,255,0.16)',
                    callback: () => {
                        if (aoParar) aoParar();
                        liberarAcoes();
                    },
                },
            ]
        );
    }

    function confirmarRelatoCheckout(dados, botaoOut, opcoes = {}) {
        const statusKey = dados.status_key || `checkout_${dados.task_id}`;
        if (!dados || !dados.task_id) return;
        if (relatosCheckoutEmConfirmacao.has(statusKey)) return;
        relatosCheckoutEmConfirmacao.add(statusKey);
        setBotao(botaoOut, `Conferir relato Task #${dados.task_id}`, '#8e44ad', true, AUVO_ETAPAS_PROGRESSO['relato_ok']);
        const enviarConfirmacaoRelato = (relatoFinal) => {
            setBotao(botaoOut, `Lançando Check-Out...`, '#2980b9', true, AUVO_ETAPAS_PROGRESSO['checkout']);
            fetch('http://localhost:8765/auvo-checkout-confirmar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_id: dados.task_id,
                    status_key: statusKey,
                    flow_key: dados.flow_key || null,
                    relato: relatoFinal,
                    data: dados.data || null,
                    hora: dados.hora || null,
                    cancel_key: dados.cancel_key || null,
                }),
            })
            .then(r => r.json())
            .then(resp => {
                if (tratarRespostaCancelada(resp, botaoOut, () => resetBotaoCheckout(botaoOut), 'Check-out cancelado.')) return;
                if (resp.status !== 'ok') throw new Error(resp.mensagem || 'Erro ao confirmar relato.');
                if (opcoes.aoConfirmado) opcoes.aoConfirmado(resp);
            })
            .catch(err => {
                setBotao(botaoOut, 'Erro no relato', '#e74c3c', false);
                mostrarNotificacao(`Erro ao confirmar relato: ${err.message}`, 'erro');
                resetBotaoCheckout(botaoOut);
                liberarAcoes();
            });
        };

        mostrarPopupRelatoCheckout(
            { ...dados, status_key: statusKey },
            botaoOut,
            (relatoEditado) => {
                const verificacaoEquipamento = dados.verificacao_equipamento || {};
                if (verificacaoEquipamento.status === 'selecionar') {
                    mostrarSelecaoEquipamento(
                        { ...verificacaoEquipamento, status_key: statusKey },
                        botaoOut,
                        (equipamentoEscolhido) => {
                            fetch('http://localhost:8765/auvo-checkout-selecionar-equipamento', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    codigo_cliente: dados.codigo_cliente || null,
                                    status_key: statusKey,
                                    task_id: dados.task_id,
                                    empresa: dados.empresa || '',
                                    equipamento: equipamentoEscolhido,
                                    relato: relatoEditado,
                                }),
                            })
                            .then(r => r.json())
                            .then(resp => {
                                if (resp.status !== 'ok') throw new Error(resp.mensagem || 'Erro ao selecionar equipamento.');
                                dados.verificacao_equipamento = {
                                    status: resp.vinculado ? 'vinculado' : 'nao_vinculado',
                                    equipamento: resp.equipamento,
                                };
                                enviarConfirmacaoRelato(resp.relato || relatoEditado);
                            })
                            .catch(err => {
                                setBotao(botaoOut, 'Erro no equipamento', '#e74c3c', false);
                                mostrarNotificacao(`Erro ao selecionar equipamento: ${err.message}`, 'erro');
                                resetBotaoCheckout(botaoOut);
                                liberarAcoes();
                            });
                        },
                        () => {
                            fetch('http://localhost:8765/cancel', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ chave: dados.flow_key || statusKey }),
                            }).catch(() => {});
                            resetBotaoCheckout(botaoOut);
                            liberarAcoes();
                        }
                    );
                    return;
                }
                enviarConfirmacaoRelato(relatoEditado);
                return;
            },
            () => {
                fetch('http://localhost:8765/cancel', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chave: dados.flow_key || statusKey }),
                }).catch(() => {});
                resetBotaoCheckout(botaoOut);
                liberarAcoes();
            }
        );
    }

    function iniciarPollingCheckoutConfirmado(dados, botaoOut) {
        const chaveCheckout = dados.status_key || `checkout_${dados.task_id}`;
        const limparCancelarCheckout = mostrarCancelamentoProcesso(botaoOut, chaveCheckout, `Check-out Task #${dados.task_id}`, () => resetBotaoCheckout(botaoOut));
        iniciarPolling(chaveCheckout, 1000, (s) => {
            if (tratarCancelado(s, botaoOut, limparCancelarCheckout, () => resetBotaoCheckout(botaoOut), 'Check-out cancelado.')) return true;
            if (s.status === 'auvo_instavel') {
                limparCancelarCheckout();
                setBotao(botaoOut, 'Auvo com Instabilidade', '#c0392b', false);
                mostrarNotificacao('O Auvo ficou instável durante o check-out. Verifique e tente novamente.', 'erro');
                resetBotaoCheckout(botaoOut);
                return true;
            }
            if (s.status === 'relato_ok') {
                setBotao(botaoOut, 'Lançando Check-Out...', '#2980b9', true, AUVO_ETAPAS_PROGRESSO['relato_ok']);
            }
            if (s.status === 'done') {
                limparCancelarCheckout();
                setBotao(botaoOut, `Check-Out salvo ${dados.hora || s.hora || ''}`, '#27ae60', true);
                mostrarNotificacao(`Check-out concluído com sucesso${dados.hora ? ` às ${dados.hora}` : ''}.`, 'sucesso');
                resetBotaoCheckout(botaoOut);
                return true;
            }
            return false;
        });
    }

    function textoDataHoraTask(t) {
        const data = t.data_formatada || t.data || '';
        const hora = t.hora ? t.hora.substring(0, 5) : '';
        if (data && hora) return `${data} ${hora}`;
        return data || hora || 'data/hora não informada';
    }

    function dataHojeISO() {
        const agora = new Date();
        return `${agora.getFullYear()}-${String(agora.getMonth() + 1).padStart(2, '0')}-${String(agora.getDate()).padStart(2, '0')}`;
    }

    function taskEhHoje(task) {
        return !!task && String(task.data || '').slice(0, 10) === dataHojeISO();
    }

    function mostrarConfirmacaoTaskCheckout(task, empresa, botaoOut, aoUsarTask, aoCriarFluxo, aoCancelar) {
        const existente = document.getElementById('auvo-notif');
        if (existente) existente.remove();

        const notif = document.createElement('div');
        notif.id = 'auvo-notif';
        Object.assign(notif.style, {
            position: 'fixed', bottom: '24px', right: '24px', zIndex: '99999',
            background: '#2c3e50', color: 'white',
            borderRadius: '10px', padding: '16px 20px', maxWidth: '470px',
            boxShadow: '0 6px 24px rgba(0,0,0,0.3)', fontFamily: 'sans-serif',
            fontSize: '14px', lineHeight: '1.5',
        });

        const titulo = document.createElement('div');
        titulo.style.cssText = 'font-weight:bold; margin-bottom:10px; font-size:15px;';
        titulo.textContent = 'Atendimento aberto encontrado';
        notif.appendChild(titulo);

        const detalhes = document.createElement('div');
        detalhes.style.cssText = 'margin-bottom:12px;';
        detalhes.textContent = `${empresa} - Task #${task.taskID} - ${textoDataHoraTask(task)}`;
        notif.appendChild(detalhes);

        const usar = document.createElement('button');
        usar.textContent = 'Lançar nessa task';
        Object.assign(usar.style, {
            display: 'block', width: '100%', margin: '6px 0',
            padding: '9px 12px', border: 'none', borderRadius: '6px',
            background: '#27ae60', color: 'white', cursor: 'pointer',
            fontWeight: 'bold', textAlign: 'left',
        });
        usar.onclick = () => {
            notif.remove();
            aoUsarTask();
        };
        notif.appendChild(usar);

        const fluxo = document.createElement('button');
        fluxo.textContent = 'Criar fluxo check-in + check-out';
        Object.assign(fluxo.style, {
            display: 'block', width: '100%', margin: '6px 0',
            padding: '9px 12px', border: 'none', borderRadius: '6px',
            background: '#2980b9', color: 'white', cursor: 'pointer',
            fontWeight: 'bold', textAlign: 'left',
        });
        fluxo.onclick = () => {
            notif.remove();
            aoCriarFluxo();
        };
        notif.appendChild(fluxo);

        const cancelar = document.createElement('button');
        cancelar.textContent = 'Cancelar';
        Object.assign(cancelar.style, {
            display: 'block', width: '100%', margin: '8px 0 0',
            padding: '7px', border: 'none', borderRadius: '6px',
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

    function mostrarSeletorTasks(tasks, hora, empresa, botaoOut, codigoCliente, sistemaCliente) {
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
        titulo.textContent = `Encontrei ${tasks.length} atendimentos abertos nos últimos 10 dias para ${empresa}. Qual devo finalizar?`;
        notif.appendChild(titulo);

        tasks.forEach((t, idx) => {
            const btn = document.createElement('button');
            const dataHora = textoDataHoraTask(t);
            const desc = t.descricao ? t.descricao.substring(0, 60) : '';
            btn.textContent = `#${t.taskID} - ${dataHora} - ${desc}`;
            Object.assign(btn.style, {
                display: 'block', width: '100%', margin: '4px 0',
                padding: '8px 12px', border: 'none', borderRadius: '6px',
                background: idx % 2 === 0 ? '#2980b9' : '#16a085',
                color: 'white', cursor: 'pointer', fontWeight: 'bold',
                textAlign: 'left', fontSize: '13px',
            });
            btn.onclick = () => {
                notif.remove();
                const usarTaskSelecionada = () => {
                    const cancelKeyCheckoutSelecionadoInicial = criarChaveCancelamento('checkout');
                    mostrarCancelamentoProcesso(botaoOut, cancelKeyCheckoutSelecionadoInicial, `Check-out Task #${t.taskID}`, () => resetBotaoCheckout(botaoOut));
                    setBotao(botaoOut, `Checkout Task #${t.taskID}...`, '#e67e22', true);
                    fetch('http://localhost:8765/auvo-checkout', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            empresa:          empresa,
                            task_id_escolhido: t.taskID,
                            codigo_cliente:   codigoCliente,
                            sistema:          sistemaCliente || null,
                            cancel_key:       cancelKeyCheckoutSelecionadoInicial,
                            confirmar_task_checkout: true,
                        }),
                    })
                    .then(r => r.json())
                    .then(data => {
                        if (tratarRespostaCancelada(data, botaoOut, () => resetBotaoCheckout(botaoOut), 'Check-out cancelado.')) return;
                        if (data.status === 'auvo_instavel') {
                            setBotao(botaoOut, '⚠️ Auvo com Instabilidade', '#c0392b', false);
                            mostrarNotificacao('O Auvo ficou instável durante o check-out.', 'erro');
                            resetBotaoCheckout(botaoOut);
                            return;
                        }
                        if (data.status === 'confirmar_relato') {
                            confirmarRelatoCheckout(
                                { ...data, cancel_key: cancelKeyCheckoutSelecionadoInicial },
                                botaoOut,
                                { aoConfirmado: () => iniciarPollingCheckoutConfirmado(data, botaoOut) }
                            );
                            return;
                        }
                        if (data.status !== 'ok') throw new Error(data.mensagem || 'Erro desconhecido');

                        iniciarPollingCheckoutConfirmado(data, botaoOut);
                    })
                    .catch(err => {
                        setBotao(botaoOut, '❌ Erro!', '#e74c3c', false);
                        mostrarNotificacao(`Erro: ${err.message}`, 'erro');
                        resetBotaoCheckout(botaoOut);
                    });
                };

                if (taskEhHoje(t)) {
                    usarTaskSelecionada();
                    return;
                }

                setBotao(botaoOut, 'Confirmar task', '#8e44ad', false);
                mostrarConfirmacaoTaskCheckout(
                    t,
                    empresa,
                    botaoOut,
                    usarTaskSelecionada,
                    () => iniciarFluxoCompleto(botaoOut, empresa, codigoCliente, null, sistemaCliente),
                    () => {
                        resetBotaoCheckout(botaoOut);
                        liberarAcoes();
                    }
                );
            };
            notif.appendChild(btn);
        });

        const criarFluxo = document.createElement('button');
        criarFluxo.textContent = 'Criar fluxo check-in + check-out';
        Object.assign(criarFluxo.style, {
            display: 'block', width: '100%', margin: '8px 0 0',
            padding: '8px 12px', border: 'none', borderRadius: '6px',
            background: '#2980b9', color: 'white',
            cursor: 'pointer', fontSize: '13px', fontWeight: 'bold',
            textAlign: 'left',
        });
        criarFluxo.onclick = () => {
            notif.remove();
            iniciarFluxoCompleto(botaoOut, empresa, codigoCliente, null, sistemaCliente);
        };
        notif.appendChild(criarFluxo);

        const cancelar = document.createElement('button');
        cancelar.textContent = 'Cancelar';
        Object.assign(cancelar.style, {
            display: 'block', width: '100%', margin: '8px 0 0',
            padding: '6px', border: 'none', borderRadius: '6px',
            background: 'rgba(255,255,255,0.15)', color: 'white',
            cursor: 'pointer', fontSize: '13px',
        });
        cancelar.onclick = () => { notif.remove(); resetBotaoCheckout(botaoOut); liberarAcoes(); };
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
                position: relative;
            }
            #botao-auvo.auvo-cancelavel, #botao-auvo-checkout.auvo-cancelavel, #botao-auvo-pausa.auvo-cancelavel, #botao-auvo-despausa.auvo-cancelavel {
                cursor: pointer !important;
                opacity: 1 !important;
            }
            #botao-auvo.auvo-cancelavel:hover .auvo-progress-inner, #botao-auvo-checkout.auvo-cancelavel:hover .auvo-progress-inner, #botao-auvo-pausa.auvo-cancelavel:hover .auvo-progress-inner, #botao-auvo-despausa.auvo-cancelavel:hover .auvo-progress-inner {
                color: transparent;
            }
            #botao-auvo.auvo-cancelavel:hover .auvo-progress-inner::after, #botao-auvo-checkout.auvo-cancelavel:hover .auvo-progress-inner::after, #botao-auvo-pausa.auvo-cancelavel:hover .auvo-progress-inner::after, #botao-auvo-despausa.auvo-cancelavel:hover .auvo-progress-inner::after {
                content: '×';
                color: #b8c0c8;
                font: 700 18px/1 Arial, sans-serif;
                position: absolute;
                inset: 0;
                display: inline-flex;
                align-items: center;
                justify-content: center;
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
        const cancelavel = !!botao.dataset.auvoCancelKey;
        botao.disabled = disabled && !cancelavel;
        botao.style.cursor = disabled && !cancelavel ? 'not-allowed' : 'pointer';
        botao.style.opacity = disabled && !cancelavel ? '0.78' : '1';
        botao.style.boxShadow = disabled ? 'none' : `0 0 0 1px ${cor}22, 0 4px 12px rgba(0,0,0,0.18)`;
        botao.style.transform = 'translateY(0)';
        botao.title = cancelavel ? `${botao.dataset.auvoCancelLabel || label} - clique para cancelar` : label;
        botao.setAttribute('aria-label', botao.title);
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
        const cancelavel = !!botao.dataset.auvoCancelKey;
        botao.disabled = !cancelavel;
        if (!cancelavel) botao.blur();
        botao.style.cursor = cancelavel ? 'pointer' : 'not-allowed';
        botao.style.opacity = '1';
        botao.style.boxShadow = 'none';
        botao.dataset.auvoProgress = String(percentual);
        botao.title = cancelavel ? `${botao.dataset.auvoCancelLabel || label} - clique para cancelar` : label;
        botao.setAttribute('aria-label', botao.title);
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
            delete botao.dataset.auvoCancelKey;
            delete botao.dataset.auvoCancelLabel;
            delete botao.dataset.auvoCanceling;
            botao.auvoCancelReset = null;
            botao.classList.remove('auvo-cancelavel');
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

    function solicitarCancelamentoProcesso(chave) {
        return fetch('http://localhost:8765/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chave }),
        }).then(r => r.json());
    }

    function criarChaveCancelamento(tipo) {
        return `${tipo}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    }

    function mostrarCancelamentoProcesso(botao, chave, label, aoCancelar) {
        if (!botao || !chave) return () => {};
        botao.dataset.auvoCancelKey = chave;
        botao.dataset.auvoCancelLabel = label || 'Processo em andamento';
        botao.dataset.auvoCanceling = '0';
        botao.auvoCancelReset = aoCancelar || null;
        botao.classList.add('auvo-cancelavel');
        botao.disabled = false;
        botao.style.cursor = 'pointer';
        botao.title = `${botao.dataset.auvoCancelLabel} - clique para cancelar`;
        botao.setAttribute('aria-label', botao.title);
        return () => {
            if (botao.dataset.auvoCancelKey === chave) {
                delete botao.dataset.auvoCancelKey;
                delete botao.dataset.auvoCancelLabel;
                delete botao.dataset.auvoCanceling;
                botao.auvoCancelReset = null;
                botao.classList.remove('auvo-cancelavel');
            }
        };
    }

    async function processarCancelamentoBotao(botao) {
        const chave = botao && botao.dataset.auvoCancelKey;
        if (!chave) return false;
        if (botao.dataset.auvoCanceling === '1') return true;
        botao.dataset.auvoCanceling = '1';
        const progresso = Number(botao.dataset.auvoProgress || 0) || undefined;
        setBotao(botao, 'Cancelando...', '#7f8c8d', true, progresso);
        try {
            const data = await solicitarCancelamentoProcesso(chave);
            if (data.status !== 'ok') throw new Error(data.mensagem || 'Falha ao cancelar');
            mostrarNotificacao('Cancelamento solicitado. O processo será interrompido na próxima etapa segura.', 'aviso');
        } catch (e) {
            botao.dataset.auvoCanceling = '0';
            mostrarNotificacao(`Erro ao cancelar: ${e.message}`, 'erro');
        }
        return true;
    }
    function tratarCancelado(s, botao, limparCancelar, resetar, mensagem) {
        if (s.status !== 'cancelado') return false;
        if (limparCancelar) limparCancelar();
        setBotao(botao, 'Processo cancelado', '#7f8c8d', false);
        mostrarNotificacao(mensagem || 'Processo cancelado.', 'aviso');
        resetar();
        return true;
    }

    function tratarRespostaCancelada(data, botao, resetar, mensagem) {
        if (!data || data.status !== 'cancelado') return false;
        setBotao(botao, 'Processo cancelado', '#7f8c8d', false);
        mostrarNotificacao(mensagem || data.mensagem || 'Processo cancelado.', 'aviso');
        resetar();
        return true;
    }

    // ── Fluxo de checkout simples ─────────────────────────────────────────
    function iniciarFluxoCheckout(botaoOut, textoEmpresa, codigoCliente, sistemaCliente) {
        bloquearAcaoOposta('checkout');
        const cancelKeyCheckoutInicial = criarChaveCancelamento('checkout');
        mostrarCancelamentoProcesso(botaoOut, cancelKeyCheckoutInicial, 'Check-out', () => resetBotaoCheckout(botaoOut));
        setBotao(botaoOut, 'Gerando relato...', '#8e44ad', true, AUVO_ETAPAS_PROGRESSO['gerando_relato']);

        fetch('http://localhost:8765/auvo-checkout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ empresa: textoEmpresa, codigo_cliente: codigoCliente || null, sistema: sistemaCliente || null, cancel_key: cancelKeyCheckoutInicial }),
        })
        .then(r => r.json())
        .then(data => {
            if (tratarRespostaCancelada(data, botaoOut, () => resetBotaoCheckout(botaoOut), 'Check-out cancelado.')) return;
            // ── Sem atendimento aberto ────────────────────────────────────
            if (data.status === 'multiplas_empresas') {
                setBotao(botaoOut, 'Check-Out', '#e67e22', false);
                mostrarSeletorEmpresas(
                    data.empresas,
                    textoEmpresa,
                    botaoOut,
                    (empresaEscolhida) => iniciarFluxoCheckout(botaoOut, textoEmpresa, empresaEscolhida.codigo, sistemaCliente),
                    () => resetBotaoCheckout(botaoOut)
                );
                return;
            }
            if (data.status === 'confirmar_task_checkout') {
                const taskConfirmacao = data.task || { taskID: data.task_id, data: data.data, hora: data.hora };
                const usarTaskConfirmada = () => {
                    const cancelKeyConfirmado = criarChaveCancelamento('checkout');
                    mostrarCancelamentoProcesso(botaoOut, cancelKeyConfirmado, `Check-out Task #${data.task_id}`, () => resetBotaoCheckout(botaoOut));
                    setBotao(botaoOut, `Checkout Task #${data.task_id}...`, '#e67e22', true);
                    fetch('http://localhost:8765/auvo-checkout', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            empresa: textoEmpresa,
                            task_id_escolhido: data.task_id,
                            codigo_cliente: data.codigo_cliente,
                            sistema: sistemaCliente || null,
                            cancel_key: cancelKeyConfirmado,
                            confirmar_task_checkout: true,
                        }),
                    })
                    .then(r => r.json())
                    .then(resp => {
                        if (tratarRespostaCancelada(resp, botaoOut, () => resetBotaoCheckout(botaoOut), 'Check-out cancelado.')) return;
                        if (resp.status === 'confirmar_relato') {
                            confirmarRelatoCheckout(
                                { ...resp, cancel_key: cancelKeyConfirmado },
                                botaoOut,
                                { aoConfirmado: () => iniciarPollingCheckoutConfirmado(resp, botaoOut) }
                            );
                            return;
                        }
                        if (resp.status !== 'ok') throw new Error(resp.mensagem || 'Erro desconhecido');
                        iniciarPollingCheckoutConfirmado(resp, botaoOut);
                    })
                    .catch(err => {
                        setBotao(botaoOut, 'Erro', '#e74c3c', false);
                        mostrarNotificacao(`Erro no checkout: ${err.message}`, 'erro');
                        resetBotaoCheckout(botaoOut);
                    });
                };

                if (taskEhHoje(taskConfirmacao)) {
                    usarTaskConfirmada();
                    return;
                }

                setBotao(botaoOut, 'Confirmar task', '#8e44ad', false);
                mostrarConfirmacaoTaskCheckout(
                    taskConfirmacao,
                    data.empresa || textoEmpresa,
                    botaoOut,
                    usarTaskConfirmada,
                    () => iniciarFluxoCompleto(botaoOut, textoEmpresa, data.codigo_cliente, null, sistemaCliente),
                    () => {
                        resetBotaoCheckout(botaoOut);
                        liberarAcoes();
                    }
                );
                return;
            }

            if (data.status === 'sem_atendimento') {
                setBotao(botaoOut, 'Check-Out', '#e67e22', false);
                mostrarNotificacao(
                    'Não foi encontrado um atendimento aberto nos últimos 10 dias para esta empresa.\nDeseja criar o Check-in e já fazer o Check-out?',
                    'aviso',
                    [
                        {
                            label: '✅ Criar e finalizar',
                            cor: '#27ae60',
                            callback: () => iniciarFluxoCompleto(botaoOut, textoEmpresa, data.codigo_cliente, null, sistemaCliente),
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
                mostrarSeletorTasks(data.tasks, data.hora, textoEmpresa, botaoOut, data.codigo_cliente, sistemaCliente);
                return;
            }

            // ── Auvo com instabilidade ────────────────────────────────────
            if (data.status === 'auvo_instavel') {
                setBotao(botaoOut, '⚠️ Auvo com Instabilidade', '#c0392b', false);
                mostrarNotificacao('O Auvo está com instabilidade no momento. Tente novamente em alguns instantes.', 'erro');
                resetBotaoCheckout(botaoOut);
                return;
            }

            // ── Confirmação do relato antes do checkout ───────────────────
            if (data.status === 'confirmar_relato') {
                confirmarRelatoCheckout(
                    { ...data, cancel_key: cancelKeyCheckoutInicial },
                    botaoOut,
                    { aoConfirmado: () => iniciarPollingCheckoutConfirmado(data, botaoOut) }
                );
                return;
            }

            if (data.status !== 'ok') {
                throw new Error(data.mensagem || 'Erro desconhecido');
            }

            iniciarPollingCheckoutConfirmado(data, botaoOut);
        })
        .catch(err => {
            setBotao(botaoOut, '❌ Erro!', '#e74c3c', false);
            mostrarNotificacao(`Erro no checkout: ${err.message}`, 'erro');
            resetBotaoCheckout(botaoOut);
        });
    }

    // ── Fluxo completo (sem checkin → cria checkin + checkout) ───────────
    function iniciarFluxoCompleto(botaoOut, textoEmpresa, codigoCliente, inicioEscolhido, sistemaCliente, opcoes = {}) {
        bloquearAcaoOposta('checkout');
        const cancelKeyFluxoInicial = criarChaveCancelamento('fluxo');
        mostrarCancelamentoProcesso(botaoOut, cancelKeyFluxoInicial, 'Criando e finalizando atendimento', () => resetBotaoCheckout(botaoOut));
        setBotao(botaoOut, 'Criando atendimento...', '#8e44ad', true, AUVO_ETAPAS_PROGRESSO['iniciando']);

        fetch('http://localhost:8765/auvo-checkout-criar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ empresa: textoEmpresa, codigo_cliente: codigoCliente, inicio_escolhido: inicioEscolhido || null, sistema: sistemaCliente || null, continuar_sem_contrato: !!opcoes.continuarSemContrato, continuar_sem_segmento: !!opcoes.continuarSemSegmento, cancel_key: cancelKeyFluxoInicial }),
        })
        .then(r => r.json())
        .then(data => {
            if (tratarRespostaCancelada(data, botaoOut, () => resetBotaoCheckout(botaoOut), 'Processo cancelado.')) return;
            if (data.status === 'multiplas_empresas') {
                setBotao(botaoOut, 'Check-Out', '#e67e22', false);
                mostrarSeletorEmpresas(
                    data.empresas,
                    textoEmpresa,
                    botaoOut,
                    (empresaEscolhida) => iniciarFluxoCompleto(botaoOut, textoEmpresa, empresaEscolhida.codigo, null, sistemaCliente),
                    () => resetBotaoCheckout(botaoOut)
                );
                return;
            }
            if (data.status === 'multiplos_inicios') {
                setBotao(botaoOut, 'Check-Out', '#e67e22', false);
                mostrarSeletorInicios(
                    data.inicios,
                    botaoOut,
                    (inicioEscolhido) => iniciarFluxoCompleto(botaoOut, textoEmpresa, data.codigo_cliente || codigoCliente, inicioEscolhido, sistemaCliente),
                    () => resetBotaoCheckout(botaoOut)
                );
                return;
            }
            if (data.status === 'cliente_sem_segmento' || data.status === 'cliente_sem_contrato') {
                const opcaoContinuar = data.status === 'cliente_sem_segmento' ? { continuarSemSegmento: true } : { continuarSemContrato: true };
                setBotao(botaoOut, data.status === 'cliente_sem_segmento' ? 'Cliente sem segmento' : 'Cliente sem contrato', '#f39c12', false);
                mostrarConfirmacaoSegmento(data, botaoOut, () => iniciarFluxoCompleto(botaoOut, textoEmpresa, data.codigo_cliente || codigoCliente, inicioEscolhido, sistemaCliente, opcaoContinuar), () => resetBotaoCheckout(botaoOut));
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

            const limparCancelarFluxo = mostrarCancelamentoProcesso(botaoOut, flowKey, 'Criando e finalizando atendimento', () => resetBotaoCheckout(botaoOut));
            iniciarPolling(flowKey, 1000, (s) => {
                if (tratarCancelado(s, botaoOut, limparCancelarFluxo, () => resetBotaoCheckout(botaoOut), 'Processo cancelado.')) return true;
                if (s.status === 'auvo_instavel') {
                    limparCancelarFluxo();
                    setBotao(botaoOut, '⚠️ Auvo com Instabilidade', '#c0392b', false);
                    mostrarNotificacao('O Auvo ficou instável durante o processo. Verifique e tente novamente.', 'erro');
                    resetBotaoCheckout(botaoOut);
                    return true;
                }
                if (s.status === 'aguardando_confirmacao_textos') {
                    confirmarTextosFluxo(
                        {
                            flow_key: flowKey,
                            descricao: s.descricao,
                            relato: s.relato,
                        },
                        botaoOut
                    );
                    return false;
                }
                if (s.status === 'aguardando_confirmacao_relato') {
                    setBotao(botaoOut, 'Conferir relato...', '#8e44ad', true, AUVO_ETAPAS_PROGRESSO['relato_ok']);
                    confirmarRelatoCheckout(
                        {
                            task_id: s.task_id,
                            status_key: s.status_key,
                            flow_key: flowKey,
                            relato: s.relato,
                            data: s.data,
                            hora: s.hora,
                            cancel_key: flowKey,
                            verificacao_equipamento: s.verificacao_equipamento || null,
                        },
                        botaoOut
                    );
                    return false;
                }
                if (etapas[s.status]) {
                    const [label, cor, pct] = etapas[s.status];
                    setBotao(botaoOut, label, cor, true, pct);
                }
                if (s.status === 'done') {
                    limparCancelarFluxo();
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

    function obterReferenciaAtendimentoAtual() {
        return Array.from(document.querySelectorAll('span'))
            .find(el => el.textContent.trim().includes('Protocolo')) || null;
    }

    function obterSistemaAtual(referencia) {
    const seletor = 'div.p-chip-text[data-pc-section="label"], .p-chip-text[data-pc-section="label"]';
    const textoChip = (el) => (el?.innerText || el?.textContent || '').trim();
    const ehSistema = (el) => {
        const texto = textoChip(el);
        return texto && !/^(suporte|interno|externo)$/i.test(texto);
    };

    const chips = Array.from(document.querySelectorAll(seletor))
        .filter(el => !el.closest('article.attendance_item'))
        .filter(ehSistema);

    if (chips.length === 0) return '';
    return chips.map(textoChip).join(' | ');
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
        const cancelKeyDespausaInicial = criarChaveCancelamento('despausa');
        mostrarCancelamentoProcesso(botaoDespausa, cancelKeyDespausaInicial, 'Fim da pausa', () => resetBotaoDespausa(botaoDespausa));
        setBotao(botaoDespausa, 'Finalizando pausa...', '#16a085', true, AUVO_ETAPAS_PROGRESSO['iniciando_pausa']);

        fetch('http://localhost:8765/auvo-despausa', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ empresa: textoEmpresa, ...(dadosDespausa || {}), cancel_key: cancelKeyDespausaInicial }),
        })
        .then(r => r.json())
        .then(data => {
            if (tratarRespostaCancelada(data, botaoDespausa, () => resetBotaoDespausa(botaoDespausa), 'Fim da pausa cancelado.')) return;
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
            const limparCancelarStatus = mostrarCancelamentoProcesso(botaoDespausa, data.status_key, `Processo Task #${data.task_id}`, () => resetBotaoDespausa(botaoDespausa));
            iniciarPolling(data.status_key, 1000, (s) => {
                if (tratarCancelado(s, botaoDespausa, limparCancelarStatus, () => resetBotaoDespausa(botaoDespausa), 'Fim da pausa cancelado.')) return true;
                if (s.status === 'auvo_instavel') {
                    limparCancelarStatus();
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
                    limparCancelarStatus();
                    setBotao(botaoDespausa, 'Erro!', '#e74c3c', false);
                    mostrarNotificacao(s.mensagem || 'Erro ao lançar fim da pausa.', 'erro');
                    resetBotaoDespausa(botaoDespausa);
                    return true;
                }
                if (s.status === 'done') {
                    limparCancelarStatus();
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
        const cancelKeyPausaInicial = criarChaveCancelamento('pausa');
        mostrarCancelamentoProcesso(botaoPausa, cancelKeyPausaInicial, 'Pausa', () => resetBotaoPausa(botaoPausa));
        setBotao(botaoPausa, 'Iniciando pausa...', '#8e44ad', true, AUVO_ETAPAS_PROGRESSO['iniciando_pausa']);

        fetch('http://localhost:8765/auvo-pausa', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ empresa: textoEmpresa, ...(dadosPausa || {}), cancel_key: cancelKeyPausaInicial }),
        })
        .then(r => r.json())
        .then(data => {
            if (tratarRespostaCancelada(data, botaoPausa, () => resetBotaoPausa(botaoPausa), 'Pausa cancelada.')) return;
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
            const limparCancelarStatus = mostrarCancelamentoProcesso(botaoPausa, data.status_key, `Processo Task #${data.task_id}`, () => resetBotaoPausa(botaoPausa));
            iniciarPolling(data.status_key, 1000, (s) => {
                if (tratarCancelado(s, botaoPausa, limparCancelarStatus, () => resetBotaoPausa(botaoPausa), 'Pausa cancelada.')) return true;
                if (s.status === 'auvo_instavel') {
                    limparCancelarStatus();
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
                    limparCancelarStatus();
                    setBotao(botaoPausa, 'Erro!', '#e74c3c', false);
                    mostrarNotificacao(s.mensagem || 'Erro ao lançar início da pausa.', 'erro');
                    resetBotaoPausa(botaoPausa);
                    return true;
                }
                if (s.status === 'done') {
                    limparCancelarStatus();
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
    function iniciarFluxoCheckin(botao, textoEmpresa, codigoCliente, inicioEscolhido, sistemaCliente, opcoes = {}) {
        bloquearAcaoOposta('checkin');
        const cancelKeyCheckinInicial = criarChaveCancelamento('checkin');
        mostrarCancelamentoProcesso(botao, cancelKeyCheckinInicial, 'Check-in', () => resetBotaoCheckin(botao));
        setBotao(botao, 'Analisando chat...', '#2ecc71', true, AUVO_ETAPAS_PROGRESSO['analisando']);

        fetch('http://localhost:8765/auvo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ empresa: textoEmpresa, codigo_cliente: codigoCliente || null, inicio_escolhido: inicioEscolhido || null, sistema: sistemaCliente || null, continuar_sem_contrato: !!opcoes.continuarSemContrato, continuar_sem_segmento: !!opcoes.continuarSemSegmento, cancel_key: cancelKeyCheckinInicial }),
        })
        .then(r => r.json())
        .then(data => {
            if (tratarRespostaCancelada(data, botao, () => resetBotaoCheckin(botao), 'Check-in cancelado.')) return;
            if (data.status === 'multiplas_empresas') {
                setBotao(botao, 'Lancar no Auvo', '#2ecc71', false);
                mostrarSeletorEmpresas(
                    data.empresas,
                    textoEmpresa,
                    botao,
                    (empresaEscolhida) => iniciarFluxoCheckin(botao, textoEmpresa, empresaEscolhida.codigo, null, sistemaCliente),
                    () => resetBotaoCheckin(botao)
                );
                return;
            }

            if (data.status === 'multiplos_inicios') {
                setBotao(botao, 'Lancar no Auvo', '#2ecc71', false);
                mostrarSeletorInicios(
                    data.inicios,
                    botao,
                    (inicioEscolhido) => iniciarFluxoCheckin(botao, textoEmpresa, data.codigo_cliente || codigoCliente, inicioEscolhido, sistemaCliente),
                    () => resetBotaoCheckin(botao)
                );
                return;
            }
            if (data.status === 'cliente_sem_segmento' || data.status === 'cliente_sem_contrato') {
                const opcaoContinuar = data.status === 'cliente_sem_segmento' ? { continuarSemSegmento: true } : { continuarSemContrato: true };
                setBotao(botao, data.status === 'cliente_sem_segmento' ? 'Cliente sem segmento' : 'Cliente sem contrato', '#f39c12', false);
                mostrarConfirmacaoSegmento(data, botao, () => iniciarFluxoCheckin(botao, textoEmpresa, data.codigo_cliente || codigoCliente, inicioEscolhido, sistemaCliente, opcaoContinuar), () => resetBotaoCheckin(botao));
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

            const chaveCheckin = String(data.task_id);
            const limparCancelarCheckin = mostrarCancelamentoProcesso(botao, chaveCheckin, `Check-in Task #${data.task_id}`, () => resetBotaoCheckin(botao));
            iniciarPolling(chaveCheckin, 1000, (s) => {
                if (tratarCancelado(s, botao, limparCancelarCheckin, () => resetBotaoCheckin(botao), 'Check-in cancelado.')) return true;
                if (s.status === 'auvo_instavel') {
                    limparCancelarCheckin();
                    setBotao(botao, 'Auvo com Instabilidade', '#c0392b', false);
                    mostrarNotificacao('O Auvo ficou instavel durante o check-in. Verifique a task e tente o check-in manualmente se necessario.', 'erro');
                    resetBotaoCheckin(botao);
                    return true;
                }
                if (s.status === 'done') {
                    limparCancelarCheckin();
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
        const spanProtocolo = obterReferenciaAtendimentoAtual();
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
            if (botao.dataset.auvoCancelKey) { processarCancelamentoBotao(botao); return; }
            if (botao.disabled) return;
            const textoEmpresa = obterDadosEmpresaAtual();
            const sistemaCliente = obterSistemaAtual(obterReferenciaAtendimentoAtual());
            iniciarFluxoCheckin(botao, textoEmpresa, null, null, sistemaCliente);
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
            if (botaoOut.dataset.auvoCancelKey) { processarCancelamentoBotao(botaoOut); return; }
            if (botaoOut.disabled) return;
            const textoEmpresa = obterDadosEmpresaAtual();
            const sistemaCliente = obterSistemaAtual(obterReferenciaAtendimentoAtual());
            iniciarFluxoCheckout(botaoOut, textoEmpresa, null, sistemaCliente);
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
            if (botaoPausa.dataset.auvoCancelKey) { processarCancelamentoBotao(botaoPausa); return; }
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
            if (botaoDespausa.dataset.auvoCancelKey) { processarCancelamentoBotao(botaoDespausa); return; }
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