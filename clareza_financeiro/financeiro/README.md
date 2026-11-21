<!-- Documenta a instalacao, o uso, os testes e a organizacao dos arquivos do projeto. -->
# Clareza — Gerenciamento financeiro

Projeto Django + MySQL para registrar despesas pagas, anexar boletos ou comprovantes e apurar gastos. A interface mantém o menu lateral, o visual bege e as ações arredondadas.

## Integração com Telegram

O bot opcional recebe comprovantes, permite revisar e salvar despesas na conversa, consulta gastos e entrega anexos mediante solicitação. Veja o [guia detalhado de criação, configuração e funcionamento da integração](TELEGRAM.md). O guia explica BotFather, token, vínculo de contas, métodos da API, polling, testes e solução de problemas.
## Iniciar no Windows com Docker Desktop

1. Abra o PowerShell na pasta que contém `compose.yaml`.
2. Copie a configuração: `Copy-Item .env.example .env`.
3. Edite `.env` e substitua as chaves e senhas. Para gerar `SECRET_KEY`: `python -c "import secrets; print(secrets.token_urlsafe(50))"`.
4. Execute `docker compose up --build -d`.
5. Crie o usuário: `docker compose exec web python manage.py createsuperuser`.
6. Acesse http://localhost:8000.

O contêiner web espera uma consulta MySQL válida, aplica as migrações e só então inicia o Django. Para acompanhar: `docker compose logs -f web`.

## Atualizar uma instalação existente sem perder dados

Antes da atualização, faça o backup normal do banco e do volume de comprovantes. Em seguida, substitua os arquivos da aplicação e execute:

```powershell
docker compose build --pull web
docker compose up -d db
docker compose up -d --no-deps web
docker compose exec web python manage.py showmigrations despesas
docker compose exec web python manage.py test
```

A migração `0002_extraction_fields` apenas acrescenta beneficiário e identificação da transação e atualiza o rótulo do valor; registros existentes são preservados. O volume `mysql_data` continua sendo usado e o volume `receipts` não é recriado. Não execute `docker compose down -v`, pois `-v` remove esses volumes.

## Extração e cadastro

Em **Nova despesa**, selecione PDF, PNG ou JPEG e clique em **Extrair informações**. O sistema tenta preencher apenas campos vazios e identifica visualmente as sugestões. Revise tudo e clique em **Salvar despesa** para criar o registro. A extração, isoladamente, não cria despesas.

PDFs com texto são lidos diretamente com `pypdf`. Páginas escaneadas e imagens são processadas localmente pelo Tesseract com o idioma português; o Poppler converte páginas PDF quando necessário. Nenhum documento é enviado a API externa.

O arquivo extraído fica em área privada por até uma hora, ligado ao usuário por token assinado. Ele é movido para o anexo da despesa ao salvar, sem novo upload. Arquivos expirados são removidos a cada hora no contêiner e também antes de uma nova extração.

O parser é conservador:

- procura descrição, beneficiário, valor pago, data do pagamento e identificação da transação;
- não usa pagador como beneficiário, vencimento como data de pagamento nem valor original como valor pago;
- deixa valores e datas ambíguos em branco e mostra um aviso;
- só sugere categoria quando uma palavra-chave aponta para uma única categoria existente;
- avisa quando não há evidência clara de pagamento, porque boleto não é comprovante;
- mantém o preenchimento manual disponível quando o OCR falha ou o PDF é protegido.

## Segurança e limites

- autenticação, propriedade do registro e CSRF protegem upload, extração e download;
- formato, assinatura e conteúdo são validados no servidor;
- tamanho máximo de 10 MB, até 5 páginas, imagens de até 20 megapixels/6000 pixels por lado e processamento de até 30 segundos;
- caminhos e nomes finais são aleatórios; temporários usam diretório não público;
- conteúdo extraído e dados pessoais não são enviados aos logs;
- valores monetários continuam armazenados como `Decimal`/`DecimalField`.

## Testes

No Docker:

```powershell
docker compose exec web python manage.py test
docker compose exec web python manage.py check
docker compose exec web python manage.py makemigrations --check --dry-run
```

Localmente, com dependências instaladas:

