# Orquestração dos agentes no Clareza

## Aplicação a cada prompt

Esta é a preferência do usuário para o trabalho neste repositório. A cada novo
prompt, o agente principal deve identificar o objetivo, avaliar a complexidade
e escolher o modelo da tabela antes de executar o trabalho. Instruções de maior
prioridade, restrições da sessão e pedidos explícitos do usuário prevalecem.

O usuário autoriza a delegação e a seleção dos modelos abaixo. O agente principal
continua responsável por coordenar, conferir o resultado e responder ao usuário.
Esta regra orienta a delegação disponível no Codex; não troca o modelo da conversa,
não inicia um serviço externo e não adiciona chamadas à API OpenAI na aplicação.

## Escolha do modelo

| Tipo de trabalho | Modelo do subagente | Critério e exemplos |
| --- | --- | --- |
| Simples | `gpt-6.2-sol` | Objetivo delimitado, baixo risco e poucas dependências: localizar código, explicar um arquivo, adicionar comentários ou ajustar documentação. |
| Implementação | `gpt-6.1-sol` | Alterar comportamento com requisitos claros: criar funcionalidades, corrigir bugs, integrar serviços e escrever os testes correspondentes. |
| Complexo | `gpt-6.2-astra` | Resolver ambiguidades técnicas relevantes, projetar mudanças entre subsistemas, diagnosticar falhas difíceis ou revisar segurança, permissões e integridade financeira. |

Quando uma tarefa se encaixar em mais de uma categoria, usar a de maior
complexidade. Uma alteração em valores financeiros ou autorização exige revisão
complexa, mesmo que o trecho de código seja curto. A complexidade pode ser
reavaliada quando a exploração revelar novos fatos.

## Fluxo de execução

1. Avaliar cada prompt com o contexto existente. Respostas sociais, confirmações
   sem trabalho adicional e esclarecimentos imediatos podem ser respondidos pelo
   principal, sem criar subagente.
2. Para trabalho substantivo, conferir os modelos e as ferramentas realmente
   disponíveis na sessão e delegar ao modelo escolhido. Não iniciar os três
   modelos automaticamente para toda tarefa.
3. Ao selecionar um modelo explicitamente, usar seu identificador exato e
   `fork_turns="none"` se a ferramenta exigir. Fornecer ao subagente um resumo
   suficiente: pedido do usuário, decisões anteriores, arquivos pertinentes,
   restrições, escopo autorizado e critérios de conclusão. Transmitir estas
   instruções no contexto do trabalho delegado.
4. Preferir um único executor. Dividir entre agentes apenas atividades
   independentes que tragam benefício concreto. Respeitar o limite de agentes da
   sessão e atribuir arquivos distintos a editores simultâneos.
5. Em trabalhos complexos, usar Astra para diagnóstico ou definição da abordagem;
   depois usar Sol 6.1 para implementação quando houver código a alterar. Astra
   deve revisar mudanças de autorização ou integridade financeira antes da
   conclusão. Reutilizar agentes existentes quando apropriado.
6. O principal deve conferir as alterações e os testes relevantes, resolver
   divergências e entregar uma resposta única. Não aceitar uma tarefa como
   concluída apenas porque um subagente declarou sucesso.

## Disponibilidade e comunicação

- Se o modelo escolhido estiver indisponível ou a delegação falhar, informar
  brevemente e continuar com o agente principal. Não substituir silenciosamente
  por outro modelo e não afirmar que um modelo foi usado sem confirmação.
- Se não houver ferramenta de delegação, aplicar a classificação para organizar
  o trabalho do principal e explicar a limitação quando ela afetar a execução.
- Ao delegar, informar em uma frase o modelo e a finalidade. Na conclusão,
  relatar o resultado e a validação; listar modelos apenas quando for útil ou
  solicitado. Não prometer economia de custo ou velocidade sem medição.
- Não delegar segredos desnecessários: tokens, senhas, arquivos `.env` completos
  e documentos financeiros reais devem permanecer fora dos prompts dos agentes.
- Preservar alterações locais do usuário e executar apenas ações autorizadas.

## Persistência

Estas instruções valem para os prompts neste repositório quando o ambiente carregar
este `AGENTS.md`. Elas não configuram outros projetos nem garantem disponibilidade
futura dos modelos. Uma nova preferência explícita do usuário deve ser incorporada
a este arquivo para persistir além da conversa atual.
