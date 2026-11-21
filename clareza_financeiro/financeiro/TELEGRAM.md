<!-- Guia da integração: explica a criação do bot, as variáveis de ambiente, a inicialização no Docker, o vínculo de contas, os fluxos da API, os testes e a solução de problemas. -->
# Telegram no Clareza: instalação e como a integração funciona

Este guia acompanha a implementação. Você pode começar pela instalação e depois seguir o caminho de uma mensagem no código. Não é necessário cadastrar uma aplicação de cliente Telegram, obter `api_id` ou usar sua conta pessoal como robô: usamos a **Bot API**, própria para bots.

## 1. O que estamos conectando

O bot é uma conta especial do Telegram. Você conversa com ela pelo aplicativo; o programa que decide as respostas roda no seu Docker, junto ao Clareza. Criar a conta do bot não coloca nosso código nos servidores do Telegram.

```text
Seu aplicativo Telegram
       ↕
Servidores do Telegram / Bot API
       ↕ HTTPS (conexões iniciadas pelo bot)
Serviço Docker telegram
       ├── Django / validação / OCR
       ├── MySQL compartilhado com o site
       └── volumes privados e comprovantes
```

**API** é a interface pela qual programas conversam. Neste caso, enviamos requisições HTTPS e recebemos respostas em JSON. Um método é uma operação disponível nessa interface, como `getMe` ou `sendMessage`.

O computador precisa estar ligado, com internet, Docker e os serviços funcionando. Ao desligá-lo, o bot para de responder. O Telegram guarda atualizações pendentes por até 24 horas; isso não é uma fila permanente para documentos enviados durante longas interrupções.