```powershell
$env:TEST_SQLITE='1'
$env:SECRET_KEY='somente-para-testes'
python manage.py test
```

Os testes usam conteúdo sintético e cobrem texto selecionável, `Decimal`, datas e valores ambíguos, boleto sem prova de pagamento, arquivo inválido, CSRF, autenticação, token temporário e acesso entre usuários.

## Limitações

OCR depende da nitidez, orientação, fonte e organização do documento. O parser usa rótulos e palavras-chave em português e pode deixar campos em branco; ele não valida autenticidade bancária, assinatura digital nem quitação. PDFs protegidos não são descriptografados. Documentos acima dos limites podem ser anexados dentro do limite de 10 MB, mas devem ser cadastrados manualmente. Toda sugestão precisa de revisão humana.


## Para que serve cada arquivo?

O caminho mais fácil para entender a aplicação é seguir este fluxo:

1. `config/urls.py` recebe o endereço acessado e escolhe uma função ou classe de `despesas/views.py`.
2. A view usa `despesas/forms.py` para validar a entrada e `despesas/models.py` para consultar ou salvar despesas no banco.
3. Os arquivos de `templates/` montam a página; os de `static/` cuidam da aparência e das interações no navegador.
4. Na extração, `static/extraction.js` envia o documento à view, que chama `despesas/document_extraction.py`. O cadastro só acontece quando você salva o formulário.

Os caminhos abaixo partem da raiz do repositório. Cada arquivo também tem uma explicação no início.

