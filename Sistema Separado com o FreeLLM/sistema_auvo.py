import pandas as pd
import difflib
import unicodedata
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from playwright.sync_api import sync_playwright
from datetime import datetime,timedelta,timezone
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import pyperclip
import os
import time
import ctypes
from ctypes import wintypes
import atexit
import uuid
import sys

# ---------------------------------------------------------------------------
# Modo de execução / separação do Playwright
# ---------------------------------------------------------------------------
SERVER_MODE = "--server" in sys.argv
PLAYWRIGHT_SERVER_HOST = os.getenv("PLAYWRIGHT_SERVER_HOST", "192.168.18.30")
PLAYWRIGHT_SERVER_PORT = int(os.getenv("PLAYWRIGHT_SERVER_PORT", "8766"))
PLAYWRIGHT_SERVER_URL = f"http://{PLAYWRIGHT_SERVER_HOST}:{PLAYWRIGHT_SERVER_PORT}"
# Altere este token antes de compilar e use o MESMO valor no cliente e no servidor.
PLAYWRIGHT_SERVER_TOKEN = os.getenv("PLAYWRIGHT_SERVER_TOKEN", "AUVO-MTdgN8JCKdAQsgtVI0uqT9cku_ernJth")
PLAYWRIGHT_REQUEST_TIMEOUT = (3, 15)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
REMOTE_PLAYWRIGHT_KEYS = {}
REMOTE_PLAYWRIGHT_KEYS_LOCK = threading.Lock()
REMOTE_PLAYWRIGHT_HTTP = requests.Session()

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
            # os._exit() não executa os handlers de atexit, então a limpeza
            # da presença precisa ser chamada explicitamente aqui.
            try:
                _encerrar_presenca_automacao()
            except Exception:
                pass
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
APP_ICON_PATH = os.path.join(APP_DIR, "sistema_auvo.ico")
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
# SUPABASE_URL e SUPABASE_ANON_KEY não são segredos sensíveis: a proteção real
# está nas políticas de RLS das tabelas "credenciais_auvo" e "usuarios_auvo" (só usuário autenticado lê).
SUPABASE_URL = "https://pvtoqrxhgqvuagpvevze.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InB2dG9xcnhoZ3F2dWFncHZldnplIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQwMzk0MzksImV4cCI6MjA5OTYxNTQzOX0.EIulEl2iSGMqg_D_cWKbYWsdLDuGgkypgSa5fCSPrHY"

_PASTA_CONFIG = APP_DIR
CONFIG_SUPABASE_PATH = r"\\SERVIDOR\Golden\Automacoes\Automacao Suporte\config_supabase.json"
ERRO_INICIALIZACAO_LOG = os.path.join(_PASTA_CONFIG, "erro_inicializacao.log")

_CREDENCIAIS_AUVO_CACHE = {}
_USUARIOS_AUVO_CACHE = {}
_USUARIOS_AUVO_CACHE_LOCK = threading.Lock()


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
                .select("api_key, api_token")
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


def _obter_usuario_auvo(cliente, nome_atendente: str) -> dict:
    """Busca usuário/senha do Auvo na tabela usuarios_auvo pelo nome do atendente."""
    nome_atendente = str(nome_atendente or "").strip()
    if not nome_atendente:
        raise RuntimeError("Nome do atendente não informado para localizar as credenciais do Auvo.")

    chave = nome_atendente.casefold()
    with _USUARIOS_AUVO_CACHE_LOCK:
        cached = _USUARIOS_AUVO_CACHE.get(chave)
        if cached:
            return dict(cached)

    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            tabela = cliente.table("usuarios_auvo")
            resp = (
                tabela.select("id, usuario, senha, nome")
                .eq("nome", nome_atendente)
                .execute()
            )
            if not resp.data:
                resp = (
                    tabela.select("id, usuario, senha, nome")
                    .ilike("nome", nome_atendente)
                    .execute()
                )
            if len(resp.data or []) > 1:
                raise RuntimeError(
                    f"Existem múltiplos cadastros em usuarios_auvo para '{nome_atendente}'. "
                    "Mantenha apenas um cadastro por atendente."
                )
            if not resp.data:
                raise RuntimeError(
                    f"Nenhum usuário Auvo encontrado na tabela usuarios_auvo para '{nome_atendente}'."
                )

            credencial = resp.data[0]
            if not credencial.get("usuario") or not credencial.get("senha"):
                raise RuntimeError(
                    f"O cadastro de '{nome_atendente}' em usuarios_auvo está sem usuário ou senha do Auvo."
                )

            with _USUARIOS_AUVO_CACHE_LOCK:
                _USUARIOS_AUVO_CACHE[chave] = dict(credencial)
            return dict(credencial)
        except Exception as e:
            ultimo_erro = e
            if tentativa < 3:
                print(
                    f"⚠️ Falha ao buscar usuário '{nome_atendente}' em usuarios_auvo "
                    f"({tentativa}/3): {e}. Tentando novamente..."
                )
                time.sleep(2)

    raise RuntimeError(
        f"Não foi possível obter as credenciais de '{nome_atendente}' em usuarios_auvo: {ultimo_erro}"
    )


_CREDENCIAL_IA_CACHE = {}


AGENDAMENTO_EXTERNO_TIPOS = {
    205918: {"nome": "Treinamento Presencial", "instrucao": "Crie uma orientação curta de treinamento presencial. Resuma o tema, o público/objetivo e qualquer preparação mencionada. Use no máximo duas frases."},
    205136: {"nome": "Visita Técnica", "instrucao": "Crie uma orientação de visita técnica: primeiro o problema informado pelo cliente, depois o que deve ser verificado ou feito no local. Seja direto."},
    200376: {"nome": "Instalação", "instrucao": "Crie uma ordem de instalação: informe o que instalar, onde e detalhes técnicos citados pelo cliente. Escreva de forma direta, preferencialmente em caixa alta."},
}

# Equipamentos do Suporte
EQUIPAMENTOS_SUPORTE = [
    "IDFACE",
    "IDFACE MAX",
    "EVO 40",
    "TOPDATA F4",
    "REP ID CLASS",
    "TopData Inner",
    "Kurumim",
]

# Equipamentos da Técnica (para referência / uso futuro)
EQUIPAMENTOS_TECNICA = [
    "Sinaleiro",
    "Catraca",
    "Cartográfico",
]

FREELLMAPI_BASE_URL = os.getenv("FREELLMAPI_BASE_URL", "http://192.168.18.30:31415/v1").rstrip("/")
FREELLMAPI_MODEL = os.getenv("FREELLMAPI_MODEL", "gemini-3.5-flash-lite").strip() or "gemini-3.5-flash-lite"
AGENDAMENTOS_EXTERNOS_PENDENTES = {}
AGENDAMENTOS_EXTERNOS_LOCK = threading.Lock()
_FREELLMAPI_API_KEY_CACHE = ""


def _obter_credencial_freellmapi() -> str:
    """Prioriza a chave FreeLLMAPI da linha id=2 no Supabase."""
    global _FREELLMAPI_API_KEY_CACHE
    if _FREELLMAPI_API_KEY_CACHE:
        return _FREELLMAPI_API_KEY_CACHE
    try:
        resposta = (
            _supabase_client.table("credenciais_ia")
            .select("api_key")
            .eq("id", 2)
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise RuntimeError(
            "Não foi possível ler credenciais_ia.api_key no Supabase para id=2. "
            "Verifique a sessão e a policy de SELECT da tabela; a variável de ambiente não substitui a chave do cadastro."
        ) from e
    if not resposta.data:
        raise RuntimeError("Não existe credenciais_ia com id=2 no Supabase.")
    chave = str(resposta.data[0].get("api_key") or "").strip()
    if not chave:
        raise RuntimeError("credenciais_ia.api_key está vazia para id=2.")
    _FREELLMAPI_API_KEY_CACHE = chave
    return chave


def _chamar_gemini_agendamento(prompt: str, max_tokens: int = 4000) -> str:
    """Compatibilidade com os chamadores antigos; usa a API OpenAI da FreeLLMAPI."""
    return chamar_claude(prompt, max_tokens=max_tokens)
def _gerar_descricao_agendamento_externo(mensagens: list[str], task_type: int, sistema: str = "", contato: str = "") -> str:
    tipo = AGENDAMENTO_EXTERNO_TIPOS.get(int(task_type))
    if not tipo:
        raise ValueError("Tipo de atendimento inválido para agendamento.")
    if int(task_type) == 205918:
        sistema = (sistema or "sistema não informado").strip()
        contato = (contato or "cliente não informado").strip()
        return f"Treinamento de {sistema}. Falar com {contato}."
    chave_prompt = "agendamento_visita_tecnica" if int(task_type) == 205136 else "agendamento_instalacao"
    template = _PROMPTS_IA_CACHE.get(chave_prompt)
    if not template:
        # O cache só era carregado na inicialização; recarrega do Supabase.
        _obter_prompts_ia(_supabase_client, forcar=True)
        template = _PROMPTS_IA_CACHE.get(chave_prompt)
    if not template:
        print(f"⚠️ Prompt '{chave_prompt}' não encontrado em prompts_ia; usando prompt padrão do código.")
        template = (
            "Você é um assistente de suporte técnico que prepara a descrição de uma tarefa de agendamento "
            f"({tipo['nome']}) a partir da conversa de chat com o cliente.\n"
            f"{tipo['instrucao']}\n\n"
            "Histórico da conversa:\n@@HISTORICO@@"
        )
    historico = preprocessar_mensagens(mensagens)[-9000:]
    prompt = template.replace("@@HISTORICO@@", historico)
    if "@@HISTORICO@@" not in template:
        prompt += chr(10) * 2 + "Histórico da conversa:" + chr(10) + historico
    prompt += chr(10) * 2 + "Retorne somente a descrição da tarefa, em português, sem data, horário, técnico, título ou markdown. Não invente dados."
    try:
        texto = _chamar_gemini_agendamento(prompt)
    except Exception as e:
        print(f"❌ Gemini falhou ao gerar a descrição do agendamento: {e}")
        texto = ""
    descricao = texto.replace("```", "").strip()
    primeira_linha, separador, restante = descricao.partition(chr(10))
    if primeira_linha.strip().lower() in {"text", "plaintext", "markdown"}:
        descricao = restante.strip() if separador else ""
    descricao = descricao.strip(chr(34)).strip(chr(39)).strip(chr(96))
    descricao = re.sub(r"\s+", " ", descricao)
    if not descricao or texto_placeholder_ia(descricao) or descricao in {".", ":"}:
        print("Gemini retornou descrição vazia/genérica; usando resumo do histórico como fallback.")
        descricao = gerar_descricao_fallback_mensagens(mensagens)
    if not descricao:
        descricao = "Cliente entrou em contato via chat. Conferir o histórico da conversa."
    print(f"📝 Descrição final do agendamento ({tipo['nome']}): {descricao[:500]!r}")
    return descricao[:500]
def _formatar_telefone_br(numero: str) -> str:
    digitos = re.sub(r"\D", "", str(numero or ""))
    if digitos.startswith("55") and len(digitos) in (12, 13):
        digitos = digitos[2:]
    if len(digitos) == 11:
        return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}"
    if len(digitos) == 10:
        return f"({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}"
    return str(numero or "").strip()


def _identificar_contato_por_ia(mensagens: list[str]) -> str:
    """Último recurso: pergunta ao Gemini o nome da pessoa do cliente que está no chat."""
    atendentes = {str(NOME_USER or "").strip().casefold()}
    atendentes.update(str(u.get("nome") or "").strip().casefold() for u in USUARIOS.values())
    atendentes.discard("")
    historico = preprocessar_mensagens(mensagens)[-6000:]
    prompt = (
        "Abaixo está uma conversa de suporte por chat entre um atendente e um cliente.\n"
        "Diga apenas o nome (primeiro nome ou nome completo) da PESSOA DO CLIENTE que conversa com o atendente. "
        f"O atendente se chama {NOME_USER}; não o use. Se o nome do cliente não aparecer, responda exatamente NAO_INFORMADO.\n\n"
        f"Conversa:\n{historico}"
    )
    try:
        resposta = _chamar_gemini_agendamento(prompt, max_tokens=4000)
    except Exception as e:
        print(f"⚠️ Não consegui identificar o contato pela IA: {e}")
        return ""
    nome = re.sub(r"\s+", " ", resposta.replace("```", "")).strip().strip("\"'`*.,:; ")
    if not nome or "NAO_INFORMADO" in nome.upper().replace("Ã", "A") or len(nome.split()) > 4 or len(nome) > 40:
        return ""
    if not re.fullmatch(r"[A-Za-zÀ-ÿ' .-]+", nome) or nome.casefold() in atendentes:
        return ""
    print(f"👤 Contato identificado pela IA: {nome!r}")
    return nome


# Sufixos que o Speakr acrescenta ao título da conversa e não fazem parte do nome do cliente.
SUFIXOS_NOME_CONTATO = (" - Suporte Externo",)


def pegar_contato_pagina_speakr() -> tuple[str, str]:
    """Lê na conversa aberta do Speakr o nome (h2 do cabeçalho) e o telefone
    (span truncate com só dígitos). Retorna (nome, telefone); vazio se não achar."""
    nome = telefone = ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            page = _selecionar_pagina_speakr(browser)
            achados = page.evaluate("""
                () => {
                    const visivel = (el) => {
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
                    };
                    const coletar = (seletor) => Array.from(document.querySelectorAll(seletor)).map(el => {
                        const r = el.getBoundingClientRect();
                        return { texto: (el.textContent || '').trim(), visivel: visivel(el), x: Math.round(r.left), y: Math.round(r.top) };
                    });
                    return {
                        telefones: coletar('span.truncate, span.v-popper--has-tooltip')
                            .filter(c => /^\\+?\\d{10,15}$/.test(c.texto.replace(/[\\s()\\-]/g, ''))),
                        nomes: coletar('h2.v-popper--has-tooltip, h2.line-clamp-3').filter(c => c.texto)
                    };
                }
            """)
            telefones = achados.get("telefones") or []
            nomes = achados.get("nomes") or []
            print(f"📞 Telefones na página: {[(c['texto'], c['x'], c['y']) for c in telefones]}")
            print(f"👤 Nomes (h2) na página: {[(c['texto'], c['x'], c['y']) for c in nomes]}")
            escolhido_tel = next((c for c in telefones if c.get("visivel")), (telefones or [None])[0])
            escolhido_nome = next((c for c in nomes if c.get("visivel")), (nomes or [None])[0])
            if escolhido_tel:
                telefone = escolhido_tel["texto"]
            if escolhido_nome:
                nome = re.sub(r"\s+", " ", escolhido_nome["texto"]).strip()
                for sufixo in SUFIXOS_NOME_CONTATO:
                    if nome.casefold().endswith(sufixo.casefold()):
                        nome = nome[: -len(sufixo)].strip()
    except Exception as e:
        print(f"⚠️ Não consegui ler o contato na página do Speakr: {e}")
    return nome, telefone


def _extrair_contato_cliente_auvo(cliente: dict) -> tuple[str, str]:
    if not isinstance(cliente, dict):
        return "Não informado", "Não informado"
    nome = telefone = primeiro_nome = sobrenome = ""
    aliases_nome = {"contactname", "namecontact", "contactperson", "personname", "nomecontato", "fullname", "fullcontactname", "name", "nome", "firstname", "lastname"}
    aliases_telefone = {"phone", "phone1", "phone2", "telephone", "cellphone", "mobilephone", "phonenumber", "celular", "telefone", "telefone1", "telefone2"}

    def percorrer(valor, em_contato=False):
        nonlocal nome, telefone, primeiro_nome, sobrenome
        if isinstance(valor, dict):
            for chave, item in valor.items():
                chave_norm = _normalizar_texto_segmento(str(chave)).lower().replace("_", "").replace("-", "")
                marcador_contato = any(termo in chave_norm for termo in ("contact", "contato", "responsible"))
                contato_item = em_contato or marcador_contato
                if isinstance(item, (dict, list)):
                    percorrer(item, contato_item)
                    continue
                if item in (None, ""):
                    continue
                texto = str(item).strip()
                if chave_norm in aliases_telefone or any(term in chave_norm for term in ("phone", "telephone", "celular", "telefone")):
                    if not telefone:
                        telefone = texto
                    continue
                if chave_norm in {"firstname", "nome"} and contato_item and not primeiro_nome:
                    primeiro_nome = texto
                elif chave_norm in {"lastname", "sobrenome"} and contato_item and not sobrenome:
                    sobrenome = texto
                elif not nome and (chave_norm in {"contact", "contato"} or (chave_norm in aliases_nome and (contato_item or chave_norm not in {"name", "nome", "firstname", "lastname"} ))):
                    nome = texto
        elif isinstance(valor, list):
            for item in valor:
                percorrer(item, em_contato)

    percorrer(cliente)
    if not nome and primeiro_nome:
        nome = " ".join(parte for parte in (primeiro_nome, sobrenome) if parte)
    return nome or "Não informado", telefone or "Não informado"

def criar_tarefa_agendamento_externo(token, customer_id: int, task_type: int, descricao: str):
    payload = {"customerId": int(customer_id), "taskType": int(task_type), "orientation": descricao}
    response = HTTP_SESSION.post(
        "https://api.auvo.com.br/v2/tasks/",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Connection": "close"},
        timeout=AUVO_CREATE_TIMEOUT,
    )
    if response.status_code in (500, 502, 503, 504):
        raise Exception(f"HTTP {response.status_code}: Auvo instável ao criar agendamento.")
    response.raise_for_status()
    dados = response.json()
    result = dados.get("result", dados)
    if isinstance(result, list):
        result = result[0] if result else {}
    task_id = (result.get("taskID") or result.get("taskNumber") or result.get("id")) if isinstance(result, dict) else None
    if not task_id:
        raise RuntimeError(f"Auvo não retornou o identificador da tarefa: {json.dumps(dados, ensure_ascii=False)[:300]}")
    _limpar_cache_tasks()
    return int(task_id)