Referência: [Bot API](https://core.telegram.org/bots/api).

## 2. Criar seu bot no BotFather

1. Abra o perfil oficial [@BotFather](https://t.me/BotFather) no Telegram.
2. Envie `/newbot`.
3. Escolha o nome exibido, por exemplo `Clareza Financeiro`.
4. Escolha um nome de usuário disponível que termine em `bot`, por exemplo `meu_clareza_financeiro_bot`.
5. O BotFather entrega um **token**. Guarde-o no arquivo `.env` local, conforme a próxima etapa.

O nome exibido e o nome de usuário são coisas diferentes: o segundo forma o endereço `https://t.me/nome_do_bot`.

O token é a credencial que permite controlar o bot. Ele não é sua senha pessoal do Telegram e não concede acesso automático às contas do Clareza. Não o envie em conversa, print, documentação ou commit. Se for exposto, revogue-o pelo BotFather e configure o novo token.

No BotFather, você também pode usar `/setjoingroups` para desabilitar a participação em grupos. Mesmo que o bot seja adicionado a um grupo, nossa implementação só aceita conversas privadas.

Referência: [criação e token](https://core.telegram.org/bots/tutorial#obtain-your-bot-token).

## 3. Configurar e iniciar com Docker

Abra o PowerShell na pasta `clareza_financeiro/financeiro`, que contém `compose.yaml`.

Se você já usa o Clareza, **edite seu `.env` existente**. Não substitua suas senhas e sua `SECRET_KEY` pelo arquivo de exemplo. Para uma instalação nova, siga primeiro o README.

Acrescente estas variáveis, substituindo os exemplos localmente:

```dotenv
TELEGRAM_BOT_TOKEN=COLOQUE_AQUI_O_TOKEN_ENTREGUE_PELO_BOTFATHER
TELEGRAM_BOT_USERNAME=meu_clareza_financeiro_bot
```

O nome deve corresponder ao bot do token. Use-o sem `@`. Não coloque o token no código Python: ele é lido do ambiente pelos dois serviços.

Antes de atualizar uma instalação com dados, mantenha seu backup habitual do banco e dos comprovantes. Depois execute:

```powershell
docker compose --profile telegram up --build -d
docker compose logs -f telegram
```

O perfil `telegram` torna o bot opcional: o site continua utilizável sem configurar um token. A inicialização aguarda o site ficar saudável, o que acontece após as migrações do banco. O bot não executa migrações concorrentes.

O serviço confirma sua identidade com `getMe`, confere se o nome corresponde ao token, verifica a ausência de webhook e registra o menu de comandos com `setMyCommands`.

Execute **somente um serviço de polling por bot**. Não inicie uma segunda cópia do mesmo bot no terminal ou em outro computador.

Para parar apenas o bot:

```powershell
docker compose stop telegram
```

Depois de alterar as variáveis, recrie os contêineres para carregá-las:

```powershell
docker compose --profile telegram up -d --force-recreate web telegram
```

Não use `docker compose down -v`: a opção `-v` remove volumes de dados.

## 4. Vincular sua conta

1. Entre no site do Clareza com seu usuário habitual.
2. Clique em **Telegram**, no menu lateral.
3. Clique em **Gerar código de vinculação**.
4. Clique em **Abrir bot e vincular minha conta**.
5. No Telegram, clique em **Iniciar**, se solicitado.

O link contém um parâmetro `start`. Ao abri-lo, o Telegram envia `/start CODIGO` ao bot. Também é possível copiar esse comando manualmente.

O código dura dez minutos, só funciona uma vez e é armazenado como hash SHA-256 no banco. Gerar outro invalida o anterior. Não compartilhe o código: quem o utilizar poderá vincular a conta dele ao seu Clareza.

O site já sabe quem você é pela sessão de login; o Telegram fornece o identificador numérico de quem enviou a mensagem. O código conecta essas duas identidades. Usamos esse número, e não o `@username`, como identidade da pessoa.

Cada conta do Clareza aceita um vínculo ativo e cada identificador Telegram só pode pertencer a uma conta. Para trocar, desvincule primeiro pelo site. A desvinculação invalida códigos e descarta cadastros pendentes; ela não apaga mensagens já enviadas ao Telegram.

Referência: [deep linking](https://core.telegram.org/bots/features#deep-linking).

## 5. Enviar um comprovante e cadastrar a despesa

Envie um PDF, PNG ou JPEG em conversa privada com o bot. Para preservar a qualidade de imagens, prefira enviar como arquivo/documento, embora fotos também sejam aceitas.

O fluxo interno é:

1. `getUpdates` entrega um evento com os dados da mensagem e um `file_id`.
2. O código verifica o vínculo e o tipo privado da conversa.
3. `getFile` transforma a referência do arquivo em um caminho de download.
4. Nosso cliente baixa no máximo 10 MB; o tamanho informado pelo Telegram também é conferido antes do download.
5. `TemporaryUploadStore` valida extensão, assinatura e conteúdo e armazena o documento em área privada.
6. `DocumentExtractor` tenta ler o texto do PDF ou executar OCR, como no site.
7. Um `TelegramDraft` guarda as sugestões por até uma hora. Ainda não existe uma despesa.
8. O bot mostra campos, avisos e botões de edição.
9. Ao confirmar, `ExpenseForm` valida os dados, a propriedade do arquivo e os campos obrigatórios. Só então a despesa e seu anexo são salvos.

Você pode corrigir descrição, beneficiário, valor pago, data de pagamento, categoria, identificação da transação e observações. Use datas como `2026-11-19` e valores como `123.45` ou `123,45`, sem separador de milhares. Para limpar um campo opcional, envie `-`.

Se o OCR falhar em um documento válido, você poderá preencher os dados manualmente. Documento inválido é recusado. Mantêm-se os limites existentes: extração de PDFs com até cinco páginas, imagens até 20 megapixels/6000 pixels por lado e processamento até 30 segundos. PDFs válidos que excedem o limite de extração podem continuar com preenchimento manual.

Use `/pendente` para recuperar os botões e `/cancelar` para descartar o cadastro. Há um único pendente por conta. Se enviar outro documento antes de concluir, o bot pergunta se deve descartar o anterior; depois da confirmação, **reenvie o novo arquivo**. Isso evita baixar um segundo documento sem sua decisão.

O OCR sugere dados; não comprova autenticidade bancária ou quitação. Um boleto pode não representar um pagamento. Os avisos existentes continuam fazendo parte da revisão.

## 6. Botões e consultas

Os botões usam `InlineKeyboardMarkup`. O texto é o que você vê; `callback_data` é uma pequena instrução que volta ao servidor em um evento `callback_query`.

Por exemplo, `draft:42:confirm` pede a confirmação do pendente 42. O servidor não confia apenas nesse número: procura o pendente da conta vinculada e confere se o identificador coincide. O mesmo isolamento vale para detalhes e anexos de despesas.

Comandos disponíveis:

| Comando | Resultado |
| --- | --- |
| `/ajuda` | Instruções e exemplos |
| `/pendente` | Revisão do cadastro em andamento |
| `/cancelar` | Descarta o cadastro em andamento |
| `/resumo` | Total do mês até hoje e totais por categoria |
| `/despesas` | Despesas do mês até hoje, cinco por página |

Para outro período, informe início e fim. A categoria é opcional e deve ter o nome usado no sistema:

```text
/resumo 2026-11-01 2026-11-30
/despesas 2026-11-01 2026-11-30 Alimentação
```

No histórico, use **Anterior**, **Próxima** e **Filtrar categoria**. Toque na despesa para ver os detalhes e em **Receber comprovante** para receber seu arquivo. O envio usa `sendDocument` com upload multipart: o arquivo é transmitido ao Telegram sem tornar o armazenamento do Clareza público. Anexos só são enviados sob solicitação.

Não há IA de interpretação de perguntas nesta versão. Os comandos e filtros têm comportamento explícito, e os valores continuam usando `Decimal` e agregações do banco.

## 7. Long polling, identificadores e repetição de eventos

`getUpdates` espera até 30 segundos por novas mensagens. Assim que recebe uma resposta, o worker processa os eventos e faz outra chamada. Isso se chama long polling. Um webhook seria o caminho inverso: o Telegram chamaria um endereço HTTPS público nosso. Para a instalação local, polling dispensa esse endereço.

Cada evento possui um `update_id`. O parâmetro `offset` informa a partir de qual evento queremos continuar. Guardamos o próximo número no banco em `TelegramCursor`, para sobreviver a reinícios.

A alteração financeira e o avanço desse cursor são confirmados na mesma transação do banco. Se o download falhar antes disso, o evento pode ser tentado novamente. Se o cadastro já foi confirmado, repetir o evento não salva outra despesa. Um novo clique em um botão antigo também encontra o pendente encerrado.

As respostas ao Telegram são enviadas **depois** da confirmação do banco. Existe uma limitação intencional: se o programa parar nesse intervalo ou o envio falhar, a despesa pode estar salva sem que a mensagem de sucesso chegue. Não repetimos o cadastro para tentar reenviar a resposta. Confira `/despesas` ou use `/pendente`. Esta versão não possui uma fila persistente de saída, portanto não garante entrega de todas as respostas durante falhas de rede.

O download de entrada é repetido em falhas transitórias; respostas de saída podem exigir uma nova solicitação. O código respeita o intervalo indicado em erros 429 antes de continuar, mas não reenvia automaticamente respostas que falharam. Nenhuma exclusão de mensagens no Telegram é sincronizada com a exclusão de despesas.

## 8. Onde cada parte está no código

| Local | Responsabilidade |
| --- | --- |
| `despesas/telegram_api.py` | HTTPS, JSON, download limitado e envio multipart |
| `despesas/telegram_link.py` | Página autenticada, geração de código e desvinculação |
| `despesas/telegram_bot.py` | Conversa, revisão, autorização, consultas e confirmação |
| `despesas/management/commands/run_telegram_bot.py` | Inicialização, polling e tratamento de falhas de comunicação |
| `despesas/models.py` | Vínculos, pendentes e cursor de leitura |
| `despesas/test_telegram.py` | Testes com API simulada e documentos sintéticos |
| `templates/telegram.html` | Configuração do vínculo no site |
| `compose.yaml` | Worker opcional e volumes compartilhados |

Não foi adicionada biblioteca específica de Telegram. A biblioteca padrão do Python atende ao conjunto pequeno de métodos desta versão. O bot reutiliza os componentes Django existentes, em vez de criar uma segunda API pública do Clareza.

O volume `receipts` guarda anexos definitivos e `private_uploads` guarda temporários. O bot limpa pendentes expirados a cada minuto; a limpeza existente de arquivos temporários também continua ativa. As três tabelas novas entram pela migração `0003`; despesas antigas não são alteradas.

## 9. Verificar e resolver problemas

Com os contêineres ligados:

```powershell
docker compose exec web python manage.py test
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose logs --tail 50 telegram
```

Faça o teste manual com um documento fictício: vincule sua conta, envie o arquivo, corrija um campo, confirme, confira no site, consulte o resumo e solicite o anexo. Clique novamente no botão antigo de confirmação e verifique que nenhuma segunda despesa foi criada. Depois teste cancelar e desvincular.

Situações comuns:

| Sintoma | O que conferir |
| --- | --- |
| Bot não responde | Computador ligado, internet, serviço `telegram` iniciado e logs |
| Token ausente ou código 401/404 | Token correto no `.env`; recrie os contêineres após mudar variáveis |
| Nome não corresponde ao token | Nome de usuário informado pelo BotFather, sem `@` |
| Código 409 | Outra instância de polling usando o mesmo bot; encerre a duplicada |
| Webhook ativo | Este bot já foi usado por outra integração; escolha outro bot ou remova o webhook conscientemente |
| Código 403 ao responder | Você pode ter bloqueado o bot; desbloqueie e envie um novo comando |
| Código 429 | Limite de chamadas; aguarde e solicite novamente a resposta que faltou |
| Código de vínculo expirado | Gere outro no site |
| OCR não leu os dados | Corrija manualmente ou envie uma imagem mais nítida como arquivo |

Não removemos webhooks automaticamente para não desativar outra integração sem intenção. Se você decidiu transferir um bot que já usava webhook, pare a aplicação anterior e execute localmente, com os contêineres configurados:

```powershell
docker compose exec web python -c "import os; from despesas.telegram_api import TelegramAPI; TelegramAPI(os.environ['TELEGRAM_BOT_TOKEN']).call('deleteWebhook', drop_pending_updates=False)"
docker compose --profile telegram up -d telegram
```

Os logs de comunicação mostram códigos de erro, sem o token ou documentos. Ao trocar para **outro bot**, o cursor antigo não deve ser reutilizado: com o worker parado, redefina-o conscientemente e desvincule/revincule as contas. Renovar o token do mesmo bot não exige redefinir o cursor.

Referências oficiais: [métodos, limites, polling e erros](https://core.telegram.org/bots/api), [comandos e botões](https://core.telegram.org/bots/features).