| Arquivo | Para que serve |
| --- | --- |
| `.gitignore` | Define quais arquivos o Git deve ignorar, como senhas locais, caches e documentos privados. |
| `mysql/compose.yaml` | Sobe apenas um MySQL local na porta 3306. É uma alternativa separada do Compose da aplicação. |
| `clareza_financeiro/financeiro/.dockerignore` | Exclui senhas, bancos locais e uploads da cópia enviada ao construir a imagem Docker. |
| `clareza_financeiro/financeiro/.env.example` | Modelo das variáveis de ambiente. Copie para .env e substitua as chaves e senhas antes de iniciar. |
| `clareza_financeiro/financeiro/Dockerfile` | Monta a imagem da aplicação com Python, bibliotecas MySQL, Poppler e Tesseract para leitura de documentos. |
| `clareza_financeiro/financeiro/compose.yaml` | Inicia Django e MySQL juntos; configura rede, variáveis, volumes persistentes e espera pelo banco. |
| `clareza_financeiro/financeiro/config/__init__.py` | Marca config como um pacote Python; as configurações e rotas ficam nos outros arquivos desta pasta. |
| `clareza_financeiro/financeiro/config/settings.py` | Centraliza a configuração do Django: banco, autenticação, idioma, templates, arquivos e limites de upload. |
| `clareza_financeiro/financeiro/config/urls.py` | Liga cada endereço do site à sua view: painel, cadastro, edição, exclusão, extração, anexos e login/logout. |
| `clareza_financeiro/financeiro/despesas/__init__.py` | Marca despesas como um pacote Python que reúne as funcionalidades de controle de gastos. |
| `clareza_financeiro/financeiro/despesas/document_extraction.py` | Valida PDFs e imagens, guarda uploads temporários por usuário e extrai sugestáes de pagamento por texto ou OCR. Não cadastra a despesa: os dados precisam ser revisados e salvos pelo usuário. |
| `clareza_financeiro/financeiro/despesas/forms.py` | Define os campos e validações do cadastro e dos filtros; confere anexos e tokens de arquivos já extraídos. |
| `clareza_financeiro/financeiro/despesas/management/__init__.py` | Marca o pacote de gerenciamento usado pelo Django para localizar comandos próprios do projeto. |
| `clareza_financeiro/financeiro/despesas/management/commands/__init__.py` | Marca o pacote de comandos executados com python manage.py, como wait_for_db e cleanup_expired_uploads. |
| `clareza_financeiro/financeiro/despesas/management/commands/cleanup_expired_uploads.py` | Implementa python manage.py cleanup_expired_uploads para remover documentos temporários cujo prazo expirou. |
| `clareza_financeiro/financeiro/despesas/management/commands/wait_for_db.py` | Implementa python manage.py wait_for_db: tenta consultar o banco antes de continuar a inicialização. |
| `clareza_financeiro/financeiro/despesas/migrations/0001_initial.py` | Cria a estrutura inicial da tabela de despesas e sua ligação com o usuário. O Django aplica este histórico com migrate. |
| `clareza_financeiro/financeiro/despesas/migrations/0002_extraction_fields.py` | Acrescenta beneficiário e identificação da transação e atualiza o rótulo do valor, preservando os registros existentes. |
| `clareza_financeiro/financeiro/despesas/migrations/__init__.py` | Marca o pacote de migrações que o Django usa para descobrir o histórico de alterações do banco. |
| `clareza_financeiro/financeiro/despesas/models.py` | Define a despesa armazenada no banco: dono, descrição, valor, data, categoria e anexo com nome aleatório. |
| `clareza_financeiro/financeiro/despesas/tests.py` | Reúne testes automatizados do cadastro, validações, extração e isolamento entre usuários, usando documentos sintéticos. |
| `clareza_financeiro/financeiro/despesas/views.py` | Atende às requisições do site: filtra e resume gastos, exporta CSV, cadastra/edita/exclui despesas e entrega extração e anexos com acesso por usuário. |
| `clareza_financeiro/financeiro/docker-entrypoint.sh` | Inicia o contêiner: aguarda o banco, aplica migrações, limpa uploads expirados e inicia o servidor Django. Repete a limpeza a cada hora. |
| `clareza_financeiro/financeiro/manage.py` | Entrada dos comandos Django. Use python manage.py seguido de runserver, migrate, test ou createsuperuser, por exemplo. |
| `clareza_financeiro/financeiro/requirements.txt` | Lista as dependências Python e suas versões permitidas; usadas por pip install -r requirements.txt. Tesseract e Poppler são instalados pelo Dockerfile. |
| `clareza_financeiro/financeiro/static/document-preview.css` | Organiza o formulário ao lado da prévia do documento e adapta esse layout para telas menores. |
| `clareza_financeiro/financeiro/static/document-preview.js` | Mostra a prévia de PDF ou imagem selecionada, ou do anexo salvo; atualiza a indicação de substituição e libera URLs temporárias. |
| `clareza_financeiro/financeiro/static/extraction.js` | Envia o documento para extração, mostra avisos e preenche somente campos vazios; guarda o token para anexar o arquivo ao salvar a despesa. |
| `clareza_financeiro/financeiro/static/style.css` | Define a aparência geral do site: cores, menu, painel, tabelas, formulários, estados da extração e adaptação a celulares. |
| `clareza_financeiro/financeiro/templates/base.html` | Layout compartilhado das páginas: carrega o estilo, mostra o menu e o usuário e reserva o bloco content para cada tela. |
| `clareza_financeiro/financeiro/templates/dashboard.html` | Página inicial com filtros, totais, histórico paginado, exportação CSV e gastos agrupados por categoria. |
| `clareza_financeiro/financeiro/templates/delete.html` | Tela de confirmação antes de excluir uma despesa; envia a confirmação por POST com proteção CSRF. |
| `clareza_financeiro/financeiro/templates/form.html` | Tela de cadastro e edição de despesas, com campos, anexo, prévia do documento e extração disponível no cadastro. |
| `clareza_financeiro/financeiro/templates/login.html` | Tela de entrada: apresenta o formulário de autenticação do Django dentro do layout compartilhado. |
| `clareza_financeiro/financeiro/README.md` | Documentação do projeto: instalação, uso, testes, limites e este mapa de arquivos. |

### Arquivos locais e gerados automaticamente

- `.env`: guarda as configurações e senhas reais da sua máquina; não deve ser enviado ao Git. O modelo comentado é `.env.example`.
- `__pycache__/` e `*.pyc`: caches que o Python gera automaticamente; não são código para editar.
- `*.sqlite3`: bancos locais usados quando a configuração SQLite está habilitada.
- `media/` e `private_uploads/`: documentos anexados e arquivos temporários de extração; não são código-fonte.
- `.git/`: histórico e informações internas do Git; não deve ser editado manualmente.

Os arquivos `__init__.py` identificam pacotes Python. Eles podem conter apenas um comentário: não precisam executar código para cumprir essa função.
