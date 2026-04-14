import pandas as pd
import difflib
import unicodedata
import re
import requests
from playwright.sync_api import sync_playwright
from datetime import datetime
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import pyperclip
import os
import time


USUARIO_AUVO = os.environ.get("AUVO_USUARIO", "")
SENHA_AUVO   = os.environ.get("AUVO_SENHA", "")


USUARIOS = {
    "1": {"nome": "Usuario1", "id": 000000},
    "2": {"nome": "Usuario2", "id": 000000},
    "3": {"nome": "Usuario3", "id": 000000},
    "4": {"nome": "Usuario4", "id": 000000},
    "5": {"nome": "Usuario5", "id": 000000},
    "6": {"nome": "Usuario6", "id": 000000},
}


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

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

def chamar_openrouter(prompt: str, tentativas=3) -> str:
    for i in range(tentativas):
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "openai/gpt-oss-120b",
                    "messages": [
                        {
                            "role": "system",
                            "content": "Você é um técnico de suporte. Siga exatamente as regras do usuário. Não invente, não explique, não adicione nada extra."
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    "temperature": 0.2,
                    "max_tokens": 800
                },
                timeout=30
            )

            if response.status_code == 429:
                print("⚠️ Rate limit OpenRouter, tentando novamente...")
                time.sleep(2)
                continue

            response.raise_for_status()
            data = response.json()

            choices = data.get("choices")
            if not choices:
                print(f"⚠️ Tentativa {i+1} — resposta sem 'choices': {data}")
                time.sleep(2)
                continue

            texto = choices[0].get("message", {}).get("content")
            if not texto:
                print(f"⚠️ Tentativa {i+1} — content vazio: {choices[0]}")
                time.sleep(2)
                continue

            texto = texto.strip()
            print(f"✅ OpenRouter OK ({len(texto)} chars)")
            return texto

        except Exception as e:
            print(f"⚠️ Tentativa {i+1} OpenRouter falhou: {e}")
            time.sleep(2)

    return "Atendimento iniciado via chat. Verificar detalhes no histórico."



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

def pegar_descricao(texto, mensagens: list[str] = None):
    if not texto:
        return "Cliente entrou em contato via chat."

    texto = re.sub(r'Format:\s*Plain\s*text\.?', '', texto, flags=re.IGNORECASE)

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
        descricao = linhas[-1] if linhas else texto

    descricao = descricao.strip()
    descricao = re.sub(r'^[\*\-\•\s]+', '', descricao)
    descricao = descricao.replace('\n', ' ').strip()

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


def gerar_descricao_com_ia(mensagens: list[str]) -> str:
    if not mensagens:
        return "Sem mensagens disponíveis para análise."

    linhas = preprocessar_mensagens(mensagens)

    prompt = f"""Você é um técnico de suporte de TI resumindo um atendimento para registro interno.

Regras:
- Máximo 350 caracteres
- Ignore COMPLETAMENTE todas as mensagens marcadas como [TÉCNICO]
- Descreva APENAS o problema(s) do cliente, não inclua o que precisa fazer.
- Sem sugestões, causas, rascunhos, análise ou asteriscos
- Comece com o nome do cliente (primeira palavra da primeira mensagem [CLIENTE] que contenha um nome)
- NÃO mencione o técnico, AnyDesk, acesso remoto ou ações do suporte
- Responda APENAS com a descrição em texto corrido, sem checklist, sem bullets, sem nada após o ponto final
- Precisa ser em português.

Histórico:
\"\"\"
{linhas}
\"\"\"

###DESCRIÇÃO###"""

    descricao = chamar_openrouter(prompt)
    return pegar_descricao(descricao, mensagens)



def gerar_relato_com_ia(mensagens: list[str]) -> str:
    if not mensagens:
        return "Foi realizado atendimento via chat."

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

    descricao = chamar_openrouter(prompt)
    return limpar_relato(descricao)


