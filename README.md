# AutomacaoCompleta

Sistema de automação para suporte técnico em campo, integrando **Auvo** (gestão de field service), **Speakr** (captura de conversas via chat) e a **API do Claude (Anthropic)** para geração automática de relatos de atendimento.

## O que o sistema faz

- Captura mensagens de atendimento via CDP (Chrome DevTools Protocol, porta 9222) na aba do Speakr.
- Consulta clientes, segmentos e equipamentos cadastrados na Auvo através da API oficial (`api.auvo.com.br`).
- Usa a API do Claude para interpretar a conversa do atendimento e gerar automaticamente:
  - descrição do problema relatado;
  - relato técnico do atendimento;
  - identificação de equipamentos citados/relacionados.
- Automatiza check-in/check-out de tarefas na Auvo via Playwright (interface web), incluindo verificação de segmentos do cliente.
- Interface de bandeja do sistema (system tray) para rodar em segundo plano em máquinas Windows.
- Autenticação e configuração sensível (credenciais Auvo, chave da API do Claude, planilhas Google) centralizadas no **Supabase**, nunca hardcoded no código.

## Requisitos

- Python 3.10+
- Google Chrome ou Microsoft Edge (para o Playwright e para o CDP do Speakr)
- Conta Supabase configurada com as tabelas:
  - `credenciais_auvo` (usuario, senha, api_key, api_token)
  - `credenciais_ia` (anthropic_api_key)
  - `prompts_ia` (prompts customizados, opcional — há fallback embutido)

Instale as dependências:

```bash
pip install -r requirements.txt
playwright install chromium
```

## Configuração

1. Copie `.env.example` para `.env` e preencha `SUPABASE_URL`, `SUPABASE_ANON_KEY` e `CONFIG_SUPABASE_PATH`.
2. Copie `config_supabase.example.json` para o caminho indicado em `CONFIG_SUPABASE_PATH` e preencha `email`/`senha` da conta de serviço do Supabase.
3. Copie `usuarios.example.json` para `usuarios.json` (mesma pasta do script) e cadastre os atendentes da sua equipe (nome + ID do usuário na Auvo).
4. Garanta que a política de RLS da tabela `credenciais_auvo` no Supabase só permite leitura por usuário autenticado.

## Executando

```bash
python AutomacaoCompleta.py
```

O script abre um prompt para escolher o atendente (conforme `usuarios.json`), inicia o monitoramento do console via bandeja do sistema, e passa a acompanhar o Speakr/Auvo automaticamente.

> Certifique-se de abrir o Chrome com depuração remota antes de iniciar:
> `chrome.exe --remote-debugging-port=9222`

## Estrutura de arquivos sensíveis (não versionados)

| Arquivo | Descrição |
|---|---|
| `.env` | Variáveis de ambiente (URL/chave do Supabase) |
| `config_supabase.json` | Credenciais da conta de serviço do Supabase |
| `usuarios.json` | Lista de atendentes e seus IDs na Auvo |
| `state.json` | Estado local de execução do script |

Todos esses arquivos estão listados no `.gitignore` e **não devem ser commitados**.

## Aviso

Este projeto foi desenvolvido para uso interno em um fluxo específico de suporte técnico (Auvo + Speakr). Para usar em outro contexto, adapte as integrações de acordo com sua própria conta Auvo/Supabase.
