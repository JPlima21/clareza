# Clareza — Gerenciamento financeiro

Projeto Django + MySQL para registrar despesas pagas, anexar boletos ou comprovantes e apurar gastos. A interface mantém o menu lateral, o visual bege e as ações arredondadas.

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