def encontrar_inicio_atendimento(mensagens):
    padrao_gatilho = re.compile(
        r"me chamo .+ e iniciarei|iniciarei.*atendimento|vou iniciar.*atendimento|irei iniciar.*atendimento",
        re.IGNORECASE
    )
    padrao_hora = re.compile(r"(\d{1,2})[h:](\d{2})")
    sistema = re.compile(
        r"HORÁRIO DE ATENDIMENTO|transferido|atribuído|automação|"
        r"Seja bem vindo|digit|#sair|ANYDESK|agilizar|alta demanda|"
        r"Por favor|setor desejado|Suporte em Sistemas",
        re.IGNORECASE
    )

    ocorrencias = [i for i, msg in enumerate(mensagens) if padrao_gatilho.search(msg)]

    if ocorrencias:
        i   = ocorrencias[-1]
        msg = mensagens[i]
        for idx in range(i, -1, -1):
            if sistema.search(mensagens[idx]):
                continue
            hora_match = padrao_hora.search(mensagens[idx])
            if hora_match:
                h, m = hora_match.group(1).zfill(2), hora_match.group(2)
                print(f"✅ Hora encontrada no índice [{idx}]: {h}:{m} — '{mensagens[idx][:60]}'")
                return f"{h}:{m}", msg

    print("⚠️ Gatilho não encontrado. Usando hora da primeira mensagem...")
    for msg in mensagens:
        hora_match = padrao_hora.search(msg)
        if hora_match:
            h, m = hora_match.group(1).zfill(2), hora_match.group(2)
            print(f"✅ Fallback — hora encontrada: {h}:{m} — '{msg[:60]}'")
            return f"{h}:{m}", msg

    print("⚠️ Nenhuma hora encontrada no chat.")
    return None, None


def autenticar():
    url     = "https://api.auvo.com.br/v2/login/"
    payload = {
        "apiKey":   os.environ.get("AUVO_API_KEY", ""),
        "apiToken": os.environ.get("AUVO_API_TOKEN", "")
    }
    response = requests.post(url, json=payload)
    return response.json()["result"]["accessToken"]


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
    response_task = requests.post("https://api.auvo.com.br/v2/tasks/",
                                  json=payload_task, headers=headers)
    data = response_task.json()
    print(f"🔍 Resposta Auvo: {json.dumps(data, ensure_ascii=False)[:500]}")

    result = data.get("result")
    if isinstance(result, list):
        return result[0]["taskID"]
    elif isinstance(result, dict):
        return result["taskID"]
    else:
        raise Exception(f"Estrutura inesperada: {data}")


def buscar_task_aberta(token, customer_id):
    if not customer_id:
        return None

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data_hoje = datetime.now().strftime("%Y-%m-%d")

    filtros = {
        "customerId": int(customer_id),
        "startDate":  data_hoje,
        "endDate":    data_hoje,
        "idUserTo":   ID_USER,
    }

    url    = "https://api.auvo.com.br/v2/tasks/"
    params = {"paramFilter": json.dumps(filtros)}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)

        if response.status_code != 200:
            print(f"⚠️ Erro API Auvo ({response.status_code}): {response.text}")
            return None

        data   = response.json()
        result = data.get("result", {})

        if isinstance(result, dict):
            entity_list = result.get("entityList", [])
        elif isinstance(result, list):
            entity_list = result
        else:
            print(f"⚠️ Formato inesperado de result: {type(result)}")
            return None

        if entity_list:
            task = entity_list[-1]
            print(f"✅ Task encontrada: #{task['taskID']} | Status: {task.get('taskStatus')} | CheckOut: {task.get('checkOut')}")
            return task["taskID"]

        print("⚠️ entityList vazia.")
        return None

    except Exception as e:
        print(f"❌ Erro ao buscar task: {e}")
        return None



def _abrir_browser_e_pagina(playwright):
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    context = (
        browser.new_context(storage_state="state.json")
        if os.path.exists("state.json")
        else browser.new_context()
    )
    page = context.new_page()
    page.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in ["image", "stylesheet", "font"]
        else route.continue_()
    )
    return browser, context, page


def _garantir_login(page, context):
    if "login" in page.url.lower():
        print("⚠️ Sessão expirada. Logando...")
        page.locator('#usuario').fill(USUARIO_AUVO)
        page.locator('#senha').fill(SENHA_AUVO)
        page.keyboard.press("Enter")
        page.wait_for_function("!window.location.href.includes('login')", timeout=20000)
        context.storage_state(path="state.json")
        print("✅ Login realizado.")


def _clicar_editar(page):
    page.evaluate("""
        async () => {
            const findAndClick = () => {
                const btn = Array.from(document.querySelectorAll('button, a'))
                    .find(el => el.textContent.includes('Editar') || el.innerHTML.includes('fa-edit'));
                if (btn) { btn.click(); return true; }
                return false;
            };
            for (let i = 0; i < 10; i++) {
                if (findAndClick()) break;
                await new Promise(r => setTimeout(r, 500));
            }
        }
    """)


