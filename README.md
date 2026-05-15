# 🤖 Sistema Eedge — Automação de Atendimento Auvo

Sistema de automação para registro de atendimentos no [Auvo](https://auvo.com.br), integrado ao chat do Speakr. Com um clique, o sistema analisa o histórico do chat, gera descrições e relatos com IA (Gemini), e lança check-in/check-out automaticamente na plataforma.

---

## ✨ Funcionalidades

- **Check-in automático** — detecta o início do atendimento no chat e cria a task no Auvo
- **Check-out com relato** — gera um relato detalhado usando IA e lança o check-out
- **Início e fim de pausa** — registra pausas com motivo e horário direto pelo chat
- **Fluxo completo** — cria check-in e check-out em sequência quando não há task aberta
- **Detecção inteligente de data/hora** — lê marcadores do Speakr (Hoje, Ontem, DD/MM) para registrar o horário correto
- **Busca de empresa** — localiza o cliente na planilha por nome, com sugestão por similaridade
- **Injeção de botões no Speakr** — adiciona botões flutuantes na interface do chat para acionar todas as ações

---

## 🧰 Tecnologias

- Python 3.10+
- [Playwright](https://playwright.dev/python/) — automação de browser
- [Pandas](https://pandas.pydata.org/) — leitura da planilha de clientes
- [Requests](https://docs.python-requests.org/) — integração com a API do Auvo
- Gemini (Google AI) — geração de descrições e relatos via browser automation
- `http.server` — servidor HTTP local para comunicação com os botões injetados no Speakr

---

## ⚙️ Pré-requisitos

- Python 3.10 ou superior
- Microsoft Edge instalado
- Chrome/Edge aberto com depuração remota na porta `9222`
- Conta no Auvo com acesso à API
- Planilha `clientes.xlsx` com os clientes cadastrados

---

## 🚀 Instalação

```bash
# Clone o repositório
git clone https://github.com/seu-usuario/sistema-eedge.git
cd sistema-eedge

# Instale as dependências
pip install -r requirements.txt

# Instale os browsers do Playwright
playwright install msedge
```

---

## 🔐 Configuração

Crie um arquivo `.env` na raiz do projeto com suas credenciais:

```env
AUVO_USUARIO=seu_usuario
AUVO_SENHA=sua_senha
AUVO_API_KEY=sua_api_key
AUVO_API_TOKEN=seu_api_token
```

> ⚠️ **Nunca compartilhe o arquivo `.env`.** Ele já está no `.gitignore`.

---

## ▶️ Como usar

1. Abra o Edge com depuração remota:
   ```
   msedge.exe --remote-debugging-port=9222
   ```
2. Acesse o Speakr no navegador aberto
3. Execute o sistema:
   ```bash
   python sistema_Eedge.py
   ```
4. Escolha seu usuário no terminal
5. Os botões serão injetados automaticamente no chat do Speakr:
   - ✈️ **Check-in** — cria o atendimento e registra a entrada
   - ⏸️ **Pausa** — registra início de pausa com motivo
   - ▶️ **Fim da pausa** — encerra a pausa
   - ✖️ **Check-out** — gera relato com IA e finaliza o atendimento

---

## 📁 Estrutura

```
sistema-eedge/
├── sistema_Eedge.py   # Script principal
├── clientes.xlsx      # Planilha de clientes (não versionada)
├── .env               # Credenciais (não versionado)
├── .gitignore
└── README.md
```

---

## 🔒 Segurança

Este projeto usa variáveis de ambiente para todas as credenciais. Nunca adicione senhas, tokens ou API keys diretamente no código.

Arquivos ignorados pelo git:
- `.env`
- `clientes.xlsx`
- `state.json` / `gemini_state.json` (sessões salvas)
- Screenshots de erro (`*.png`)

---

## 📄 Licença

Projeto interno. Uso restrito.
