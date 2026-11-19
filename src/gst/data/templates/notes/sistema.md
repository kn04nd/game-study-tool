# {{title}}

> Jogo: {{game}} ({{engine}}, build {{build}}). Nota `{{note}}`, criada em {{date}} com o template `sistema`.
> Pré-preenchida de: {{sources}}.
> Evidência: `caminho:linha` (decomp/, assets/), chave de `analysis/<x>.json` ou `módulo+0xRVA`.
> Legenda: **[E]** lido no código ou nos dados; **[I]** inferência. O que não foi confirmado vai para "Não verificado".

## 1. Resposta curta

<!-- preencher: 3-6 linhas. O que o sistema faz, quem o dispara, o que ele muda. -->

## 2. Tipos e papéis

{{auto:system}}

<!-- preencher: confirme quais tipos acima pertencem mesmo ao sistema (gst q outline / gst q hier) e diga o papel de cada um. -->

## 3. Pontos de entrada

<!-- preencher: Update/FixedUpdate/_process/Tick, eventos, RPCs, input. Cada um com caminho:linha (gst q callers). -->

## 4. Fluxo (gatilho -> dados)

<!-- preencher: passos numerados do gatilho até o estado que muda, com caminho:linha em cada passo. -->

## 5. Regras

<!-- preencher: regras numeradas em linguagem simples, cada uma com evidência. -->

## 6. Fórmulas e constantes

<!-- preencher: fórmula, constantes com unidade e de onde vêm (código ou dado serializado). -->

## 7. Máquina de estados

<!-- preencher: stateDiagram-v2 em Mermaid com as condições nas transições, ou "não há". -->

## 8. Dados e parâmetros

{{auto:tunables}}

## 9. Conexões com outros sistemas

<!-- preencher: quem chama este sistema e o que ele chama (gst q callers / gst q callees). -->

## 10. Relevância para o meu jogo

<!-- preencher: o que vale como ideia, o que adaptar, custo de implementar. -->

## Não verificado

<!-- preencher: o que ficou como hipótese, o que dependeria de runtime ou de dado não extraído. -->
-