# --- CONFIGURAÇÕES DE ACESSO AUVO (buscadas do Supabase na inicialização) ---
_config_supabase = _carregar_config_supabase()
_supabase_client = _login_supabase(_config_supabase)
_creds_auvo = _obter_credenciais_auvo(_supabase_client)

# As credenciais de UI do Auvo são específicas de cada atendente e ficam em
# usuarios_auvo. As credenciais abaixo ficam como fallback apenas para fluxos
# locais iniciados depois da seleção do atendente; no servidor, cada job
# usa a credencial do próprio usuário.
USUARIO_AUVO   = ""
SENHA_AUVO     = ""
AUVO_API_KEY   = _creds_auvo["api_key"]
AUVO_API_TOKEN = _creds_auvo["api_token"]

# ---------------------------------------------------------------------------
# Presença da automação (heartbeat) — usado para saber quantos atendentes
# estão com a automação aberta agora, e assim contabilizar corretamente os
# atendentes de uma ordem originada de ligações.
# ---------------------------------------------------------------------------
SESSAO_HEARTBEAT_INTERVALO_S = 20   # intervalo entre pulsos de presença
SESSAO_TIMEOUT_S             = 60   # sem pulso por esse tempo = considerado fechado

def _registrar_presenca_automacao():
    """Marca (ou renova) que este atendente está com a automação aberta."""
    try:
        _supabase_client.table("sessoes_ativas").upsert({
            "nome_atendente": NOME_USER,
            "atualizado_em": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        print(f"⚠️ Falha ao registrar presença da automação: {e}")

def _encerrar_presenca_automacao():
    """Remove o registro de presença deste atendente ao fechar a automação."""
    try:
        _supabase_client.table("sessoes_ativas").delete().eq("nome_atendente", NOME_USER).execute()
    except Exception as e:
        print(f"⚠️ Falha ao encerrar presença da automação: {e}")

def _loop_heartbeat_presenca():
    while True:
        time.sleep(SESSAO_HEARTBEAT_INTERVALO_S)
        _registrar_presenca_automacao()

def _contar_atendentes_com_automacao_aberta() -> int:
    """Conta quantos atendentes têm a automação aberta agora (pulso recente)."""
    limite = (datetime.now(timezone.utc) - timedelta(seconds=SESSAO_TIMEOUT_S)).isoformat()
    try:
        resultado = (
            _supabase_client.table("sessoes_ativas")
            .select("nome_atendente")
            .gte("atualizado_em", limite)
            .execute()
        )
        return len(resultado.data or [])
    except Exception as e:
        print(f"⚠️ Falha ao contar atendentes com automação aberta: {e}")
        return 0

user32 = ctypes.windll.user32
largura = user32.GetSystemMetrics(78)
altura = user32.GetSystemMetrics(79)

# --- USUÁRIOS ---
USUARIOS = {
    "1": {"nome": "Larissa",  "id": 208261},
    "2": {"nome": "Gustavo",  "id": 201025},
    "3": {"nome": "Caio",     "id": 201024},
    "4": {"nome": "Mateus",   "id": 200999},
    "5": {"nome": "Felipe",   "id": 208259},
    "6": {"nome": "Diogo",    "id": 208260},
    "7": {"nome": "Uarley",   "id": 200554},  # Encarregado
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

if SERVER_MODE:
    USUARIO_ATUAL = {"nome": "Servidor Playwright", "id": 0}
else:
    USUARIO_ATUAL = selecionar_usuario()

ID_USER       = USUARIO_ATUAL["id"]
NOME_USER     = USUARIO_ATUAL["nome"]

# Cada atendente usa o usuário/senha correspondente da tabela usuarios_auvo.
# O servidor fará a mesma busca por atendente e a senha nunca é enviada pela rede.
if not SERVER_MODE:
    _credencial_usuario_atual = _obter_usuario_auvo(_supabase_client, NOME_USER)
    USUARIO_AUVO = _credencial_usuario_atual["usuario"]
    SENHA_AUVO = _credencial_usuario_atual["senha"]

# Presença e bandeja pertencem somente ao processo do atendente.
if not SERVER_MODE:
    _registrar_presenca_automacao()
    threading.Thread(target=_loop_heartbeat_presenca, daemon=True).start()
    atexit.register(_encerrar_presenca_automacao)
    _iniciar_bandeja_sistema()


# ---------------------------------------------------------------------------
# Configuração da API do Claude (Anthropic)
# ---------------------------------------------------------------------------
FREELLMAPI_TIMEOUT = 180
FREELLMAPI_MAX_TOKENS = 4000

AUVO_TOKEN_CACHE = {"token": None, "expires_at": 0}
AUVO_TOKEN_LOCK = threading.Lock()
AUVO_STATE_PATH = os.path.join(APP_DIR, "state.json")
PLAYWRIGHT_STATE_DIR = os.path.join(APP_DIR, "sessions")
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
def chamar_claude(prompt: str, tentativas: int = 2, max_tokens: int = None) -> str:
    """Chama o endpoint OpenAI compatível da FreeLLMAPI."""
    limite_saida = max_tokens or FREELLMAPI_MAX_TOKENS
    headers = {
        "Authorization": f"Bearer {_obter_credencial_freellmapi()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": FREELLMAPI_MODEL,
        "max_tokens": limite_saida,
        "messages": [{"role": "user", "content": prompt}],
    }
    if FREELLMAPI_MODEL.lower() == "fusion" or FREELLMAPI_MODEL.lower().startswith("fusion:"):
        # Só reduz o painel quando Fusion for selecionado explicitamente.
        payload["fusion"] = {"k": 2}
    url = f"{FREELLMAPI_BASE_URL}/chat/completions"

    for i in range(tentativas):
        try:
            print(f"tentativa {i+1}/{tentativas}: chamando FreeLLMAPI ({FREELLMAPI_MODEL}, max_tokens={limite_saida})...")
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=FREELLMAPI_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            print(f"timeout na tentativa {i+1} ({FREELLMAPI_TIMEOUT}s excedido).")
            time.sleep(1.5)
            continue
        except requests.exceptions.RequestException as e:
            print(f"falha de conexao na tentativa {i+1}: {e}")
            time.sleep(1.5)
            continue

        if resp.status_code == 200:
            try:
                dados = resp.json()
                escolhas = dados.get("choices") or []
                mensagem = (escolhas[0].get("message") or {}) if escolhas else {}
                texto = str(mensagem.get("content") or "").strip()
            except Exception as e:
                print(f"resposta da API em formato inesperado ({e}).")
                dados = {}
                texto = ""

            motivo = ""
            if isinstance(dados, dict):
                motivo = ((dados.get("choices") or [{}])[0]).get("finish_reason", "") or ""

            # Cortou no limite de tokens (comum em modelos de raciocínio, que
            # gastam parte do limite pensando). Descarta o texto incompleto e
            # tenta de novo com o limite dobrado.
            if motivo == "length":
                print(
                    f"⚠️ FreeLLMAPI cortou a resposta no limite de {limite_saida} tokens. "
                    "Descartando texto incompleto."
                )
                print(f"   usage: {dados.get('usage')}")  # ajuda a confirmar se foi raciocínio
                if i + 1 < tentativas:
                    limite_saida = min(limite_saida * 2, 16000)
                    payload["max_tokens"] = limite_saida
                    print(f"   Tentando de novo com limite de {limite_saida} tokens...")
                    time.sleep(1)
                    continue
                return ""

            if texto:
                servido = dados.get("model") or FREELLMAPI_MODEL
                print(f"FreeLLMAPI OK ({servido}, {len(texto)} chars)")
                return texto

            print(
                f"tentativa {i+1}: resposta vazia da API (stop_reason={motivo or 'desconhecido'}). "
                f"JSON bruto (amostra): {json.dumps(dados, ensure_ascii=False)[:300]!r}"
            )

        elif resp.status_code == 401:
            print("Chave FreeLLMAPI inválida (credenciais_ia.id=2). Confira essa credencial no Supabase. Abortando tentativas.")
            break  # nao adianta insistir com uma chave invalida

        elif resp.status_code == 429:
            detalhe = (resp.text or "").strip().replace("\r", " ").replace("\n", " ")[:300]
            retry_after = (resp.headers.get("Retry-After") or "").strip()
            print(
                f"FreeLLMAPI retornou HTTP 429 para o modelo {FREELLMAPI_MODEL}. "
                f"Resposta: {detalhe or '<sem detalhe>'}. "
                f"Retry-After: {retry_after or '<não informado>'}."
            )
            if i + 1 < tentativas and retry_after:
                try:
                    espera_429 = min(max(float(retry_after), 1.0), 30.0)
                except ValueError:
                    espera_429 = 0
                if espera_429:
                    print(f"Aguardando {espera_429:g}s antes de repetir a chamada após o limite.")
                    time.sleep(espera_429)
                    continue
            break

        elif resp.status_code >= 500:
            print(f"tentativa {i+1}: erro no servidor FreeLLMAPI ({resp.status_code}). Tentando novamente...")

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


def anexar_equipamento_apos_sistema(descricao: str, equipamento: str) -> str:
    """Coloca a informação do equipamento logo depois da linha do sistema do cliente."""
    descricao = str(descricao or "").strip()
    equipamento = re.sub(r"\s+", " ", str(equipamento or "")).strip()
    if not equipamento:
        return descricao

    info = f"Equipamento com defeito: {equipamento}."
    # Remove uma anotação anterior para evitar duplicá-la ao atualizar a task.
    descricao = re.sub(
        r"\s*Equipamento com defeito:\s*[^.\n]+\.?",
        "",
        descricao,
        flags=re.IGNORECASE,
    ).strip()
    padrao_sistema = re.compile(r"(Sistema utilizado pela cliente:\s*[^.\n]+\.)", re.IGNORECASE)
    correspondencia = padrao_sistema.search(descricao)
    if correspondencia:
        fim = correspondencia.end()
        return f"{descricao[:fim]} {info}{descricao[fim:]}".strip()

    # Sem a linha de sistema, mantém o relato e acrescenta o equipamento ao final.
    return f"{descricao.rstrip()} {info}".strip()



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
        if _MARCADOR_TRANSCRICAO_AUDIO.match(texto):
            continue
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
    # IMPORTANTE: o preâmbulo só é removido se aparecer logo no início do texto
    # (nos primeiros ~60 caracteres). Sem esse limite, a busca por "disse" ou
    # "Foi" pode casar com essas palavras ocorrendo naturalmente dentro do
    # próprio relato (ex: "...e disse que vai colocar as máquinas...") e
    # truncar todo o conteúdo real, deixando só o trecho final.
    JANELA_PREAMBULO = 60
    inicio = texto[:JANELA_PREAMBULO]
    resto = texto[JANELA_PREAMBULO:]
    inicio_limpo = re.sub(
        r'^.*?(?:disse[:\s]+|(?:aqui\s+(?:está|esta)\s+(?:o|um)\s+)?relato[:\s]+)',
        '',
        inicio,
        flags=re.IGNORECASE | re.DOTALL,
        count=1,
    )
    texto = (inicio_limpo + resto).strip()

    # Se ainda restar lixo antes do "Foi", descarta tudo antes dele — mas só
    # quando o "Foi" estiver bem no começo (mesmo motivo do bloco acima).
    match = re.search(r'\bFoi\b', texto[:JANELA_PREAMBULO], re.IGNORECASE)
    if match and match.start() > 0:
        texto = texto[match.start():]

    return texto.strip()


# ---------------------------------------------------------------------------
# Pré-processamento do histórico de mensagens
# ---------------------------------------------------------------------------
_MARCADOR_TRANSCRICAO_AUDIO = re.compile(r"^transcri[cç][aã]o do [aá]udio$", re.IGNORECASE)


def preprocessar_mensagens(mensagens: list[str]) -> str:
    linhas = []
    aguardando_conteudo_audio = False
    for msg in mensagens:
        texto = msg.strip()

        # A ferramenta de transcrição de áudio quebra o rótulo e o conteúdo
        # em linhas separadas ao colar o chat. Sem isso, o conteúdo real do
        # relato fica "perdido" no meio do histórico, sem destaque nenhum,
        # e a IA tende a gerar uma descrição genérica ("enviou um áudio")
        # em vez de usar o que foi de fato dito na transcrição.
        if _MARCADOR_TRANSCRICAO_AUDIO.match(texto):
            aguardando_conteudo_audio = True
            continue
        if aguardando_conteudo_audio:
            aguardando_conteudo_audio = False
            if texto:
                linhas.append(
                    f"[TRANSCRIÇÃO DE ÁUDIO - use este relato como base principal da descrição] {texto}"
                )
            continue

        if "➥" in msg or NOME_USER in msg or "iniciarei seu atendimento" in msg:
            linhas.append(f"[TÉCNICO] {texto}")
        elif any(p in msg for p in [
            "Atendimento transferido", "Atendimento atribuído",
            "alta demanda", "Seja bem vindo", "digit", "sair",
            "HORÁRIO DE ATENDIMENTO", "Suporte em Sistemas", "automação"
        ]):
            continue
        else:
            linhas.append(f"[CLIENTE] {texto}")
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Geração de descrição (curta)
# ---------------------------------------------------------------------------
DESCRICAO_FALLBACK = "Cliente entrou em contato via chat para atendimento."
RELATO_FALLBACK    = "Foi realizado atendimento via chat."

_PROMPTS_IA_CACHE = {}


def _obter_prompts_ia(cliente, forcar: bool = False) -> dict:
    """Busca os textos dos prompts (descricao/relato/combinado) na tabela
    prompts_ia do Supabase. Se a tabela/linha não existir ou a busca falhar,
    usa os prompts padrão embutidos no código — nunca trava a inicialização
    por causa disso, já que não é um dado sensível como a API key."""
    if _PROMPTS_IA_CACHE and not forcar:
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
    """Obtém o prompt de relato configurado no Supabase."""
    template = _PROMPTS_IA_CACHE.get("relato")
    if not template:
        raise RuntimeError(
            "Prompt 'relato' não encontrado na tabela prompts_ia do Supabase. "
            "Cadastre a linha (chave='relato') antes de gerar relatos."
        )
    return template.replace("@@HISTORICO@@", linhas)


def _prompt_combinado(linhas: str) -> str:
    """Obtém o prompt combinado configurado no Supabase."""
    template = _PROMPTS_IA_CACHE.get("combinado")
    if not template:
        raise RuntimeError(
            "Prompt 'combinado' não encontrado na tabela prompts_ia do Supabase. "
            "Cadastre a linha (chave='combinado') antes de usar o fluxo combinado."
        )
    return template.replace("@@HISTORICO@@", linhas)


def gerar_descricao_com_ia(mensagens: list[str], sistema: str = None) -> str:
    if not mensagens:
        return anexar_sistema_descricao(DESCRICAO_FALLBACK, sistema)

    linhas = preprocessar_mensagens(mensagens)
    prompt = _prompt_descricao(linhas)

    resultado = chamar_claude(prompt, max_tokens=4000)
    if not resultado:
        print("⚠️ IA sem resposta completa para descrição. Usando resumo baseado no histórico.")
        descricao = gerar_descricao_fallback_mensagens(mensagens) or DESCRICAO_FALLBACK
        return anexar_sistema_descricao(descricao, sistema)

    return anexar_sistema_descricao(pegar_descricao(resultado, mensagens), sistema)


# ---------------------------------------------------------------------------
# Geração de relato detalhado
# ---------------------------------------------------------------------------
def gerar_relato_com_ia(mensagens: list[str]) -> str:
    if not mensagens:
        relato = anexar_finalizacao_inatividade(RELATO_FALLBACK, mensagens)
        return relato

    linhas = preprocessar_mensagens(mensagens)
    prompt = _prompt_relato(linhas)

    resultado = chamar_claude(prompt, max_tokens=8000)
    if not resultado:
        print("⚠️ Claude sem resposta para relato. Usando fallback.")
        relato = anexar_finalizacao_inatividade(RELATO_FALLBACK, mensagens)
        return relato

    dados = _extrair_json_gemini(resultado)
    relato_bruto = (dados.get("relato") or "").strip()

    if not relato_bruto or texto_placeholder_ia(relato_bruto):
        # fallback: se a IA não devolveu JSON, tenta tratar a resposta bruta
        # como texto puro (compatibilidade com prompt customizado no Supabase)
        relato_bruto = resultado if not texto_eco_prompt(resultado) else RELATO_FALLBACK

    relato = anexar_finalizacao_inatividade(limpar_relato(relato_bruto), mensagens)
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
    for chave in ("descricao", "relato"):
        m = re.search(rf'"{chave}"\s*:\s*"((?:[^"\\]|\\.)*)"', texto, flags=re.IGNORECASE | re.DOTALL)
        if m:
            valor = m.group(1)
            try:
                valor = json.loads(f'"{valor}"')
            except Exception:
                valor = valor.replace('\\"', '"').replace("\n", " ")
            dados[chave] = valor
            
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
    """Retorna a descrição da task e o relato de atendimento."""
    if not mensagens:
        descricao = anexar_sistema_descricao(DESCRICAO_FALLBACK, sistema)
        relato = anexar_finalizacao_inatividade(RELATO_FALLBACK, mensagens)
        return descricao, relato

    linhas = preprocessar_mensagens(mensagens)
    prompt = _prompt_combinado(linhas)

    resultado = chamar_claude(prompt, max_tokens=8000)
    dados = _extrair_json_gemini(resultado)

    if resultado and texto_eco_prompt(resultado) and not dados:
        print(f"⚠️ Claude ecoou o próprio prompt em vez do JSON. Amostra: {resultado[:300]!r}")
        resultado = ""

    descricao = (dados.get("descricao") or "").strip()
    relato = (dados.get("relato") or "").strip()

    if not descricao or texto_placeholder_ia(descricao):
        print("⚠️ Gemini não retornou descrição válida no JSON combinado. Usando fallback.")
        descricao = gerar_descricao_fallback_mensagens(mensagens)
    if not relato or texto_placeholder_ia(relato):
        print("⚠️ Gemini não retornou relato válido no JSON combinado. Usando fallback.")
        relato = RELATO_FALLBACK

    descricao = anexar_sistema_descricao(pegar_descricao(descricao, mensagens), sistema)
    relato = anexar_finalizacao_inatividade(limpar_relato(relato), mensagens)
    return descricao, relato


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


def _normalizar_nome_cliente_sem_cobranca(nome):
    texto = unicodedata.normalize("NFKD", str(nome or "")).encode("ASCII", "ignore").decode("ASCII")
    return re.sub(r"[^a-z0-9]+", " ", texto.lower()).strip()


def _buscar_registro_cliente_sem_cobranca(nome_cliente):
    chave = _normalizar_nome_cliente_sem_cobranca(nome_cliente)
    if not chave:
        return None
    resultado = _supabase_client.table("clientes_sem_cobranca").select("id,cliente,data_atendimento").execute()
    registros = [linha for linha in (resultado.data or []) if _normalizar_nome_cliente_sem_cobranca(linha.get("cliente")) == chave]
    if not registros:
        return None
    registros.sort(key=lambda linha: str(linha.get("data_atendimento") or ""), reverse=True)
    return registros[0]


def _data_iso_para_date(valor):
    texto = str(valor or "").strip()[:10]
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass
    return None


def _feriados_nacionais_brasil(ano):
    # Páscoa pelo algoritmo gregoriano; Sexta-feira Santa é dois dias antes.
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    pascoa = datetime(ano, mes, dia).date()
    return {
        datetime(ano, mes, dia).date()
        for mes, dia in ((1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25))
    } | {pascoa - timedelta(days=2)}


def _remover_registros_cliente_sem_cobranca(nome_cliente):
    chave = _normalizar_nome_cliente_sem_cobranca(nome_cliente)
    if not chave:
        return 0
    resultado = _supabase_client.table("clientes_sem_cobranca").select("id,cliente").execute()
    ids = [
        linha.get("id")
        for linha in (resultado.data or [])
        if _normalizar_nome_cliente_sem_cobranca(linha.get("cliente")) == chave and linha.get("id") is not None
    ]
    for registro_id in ids:
        _supabase_client.table("clientes_sem_cobranca").delete().eq("id", registro_id).execute()
    return len(ids)


def _dentro_prazo_15_dias_uteis(nome_cliente, data_atendimento):
    registro = _buscar_registro_cliente_sem_cobranca(nome_cliente)
    data_base = _data_iso_para_date((registro or {}).get("data_atendimento"))
    data_atual = _data_iso_para_date(data_atendimento)
    if not data_base or not data_atual or data_atual < data_base:
        return False
    uteis = 0
    dia = data_base
    while dia < data_atual:
        dia += timedelta(days=1)
        if dia.weekday() < 5 and dia not in _feriados_nacionais_brasil(dia.year):
            uteis += 1
    if uteis > 15:
        _remover_registros_cliente_sem_cobranca(nome_cliente)
        return False
    return True

def _registrar_checkout_cliente_sem_cobranca(nome_cliente, data_checkout):
    data_iso = _data_iso_para_date(data_checkout)
    nome = str(nome_cliente or "").strip()
    if not nome or not data_iso:
        raise ValueError("Nome do cliente ou data do check-out inválida para registrar cobrança.")
    registro = _buscar_registro_cliente_sem_cobranca(nome)
    valores = {"cliente": nome, "data_atendimento": data_iso.isoformat()}
    if registro:
        _supabase_client.table("clientes_sem_cobranca").update(valores).eq("id", registro["id"]).execute()
    else:
        _supabase_client.table("clientes_sem_cobranca").insert(valores).execute()

def buscar_segmento_auvo(token, segment_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Connection": "close"}
    url = f"https://api.auvo.com.br/v2/segments/{segment_id}"
    response = HTTP_SESSION.get(url, headers=headers, timeout=AUVO_LIST_TIMEOUT)
    if response.status_code != 200:
        print(f"⚠️ Erro ao consultar segmento Auvo ({response.status_code}): {response.text[:200]}")
        return {}
    return _extrair_resultado_auvo(response.json())


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
# Ponte entre o PC do atendente e o servidor Playwright
# ---------------------------------------------------------------------------
# No servidor o lock é por usuário do Auvo: jobs do mesmo atendente são
# serializados para não disputar a mesma sessão/state.json, enquanto usuários
# diferentes podem executar Playwright simultaneamente.
PLAYWRIGHT_USER_LOCKS = {}
PLAYWRIGHT_USER_LOCKS_GUARD = threading.Lock()
PLAYWRIGHT_JOB_CONTEXT = threading.local()


def _playwright_state_path():
    contexto = getattr(PLAYWRIGHT_JOB_CONTEXT, "dados", None)
    if contexto and contexto.get("state_path"):
        return contexto["state_path"]
    return AUVO_STATE_PATH


def _playwright_credentials():
    contexto = getattr(PLAYWRIGHT_JOB_CONTEXT, "dados", None)
    if contexto and contexto.get("usuario"):
        return contexto["usuario"], contexto["senha"]
    return USUARIO_AUVO, SENHA_AUVO


def _preparar_contexto_playwright(credencial: dict, status_keys=()):
    os.makedirs(PLAYWRIGHT_STATE_DIR, exist_ok=True)
    identificador = str(credencial.get("id") or credencial.get("usuario") or "sem_id")
    identificador = re.sub(r"[^A-Za-z0-9._-]+", "_", identificador).strip("._") or "sem_id"
    state_dir = os.path.join(PLAYWRIGHT_STATE_DIR, f"usuario_{identificador}")
    os.makedirs(state_dir, exist_ok=True)
    PLAYWRIGHT_JOB_CONTEXT.dados = {
        "id": credencial.get("id"),
        "nome": credencial.get("nome"),
        "usuario": credencial.get("usuario"),
        "senha": credencial.get("senha"),
        "status_keys": tuple(str(k) for k in (status_keys or ()) if k),
        "state_path": os.path.join(state_dir, "state.json"),
    }


def _playwright_status_keys():
    contexto = getattr(PLAYWRIGHT_JOB_CONTEXT, "dados", None)
    return tuple(contexto.get("status_keys") or ()) if contexto else ()


def _limpar_contexto_playwright():
    try:
        del PLAYWRIGHT_JOB_CONTEXT.dados
    except AttributeError:
        pass


def _lock_playwright_usuario(credencial: dict):
    chave = str(
        credencial.get("id")
        or credencial.get("usuario")
        or credencial.get("nome")
        or "sem_id"
    )
    with PLAYWRIGHT_USER_LOCKS_GUARD:
        return PLAYWRIGHT_USER_LOCKS.setdefault(chave, threading.Lock())


def _remote_headers():
    return {
        "Content-Type": "application/json",
        "X-Playwright-Token": PLAYWRIGHT_SERVER_TOKEN,
        "Connection": "close",
    }


def _remote_post(path, payload):
    resp = REMOTE_PLAYWRIGHT_HTTP.post(
        f"{PLAYWRIGHT_SERVER_URL}{path}",
        json=payload,
        headers=_remote_headers(),
        timeout=PLAYWRIGHT_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "ok":
        raise RuntimeError(data.get("mensagem") or "Servidor Playwright recusou a operação.")
    return data


def _remote_get_status(status_key):
    resp = REMOTE_PLAYWRIGHT_HTTP.get(
        f"{PLAYWRIGHT_SERVER_URL}/status/{status_key}",
        headers=_remote_headers(),
        timeout=PLAYWRIGHT_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _registrar_job_remoto(keys, remote_cancel_key):
    with REMOTE_PLAYWRIGHT_KEYS_LOCK:
        for key in keys:
            if key:
                REMOTE_PLAYWRIGHT_KEYS[str(key)] = str(remote_cancel_key)


def _remover_job_remoto(keys):
    with REMOTE_PLAYWRIGHT_KEYS_LOCK:
        for key in keys:
            if key:
                REMOTE_PLAYWRIGHT_KEYS.pop(str(key), None)


def _chave_remota(chave):
    with REMOTE_PLAYWRIGHT_KEYS_LOCK:
        return str(chave) in REMOTE_PLAYWRIGHT_KEYS


def _cancelar_remoto(chave):
    with REMOTE_PLAYWRIGHT_KEYS_LOCK:
        remote_key = REMOTE_PLAYWRIGHT_KEYS.get(str(chave), chave)
    try:
        _remote_post("/cancel", {"chave": str(remote_key)})
    except Exception as e:
        print(f"⚠️ Falha ao encaminhar cancelamento para o servidor: {e}")


def _delegar_playwright_ao_servidor(tipo, payload, status_keys, cancel_keys=()):
    """Envia o trabalho de UI ao servidor usando chaves remotas únicas por job."""
    status_keys = tuple(str(k) for k in status_keys if k is not None)
    cancel_keys = tuple(str(k) for k in cancel_keys if k is not None)
    todas_chaves = tuple(dict.fromkeys(status_keys + cancel_keys))

    job_id = uuid.uuid4().hex
    remote_status = {
        key: f"job_{job_id}_{indice}"
        for indice, key in enumerate(status_keys)
    }
    remote_cancel_key = f"job_{job_id}_cancel"
    _registrar_job_remoto(todas_chaves, remote_cancel_key)
    dados_remotos = dict(payload)
    dados_remotos.update({
        "job_id": job_id,
        "nome_atendente": NOME_USER,
        "id_atendente": ID_USER,
        "remote_status_keys": list(remote_status.values()),
        "remote_cancel_key": remote_cancel_key,
    })

    try:
        endpoint = {
            "checkin": "/playwright/checkin",
            "checkout": "/playwright/checkout",
            "checkin_checkout": "/playwright/checkin-checkout",
        }[tipo]
        for key in status_keys:
            task_status[key] = "aguardando_servidor"

        try:
            resposta = _remote_post(endpoint, dados_remotos)
        except Exception as e:
            for key in status_keys:
                task_status[key] = "erro"
                task_status[f"{key}_mensagem"] = f"Servidor Playwright indisponível: {e}"
            raise

        estados_finais = {"done", "erro", "cancelado", "auvo_instavel"}
        remote_cancel_enviado = False
        falhas_status = 0
        while True:
            if any(cancelamento_solicitado(k) for k in cancel_keys + status_keys):
                if not remote_cancel_enviado:
                    _cancelar_remoto(remote_cancel_key)
                    remote_cancel_enviado = True

            resultados = {}
            for local_key, remote_key in remote_status.items():
                try:
                    resultados[local_key] = _remote_get_status(remote_key)
                    falhas_status = 0
                except Exception as e:
                    falhas_status += 1
                    print(f"⚠️ Status remoto indisponível para {local_key}: {e}")
                    if falhas_status >= 40:
                        for status_key in status_keys:
                            task_status[status_key] = "erro"
                            task_status[f"{status_key}_mensagem"] = "Servidor Playwright ficou indisponível durante a execução."
                        raise RuntimeError("Servidor Playwright ficou indisponível durante a execução.") from e
                    continue

                remoto = resultados[local_key]
                task_status[local_key] = remoto.get("status", "pending")
                for campo in ("relato", "data", "hora", "status_key", "task_id", "mensagem", "erro_registro_supabase"):
                    if campo in remoto:
                        task_status[f"{local_key}_{campo}"] = remoto[campo]
                if remoto.get("erro_registro_supabase") and local_key.startswith("checkout_"):
                    task_status[f"cobranca_sem_contrato_erro_{local_key.removeprefix('checkout_')}"] = remoto["erro_registro_supabase"]

            if resultados and all(r.get("status") in estados_finais for r in resultados.values()):
                if tipo == "checkout" and resultados.get(status_keys[0], {}).get("status") == "done":
                    for key in status_keys:
                        if key.startswith("checkout_"):
                            task_id_local = key.removeprefix("checkout_")
                            task_status.pop(f"cobranca_sem_contrato_checkout_{task_id_local}", None)
                            task_status.pop(f"cliente_sem_contrato_task_{task_id_local}", None)
                    try:
                        _salvar_tasks_sem_contrato_ativas()
                    except Exception:
                        pass
                return resultados
            time.sleep(0.7)
    finally:
        _remover_job_remoto(todas_chaves)


def _executar_job_com_lock(func, args, credencial=None, status_keys=()):
    if SERVER_MODE and credencial:
        lock = _lock_playwright_usuario(credencial)
        with lock:
            _preparar_contexto_playwright(credencial, status_keys=status_keys)
            try:
                print(
                    f"▶️ Job Playwright | atendente={credencial.get('nome')} "
                    f"| usuário Auvo={credencial.get('usuario')} | state={_playwright_state_path()}"
                )
                return func(*args)
            finally:
                _limpar_contexto_playwright()
    return func(*args)


# ---------------------------------------------------------------------------
# Helpers do Playwright
# ---------------------------------------------------------------------------
def _abrir_browser_e_pagina(playwright):
    if SERVER_MODE and getattr(sys, "frozen", False):
        bundled_browsers = os.path.join(BUNDLE_DIR, "ms-playwright")
        if os.path.isdir(bundled_browsers):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = bundled_browsers
            browser = playwright.chromium.launch(headless=True)
        else:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
    else:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
    state_path = _playwright_state_path()
    context = (
        browser.new_context(storage_state=state_path)
        if os.path.exists(state_path)
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
    usuario_auvo, senha_auvo = _playwright_credentials()
    if not usuario_auvo or not senha_auvo:
        raise RuntimeError("Credenciais do usuário Auvo não carregadas para esta sessão.")
    page.locator('#usuario').wait_for(state="visible", timeout=15000)
    page.locator('#usuario').fill(usuario_auvo)
    page.locator('#senha').fill(senha_auvo)
    page.keyboard.press("Enter")

    # Aguarda sair da tela de login
    page.wait_for_function("!window.location.href.includes('login')", timeout=25000)
    context.storage_state(path=_playwright_state_path())
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

                context.storage_state(path=_playwright_state_path())
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

                context.storage_state(path=_playwright_state_path())
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
SEM_CONTRATO_TASKS_PATH = os.path.join(APP_DIR, "clientes_sem_contrato_ativos.json")
SEM_CONTRATO_TASKS_LOCK = threading.Lock()


def _salvar_tasks_sem_contrato_ativas():
    with SEM_CONTRATO_TASKS_LOCK:
        ativos = {
            chave.removeprefix("cliente_sem_contrato_task_"): valor
            for chave, valor in task_status.items()
            if chave.startswith("cliente_sem_contrato_task_") and isinstance(valor, dict)
        }
        try:
            with open(SEM_CONTRATO_TASKS_PATH, "w", encoding="utf-8") as arquivo:
                json.dump(ativos, arquivo, ensure_ascii=False, indent=2)
        except Exception as erro:
            print(f"⚠️ Não foi possível persistir as tasks sem contrato: {erro}")


try:
    with open(SEM_CONTRATO_TASKS_PATH, "r", encoding="utf-8") as arquivo:
        _tasks_sem_contrato_salvas = json.load(arquivo)
    if isinstance(_tasks_sem_contrato_salvas, dict):
        for _task_id_sem_contrato, _dados_sem_contrato in _tasks_sem_contrato_salvas.items():
            if isinstance(_dados_sem_contrato, dict):
                task_status[f"cliente_sem_contrato_task_{_task_id_sem_contrato}"] = _dados_sem_contrato
except FileNotFoundError:
    pass
except Exception as erro:
    print(f"⚠️ Não foi possível carregar as tasks sem contrato salvas: {erro}")

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
    if not SERVER_MODE:
        _delegar_playwright_ao_servidor(
            "checkin",
            {
                "task_id": int(task_id),
                "data_checkin": data_checkin,
                "hora_checkin": hora_checkin,
                "inicio": inicio,
                "cancel_key": cancel_key,
                "nome_atendente": NOME_USER,
                "id_atendente": ID_USER,
            },
            status_keys=(str(task_id),),
            cancel_keys=(str(task_id), cancel_key),
        )
        return
    print(f"\n[UI] Iniciando check-in para Task #{task_id}...")
    status_keys_remotos = _playwright_status_keys()
    status_key = status_keys_remotos[0] if (SERVER_MODE and status_keys_remotos) else str(task_id)
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
                # O botão permanece visível enquanto o modo de edição está aberto;
                # o sinal de conclusão é o fechamento do modal da atividade.
                page.locator('input[name="datePicker"]:visible').last.wait_for(state="hidden", timeout=8000)
                page.wait_for_timeout(400)

                context.storage_state(path=_playwright_state_path())
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
# Fluxo combinado: check-in e check-out na mesma página do Auvo.
# ---------------------------------------------------------------------------
def realizar_checkin_e_checkout_ui_invisivel(task_id, data_checkin, hora_checkin,
                                               data_checkout, hora_checkout, relato,
                                               cancel_key=None):
    if not SERVER_MODE:
        _delegar_playwright_ao_servidor(
            "checkin_checkout",
            {
                "task_id": int(task_id),
                "data_checkin": data_checkin,
                "hora_checkin": hora_checkin,
                "data_checkout": data_checkout,
                "hora_checkout": hora_checkout,
                "relato": relato,
                "cancel_key": cancel_key,
                "nome_atendente": NOME_USER,
                "id_atendente": ID_USER,
            },
            status_keys=(str(task_id), f"checkout_{task_id}"),
            cancel_keys=(str(task_id), f"checkout_{task_id}", cancel_key),
        )
        return
    print(f"\n[UI] Lançando relato, check-in e check-out para Task #{task_id} na mesma página...")
    status_keys_remotos = _playwright_status_keys()
    if SERVER_MODE and len(status_keys_remotos) >= 2:
        checkin_key, checkout_key = status_keys_remotos[0], status_keys_remotos[1]
    else:
        checkin_key = str(task_id)
        checkout_key = f"checkout_{task_id}"
    cancel_keys = (checkin_key, checkout_key, cancel_key)

    for tentativa in range(1, 4):
        verificar_cancelamento(*cancel_keys)
        with sync_playwright() as playwright:
            browser, context, page = _abrir_browser_e_pagina(playwright)
            try:
                url_tarefa = f"https://app2.auvo.com.br/relatorioTarefas/DetalheTarefa/{task_id}"
                page.goto(url_tarefa, wait_until="domcontentloaded", timeout=AUVO_UI_TIMEOUT)
                _garantir_login(page, context, url_destino=url_tarefa)
                page.locator('text="Relato do usuário"').first.wait_for(state="visible", timeout=AUVO_UI_TIMEOUT)
                if _pagina_com_erro_auvo(page):
                    raise Exception("auvo_instavel: página de erro HTTP detectada na UI")

                # Relato
                _clicar_editar(page, secao="Relato do usuário")
                campo_relato = page.locator('textarea, div[contenteditable="true"]').first
                campo_relato.wait_for(state="visible", timeout=8000)
                campo_relato.fill(relato)

                # Mantém as alterações pendentes e abre diretamente o menu de
                # atividades para lançar check-in e check-out antes do salvamento.
                verificar_cancelamento(*cancel_keys)
                page.locator("#adicionarAtividadeButton").wait_for(state="visible", timeout=8000)
                _clicar_item_dropdown(page, "Check-In")
                page.locator('input[name="datePicker"]:visible').last.wait_for(state="visible", timeout=12000)
                _preencher_data_hora(page, data_checkin, hora_checkin)

                # Check-out sem fechar, navegar ou sair do modo de edição.
                verificar_cancelamento(*cancel_keys)
                _clicar_item_dropdown(page, "Check-Out")
                page.locator('input[name="datePicker"]:visible').last.wait_for(state="visible", timeout=12000)
                _preencher_data_hora(page, data_checkout, hora_checkout)

                # Salva relato, check-in e check-out juntos ao fim do fluxo.
                page.locator("#edicao-salvar:visible").last.click(force=True)
                page.wait_for_selector("#edicao-salvar", state="hidden", timeout=12000)

                context.storage_state(path=_playwright_state_path())
                _limpar_cache_tasks()
                task_status[checkin_key] = "done"
                task_status[checkout_key] = "done"
                limpar_cancelamento(checkin_key)
                limpar_cancelamento(checkout_key)
                print(f"✓ Task #{task_id} — relato, check-in e check-out salvos na mesma página!")
                return
            except ProcessoCancelado:
                task_status[checkin_key] = "cancelado"
                task_status[checkout_key] = "cancelado"
                return
            except Exception as e:
                print(f"[UI ERROR] Tentativa {tentativa}/3 — fluxo combinado Task #{task_id}: {e}")
                if tentativa == 3:
                    instavel = detectar_instabilidade_auvo(e) or "instabilidade" in str(e).lower()
                    task_status[checkin_key] = "auvo_instavel" if instavel else "erro"
                    task_status[checkout_key] = "auvo_instavel" if instavel else "erro"
                    raise
                dormir_com_cancelamento(tentativa * 8, *cancel_keys)
            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Check-out (com retry em instabilidade do Auvo)
# ---------------------------------------------------------------------------
def realizar_checkout_ui_invisivel(task_id, data_checkout, hora_checkout, relato, cancel_key=None, contexto_remoto=None):
    if not SERVER_MODE:
        status_key = f"checkout_{task_id}"
        _delegar_playwright_ao_servidor(
            "checkout",
            {
                "task_id": int(task_id),
                "data_checkout": data_checkout,
                "hora_checkout": hora_checkout,
                "relato": relato,
                "cancel_key": cancel_key,
                "contexto_remoto": contexto_remoto,
                "nome_atendente": NOME_USER,
                "id_atendente": ID_USER,
            },
            status_keys=(status_key,),
            cancel_keys=(status_key, cancel_key),
        )
        return
    print(f"\n[UI] Iniciando check-out para Task #{task_id}...")
    status_keys_remotos = _playwright_status_keys()
    status_key = status_keys_remotos[0] if (SERVER_MODE and status_keys_remotos) else f"checkout_{task_id}"
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

                context.storage_state(path=_playwright_state_path())
                print(f"✓ Check-out Task #{task_id} salvo!")
                _limpar_cache_tasks()
                registro_cobranca = task_status.pop(f"cobranca_sem_contrato_checkout_{task_id}", None)
                if registro_cobranca:
                    try:
                        _registrar_checkout_cliente_sem_cobranca(registro_cobranca["cliente"], registro_cobranca["data_checkout"])
                        print(f"✓ Check-out de {registro_cobranca['cliente']} salvo na tabela clientes_sem_cobranca.")
                    except Exception as erro_registro:
                        task_status[f"cobranca_sem_contrato_erro_{task_id}"] = str(erro_registro)
                        print(f"⚠️ Check-out salvo, mas falhou atualização da tabela clientes_sem_cobranca: {erro_registro}")
                task_status.pop(f"cliente_sem_contrato_task_{task_id}", None)
                _salvar_tasks_sem_contrato_ativas()
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

_CLIENTES_DF_CACHE = None
_CLIENTES_CACHE_TIMESTAMP = 0.0
CLIENTES_CACHE_TTL_SEGUNDOS = 120  # recarrega da base no máximo a cada 2 min


def _obter_clientes_df(cliente, forcar_atualizacao=False):
    """Busca a tabela `clientes` no Supabase (colunas 'Código' e 'Nome') e monta
    o DataFrame usado pra busca fuzzy de empresa. Pagina de 1000 em 1000
    linhas (limite padrão do PostgREST) até esgotar os registros.

    O resultado fica em cache por CLIENTES_CACHE_TTL_SEGUNDOS pra não bater
    no Supabase a cada mensagem — mas expira sozinho, então uma alteração
    feita na tabela clientes enquanto a automação está aberta é refletida
    em no máximo esse intervalo (em vez de só depois de reiniciar o script,
    como acontecia com o cache antigo)."""
    global _CLIENTES_DF_CACHE, _CLIENTES_CACHE_TIMESTAMP

    cache_valido = (
        _CLIENTES_DF_CACHE is not None
        and (time.time() - _CLIENTES_CACHE_TIMESTAMP) < CLIENTES_CACHE_TTL_SEGUNDOS
    )
    if cache_valido and not forcar_atualizacao:
        return _CLIENTES_DF_CACHE

    linhas = []
    tamanho_pagina = 1000
    inicio = 0
    while True:
        resp = (
            cliente.table("clientes")
            .select("Código, Nome")
            .range(inicio, inicio + tamanho_pagina - 1)
            .execute()
        )
        dados = resp.data or []
        linhas.extend(dados)
        if len(dados) < tamanho_pagina:
            break
        inicio += tamanho_pagina

    if not linhas:
        # PostgREST não lança erro quando uma política de RLS bloqueia as
        # linhas: ele simplesmente devolve uma lista vazia com status 200.
        # Se chegou aqui, o mais provável é que a tabela `clientes` ainda
        # não tenha uma policy de SELECT liberada pro usuário/role que faz
        # login em _login_supabase (a mesma que já funciona para as
        # credenciais do Auvo e os prompts da IA).
        raise RuntimeError(
            "A consulta à tabela 'clientes' no Supabase voltou vazia. "
            "Verifique se existe uma policy de RLS de SELECT liberando o "
            "acesso pro usuário autenticado (o botão 'Add RLS policy' na "
            "tela do Supabase indica que ainda não há nenhuma). Se já "
            "existir policy, confirme também se os nomes das colunas são "
            "exatamente 'Código' e 'Nome' (maiúsculas/acentos)."
        )

    df = pd.DataFrame(linhas)
    if "Código" not in df.columns or "Nome" not in df.columns:
        raise RuntimeError(
            "A tabela 'clientes' no Supabase devolveu colunas diferentes do "
            f"esperado: {list(df.columns)}. Esperava 'Código' e 'Nome'. "
            "Ajuste os nomes no .select() da função _obter_clientes_df."
        )

    df = df.rename(columns={"Código": "codigo", "Nome": "nome"})
    df["nome"] = df["nome"].fillna("")
    df["nome_normalizado"] = df["nome"].apply(normalizar)
    _CLIENTES_DF_CACHE = df
    _CLIENTES_CACHE_TIMESTAMP = time.time()
    print(f"✅ Clientes carregados do Supabase: {len(df)} registros")
    return df


# Carrega uma vez na inicialização só pra falhar cedo se RLS/colunas
# estiverem errados. A partir daqui, quem busca clientes chama
# _obter_clientes_df(_supabase_client) de novo (buscar_opcoes_empresas usa
# esse retorno), que decide sozinho se reaproveita o cache ou recarrega.
_obter_clientes_df(_supabase_client)

def buscar_opcoes_empresas(nome_empresa, limite=8):
    if not nome_empresa:
        return []

    termo = normalizar(limpar_nome_empresa(nome_empresa))
    if not termo:
        return []

    df = _obter_clientes_df(_supabase_client)

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

                    const idsRobo = new Set(['botao-auvo', 'botao-auvo-checkout', 'botao-auvo-agendamentos', 'botao-auvo-pausa', 'botao-auvo-despausa', 'botao-auvo-ligacao', 'botao-auvo-equipamento-problema']);
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
def fluxo_checkin_e_checkout(empresa, mensagens, codigo_cliente, inicio_escolhido=None, sistema=None, cancel_key=None, registrar_cliente_sem_cobranca=False):
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
        def _gerar_textos():
            descricao_resultado[0], relato_resultado[0] = gerar_descricao_e_relato_com_ia(mensagens, sistema)
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

        task_status[chave] = "lancando_checkin_checkout"
        realizar_checkin_e_checkout_ui_invisivel(
            task_id, data_inicio, hora_inicio, data_checkout or data_inicio,
            hora_checkout, relato_confirmado, cancel_key=cancel_key or chave,
        )
        verificar_cancelamento(chave, cancel_key, str(task_id), f"checkout_{task_id}")
        if task_status.get(f"checkout_{task_id}") != "done":
            raise Exception("O check-out não foi confirmado na interface do Auvo.")

        verificar_cancelamento(chave, cancel_key, f"checkout_{task_id}")
        if registrar_cliente_sem_cobranca:
            try:
                _registrar_checkout_cliente_sem_cobranca(empresa, data_checkout or data_inicio)
            except Exception as erro_registro:
                task_status[f"{chave}_erro_registro_supabase"] = str(erro_registro)
                print(f"⚠️ Fluxo salvo no Auvo, mas falhou atualização da tabela clientes_sem_cobranca: {erro_registro}")
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
def preparar_confirmacao_relato_checkout(task_id, data_checkout, hora_checkout, relato, cancel_key=None, flow_key=None):
    status_key = f"checkout_{task_id}"
    task_status[status_key] = "aguardando_confirmacao_relato"
    task_status[f"{status_key}_relato"] = relato or RELATO_FALLBACK
    task_status[f"{status_key}_data"] = data_checkout
    task_status[f"{status_key}_hora"] = hora_checkout
    task_status[f"{status_key}_cancel_key"] = cancel_key
    if flow_key:
        task_status[flow_key] = "aguardando_confirmacao_relato"
        task_status[f"{flow_key}_checkout_key"] = status_key
        task_status[f"{flow_key}_relato"] = relato or RELATO_FALLBACK
        task_status[f"{flow_key}_data_checkout"] = data_checkout
        task_status[f"{flow_key}_hora_checkout"] = hora_checkout
        task_status[f"{flow_key}_task_id"] = task_id
    return {
        "status": "confirmar_relato",
        "task_id": task_id,
        "status_key": status_key,
        "flow_key": flow_key,
        "relato": relato or RELATO_FALLBACK,
        "data": data_checkout,
        "hora": hora_checkout,
    }


def aguardar_confirmacao_relato_checkout(task_id, data_checkout, hora_checkout, relato, cancel_key=None, flow_key=None):
    status_key = f"checkout_{task_id}"
    preparar_confirmacao_relato_checkout(task_id, data_checkout, hora_checkout, relato, cancel_key=cancel_key, flow_key=flow_key)
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
            if SERVER_MODE and self.headers.get("X-Playwright-Token", "") != PLAYWRIGHT_SERVER_TOKEN:
                self.send_response(401)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "erro", "mensagem": "Token inválido."}).encode())
                return
            task_id = self.path.split("/")[-1]
            status  = task_status.get(task_id, "pending")
            extra   = {}

            # O processo do atendente espelha os status remotos em task_status.
            # Isso mantém a UI usando as mesmas chaves antigas, enquanto o
            # servidor trabalha internamente com IDs de job únicos.

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
                ]:
                    if origem in task_status:
                        extra[destino] = task_status[origem]

            if task_id.startswith("checkout_"):
                id_checkout = task_id.removeprefix("checkout_")
                erro_registro = task_status.get(f"cobranca_sem_contrato_erro_{id_checkout}")
                if erro_registro:
                    extra["erro_registro_supabase"] = erro_registro
                for origem, destino in [
                    (f"{task_id}_relato", "relato"),
                    (f"{task_id}_data", "data"),
                    (f"{task_id}_hora", "hora"),
                ]:
                    if origem in task_status:
                        extra[destino] = task_status[origem]

            if task_id.startswith("full_flow_"):
                erro_registro = task_status.get(f"{task_id}_erro_registro_supabase")
                if erro_registro:
                    extra["erro_registro_supabase"] = erro_registro
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

        elif self.path == "/registro-ligacao-status":
            try:
                abertos = (
                    _supabase_client.table("registro_ligacoes")
                    .select("id")
                    .eq("nome_atendente", NOME_USER)
                    .eq("statusdeligação", True)
                    .limit(1)
                    .execute()
                ).data or []
                self._enviar_json({
                    "status": "ok",
                    "em_ligacao": bool(abertos),
                    "registro_id": abertos[0].get("id") if abertos else None,
                })
            except Exception as e:
                print(f"❌ Erro ao consultar ligação: {e}")
                self._enviar_json({"status": "erro", "mensagem": str(e)})

    def do_POST(self):
        # ── REGISTRO DE LIGAÇÃO ─────────────────────────────────────────────
        if self.path == "/registro-ligacao":
            try:
                content_length = int(self.headers.get("Content-Length", 0) or 0)
                dados = json.loads(self.rfile.read(content_length) or b"{}")
                # O usuário é escolhido no início da automação. Não usamos o
                # texto da sidebar, pois ele pode conter rótulos como “Ligação”.
                nome_atendente = NOME_USER
                em_ligacao = dados.get("em_ligacao")

                if not isinstance(em_ligacao, bool):
                    raise ValueError("O status da ligação é inválido.")

                tabela_ligacoes = _supabase_client.table("registro_ligacoes")
                if em_ligacao:
                    # `hora_ligação` guarda o início da chamada, inclusive após
                    # o status ser alterado para false no encerramento. Outro
                    # sistema consulta esse status, então a ligação precisa
                    # ficar registrada no banco assim que é iniciada.
                    resultado = tabela_ligacoes.insert({
                        "nome_atendente": nome_atendente,
                        "statusdeligação": True,
                        "hora_ligação": datetime.now().strftime("%H:%M:%S"),
                    }).execute()
                    registros = resultado.data or []
                    if not registros:
                        raise RuntimeError("O Supabase não retornou o registro da ligação.")
                    resposta = {"status": "ok", "registro_id": registros[0].get("id"), "em_ligacao": True}
                else:
                    # Ao finalizar a ligação, o front pergunta se ela vai virar
                    # uma ordem de serviço.
                    vai_para_ordem = dados.get("vai_para_ordem")
                    if not isinstance(vai_para_ordem, bool):
                        raise ValueError("É necessário informar se a ligação vai para a ordem.")

                    if not vai_para_ordem:
                        # Não vai pra ordem: apaga o registro desta ligação.
                        resultado = (
                            tabela_ligacoes.delete()
                            .eq("nome_atendente", nome_atendente)
                            .eq("statusdeligação", True)
                            .execute()
                        )
                        if not (resultado.data or []):
                            raise RuntimeError("Nenhuma ligação em andamento foi encontrada para este atendente.")
                        resposta = {"status": "ok", "em_ligacao": False, "vai_para_ordem": False}
                    else:
                        # Vai pra ordem: só encerra a ligação (statusdeligação
                        # = False). Não existe coluna própria de confirmação —
                        # como quem responde "não" já é excluído, toda ligação
                        # que sobra com status False já é uma confirmação.
                        resultado = (
                            tabela_ligacoes.update({"statusdeligação": False})
                            .eq("nome_atendente", nome_atendente)
                            .eq("statusdeligação", True)
                            .execute()
                        )
                        if not (resultado.data or []):
                            raise RuntimeError("Nenhuma ligação em andamento foi encontrada para este atendente.")

                        # A ordem contabiliza até tantos atendentes quantos
                        # estiverem com a automação aberta agora. Quando o
                        # número de confirmações bater com esse total, zera
                        # a tabela de ligações para a próxima ordem.
                        total_atendentes = _contar_atendentes_com_automacao_aberta()
                        confirmados = (
                            tabela_ligacoes.select("id", count="exact")
                            .eq("statusdeligação", False)
                            .execute()
                        )
                        qtd_confirmados = confirmados.count if confirmados.count is not None else len(confirmados.data or [])

                        ordem_zerada = False
                        if total_atendentes > 0 and qtd_confirmados >= total_atendentes:
                            tabela_ligacoes.delete().neq("id", 0).execute()
                            ordem_zerada = True

                        resposta = {
                            "status": "ok",
                            "em_ligacao": False,
                            "vai_para_ordem": True,
                            "contagem_ordem": qtd_confirmados,
                            "total_atendentes": total_atendentes,
                            "ordem_zerada": ordem_zerada,
                        }
            except Exception as e:
                print(f"❌ Erro ao registrar ligação: {e}")
                resposta = {"status": "erro", "mensagem": str(e)}
            self._enviar_json(resposta)

        # ── EXECUÇÃO REMOTA DO PLAYWRIGHT ───────────────────────────────────
        elif SERVER_MODE and self.path in ("/playwright/checkin", "/playwright/checkout", "/playwright/checkin-checkout"):
            try:
                if self.headers.get("X-Playwright-Token", "") != PLAYWRIGHT_SERVER_TOKEN:
                    self.send_response(401)
                    self.send_header("Content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "erro", "mensagem": "Token inválido."}).encode())
                    return

                content_length = int(self.headers.get('Content-Length', 0) or 0)
                dados = json.loads(self.rfile.read(content_length) or b"{}")
                task_id = int(dados.get("task_id"))
                nome_atendente = str(dados.get("nome_atendente") or "").strip()
                if not nome_atendente:
                    raise RuntimeError("O job não informou o atendente do Auvo.")

                # A senha nunca trafega entre o PC e o servidor: o servidor
                # resolve a credencial diretamente no Supabase.
                credencial = _obter_usuario_auvo(_supabase_client, nome_atendente)
                remote_status_keys = [str(k) for k in (dados.get("remote_status_keys") or []) if k]
                remote_cancel_key = dados.get("remote_cancel_key") or dados.get("cancel_key")

                if self.path == "/playwright/checkin":
                    if len(remote_status_keys) != 1:
                        raise RuntimeError("Job de check-in sem chave remota de status válida.")
                    status_key = remote_status_keys[0]
                    limpar_cancelamento(status_key)
                    if remote_cancel_key:
                        limpar_cancelamento(remote_cancel_key)
                    task_status[status_key] = "aguardando_servidor"
                    args = (task_id, dados.get("data_checkin"), dados.get("hora_checkin"), dados.get("inicio"), remote_cancel_key)
                    func = realizar_checkin_ui_invisivel
                    status_keys = (status_key,)
                elif self.path == "/playwright/checkout":
                    if len(remote_status_keys) != 1:
                        raise RuntimeError("Job de checkout sem chave remota de status válida.")
                    status_key = remote_status_keys[0]
                    limpar_cancelamento(status_key)
                    if remote_cancel_key:
                        limpar_cancelamento(remote_cancel_key)
                    task_status[status_key] = "aguardando_servidor"
                    args = (
                        task_id, dados.get("data_checkout"), dados.get("hora_checkout"),
                        dados.get("relato", ""), remote_cancel_key, dados.get("contexto_remoto")
                    )
                    if dados.get("contexto_remoto"):
                        task_status[f"cobranca_sem_contrato_checkout_{task_id}"] = dados["contexto_remoto"]
                    func = realizar_checkout_ui_invisivel
                    status_keys = (status_key,)
                else:
                    if len(remote_status_keys) != 2:
                        raise RuntimeError("Job combinado sem as duas chaves remotas de status.")
                    checkin_key, checkout_key = remote_status_keys
                    for key in (checkin_key, checkout_key):
                        limpar_cancelamento(key)
                        task_status[key] = "aguardando_servidor"
                    if remote_cancel_key:
                        limpar_cancelamento(remote_cancel_key)
                    status_key = checkin_key
                    args = (
                        task_id, dados.get("data_checkin"), dados.get("hora_checkin"),
                        dados.get("data_checkout"), dados.get("hora_checkout"),
                        dados.get("relato", ""), remote_cancel_key
                    )
                    func = realizar_checkin_e_checkout_ui_invisivel
                    status_keys = (checkin_key, checkout_key)

                threading.Thread(
                    target=_executar_job_com_lock,
                    args=(func, args, credencial, status_keys),
                    daemon=True,
                ).start()
                self._enviar_json({
                    "status": "ok",
                    "status_key": status_key,
                    "status_keys": list(status_keys),
                    "job_id": dados.get("job_id"),
                    "atendente": credencial.get("nome"),
                    "estado_inicial": "aguardando_servidor",
                })
            except Exception as e:
                print(f"❌ Erro ao aceitar job Playwright: {e}")
                self._enviar_json({"status": "erro", "mensagem": str(e)})
            return

        # ── CANCELAMENTO ────────────────────────────────────────────────────
        elif self.path == "/cancel":
            try:
                content_length = int(self.headers.get('Content-Length', 0) or 0)
                dados = json.loads(self.rfile.read(content_length) or b"{}")
                chave = dados.get("chave") or dados.get("key") or dados.get("task_id")
                if not chave:
                    raise Exception("Chave de processo inválida para cancelamento.")
                if (not SERVER_MODE) and _chave_remota(chave):
                    _cancelar_remoto(chave)
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
                        args=(
                            task_id, data_checkout, hora_checkout, relato, cancel_key,
                            task_status.get(f"cobranca_sem_contrato_checkout_{task_id}")
                        )
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
        elif self.path == "/auvo-agendamento-externo":
            try:
                content_length = int(self.headers.get("Content-Length", 0) or 0)
                dados = json.loads(self.rfile.read(content_length) or b"{}")
                empresa = str(dados.get("empresa") or "").strip()
                codigo_cliente = dados.get("codigo_cliente")
                codigo_cliente, opcoes_empresas = resolver_cliente_para_requisicao(empresa, codigo_cliente)
                if not codigo_cliente:
                    if opcoes_empresas:
                        self._enviar_json({"status": "multiplas_empresas", "empresas": opcoes_empresas, "empresa": empresa})
                        return
                    raise RuntimeError(f"Cliente '{empresa}' não encontrado no cadastro do Supabase.")
                mensagens = pegar_mensagens_chat()
                if not mensagens:
                    raise RuntimeError("Não consegui copiar as mensagens da conversa. Deixe o chat aberto e tente novamente.")
                task_type = int(dados.get("tipo_atendimento") or 0)
                tipo = AGENDAMENTO_EXTERNO_TIPOS.get(task_type)
                if not tipo:
                    raise ValueError("Selecione um tipo de atendimento válido.")
                equipamento = str(dados.get("equipamento") or "").strip()
                token = autenticar()
                cliente_auvo = buscar_cliente_auvo(token, codigo_cliente)
                nome_cliente = str(cliente_auvo.get("name") or empresa).strip()
                contato_nome, contato_telefone = _extrair_contato_cliente_auvo(cliente_auvo)
                nome_pagina, telefone_pagina = pegar_contato_pagina_speakr()
                if telefone_pagina:
                    contato_telefone = _formatar_telefone_br(telefone_pagina)
                if nome_pagina:
                    contato_nome = nome_pagina
                sistema = str(dados.get("sistema") or "").strip()
                if contato_nome == "Não informado":
                    contato_nome = extrair_nome_cliente(mensagens, NOME_USER) or ""
                if not contato_nome:
                    nomes_atendentes = {NOME_USER.strip().casefold()}
                    nomes_atendentes.update(str(u.get("nome") or "").strip().casefold() for u in USUARIOS.values())
                    for mensagem in reversed(mensagens):
                        marcador = re.search(r"\d{1,2}[h:]\d{2}\s*[-–]\s*(.+?)\s+disse\s*:", str(mensagem), re.IGNORECASE)
                        if marcador:
                            nome_remetente = re.sub(r"\s+", " ", marcador.group(1)).strip()
                            remetente_norm = nome_remetente.casefold()
                            eh_atendente = any(nome and (remetente_norm == nome or remetente_norm.startswith(nome + " ")) for nome in nomes_atendentes)
                            if not eh_atendente and nome_remetente:
                                contato_nome = nome_remetente
                                break
                if not contato_nome:
                    print("ℹ️ Contato não achado pelos marcadores do chat. Amostra das linhas copiadas:")
                    for _linha in mensagens[:6]:
                        print("   |", str(_linha)[:140])
                    contato_nome = _identificar_contato_por_ia(mensagens)
                if not contato_nome:
                    contato_nome = "Contato não identificado — conferir no chat"
                print(f"📞 Contato: {contato_nome!r} / {contato_telefone!r} | mensagens do chat: {len(mensagens)}")
                descricao = _gerar_descricao_agendamento_externo(mensagens, task_type, sistema, contato_nome)
                # Se equipamento foi selecionado, posiciona após o sistema do cliente.
                if equipamento:
                    descricao = anexar_equipamento_apos_sistema(descricao, equipamento)
                print("📤 Enviando descrição para o popup do navegador.")
                job_id = uuid.uuid4().hex
                with AGENDAMENTOS_EXTERNOS_LOCK:
                    AGENDAMENTOS_EXTERNOS_PENDENTES[job_id] = {
                        "token": token, "codigo_cliente": int(codigo_cliente), "tipo": task_type,
                        "empresa": nome_cliente, "descricao": descricao, "contato_nome": contato_nome,
                        "contato_telefone": contato_telefone, "criado_em": time.time(),
                        "equipamento": equipamento,
                    }
                self._enviar_json({"status": "confirmar_descricao", "job_id": job_id, "empresa": nome_cliente,
                                   "tipo": tipo["nome"], "descricao": descricao, "contato_nome": contato_nome,
                                   "contato_telefone": contato_telefone, "equipamento": equipamento})
            except Exception as e:
                print(f"Erro ao preparar agendamento externo: {e}")
                self._enviar_json({"status": "erro", "mensagem": str(e)})
            return
        elif self.path == "/auvo-agendamento-externo-confirmar":
            try:
                content_length = int(self.headers.get("Content-Length", 0) or 0)
                dados = json.loads(self.rfile.read(content_length) or b"{}")
                job_id = str(dados.get("job_id") or "")
                descricao = str(dados.get("descricao") or "").strip()
                if not descricao:
                    raise ValueError("A descrição não pode ficar vazia.")
                with AGENDAMENTOS_EXTERNOS_LOCK:
                    pendente = AGENDAMENTOS_EXTERNOS_PENDENTES.get(job_id)
                    if pendente and time.time() - pendente["criado_em"] > 900:
                        AGENDAMENTOS_EXTERNOS_PENDENTES.pop(job_id, None)
                        pendente = None
                    if not pendente:
                        raise RuntimeError("Esta confirmação expirou. Inicie o agendamento novamente.")
                task_id = criar_tarefa_agendamento_externo(pendente["token"], pendente["codigo_cliente"], pendente["tipo"], descricao)
                equipamento_selecionado = pendente.get("equipamento", "")
                texto_copiado = (
                    f"Cliente : {pendente['empresa']}" + chr(10) +
                    f"Contato: {pendente['contato_nome']} - {pendente['contato_telefone']}" + chr(10) +
                    f"Problema : {descricao}" + chr(10) +
                    "Data do agendamento : ########" + chr(10) +
                    (f"Equipamento: {equipamento_selecionado}" + chr(10) if equipamento_selecionado else "") +
                    "Local : " + chr(10) + "Responsavel : "
                )
                pyperclip.copy(texto_copiado)
                with AGENDAMENTOS_EXTERNOS_LOCK:
                    AGENDAMENTOS_EXTERNOS_PENDENTES.pop(job_id, None)
                _limpar_cache_tasks()
                self._enviar_json({"status": "ok", "task_id": task_id, "empresa": pendente["empresa"],
                                   "tipo": AGENDAMENTO_EXTERNO_TIPOS[pendente["tipo"]]["nome"],
                                   "texto_copiado": texto_copiado})
            except Exception as e:
                self._enviar_json({"status": "erro", "mensagem": str(e)})
            return
        elif self.path == "/auvo-agendamento-externo-cancelar":
            try:
                content_length = int(self.headers.get("Content-Length", 0) or 0)
                dados = json.loads(self.rfile.read(content_length) or b"{}")
                with AGENDAMENTOS_EXTERNOS_LOCK:
                    AGENDAMENTOS_EXTERNOS_PENDENTES.pop(str(dados.get("job_id") or ""), None)
                self._enviar_json({"status": "ok"})
            except Exception as e:
                self._enviar_json({"status": "erro", "mensagem": str(e)})
            return
        elif self.path == "/auvo-equipamento-problema":
            try:
                content_length = int(self.headers.get("Content-Length", 0) or 0)
                dados = json.loads(self.rfile.read(content_length) or b"{}")
                equipamento = str(dados.get("equipamento") or "").strip()
                empresa = str(dados.get("empresa") or "").strip()
                codigo_cliente = dados.get("codigo_cliente")
                mensagens_chat_id = dados.get("mensagens_chat_id")

                if not equipamento:
                    raise ValueError("Equipamento não informado.")
                if not empresa:
                    raise ValueError("Não consegui identificar a empresa desta conversa. Abra o atendimento do cliente e tente novamente.")
                if empresa.casefold() in {"desconhecida", "unknown", "empresa não identificada"}:
                    raise ValueError("A empresa desta conversa não foi identificada. Atualize a conversa e tente novamente.")

                # Resolve o cliente
                codigo_cliente, opcoes_empresas = resolver_cliente_para_requisicao(empresa, codigo_cliente)
                if not codigo_cliente:
                    if opcoes_empresas:
                        self._enviar_json({"status": "multiplas_empresas", "empresas": opcoes_empresas, "empresa": empresa})
                        return
                    raise RuntimeError(f"Cliente '{empresa}' não encontrado no cadastro do Supabase.")

                token = autenticar()

                # Busca tasks recentes do cliente para encontrar a task ativa
                tasks = _buscar_entity_list_tasks(token, codigo_cliente, dias=1, cache_ttl=0)
                task_id = None
                for t in tasks:
                    status = _status_task_auvo(t)
                    if status in ("em_andamento", "a_caminho", "pendente"):
                        task_id = t.get("id") or t.get("taskID") or t.get("taskNumber")
                        if task_id:
                            break

                if not task_id:
                    # Se não achou task ativa, tenta buscar a task criada mais recentemente
                    tasks_todas = _buscar_entity_list_tasks(token, codigo_cliente, dias=7, cache_ttl=0)
                    if tasks_todas:
                        task_id = tasks_todas[0].get("id") or tasks_todas[0].get("taskID") or tasks_todas[0].get("taskNumber")

                if not task_id:
                    raise RuntimeError("Nenhuma task encontrada para este cliente. Crie um atendimento primeiro.")

                # Busca a descrição atual da task
                task_detalhes = None
                for t in tasks_todas if 'tasks_todas' in locals() else _buscar_entity_list_tasks(token, codigo_cliente, dias=7, cache_ttl=0):
                    tid = t.get("id") or t.get("taskID") or t.get("taskNumber")
                    if str(tid) == str(task_id):
                        task_detalhes = t
                        break

                descricao_atual = ""
                if task_detalhes:
                    descricao_atual = str(task_detalhes.get("orientation") or task_detalhes.get("description") or "").strip()

                # Mantém o relato primeiro e posiciona o equipamento após a informação do sistema.
                nova_descricao = anexar_equipamento_apos_sistema(descricao_atual, equipamento)

                # Atualiza a task no Auvo
                # A API Auvo edita tasks via PATCH com um JSON Patch Document.
                payload = [{"op": "replace", "path": "orientation", "value": nova_descricao}]
                response = HTTP_SESSION.patch(
                    f"https://api.auvo.com.br/v2/tasks/{int(task_id)}",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Connection": "close"},
                    timeout=AUVO_CREATE_TIMEOUT,
                )
                if response.status_code in (500, 502, 503, 504):
                    raise Exception(f"HTTP {response.status_code}: Auvo instável ao atualizar task.")
                if response.status_code not in (200, 204):
                    raise RuntimeError(f"HTTP {response.status_code} ao atualizar task no Auvo: {response.text[:300]}")

                _limpar_cache_tasks()
                self._enviar_json({"status": "ok", "task_id": int(task_id), "descricao": nova_descricao, "equipamento": equipamento})
            except Exception as e:
                print(f"Erro ao atualizar equipamento com defeito: {e}")
                self._enviar_json({"status": "erro", "mensagem": str(e)})
            return
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
                if segmento.get("status") == "cliente_sem_contrato" and _dentro_prazo_15_dias_uteis(empresa, data_chat):
                    segmento = {"ok": True, "status": "sem_contrato_dentro_prazo"}
                if not segmento.get("ok"):
                    resposta_sem_segmento = resposta_cliente_sem_segmento(empresa, codigo_cliente, segmento.get("status"))
                    if segmento.get("status") == "cliente_sem_contrato":
                        resposta_sem_segmento["mensagem"] = "Cliente sem nenhum contrato. Deseja continuar o atendimento?"
                    self._enviar_json(resposta_sem_segmento)
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

                if segmento.get("status") in ("continuar_sem_contrato", "sem_contrato_dentro_prazo"):
                    task_status[f"cliente_sem_contrato_task_{task_id}"] = {"cliente": empresa, "codigo_cliente": codigo_cliente}
                    _salvar_tasks_sem_contrato_ativas()

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

                confirmou_task_checkout = bool(dados.get("confirmar_task_checkout"))

                mensagens      = pegar_mensagens_chat()
                verificar_cancelamento(cancel_key)
                hora_checkout, data_checkout = pegar_hora_e_data_ultima_mensagem(mensagens)
                if not hora_checkout:
                    raise Exception("Hora de checkout não encontrada no chat.")

                relato_resultado = [None]

                def gerar_relato():
                    print("🤖 Gerando relato detalhado com IA...")
                    _, relato_resultado[0] = gerar_descricao_e_relato_com_ia(mensagens, dados.get("sistema"))
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
                for task in tasks:
                    task["sem_contrato"] = bool(task_status.get(f"cliente_sem_contrato_task_{task.get('taskID')}"))

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

                if not task_id_escolhido:
                    if len(tasks) > 1:
                        self._enviar_json({
                            "status":         "multiplas_tasks",
                            "tasks":          tasks,
                            "hora":           hora_checkout,
                            "codigo_cliente": codigo_cliente,
                            "empresa":        empresa,
                                            })
                        return

                    self._enviar_json({
                        "status":         "confirmar_task_checkout",
                        "task":           tasks[0],
                        "task_id":        tasks[0]["taskID"],
                        "sem_contrato":   bool(tasks[0].get("sem_contrato")),
                        "hora":           hora_checkout,
                        "data":           data_checkout,
                        "codigo_cliente": codigo_cliente,
                        "empresa":        empresa,
                                    })
                    return

                if task_id_escolhido:
                    tarefa_escolhida = next((t for t in tasks if int(t.get("taskID", 0)) == int(task_id_escolhido)), None)
                    eh_sem_contrato = bool(tarefa_escolhida and tarefa_escolhida.get("sem_contrato"))
                    if eh_sem_contrato and not dados.get("confirmar_cobranca_sem_contrato"):
                        self._enviar_json({"status": "confirmar_cobranca_sem_contrato", "codigo_cliente": codigo_cliente, "empresa": empresa, "task_id": int(task_id_escolhido)})
                        return
                    if eh_sem_contrato and dados.get("confirmar_cobranca_sem_contrato"):
                        task_status[f"cobranca_sem_contrato_checkout_{int(task_id_escolhido)}"] = {"cliente": empresa, "data_checkout": data_checkout}

                if task_id_escolhido and not confirmou_task_checkout:
                    raise Exception("Confirme se deseja lançar o check-out nesta task ou criar um fluxo novo.")

                # ── Task escolhida e confirmada pelo usuário ──────────────────
                # Só gera o relato depois que o usuário decidiu usar uma task
                # existente. Se ele optar pelo fluxo novo, este endpoint não
                # consome tokens; o fluxo combinado gera os textos uma vez só.
                gerar_relato()
                task_status[f"checkout_contexto_{codigo_cliente}"] = {
                    "mensagens": mensagens,
                    "relato": relato_resultado[0],
                    "hora_checkout": hora_checkout,
                    "data_checkout": data_checkout,
                    "empresa": empresa,
                    "sistema": dados.get("sistema"),
                }
                if task_id_escolhido:
                    task_id = int(task_id_escolhido)
                    relato = relato_resultado[0]
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
                if inicio_escolhido and inicio_escolhido.get("data"):
                    data_inicio_sem_contrato = inicio_escolhido.get("data")
                else:
                    _, data_inicio_sem_contrato, _ = encontrar_inicio_atendimento(mensagens)
                if segmento.get("status") == "cliente_sem_contrato" and _dentro_prazo_15_dias_uteis(empresa, data_inicio_sem_contrato):
                    segmento = {"ok": True, "status": "sem_contrato_dentro_prazo"}
                if not segmento.get("ok"):
                    resposta_sem_segmento = resposta_cliente_sem_segmento(empresa, codigo_cliente, segmento.get("status"))
                    if segmento.get("status") == "cliente_sem_contrato":
                        resposta_sem_segmento["mensagem"] = "Cliente sem nenhum contrato. Deseja continuar o atendimento?"
                    self._enviar_json(resposta_sem_segmento)
                    return

                cliente_sem_contrato = segmento.get("status") in ("continuar_sem_contrato", "sem_contrato_dentro_prazo")
                if cliente_sem_contrato and not dados.get("confirmar_cobranca_sem_contrato"):
                    self._enviar_json({"status": "confirmar_cobranca_sem_contrato_fluxo", "codigo_cliente": codigo_cliente, "empresa": empresa})
                    return
                chave     = f"full_flow_{codigo_cliente}"
                limpar_cancelamento(chave)
                task_status[chave] = "iniciando"

                threading.Thread(
                    target=fluxo_checkin_e_checkout,
                    args=(empresa, mensagens, codigo_cliente, inicio_escolhido, sistema_cliente, cancel_key, bool(cliente_sem_contrato))
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
    if SERVER_MODE:
        bind = os.getenv("PLAYWRIGHT_SERVER_BIND", "0.0.0.0")
        print(f"🌍 Servidor Playwright multiusuário rodando em http://{PLAYWRIGHT_SERVER_HOST}:{PLAYWRIGHT_SERVER_PORT}")
        ThreadingHTTPServer((bind, PLAYWRIGHT_SERVER_PORT), AuvoHandler).serve_forever()
    else:
        print(f"🌍 Servidor local rodando em http://localhost:8765")
        ThreadingHTTPServer(("localhost", 8765), AuvoHandler).serve_forever()

threading.Thread(target=iniciar_servidor, daemon=True).start()


# ---------------------------------------------------------------------------
# Injeção do botão no Speakr
# ---------------------------------------------------------------------------
SCRIPT_BOTAO = r"""
(function() {
    // ── Notificação flutuante ─────────────────────────────────────────────
    try {
        (window.auvoNotificacoesNativas || []).forEach(notificacao => {
            try { notificacao.close(); } catch (e) {}
        });
        window.auvoNotificacoesNativas = [];
    } catch (e) {}
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
            #auvo-notif { top: 18px !important; bottom: auto !important; left: 50% !important; right: auto !important; transform: translateX(-50%) !important; }
            #auvo-notif::before { display: none !important; content: none !important; }
        `;

        function medirAreaBotoesAuvo() {
            const ids = ['botao-auvo', 'botao-auvo-checkout', 'botao-auvo-agendamentos', 'botao-auvo-pausa', 'botao-auvo-despausa', 'botao-auvo-ligacao', 'botao-auvo-equipamento-problema'];
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
    function mostrarModalTipoAgendamento(botao, empresa) {
        const anterior = document.getElementById('auvo-modal-tipo-agendamento');
        if (anterior) anterior.remove();
        const modal = document.createElement('div');
        modal.id = 'auvo-modal-tipo-agendamento';
        Object.assign(modal.style, {
            position: 'fixed', right: '24px', bottom: '24px', zIndex: '2147483647',
            width: 'min(370px, calc(100vw - 32px))', boxSizing: 'border-box',
            background: '#202a35', color: '#fff', border: '1px solid rgba(255,255,255,.14)',
            borderRadius: '12px', padding: '16px', boxShadow: '0 12px 36px rgba(0,0,0,.45)',
            font: '13px/1.45 Arial,sans-serif',
        });
        const titulo = document.createElement('strong');
        titulo.textContent = 'Agendamentos — escolha o tipo de atendimento';
        titulo.style.cssText = 'display:block;font-size:15px;margin-bottom:5px;';
        modal.appendChild(titulo);
        const ajuda = document.createElement('div');
        ajuda.textContent = `${empresa} · após escolher, aguarde a cópia do chat e a criação da tarefa.`;
        ajuda.style.cssText = 'opacity:.78;margin-bottom:12px;';
        modal.appendChild(ajuda);
        [
            { id: 205136, nome: 'Visita Técnica', cor: '#2980b9' },
            { id: 200376, nome: 'Instalação', cor: '#16a085' },
            { id: 205918, nome: 'Treinamento Presencial', cor: '#8e44ad' },
        ].forEach(tipo => {
            const opcao = document.createElement('button');
            opcao.type = 'button';
            opcao.textContent = tipo.nome;
            Object.assign(opcao.style, {
                display: 'block', width: '100%', margin: '6px 0', padding: '9px 12px',
                border: '0', borderRadius: '7px', color: 'white', background: tipo.cor,
                cursor: 'pointer', fontWeight: '700', textAlign: 'left',
            });
            opcao.onclick = () => {
                modal.remove();
                // Após escolher o tipo, mostra o modal de equipamento
                mostrarModalEquipamentoAgendamento(botao, empresa, tipo.id, tipo.nome);
            };
            modal.appendChild(opcao);
        });
        const cancelar = document.createElement('button');
        cancelar.type = 'button';
        cancelar.textContent = 'Cancelar';
        Object.assign(cancelar.style, {
            display: 'block', width: '100%', marginTop: '10px', padding: '8px',
            border: '0', borderRadius: '7px', background: 'rgba(255,255,255,.12)',
            color: 'white', cursor: 'pointer',
        });
        cancelar.onclick = () => {
            modal.remove();
            setBotao(botao, 'Agendamentos', '#3498db', false);
            limparContextoPill(botao);
        };
        modal.appendChild(cancelar);
        document.body.appendChild(modal);
    }

    function mostrarModalEquipamentoAgendamento(botao, empresa, tipoId, tipoNome) {
        const anterior = document.getElementById('auvo-modal-equipamento-agendamento');
        if (anterior) anterior.remove();
        const modal = document.createElement('div');
        modal.id = 'auvo-modal-equipamento-agendamento';
        Object.assign(modal.style, {
            position: 'fixed', right: '24px', bottom: '24px', zIndex: '2147483647',
            width: 'min(370px, calc(100vw - 32px))', boxSizing: 'border-box',
            background: '#202a35', color: '#fff', border: '1px solid rgba(255,255,255,.14)',
            borderRadius: '12px', padding: '16px', boxShadow: '0 12px 36px rgba(0,0,0,.45)',
            font: '13px/1.45 Arial,sans-serif',
        });
        const titulo = document.createElement('strong');
        titulo.textContent = `${tipoNome} — escolha o equipamento`;
        titulo.style.cssText = 'display:block;font-size:15px;margin-bottom:5px;';
        modal.appendChild(titulo);
        const ajuda = document.createElement('div');
        ajuda.textContent = `${empresa} · o equipamento será adicionado à descrição e ao resumo copiado.`;
        ajuda.style.cssText = 'opacity:.78;margin-bottom:12px;';
        modal.appendChild(ajuda);
        const equipamentos = [
            "IDFACE",
            "IDFACE MAX",
            "EVO 40",
            "TOPDATA F4",
            "REP ID CLASS",
            "TopData Inner",
            "Kurumim",
        ];
        equipamentos.forEach((equip, idx) => {
            const opcao = document.createElement('button');
            opcao.type = 'button';
            opcao.textContent = equip;
            Object.assign(opcao.style, {
                display: 'block', width: '100%', margin: '5px 0', padding: '10px 12px',
                border: '0', borderRadius: '7px', color: 'white', background: '#2980b9',
                cursor: 'pointer', fontWeight: '700', textAlign: 'left',
            });
            opcao.onclick = () => {
                modal.remove();
                iniciarAgendamentoExterno(botao, empresa, null, tipoId, null, obterSistemaAtual(obterReferenciaAtendimentoAtual()), equip);
            };
            modal.appendChild(opcao);
        });
        const cancelar = document.createElement('button');
        cancelar.type = 'button';
        cancelar.textContent = 'Cancelar';
        Object.assign(cancelar.style, {
            display: 'block', width: '100%', marginTop: '10px', padding: '8px',
            border: '0', borderRadius: '7px', background: 'rgba(255,255,255,.12)',
            color: 'white', cursor: 'pointer',
        });
        cancelar.onclick = () => {
            modal.remove();
            setBotao(botao, 'Agendamentos', '#3498db', false);
            limparContextoPill(botao);
        };
        modal.appendChild(cancelar);
        document.body.appendChild(modal);
    }
    function mostrarModalEquipamentoProblema(botao, empresa) {
        const anterior = document.getElementById('auvo-modal-equipamento-problema');
        if (anterior) anterior.remove();
        const modal = document.createElement('div');
        modal.id = 'auvo-modal-equipamento-problema';
        Object.assign(modal.style, {
            position: 'fixed', right: '24px', bottom: '24px', zIndex: '2147483647',
            width: 'min(370px, calc(100vw - 32px))', boxSizing: 'border-box',
            background: '#202a35', color: '#fff', border: '1px solid rgba(255,255,255,.14)',
            borderRadius: '12px', padding: '16px', boxShadow: '0 12px 36px rgba(0,0,0,.45)',
            font: '13px/1.45 Arial,sans-serif',
        });
        const titulo = document.createElement('strong');
        titulo.textContent = 'Problema no Equipamento — escolha o equipamento';
        titulo.style.cssText = 'display:block;font-size:15px;margin-bottom:5px;';
        modal.appendChild(titulo);
        const ajuda = document.createElement('div');
        ajuda.textContent = `${empresa} · o equipamento será adicionado à descrição do atendimento.`;
        ajuda.style.cssText = 'opacity:.78;margin-bottom:12px;';
        modal.appendChild(ajuda);
        const equipamentos = [
            "IDFACE",
            "IDFACE MAX",
            "EVO 40",
            "TOPDATA F4",
            "REP ID CLASS",
            "TopData Inner",
            "Kurumim",
        ];
        equipamentos.forEach((equip, idx) => {
            const opcao = document.createElement('button');
            opcao.type = 'button';
            opcao.textContent = equip;
            Object.assign(opcao.style, {
                display: 'block', width: '100%', margin: '5px 0', padding: '10px 12px',
                border: '0', borderRadius: '7px', color: 'white', background: '#e67e22',
                cursor: 'pointer', fontWeight: '700', textAlign: 'left',
            });
            opcao.onclick = () => {
                modal.remove();
                botao.disabled = true;
                botao.style.opacity = '.65';
                botao.style.cursor = 'wait';
                const textoEmpresa = obterDadosEmpresaAtual();
                mostrarNotificacao(`Adicionando o leitor "${equip}" na descrição do atendimento... aguarde.`, 'info');
                fetch('http://localhost:8765/auvo-equipamento-problema', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ equipamento: equip, empresa: textoEmpresa }),
                })
                .then(r => r.json())
                .then(data => {
                    botao.disabled = false;
                    botao.style.opacity = '1';
                    botao.style.cursor = 'pointer';
                    if (data.status === 'ok') {
                        mostrarNotificacao(`Equipamento "${equip}" adicionado à task #${data.task_id}.`, 'sucesso');
                    } else if (data.status === 'multiplas_empresas') {
                        mostrarSeletorEmpresas(
                            data.empresas,
                            textoEmpresa,
                            botao,
                            empresaEscolhida => {
                                mostrarNotificacao(`Adicionando o leitor "${equip}" na descrição do atendimento... aguarde.`, 'info');
                                fetch('http://localhost:8765/auvo-equipamento-problema', {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({ equipamento: equip, empresa: empresaEscolhida.nome, codigo_cliente: empresaEscolhida.codigo }),
                                })
                                .then(r => r.json())
                                .then(d => {
                                    if (d.status === 'ok') {
                                        mostrarNotificacao(`Equipamento "${equip}" adicionado à task #${d.task_id}.`, 'sucesso');
                                    } else {
                                        mostrarNotificacao(`Erro: ${d.mensagem}`, 'erro');
                                    }
                                });
                            },
                            () => {}
                        );
                    } else {
                        mostrarNotificacao(`Erro: ${data.mensagem}`, 'erro');
                    }
                })
                .catch(err => {
                    botao.disabled = false;
                    botao.style.opacity = '1';
                    botao.style.cursor = 'pointer';
                    mostrarNotificacao(`Erro ao registrar equipamento: ${err.message}`, 'erro');
                });
            };
            modal.appendChild(opcao);
        });
        const cancelar = document.createElement('button');
        cancelar.type = 'button';
        cancelar.textContent = 'Cancelar';
        Object.assign(cancelar.style, {
            display: 'block', width: '100%', marginTop: '10px', padding: '8px',
            border: '0', borderRadius: '7px', background: 'rgba(255,255,255,.12)',
            color: 'white', cursor: 'pointer',
        });
        cancelar.onclick = () => {
            modal.remove();
        };
        modal.appendChild(cancelar);
        document.body.appendChild(modal);
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
        notif.id = 'auvo-popup-textos-fluxo';
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
        notif.id = 'auvo-popup-relato-checkout';
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
                enviarConfirmacaoRelato(relatoEditado);
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
                mostrarNotificacao(s.erro_registro_supabase ? `Check-out salvo, mas não foi possível atualizar a tabela de cobrança: ${s.erro_registro_supabase}` : `Check-out concluído com sucesso${dados.hora ? ` às ${dados.hora}` : ''}.`, s.erro_registro_supabase ? 'erro' : 'sucesso');
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
                const usarTaskSelecionada = (cobrancaConfirmada = false) => {
                    if (t.sem_contrato && !cobrancaConfirmada) {
                        confirmarCobrancaSemContrato(
                            () => usarTaskSelecionada(true),
                            () => { mostrarNotificacao('Check-out cancelado. Confirme a cobrança antes de finalizar este atendimento.', 'info'); resetBotaoCheckout(botaoOut); }
                        );
                        return;
                    }const cancelKeyCheckoutSelecionadoInicial = criarChaveCancelamento('checkout');
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
                            confirmar_cobranca_sem_contrato: cobrancaConfirmada,
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
        calendar: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01"/></svg>',
        pause: '<svg class="auvo-pause-neon" viewBox="0 0 24 24" aria-hidden="true"><rect x="4.7" y="3.4" width="5.8" height="17.2" rx="2.9"/><rect x="13.5" y="3.4" width="5.8" height="17.2" rx="2.9"/></svg>',
        play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7Z"/></svg>',
        check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>',
        error: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>',
        warning: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2 20h20Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>',
        lock: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>',
        phone: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.12.9.33 1.78.62 2.64a2 2 0 0 1-.45 2.11L8.01 9.74a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.86.29 1.74.5 2.64.62A2 2 0 0 1 22 16.92Z"/><path d="M15.05 5A5 5 0 0 1 19 8.95M15.05 1A9 9 0 0 1 23 8.94"/></svg>',
        phoneActive: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.12.9.33 1.78.62 2.64a2 2 0 0 1-.45 2.11L8.01 9.74a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.86.29 1.74.5 2.64.62A2 2 0 0 1 22 16.92Z"/><path d="M15.05 5A5 5 0 0 1 19 8.95M15.05 1A9 9 0 0 1 23 8.94"/></svg>',
        tools: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13.5 18H4.5A2.5 2.5 0 0 1 2 15.5v-10A2.5 2.5 0 0 1 4.5 3h12A2.5 2.5 0 0 1 19 5.5V12"/><path d="M12.5 3v15"/><circle cx="7.2" cy="9" r="1.7"/><path d="M4.2 14.8c.4-1.7 1.6-2.6 3-2.6s2.6.9 3 2.6"/><path d="M15.7 7h.01M15.7 10.5h.01"/><path d="M18 12.8 22.6 21.4H13.4Z"/><path d="M18 16.1v2.3M18 20h.01"/></svg>'
    };

    function garantirEstiloBotoesAuvo() {
        if (document.getElementById('auvo-botoes-style')) return;
        const style = document.createElement('style');
        style.id = 'auvo-botoes-style';
        style.textContent = `
            #auvo-automation-pill {
                position: fixed; right: 24px; bottom: 92px; z-index: 2147483000;
                display: flex; align-items: center; gap: 4px; padding: 4px 6px;
                border: 1px solid rgba(255,255,255,.12); border-radius: 999px;
                background: rgba(45,45,49,.97); box-shadow: 0 10px 24px rgba(0,0,0,.42);
                backdrop-filter: blur(8px); pointer-events: auto;
            }
            #auvo-automation-pill > button {
                position: relative; width: 30px !important; height: 30px !important;
                min-width: 30px !important; padding: 0 !important; flex: 0 0 30px;
                display: inline-flex; align-items: center; justify-content: center;
                border: 0 !important; border-radius: 50% !important;
                background: #38383d !important; box-shadow: none !important;
                transition: filter .15s ease, transform .15s ease;
            }
            #auvo-automation-pill > button:hover { filter: brightness(1.18); transform: translateY(-1px); }
            #auvo-automation-pill > button:active { transform: scale(.94); }
            #auvo-automation-pill > button::after {
                content: attr(title); position: absolute; bottom: calc(100% + 9px); left: 50%;
                transform: translateX(-50%); padding: 6px 8px; border-radius: 7px;
                background: #171717; color: #f2f2f2; font: 11px/1.2 Arial,sans-serif;
                white-space: nowrap; opacity: 0; pointer-events: none;
                box-shadow: 0 6px 16px rgba(0,0,0,.35); transition: opacity .12s ease;
            }
            #auvo-automation-pill > button:hover::after { opacity: 1; }
            @media (max-width: 600px) {
                #auvo-automation-pill { right: 12px; bottom: 78px; gap: 3px; }
                #auvo-automation-pill > button { width: 28px !important; height: 28px !important; min-width: 28px !important; flex-basis: 28px; }
            }
            #auvo-pill-context { display:flex; flex-direction:column; justify-content:center; gap:3px; min-width:105px; max-width:150px; padding:2px 8px 2px 4px; border-right:1px solid rgba(255,255,255,.18); cursor:grab; touch-action:none; user-select:none; line-height:1.15; }
            #auvo-pill-context:active { cursor:grabbing; }
            #auvo-pill-company { color:#fff; font:700 10px/1.2 Arial,sans-serif; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
            #auvo-pill-process { color:#b7b7c0; font:9px/1.2 Arial,sans-serif; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
            #auvo-automation-pill.auvo-pill-dragging { transition:none !important; }
            @media (max-width:600px) { #auvo-pill-context { min-width:88px; max-width:115px; padding-right:6px; } }
            #botao-auvo .auvo-icon, #botao-auvo-checkout .auvo-icon, #botao-auvo-agendamentos .auvo-icon, #botao-auvo-pausa .auvo-icon, #botao-auvo-despausa .auvo-icon, #botao-auvo-ligacao .auvo-icon, #botao-auvo-equipamento-problema .auvo-icon {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 22px;
                height: 22px;
            }
            #botao-auvo .auvo-icon svg, #botao-auvo-checkout .auvo-icon svg, #botao-auvo-agendamentos .auvo-icon svg, #botao-auvo-pausa .auvo-icon svg, #botao-auvo-despausa .auvo-icon svg, #botao-auvo-ligacao .auvo-icon svg, #botao-auvo-equipamento-problema .auvo-icon svg {
                width: 22px;
                height: 22px;
                stroke: currentColor;
                stroke-width: 2;
                stroke-linecap: round;
                stroke-linejoin: round;
                fill: none;
            }
            #botao-auvo-ligacao .auvo-icon svg, #botao-auvo-equipamento-problema .auvo-icon svg {
                stroke-width: 1.7;
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
            #botao-auvo .auvo-progress, #botao-auvo-checkout .auvo-progress, #botao-auvo-agendamentos .auvo-progress, #botao-auvo-pausa .auvo-progress, #botao-auvo-despausa .auvo-progress {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 34px;
                height: 34px;
                border-radius: 50%;
            }
            #botao-auvo .auvo-progress-inner, #botao-auvo-checkout .auvo-progress-inner, #botao-auvo-agendamentos .auvo-progress-inner, #botao-auvo-pausa .auvo-progress-inner, #botao-auvo-despausa .auvo-progress-inner {
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
        if (botao && botao.id === 'botao-auvo-agendamentos') return 'agendamento';
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
        const label = texto || (tipo === 'checkout' ? 'Check-out' : tipo === 'agendamento' ? 'Agendamentos' : tipo === 'pausa' ? 'Iniciar pausa' : tipo === 'despausa' ? 'Fim da pausa' : 'Lançar atendimento');
        const normalIcon = tipo === 'checkout' ? AUVO_ICONS.checkout : tipo === 'agendamento' ? AUVO_ICONS.calendar : tipo === 'pausa' ? AUVO_ICONS.pause : tipo === 'despausa' ? AUVO_ICONS.play : AUVO_ICONS.plane;
        const lower = label.toLowerCase();
        if (window.auvoPillButton === botao) {
            const processoPill = document.getElementById('auvo-pill-process');
            if (processoPill) processoPill.textContent = label;
        }

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
        const agendamento = document.getElementById('botao-auvo-agendamentos');
        window.auvoOperacaoAtiva = tipo;

        const bloqueados = [
            [checkin, 'Check-in'],
            [checkout, 'Check-out'],
            [pausa, 'Pausa'],
            [despausa, 'Despausar'],
            [agendamento, 'Agendamentos'],
        ].filter(([btn]) => btn && tipoBotao(btn) !== tipo);
        [checkin, checkout, pausa, despausa, agendamento].forEach(btn => btn && btn.blur());

        bloqueados.forEach(([btn, nome]) => {
            const aguardando = tipo === 'checkin' ? 'o Check-in' : tipo === 'checkout' ? 'o Check-out' : tipo === 'agendamento' ? 'o Agendamento' : 'a Pausa';
            setBotao(btn, `Aguarde ${aguardando}...`, '#7f8c8d', true);
            btn.title = `${nome} bloqueado enquanto a ação atual está em andamento.`;
        });    }

    function liberarAcoes() {
        window.auvoOperacaoAtiva = null;
        const checkin = botaoCheckin();
        const checkout = botaoCheckout();
        const pausa = botaoPausa();
        const despausa = botaoDespausa();
        const agendamento = document.getElementById('botao-auvo-agendamentos');
        if (checkin) setBotao(checkin, 'Lançar atendimento no Auvo', '#2ecc71', false);
        if (checkout) setBotao(checkout, 'Fazer check-out', '#e67e22', false);
        if (pausa) setBotao(pausa, 'Iniciar pausa', '#8e44ad', false);
        if (despausa) setBotao(despausa, 'Fim da pausa', '#16a085', false);
        if (agendamento) setBotao(agendamento, 'Agendamentos', '#3498db', false);
    }

    function limparContextoPill(botao) {
        if (botao && window.auvoPillButton !== botao) return;
        window.auvoPillButton = null;
        const empresa = document.getElementById('auvo-pill-company');
        const processo = document.getElementById('auvo-pill-process');
        if (empresa) empresa.textContent = 'Nenhuma empresa selecionada';
        if (processo) processo.textContent = 'Aguardando procedimento';
    }
    function resetBotaoCheckin(botao) {
        setTimeout(liberarAcoes, 5000);
        setTimeout(() => limparContextoPill(botao), 5000);
    }

    function resetBotaoCheckout(botao) {
        setTimeout(liberarAcoes, 5000);
        setTimeout(() => limparContextoPill(botao), 5000);
    }

    function resetBotaoPausa(botao) {
        setTimeout(liberarAcoes, 5000);
        setTimeout(() => limparContextoPill(botao), 5000);
    }

    function resetBotaoDespausa(botao) {
        setTimeout(liberarAcoes, 5000);
        setTimeout(() => limparContextoPill(botao), 5000);
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

    function confirmarCobrancaSemContrato(aoConfirmar, aoCancelar) {
        mostrarNotificacao(
            'Este atendimento foi continuado para um cliente sem contrato. A cobrança do cliente já foi feita?',
            'aviso',
            [
                { label: 'Sim, já foi cobrada', cor: '#27ae60', callback: aoConfirmar },
                { label: 'Não, cancelar check-out', cor: 'rgba(255,255,255,0.18)', callback: aoCancelar },
            ]
        );
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
                const usarTaskConfirmada = (cobrancaConfirmada = false) => {
                    if (data.sem_contrato && !cobrancaConfirmada) {
                        confirmarCobrancaSemContrato(
                            () => usarTaskConfirmada(true),
                            () => { mostrarNotificacao('Check-out cancelado. Confirme a cobrança antes de finalizar este atendimento.', 'info'); resetBotaoCheckout(botaoOut); }
                        );
                        return;
                    }const cancelKeyConfirmado = criarChaveCancelamento('checkout');
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
                            confirmar_cobranca_sem_contrato: cobrancaConfirmada,
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
            body: JSON.stringify({ empresa: textoEmpresa, codigo_cliente: codigoCliente, inicio_escolhido: inicioEscolhido || null, sistema: sistemaCliente || null, continuar_sem_contrato: !!opcoes.continuarSemContrato, continuar_sem_segmento: !!opcoes.continuarSemSegmento, confirmar_cobranca_sem_contrato: !!opcoes.confirmarCobrancaSemContrato, cancel_key: cancelKeyFluxoInicial }),
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
            if (data.status === 'confirmar_cobranca_sem_contrato_fluxo') {
                confirmarCobrancaSemContrato(
                    () => iniciarFluxoCompleto(botaoOut, textoEmpresa, data.codigo_cliente || codigoCliente, inicioEscolhido, sistemaCliente, { ...opcoes, confirmarCobrancaSemContrato: true }),
                    () => { mostrarNotificacao('Fluxo cancelado. A cobrança precisa estar confirmada para continuar.', 'info'); resetBotaoCheckout(botaoOut); }
                );
                return;
            }            if (data.status === 'cliente_sem_segmento' || data.status === 'cliente_sem_contrato') {
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
                    mostrarNotificacao(s.erro_registro_supabase ? `Atendimento concluído, mas não foi possível atualizar a tabela de cobrança: ${s.erro_registro_supabase}` : `Atendimento criado, check-in e check-out concluídos. Task #${tid}.`, s.erro_registro_supabase ? 'erro' : 'sucesso');
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
        const empresa = spanEmpresa && spanEmpresa.nextElementSibling
            ? spanEmpresa.nextElementSibling.innerText.trim() : '';
        return empresa && empresa.toLowerCase() !== 'desconhecida' ? empresa : '';
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
    function mostrarPopupDescricaoAgendamento(dados, botao) {
        const anterior = document.getElementById("auvo-popup-descricao-agendamento");
        if (anterior) anterior.remove();
        const popup = document.createElement("div");
        popup.id = "auvo-popup-descricao-agendamento";
        Object.assign(popup.style, { position: "fixed", right: "24px", bottom: "24px", zIndex: "2147483647", width: "min(560px, calc(100vw - 32px))", boxSizing: "border-box", background: "#2c3e50", color: "white", borderRadius: "10px", padding: "16px 20px", boxShadow: "0 6px 24px rgba(0,0,0,.35)", font: "14px/1.5 Arial,sans-serif" });
        const titulo = document.createElement("div");
        titulo.textContent = `${dados.tipo} para ${dados.empresa} · confira a descrição antes de lançar`;
        titulo.style.cssText = "font-weight:bold;margin-bottom:8px;font-size:15px";
        popup.appendChild(titulo);
        const contato = document.createElement("div");
        contato.textContent = `Contato: ${dados.contato_nome || "Não informado"} - ${dados.contato_telefone || "Não informado"}`;
        contato.style.cssText = "opacity:.85;margin-bottom:8px";
        popup.appendChild(contato);
        const campo = document.createElement("textarea");
        campo.value = dados.descricao || "";
        Object.assign(campo.style, { width: "100%", minHeight: "130px", resize: "vertical", boxSizing: "border-box", padding: "10px", borderRadius: "6px", border: "1px solid #aaa", font: "13px/1.45 Arial,sans-serif", color: "#111", background: "#fff" });
        popup.appendChild(campo);
        const botoes = document.createElement("div");
        botoes.style.cssText = "display:flex;justify-content:flex-end;gap:8px;margin-top:10px";
        const cancelar = document.createElement("button");
        cancelar.textContent = "Cancelar";
        const confirmar = document.createElement("button");
        confirmar.textContent = "Confirmar e lançar";
        [cancelar, confirmar].forEach(b => Object.assign(b.style, { padding: "8px 12px", border: "0", borderRadius: "6px", color: "white", cursor: "pointer", fontWeight: "bold" }));
        cancelar.style.background = "rgba(255,255,255,.16)";
        confirmar.style.background = "#27ae60";
        cancelar.onclick = () => {
            fetch("http://localhost:8765/auvo-agendamento-externo-cancelar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: dados.job_id }) }).catch(() => {});
            popup.remove(); liberarAcoes(); limparContextoPill(botao); setBotao(botao, "Agendamentos", "#3498db", false);
        };
        confirmar.onclick = () => {
            const descricao = campo.value.trim();
            if (!descricao) { mostrarNotificacao("A descrição não pode ficar vazia.", "erro"); campo.focus(); return; }
            confirmar.disabled = true; confirmar.textContent = "Lançando...";
            fetch("http://localhost:8765/auvo-agendamento-externo-confirmar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: dados.job_id, descricao }) })
                .then(r => r.json()).then(resp => {
                    if (resp.status !== "ok") throw new Error(resp.mensagem || "Não foi possível criar a tarefa.");
                    popup.remove(); setBotao(botao, "Agendamento criado e copiado", "#27ae60", true);
                    mostrarNotificacao(`Agendamento criado: ${resp.tipo} para ${resp.empresa}, Task #${resp.task_id}. O resumo com contato e descrição foi copiado.`, "sucesso");
                    setTimeout(() => { liberarAcoes(); limparContextoPill(botao); }, 4000);
                }).catch(err => { confirmar.disabled = false; confirmar.textContent = "Confirmar e lançar"; mostrarNotificacao(`Erro ao lançar agendamento: ${err.message}`, "erro"); });
        };
        botoes.append(cancelar, confirmar); popup.appendChild(botoes); document.body.appendChild(popup); campo.focus();
    }
    // ── Inserção dos botões ───────────────────────────────────────────────
    function iniciarAgendamentoExterno(botao, textoEmpresa, codigoCliente, tipoAtendimento, mensagensChatId = null, sistemaCliente = "", equipamento = "") {
        bloquearAcaoOposta('agendamento');
        setBotao(botao, 'Copiando conversa...', '#3498db', true);
        const processo = document.getElementById('auvo-pill-process');
        if (processo) processo.textContent = 'Analisando conversa e preparando descrição';
        fetch('http://localhost:8765/auvo-agendamento-externo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ empresa: textoEmpresa, codigo_cliente: codigoCliente || null, tipo_atendimento: tipoAtendimento, mensagens_chat_id: mensagensChatId, sistema: sistemaCliente, equipamento: equipamento }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'multiplas_empresas') {
                setBotao(botao, 'Escolha a empresa...', '#3498db', true);
                mostrarSeletorEmpresas(
                    data.empresas,
                    textoEmpresa,
                    botao,
                    empresaEscolhida => iniciarAgendamentoExterno(botao, textoEmpresa, empresaEscolhida.codigo, tipoAtendimento, data.mensagens_chat_id, sistemaCliente, equipamento),
                    () => { liberarAcoes(); limparContextoPill(botao); }
                );
                return;
            }
            if (data.status === 'confirmar_descricao') {
                setBotao(botao, 'Confira a descrição...', '#3498db', true);
                mostrarPopupDescricaoAgendamento(data, botao);
                return;
            }
            if (data.status !== 'ok') throw new Error(data.mensagem || 'Não foi possível preparar o agendamento.');
        })
        .catch(erro => {
            mostrarNotificacao(`Erro no agendamento: ${erro.message}`, 'erro');
            liberarAcoes();
            limparContextoPill(botao);
        });
    }
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
    function obterNomeAtendente() {
        const sidebar = document.querySelector('nav[aria-label="Sidebar"]') || document.querySelector('nav');
        if (!sidebar) return '';

        const textosIgnorados = new Set(['dashboard', 'chat', 'cliente', 'clientes', 'financeiro', 'suporte', 'início', 'inicio', 'alerta sonoro', 'voltar à gestão', 'voltar a gestão']);
        const candidatos = Array.from(sidebar.querySelectorAll('span, p, div'))
            .map(el => (el.innerText || el.textContent || '').trim())
            .filter(texto => texto && texto.length <= 80 && !textosIgnorados.has(texto.toLowerCase()));
        return candidatos.at(-1) || '';
    }

    function inserirBotaoLigacao() {
        if (document.getElementById('botao-auvo-ligacao')) return;
        const sidebar = document.querySelector('nav[aria-label="Sidebar"]') || document.querySelector('nav');
        if (!sidebar) return;

        const labelCliente = Array.from(sidebar.querySelectorAll('span, p, div'))
            .find(el => /^(cliente|clientes)$/i.test((el.innerText || el.textContent || '').trim()));
        if (!labelCliente) return;
        const itemCliente = labelCliente.closest('a, button, [role="link"], [role="button"]') || labelCliente.parentElement;
        if (!itemCliente) return;

        const botaoLigacao = document.createElement('button');
        botaoLigacao.id = 'botao-auvo-ligacao';
        Object.assign(botaoLigacao.style, {
            width: '100%', minHeight: '58px', padding: '8px 4px', border: '0',
            background: 'transparent', color: '#cbd5e1', cursor: 'pointer',
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '3px',
            fontSize: '11px', lineHeight: '1.15', transition: 'background-color .18s ease, color .18s ease',
        });

        function definirEstadoLigacao(emLigacao, pendente = false) {
            botaoLigacao.dataset.emLigacao = emLigacao ? '1' : '0';
            botaoLigacao.disabled = pendente;
            botaoLigacao.style.opacity = pendente ? '.65' : '1';
            botaoLigacao.style.cursor = pendente ? 'wait' : 'pointer';
            botaoLigacao.style.color = emLigacao ? '#22c55e' : '#cbd5e1';
            botaoLigacao.style.background = emLigacao ? 'rgba(34,197,94,.14)' : 'transparent';
            const titulo = emLigacao ? 'Encerrar ligação' : 'Iniciar ligação';
            const icone = emLigacao ? AUVO_ICONS.phoneActive : AUVO_ICONS.phone;
            botaoLigacao.innerHTML = `<span class="auvo-icon" style="width:22px;height:22px;display:inline-flex">${icone}</span><span>${emLigacao ? 'Em ligação' : 'Ligação'}</span>`;
            botaoLigacao.title = titulo;
            botaoLigacao.setAttribute('aria-label', titulo);
        }

        botaoLigacao.onmouseenter = () => { if (!botaoLigacao.disabled) botaoLigacao.style.background = botaoLigacao.dataset.emLigacao === '1' ? 'rgba(34,197,94,.20)' : 'rgba(255,255,255,.08)'; };
        botaoLigacao.onmouseleave = () => { if (!botaoLigacao.disabled) botaoLigacao.style.background = botaoLigacao.dataset.emLigacao === '1' ? 'rgba(34,197,94,.14)' : 'transparent'; };
        // Antes de disponibilizar o botão, sincroniza o estado salvo no
        // Supabase para que uma ligação aberta sobreviva a recarregamentos
        // e para que outros sistemas consigam consultar o status ao vivo.
        definirEstadoLigacao(false, true);
        fetch('http://localhost:8765/registro-ligacao-status')
            .then(resposta => resposta.json())
            .then(dados => {
                if (dados.status !== 'ok') throw new Error(dados.mensagem || 'Falha ao consultar ligação.');
                if (dados.registro_id) botaoLigacao.dataset.registroId = String(dados.registro_id);
                definirEstadoLigacao(dados.em_ligacao === true);
            })
            .catch(erro => {
                definirEstadoLigacao(false);
                mostrarNotificacao(`Erro ao consultar ligação: ${erro.message}`, 'erro');
            });

        async function enviarAtualizacaoLigacao(iniciar, vaiParaOrdem) {
            botaoLigacao.disabled = true;
            botaoLigacao.style.cursor = 'wait';
            try {
                const corpo = {
                    em_ligacao: iniciar,
                    registro_id: botaoLigacao.dataset.registroId || null,
                };
                if (!iniciar) corpo.vai_para_ordem = vaiParaOrdem;

                const resposta = await fetch('http://localhost:8765/registro-ligacao', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(corpo),
                });
                const dados = await resposta.json();
                if (dados.status !== 'ok') throw new Error(dados.mensagem || 'Não foi possível atualizar a ligação.');
                if (dados.registro_id) botaoLigacao.dataset.registroId = String(dados.registro_id);
                definirEstadoLigacao(dados.em_ligacao === true);

                if (!iniciar) {
                    if (vaiParaOrdem) {
                        mostrarNotificacao(
                            dados.ordem_zerada
                                ? 'Ligação registrada na ordem. Todos os atendentes confirmaram — registros de ligação zerados.'
                                : `Ligação registrada na ordem (${dados.contagem_ordem}/${dados.total_atendentes} atendentes).`,
                            'sucesso'
                        );
                    } else {
                        mostrarNotificacao('Ligação encerrada e excluída (não entrou na ordem).', 'info');
                    }
                }
            } catch (erro) {
                botaoLigacao.disabled = false;
                botaoLigacao.style.cursor = 'pointer';
                mostrarNotificacao(`Erro ao atualizar ligação: ${erro.message}`, 'erro');
            }
        }

        botaoLigacao.onclick = () => {
            if (botaoLigacao.disabled) return;
            const iniciar = botaoLigacao.dataset.emLigacao !== '1';

            if (iniciar) {
                enviarAtualizacaoLigacao(true, null);
                return;
            }

            // Ao finalizar a ligação, pergunta (num popup, não no confirm()
            // nativo do navegador) se ela vai virar uma ordem de serviço.
            // Se a pessoa responder que não, o registro é excluído.
            mostrarNotificacao(
                'Essa ligação vai virar uma ordem de serviço?',
                'aviso',
                [
                    {
                        label: 'Sim, vai pra ordem',
                        cor: '#27ae60',
                        callback: () => enviarAtualizacaoLigacao(false, true),
                    },
                    {
                        label: 'Não',
                        cor: 'rgba(255,255,255,0.16)',
                        callback: () => enviarAtualizacaoLigacao(false, false),
                    },
                ]
            );
        };

        itemCliente.insertAdjacentElement('afterend', botaoLigacao);

        // --- BOTÃO PROBLEMA NO EQUIPAMENTO ---
        const botaoEquipamentoProblema = document.createElement('button');
        botaoEquipamentoProblema.id = 'botao-auvo-equipamento-problema';
        botaoEquipamentoProblema.title = 'Registrar problema no equipamento';
        botaoEquipamentoProblema.setAttribute('aria-label', 'Registrar problema no equipamento');
        Object.assign(botaoEquipamentoProblema.style, {
            width: '100%', minHeight: '58px', padding: '8px 4px', border: '0',
            background: 'transparent', color: '#cbd5e1', cursor: 'pointer',
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '3px',
            fontSize: '11px', lineHeight: '1.15', transition: 'background-color .18s ease, color .18s ease',
        });
        botaoEquipamentoProblema.innerHTML = `<span class="auvo-icon" style="width:22px;height:22px;display:inline-flex">${AUVO_ICONS.tools}</span><span>Equip.</span>`;

        botaoEquipamentoProblema.onmouseenter = () => { if (!botaoEquipamentoProblema.disabled) botaoEquipamentoProblema.style.background = 'rgba(255,255,255,.08)'; };
        botaoEquipamentoProblema.onmouseleave = () => { if (!botaoEquipamentoProblema.disabled) botaoEquipamentoProblema.style.background = 'transparent'; };

        botaoEquipamentoProblema.onclick = () => {
            if (botaoEquipamentoProblema.disabled) return;
            const empresa = obterDadosEmpresaAtual();
            if (!empresa) {
                mostrarNotificacao('Não consegui identificar a empresa desta conversa.', 'erro');
                return;
            }
            mostrarModalEquipamentoProblema(botaoEquipamentoProblema, empresa);
        };

        itemCliente.insertAdjacentElement('afterend', botaoEquipamentoProblema);
    }

    function verificarEInserir() {
        garantirEstiloBotoesAuvo();
        inserirBotaoLigacao();
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
            window.auvoPillButton = botao;
            document.getElementById('auvo-pill-company').textContent = textoEmpresa || 'Empresa não identificada';
            document.getElementById('auvo-pill-process').textContent = 'Iniciando check-in';
            mostrarNotificacao('Aguarde a automação clicar no botão Copiar e terminar de ler a conversa antes de sair ou trocar de atendimento.', 'aviso');
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
            window.auvoPillButton = botaoOut;
            document.getElementById('auvo-pill-company').textContent = textoEmpresa || 'Empresa não identificada';
            document.getElementById('auvo-pill-process').textContent = 'Iniciando check-out';
            mostrarNotificacao('Aguarde a automação clicar no botão Copiar e terminar de ler a conversa antes de sair ou trocar de atendimento.', 'aviso');
            const sistemaCliente = obterSistemaAtual(obterReferenciaAtendimentoAtual());
            iniciarFluxoCheckout(botaoOut, textoEmpresa, null, sistemaCliente);
        };

        // --- BOTÃO AGENDAMENTOS ---
        const botaoAgendamentos = document.createElement('button');
        botaoAgendamentos.id = 'botao-auvo-agendamentos';
        botaoAgendamentos.title = 'Criar agendamento externo';
        botaoAgendamentos.setAttribute('aria-label', 'Criar agendamento externo');
        Object.assign(botaoAgendamentos.style, {
            width: '48px', height: '42px', padding: '0', minWidth: '48px',
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            backgroundColor: 'rgba(18,22,28,0.72)', color: '#3498db',
            border: '1px solid currentColor', borderRadius: '9px', cursor: 'pointer', fontWeight: '700',
            transition: 'background-color .18s ease, opacity .18s ease, box-shadow .18s ease, transform .18s ease',
            boxShadow: '0 2px 7px rgba(0,0,0,0.16)',
        });
        setBotao(botaoAgendamentos, 'Agendamentos', '#3498db', false);
        botaoAgendamentos.onclick = () => {
            if (botaoAgendamentos.disabled) return;
            const empresa = obterDadosEmpresaAtual();
            if (!empresa) {
                mostrarNotificacao('Não consegui identificar a empresa desta conversa.', 'erro');
                return;
            }
            window.auvoPillButton = botaoAgendamentos;
            document.getElementById('auvo-pill-company').textContent = empresa;
            document.getElementById('auvo-pill-process').textContent = 'Escolha o tipo de atendimento';
            mostrarNotificacao('Depois de escolher o tipo, aguarde a automação copiar a conversa e concluir a criação da tarefa antes de sair do atendimento.', 'aviso');
            mostrarModalTipoAgendamento(botaoAgendamentos, empresa);
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
            window.auvoPillButton = botaoPausa;
            document.getElementById('auvo-pill-company').textContent = textoEmpresa || 'Empresa não identificada';
            document.getElementById('auvo-pill-process').textContent = 'Iniciando pausa';
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
            window.auvoPillButton = botaoDespausa;
            document.getElementById('auvo-pill-company').textContent = textoEmpresa || 'Empresa não identificada';
            document.getElementById('auvo-pill-process').textContent = 'Encerrando pausa';
            abrirModalDespausa(
                (dadosDespausa) => iniciarFluxoDespausa(botaoDespausa, textoEmpresa, dadosDespausa),
                () => resetBotaoDespausa(botaoDespausa)
            );
        };
        const wrapper = document.createElement('div');
        wrapper.id = 'auvo-automation-pill';
        const contextoPill = document.createElement('div');
        contextoPill.id = 'auvo-pill-context';
        contextoPill.title = 'Arraste para mover a pílula';
        contextoPill.innerHTML = '<span id="auvo-pill-company">Nenhuma empresa selecionada</span><span id="auvo-pill-process">Aguardando procedimento</span>';
        wrapper.appendChild(contextoPill);
        const posicaoSalva = (() => { try { return JSON.parse(localStorage.getItem('auvoPillPosition') || 'null'); } catch (_) { return null; } })();
        if (posicaoSalva && Number.isFinite(posicaoSalva.left) && Number.isFinite(posicaoSalva.top)) {
            wrapper.style.left = `${Math.max(0, Math.min(posicaoSalva.left, innerWidth - 100))}px`;
            wrapper.style.top = `${Math.max(0, Math.min(posicaoSalva.top, innerHeight - 60))}px`;
            wrapper.style.right = 'auto'; wrapper.style.bottom = 'auto';
        }
        let arraste = null;
        contextoPill.addEventListener('pointerdown', (evento) => {
            if (evento.button !== 0) return;
            const ret = wrapper.getBoundingClientRect();
            arraste = { x:evento.clientX, y:evento.clientY, left:ret.left, top:ret.top, moveu:false };
            contextoPill.setPointerCapture(evento.pointerId);
            wrapper.classList.add('auvo-pill-dragging');
        });
        contextoPill.addEventListener('pointermove', (evento) => {
            if (!arraste) return;
            const dx=evento.clientX-arraste.x, dy=evento.clientY-arraste.y;
            if (Math.abs(dx)+Math.abs(dy)>3) arraste.moveu=true;
            if (!arraste.moveu) return;
            const left=Math.max(0,Math.min(arraste.left+dx,innerWidth-wrapper.offsetWidth));
            const top=Math.max(0,Math.min(arraste.top+dy,innerHeight-wrapper.offsetHeight));
            wrapper.style.left=`${left}px`; wrapper.style.top=`${top}px`;
            wrapper.style.right='auto'; wrapper.style.bottom='auto';
        });
        const finalizarArraste = () => {
            if (!arraste) return;
            wrapper.classList.remove('auvo-pill-dragging');
            if (arraste.moveu) { const ret=wrapper.getBoundingClientRect(); try { localStorage.setItem('auvoPillPosition',JSON.stringify({left:ret.left,top:ret.top})); } catch (_) {} }
            arraste=null;
        };
        contextoPill.addEventListener('pointerup', finalizarArraste);
        contextoPill.addEventListener('pointercancel', finalizarArraste);
        wrapper.setAttribute('role', 'group');
        wrapper.setAttribute('aria-label', 'Ações de automação Auvo');
        wrapper.appendChild(botao);
        wrapper.appendChild(botaoPausa);
        wrapper.appendChild(botaoDespausa);
        wrapper.appendChild(botaoOut);
        wrapper.appendChild(botaoAgendamentos);
        document.body.appendChild(wrapper);
    }

    setInterval(verificarEInserir, 1000);
})();
"""

if SERVER_MODE:
    print("✅ Modo servidor Playwright ativo. Aguardando jobs remotos...")
    while True:
        time.sleep(3600)
else:
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            page = _selecionar_pagina_speakr(browser)
            context = page.context

            # Se o navegador (porta 9222) for fechado, a conexão CDP cai e o
            # Playwright dispara este evento — aproveitamos para excluir a
            # sessão de presença na hora, sem esperar o heartbeat expirar.
            _navegador_desconectado = {"valor": False}

            def _ao_desconectar_navegador():
                _navegador_desconectado["valor"] = True
                print("\n⚠️ Navegador (porta 9222) foi fechado. Encerrando presença...")
                _encerrar_presenca_automacao()

            browser.on("disconnected", _ao_desconectar_navegador)

            try:
                context.storage_state(path=_playwright_state_path())
            except Exception:
                pass

            page.evaluate(SCRIPT_BOTAO)
            print("✓ Botão injetado com sucesso!")
            print("🚀 Sistema pronto. Aguardando cliques no Speakr...")

            falhas_consecutivas = 0
            while True:
                if _navegador_desconectado["valor"]:
                    print("Encerrando automação: navegador foi fechado.")
                    break
                try:
                    if page.is_closed() or not _pagina_parece_speakr(page):
                        page = _selecionar_pagina_speakr(browser)
                    page.evaluate(SCRIPT_BOTAO)
                    falhas_consecutivas = 0
                    time.sleep(5)
                except Exception as e:
                    if _navegador_desconectado["valor"]:
                        break
                    falhas_consecutivas += 1
                    espera = min(10 * falhas_consecutivas, 60)
                    print(f"⚠️ Falha ao reinjetar botão (#{falhas_consecutivas}): {e}")
                    print(f"   Aguardando {espera}s antes de tentar novamente...")
                    time.sleep(espera)

        except Exception as e:
            print(f"Erro ao conectar: {e}")
            input("Pressione Enter para fechar...")