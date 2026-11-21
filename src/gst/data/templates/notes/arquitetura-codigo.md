# {{title}}

> Jogo: {{game}} ({{engine}}, build {{build}}). Nota `{{note}}`, criada em {{date}} com o template `arquitetura-codigo`.
> Pré-preenchida de: {{sources}}.
> Evidência: `caminho:linha` (decomp/) ou chave de `analysis/code_architecture.json`.
> Legenda: **[E]** lido no código; **[I]** inferência. As contagens das tabelas são sinais de regex, não fatos: confirme
> abrindo os exemplos antes de afirmar. O que não foi confirmado vai para "Não verificado".

## 1. Resposta curta

<!-- preencher: 4-8 linhas. Como o código do jogo está organizado, o estilo dominante (herança de MonoBehaviour,
composição, dados em asset, eventos, singletons...) e o que mais vale estudar nele. -->

## 2. Estrutura: assemblies, namespaces e pastas

{{auto:code_modules}}

<!-- preencher: o que cada assembly/namespace faz (abra 2-3 arquivos de cada um com gst q outline). Separe o código
do jogo, o framework do estúdio e as bibliotecas de terceiros. -->

## 3. Bibliotecas de terceiros: para que e onde

{{auto:code_libraries}}

<!-- preencher: para cada biblioteca relevante, o que o jogo faz com ela e os arquivos centrais que a usam
(caminho:linha). Diga as que vêm no build mas o código do jogo não usa diretamente. -->

## 4. Pontos de entrada, managers e ciclo de vida

{{auto:code_entry}}

<!-- preencher: como o jogo sobe (RuntimeInitializeOnLoadMethod, bootstrap, primeira cena), quem são os managers
centrais e como se acham (singleton, service locator, referência serializada), a ordem de execução que importa e
por quê. Passos numerados com caminho:linha. -->

## 5. Padrões de projeto

{{auto:code_patterns:architecture,events,data,async,network}}

<!-- preencher: para cada padrão usado de verdade: como o jogo o implementa (um exemplo lido, caminho:linha), por que
ele resolve o problema ali e o custo. Eventos: quem publica, quem assina, se desassina. Máquinas de estado: quais e
como transitam. Dados: o que vive em ScriptableObject/prefab vs código. -->

## 6. Técnicas de desempenho

{{auto:code_patterns:performance}}

<!-- preencher: cada técnica que o jogo usa para gastar menos CPU/GC (pooling, NonAlloc, cache de componentes, ids
pré-calculados, sqrMagnitude, atualização espaçada, LOD/culling, structs, jobs), com um exemplo lido e o ganho. -->

## 7. Custos nos métodos por frame

{{auto:code_patterns:hot-cost}}

<!-- preencher: confirme nos exemplos se o custo é real (roda todo frame?) ou só em debug/editor; o que você faria
diferente. -->

## 8. Relevância para o meu jogo

<!-- preencher: o que copiar (padrões e técnicas que valem para o meu jogo), o que adaptar, o que evitar, e a ordem em
que eu implementaria. -->

## Não verificado

<!-- preencher: contagens não conferidas, padrões só inferidos pelo nome, o que dependeria de profiler/runtime. -->
-