def _preencher_data_hora(page, data_checkin, hora):
    data_formatada = datetime.strptime(data_checkin, "%Y-%m-%d").strftime("%d/%m/%Y")

    campo_data = page.locator('input[name="datePicker"]:visible').last
    campo_hora = page.locator('input[name="clockPicker"]:visible').last

    campo_data.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    campo_data.type(data_formatada, delay=30)

    campo_hora.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    campo_hora.type(hora, delay=30)
    page.keyboard.press("Tab")
    time.sleep(1)



task_status = {}


def realizar_checkin_ui_invisivel(task_id, data_checkin, hora_checkin, inicio=None):
    print(f"\n[UI] Iniciando check-in para Task #{task_id}...")
    with sync_playwright() as playwright:
        browser, context, page = _abrir_browser_e_pagina(playwright)
        try:
            url_tarefa = f"https://app2.auvo.com.br/relatorioTarefas/DetalheTarefa/{task_id}"

            page.route("**/*", lambda route: route.abort()
                if route.request.resource_type in ["image", "stylesheet", "font", "media", "other"]
                else route.continue_())

            page.goto(url_tarefa, wait_until="domcontentloaded")
            _garantir_login(page, context)

            if url_tarefa not in page.url:
                print(f"⚠️ Redirecionando para tarefa...")
                page.goto(url_tarefa, wait_until="domcontentloaded")

            aba_horas = page.locator('text="Controle de horas"').first
            aba_horas.wait_for(state="visible", timeout=20000)
            aba_horas.click(force=True)

            _clicar_editar(page)

            page.locator('button:has-text("Adicionar Atividade")').first.wait_for(state="visible", timeout=10000)
            page.locator('button:has-text("Adicionar Atividade")').first.click()
            page.locator('a:has-text("Check-In")').first.click()

            _preencher_data_hora(page, data_checkin, hora_checkin)

            btn_salvar = page.locator("#edicao-salvar")
            btn_salvar.click(force=True)
            btn_salvar.wait_for(state="hidden", timeout=10000)

            context.storage_state(path="state.json")
            print(f"✓ Task #{task_id} — check-in salvo!")
            task_status[str(task_id)] = "done"
            if inicio:
                total = time.time() - inicio
                print(f"⏱️ Tempo total (clique → check-in salvo): {total:.1f}s")

        except Exception as e:
            print(f"[UI ERROR] Falha no check-in Task #{task_id}: {e}")
            try:
                page.screenshot(path=f"erro_checkin_{task_id}.png")
                print(f"📸 Screenshot salvo: erro_checkin_{task_id}.png")
            except Exception:
                pass
        finally:
            browser.close()



def realizar_checkout_ui_invisivel(task_id, data_checkin, hora_checkout, relato):
    print(f"\n[UI] Iniciando check-out para Task #{task_id}...")
    with sync_playwright() as playwright:
        browser, context, page = _abrir_browser_e_pagina(playwright)
        try:
            url_tarefa = f"https://app2.auvo.com.br/relatorioTarefas/DetalheTarefa/{task_id}"

            page.route("**/*", lambda route: route.abort()
                if route.request.resource_type in ["image", "stylesheet", "font", "media", "other"]
                else route.continue_())

            page.goto(url_tarefa, wait_until="domcontentloaded")
            _garantir_login(page, context)

            if url_tarefa not in page.url:
                print(f"⚠️ Redirecionando para tarefa...")
                page.goto(url_tarefa, wait_until="domcontentloaded")

            page.locator('text="Relato do usuário"').first.wait_for(state="visible", timeout=20000)
            print(f"✅ Página pronta: {page.url}")

            print("Preenchendo relato...")
            page.locator('text="Relato do usuário"').first.click(force=True)
            _clicar_editar(page)

            campo_relato = page.locator('textarea, div[contenteditable="true"]').first
            campo_relato.wait_for(state="visible", timeout=8000)
            campo_relato.click()
            page.keyboard.press("Control+A")
            campo_relato.fill(relato)

            print("Lançando check-out...")
            page.locator('text="Controle de horas"').first.click(force=True)
            _clicar_editar(page)

            page.locator('button:has-text("Adicionar Atividade")').first.wait_for(state="visible", timeout=8000)
            page.locator('button:has-text("Adicionar Atividade")').first.click()

            page.locator('a:has-text("Check-Out")').first.wait_for(state="visible", timeout=10000)
            page.locator('a:has-text("Check-Out")').first.click()

            page.locator('input[name="datePicker"]:visible').last.wait_for(state="visible", timeout=10000)
            _preencher_data_hora(page, data_checkin, hora_checkout)

            btn_salvar = page.locator("#edicao-salvar")
            btn_salvar.wait_for(state="visible", timeout=10000)
            btn_salvar.click(force=True)
            btn_salvar.wait_for(state="hidden", timeout=10000)

            context.storage_state(path="state.json")
            print(f"✓ Check-out Task #{task_id} salvo!")
            task_status[f"checkout_{task_id}"] = "done"

        except Exception as e:
            print(f"[ERRO] Falha no check-out Task #{task_id}: {e}")
            try:
                page.screenshot(path=f"erro_checkout_{task_id}.png")
                print(f"📸 Screenshot salvo: erro_checkout_{task_id}.png")
            except Exception:
                pass
        finally:
            browser.close()


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

def buscar_codigo_empresa(nome_empresa):
    if not nome_empresa:
        return None
    nome_empresa = normalizar(limpar_nome_empresa(nome_empresa))
    for _, row in df.iterrows():
        if nome_empresa in row["nome_normalizado"]:
            return int(row["codigo"])
    nomes = df["nome_normalizado"].tolist()
    mais_proximo = difflib.get_close_matches(nome_empresa, nomes, n=1, cutoff=0.7)
    if mais_proximo:
        return int(df[df["nome_normalizado"] == mais_proximo[0]].iloc[0]["codigo"])
    return None


def pegar_hora_ultima_mensagem(mensagens):
    padrao_hora = re.compile(r"(\d{1,2})[h:](\d{2})")
    for msg in reversed(mensagens):
        hora_match = padrao_hora.search(msg)
        if hora_match:
            h, m = hora_match.group(1).zfill(2), hora_match.group(2)
            print(f"✅ Hora do checkout: {h}:{m} — '{msg[:60]}'")
            return f"{h}:{m}"
    return None


def pegar_mensagens_chat():
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0]
            page    = None
            for p_aberta in context.pages:
                if "speakr" in p_aberta.url.lower() or "auvo" in p_aberta.url.lower():
                    page = p_aberta
                    break
            if not page:
                page = context.pages[0]

            botao_copiar = page.get_by_role("button").filter(has_text=re.compile(r"^$")).nth(2)
            botao_copiar.wait_for(state="visible", timeout=5000)
            botao_copiar.click()
            time.sleep(1.5)
            conteudo_chat = pyperclip.paste()
            mensagens = [m.strip() for m in conteudo_chat.split('\n') if m.strip()]
            return list(dict.fromkeys(mensagens))
        except Exception as e:
            print(f"Erro ao capturar chat: {e}")
            return []


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
            self.send_response(200)
            self.send_header("Content-type",                "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": status}).encode())

    def do_POST(self):
        if self.path == "/auvo":
            try:
                inicio = time.time()
                content_length = int(self.headers['Content-Length'])
                dados          = json.loads(self.rfile.read(content_length))
                empresa        = dados.get("empresa", "")

                print(f"\n>> Novo Lançamento: {empresa} | Usuário: {NOME_USER} (ID: {ID_USER})")

                mensagens    = pegar_mensagens_chat()
                print("🤖 Gerando descrição com IA...")
                descricao_ia = gerar_descricao_com_ia(mensagens)
                print(f"📝 Descrição:\n{descricao_ia}\n")

                codigo_cliente = buscar_codigo_empresa(empresa)
                if not codigo_cliente:
                    raise Exception(f"Cliente '{empresa}' não encontrado.")

                hora_chat, _ = encontrar_inicio_atendimento(mensagens)
                if not hora_chat:
                    raise Exception("Hora de início não encontrada no chat.")

                token     = autenticar()
                data_hoje = datetime.now().strftime("%Y-%m-%d")
                task_id   = criar_atendimento_com_checkin(
                    token        = token,
                    customer_id  = codigo_cliente,
                    id_user_from = ID_USER,
                    id_user_to   = ID_USER,
                    task_type    = int(os.environ.get("AUVO_TASK_TYPE", "0")),
                    data_hora    = f"{data_hoje}T{hora_chat}:00",
                    descricao    = descricao_ia
                )

                print(f">> Task #{task_id} criada. Iniciando Check-in...")
                threading.Thread(
                    target=realizar_checkin_ui_invisivel,
                    args=(task_id, data_hoje, hora_chat, inicio)
                ).start()

                resposta = {"status": "ok", "task_id": task_id, "descricao": descricao_ia}

            except Exception as e:
                print("❌ Erro:", e)
                resposta = {"status": "erro", "mensagem": str(e)}

            self._enviar_json(resposta)

        elif self.path == "/auvo-checkout":
            try:
                content_length = int(self.headers['Content-Length'])
                dados = json.loads(self.rfile.read(content_length))
                empresa = dados.get("empresa", "")

                print(f"\n>> Check-out por empresa: {empresa} | Usuário: {NOME_USER}")

                mensagens = pegar_mensagens_chat()

                codigo_cliente = buscar_codigo_empresa(empresa)
                if not codigo_cliente:
                    raise Exception(f"Cliente '{empresa}' não encontrado.")

                hora_checkout = pegar_hora_ultima_mensagem(mensagens)
                if not hora_checkout:
                    raise Exception("Hora de checkout não encontrada no chat.")

                relato_resultado = [None]
                task_resultado   = [None]

                def gerar_relato():
                    print("🤖 Gerando relato detalhado com IA...")
                    relato_resultado[0] = gerar_relato_com_ia(mensagens)
                    print(f"📝 Relato:\n{relato_resultado[0]}\n")

                def buscar_task():
                    token = autenticar()
                    task_resultado[0] = buscar_task_aberta(token, str(codigo_cliente))

                t1 = threading.Thread(target=gerar_relato)
                t2 = threading.Thread(target=buscar_task)
                t1.start()
                t2.start()
                t1.join()
                t2.join()

                relato  = relato_resultado[0]
                task_id = task_resultado[0]

                if not task_id:
                    raise Exception(f"Nenhuma task encontrada hoje para '{empresa}'.")

                task_status[f"checkout_{task_id}"] = "relato_ok"
                data_hoje = datetime.now().strftime("%Y-%m-%d")

                threading.Thread(
                    target=realizar_checkout_ui_invisivel,
                    args=(task_id, data_hoje, hora_checkout, relato)
                ).start()

                resposta = {"status": "ok", "task_id": task_id, "hora": hora_checkout}

            except Exception as e:
                print("❌ Erro checkout:", e)
                resposta = {"status": "erro", "mensagem": str(e)}

            self._enviar_json(resposta)

    def _enviar_json(self, dados: dict):
        self.send_response(200)
        self.send_header("Content-type",                "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(dados, ensure_ascii=False).encode())


def iniciar_servidor():
    print(f"🌍 Servidor rodando em http://localhost:8765")
    HTTPServer(("localhost", 8765), AuvoHandler).serve_forever()

threading.Thread(target=iniciar_servidor, daemon=True).start()


SCRIPT_BOTAO = """
(function() {
    function verificarEInserir() {
        if (document.getElementById('botao-auvo')) return;
        const spanProtocolo = Array.from(document.querySelectorAll('span'))
            .find(el => el.textContent.trim().includes('Protocolo'));
        if (spanProtocolo) {

            const botao = document.createElement('button');
            botao.id        = "botao-auvo";
            botao.innerText = "Lançar no Auvo";
            Object.assign(botao.style, {
                marginLeft: "10px", padding: "5px 10px",
                backgroundColor: "#2ecc71", color: "white",
                border: "none", borderRadius: "4px",
                cursor: "pointer", fontWeight: "bold"
            });

            botao.onclick = async () => {
                if (botao.disabled) return;
                botao.innerText = "🤖 Analisando chat...";
                botao.disabled  = true;
                try {
                    const spans       = Array.from(document.querySelectorAll('span'));
                    const spanEmpresa = spans.find(el => el.textContent.trim() === 'Empresa');
                    const textoEmpresa = spanEmpresa && spanEmpresa.nextElementSibling
                        ? spanEmpresa.nextElementSibling.innerText : "Desconhecida";

                    const res  = await fetch("http://localhost:8765/auvo", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ empresa: textoEmpresa })
                    });
                    const data = await res.json();

                    if (data.status === "ok") {
                        botao.innerText = `Task #${data.task_id} — Criando Check-in...`;
                        botao.style.backgroundColor = "#3498db";

                        const intervalo = setInterval(async () => {
                            try {
                                const res2 = await fetch(`http://localhost:8765/status/${data.task_id}`);
                                const s    = await res2.json();
                                if (s.status === "done") {
                                    clearInterval(intervalo);
                                    botao.innerText = `✓ Task #${data.task_id} Salva!`;
                                    botao.style.backgroundColor = "#27ae60";
                                    setTimeout(() => {
                                        botao.innerText = "Lançar no Auvo";
                                        botao.style.backgroundColor = "#2ecc71";
                                        botao.disabled = false;
                                    }, 5000);
                                }
                            } catch(e) { clearInterval(intervalo); }
                        }, 3000);
                    } else {
                        throw new Error(data.mensagem);
                    }
                } catch (err) {
                    botao.innerText = "❌ Erro!";
                    botao.style.backgroundColor = "#e74c3c";
                    console.error("Checkin erro:", err);
                    setTimeout(() => {
                        botao.innerText = "Lançar no Auvo";
                        botao.style.backgroundColor = "#2ecc71";
                        botao.disabled = false;
                    }, 4000);
                }
            };

            const botaoOut = document.createElement('button');
            botaoOut.id        = "botao-auvo-checkout";
            botaoOut.innerText = "Check-Out";
            Object.assign(botaoOut.style, {
                padding: "5px 10px",
                backgroundColor: "#e67e22", color: "white",
                border: "none", borderRadius: "4px",
                cursor: "pointer", fontWeight: "bold"
            });

            botaoOut.onclick = async () => {
                if (botaoOut.disabled) return;

                const spans       = Array.from(document.querySelectorAll('span'));
                const spanEmpresa = spans.find(el => el.textContent.trim() === 'Empresa');
                const textoEmpresa = spanEmpresa && spanEmpresa.nextElementSibling
                    ? spanEmpresa.nextElementSibling.innerText : "Desconhecida";

                botaoOut.innerText = "🤖 Gerando relato...";
                botaoOut.style.backgroundColor = "#8e44ad";
                botaoOut.disabled  = true;
                try {
                    const res  = await fetch("http://localhost:8765/auvo-checkout", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ empresa: textoEmpresa })
                    });
                    const data = await res.json();

                    if (data.status === "ok") {
                        const intervalo = setInterval(async () => {
                            try {
                                const res2 = await fetch(`http://localhost:8765/status/checkout_${data.task_id}`);
                                const s    = await res2.json();

                                if (s.status === "relato_ok") {
                                    botaoOut.innerText = "📋 Lançando Check-Out...";
                                    botaoOut.style.backgroundColor = "#2980b9";

                                } else if (s.status === "done") {
                                    clearInterval(intervalo);
                                    botaoOut.innerText = `✓ Check-Out ${data.hora}`;
                                    botaoOut.style.backgroundColor = "#27ae60";
                                    setTimeout(() => {
                                        botaoOut.innerText = "Check-Out";
                                        botaoOut.style.backgroundColor = "#e67e22";
                                        botaoOut.disabled = false;
                                    }, 5000);
                                }
                            } catch(e) { clearInterval(intervalo); }
                        }, 3000);
                    } else {
                        throw new Error(data.mensagem);
                    }
                } catch (err) {
                    botaoOut.innerText = "❌ Erro!";
                    botaoOut.style.backgroundColor = "#e74c3c";
                    console.error("Checkout erro:", err);
                    setTimeout(() => {
                        botaoOut.innerText = "Check-Out";
                        botaoOut.style.backgroundColor = "#e67e22";
                        botaoOut.disabled = false;
                    }, 4000);
                }
            };

            const wrapper = document.createElement('span');
            wrapper.style.cssText = 'display:inline-flex; align-items:center; gap:6px;';
            wrapper.appendChild(botao);
            wrapper.appendChild(botaoOut);
            spanProtocolo.parentElement.appendChild(wrapper);
        }
    }

    setInterval(verificarEInserir, 1000);
})();
"""

with sync_playwright() as p:
    try:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page    = None
        for p_aberta in context.pages:
            if "speakr" in p_aberta.url.lower() or "auvo" in p_aberta.url.lower():
                page = p_aberta
                break
        if not page:
            page = context.pages[0]

        try:
            context.storage_state(path="state.json")
        except Exception:
            pass

        page.evaluate(SCRIPT_BOTAO)
        print("✓ Botão injetado com sucesso!")
        print("🚀 Sistema pronto. Aguardando cliques no Speakr...")

        falhas_consecutivas = 0
        while True:
            try:
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
